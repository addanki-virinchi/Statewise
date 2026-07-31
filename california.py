import csv
import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.expected_conditions import (
    element_to_be_clickable,
    presence_of_element_located,
)
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:  # pragma: no cover - fallback when webdriver_manager is unavailable
    ChromeDriverManager = None


BASE_DIR = Path(__file__).resolve().parent
SEARCH_URL = "https://search.dca.ca.gov/"
OUTPUT_FILE = BASE_DIR / "CA_DB.csv"

WAIT_SECONDS = 30
PAGE_LOAD_SLEEP = 1.0
POST_SEARCH_SLEEP = 0.5
SCROLL_POLL_SLEEP = 0.15          # how often we re-check the card count while lazy loading
SCROLL_IDLE_TIMEOUT = 4.0         # seconds without new cards before we call it done
SCROLL_MAX_SECONDS = 1800         # hard ceiling per search
HEADLESS = False  # Set to False to see the browser window during scraping

# Every card on the results page, regardless of the yes/no visibility class.
CARD_SELECTOR = "article.post"

LICENSE_TYPE_KEYWORDS = ["occupational therapist", "nursing", "dental"]

# One blank query per license type = "return everything for this license type".
# Add letters here (e.g. ["", "A", "B"]) only if the site refuses a blank search.
NAME_QUERIES = [""]

FIELDNAMES = [
    "License Type Search",
    "Name Search",
    "Name",
    "License Number",
    "License Type",
    "License Status",
    "Expiration Date",
    "Secondary Status",
    "City",
    "State",
    "County",
    "Zip",
    "Detail URL",
    "Public Record URL",
]


def normalize(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    # Do not block images here: the Cloudflare challenge widget needs them to render,
    # and the content-settings override itself reads as an automated profile.
    if HEADLESS:
        options.add_argument("--headless=new")

    profile_dir = BASE_DIR / "chrome_profile"
    if profile_dir.exists():
        options.add_argument(f"--user-data-dir={profile_dir}")

    if ChromeDriverManager is not None:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(120)
    driver.set_script_timeout(120)
    return driver


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    print("Solve Cloudflare in the browser if it appears, then press Enter to start scraping.")
    input()
    wait.until(presence_of_element_located((By.ID, "licenseType")))
    wait.until(presence_of_element_located((By.ID, "firstName")))
    wait.until(presence_of_element_located((By.ID, "lastName")))


def safe_click(driver, wait, by, value, retries=4):
    last_error = None
    for _ in range(retries):
        try:
            element = wait.until(element_to_be_clickable((by, value)))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            try:
                element.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", element)
            return element
        except (TimeoutException, StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error
    raise RuntimeError(f"Could not click element: {by}={value}")


def select_license_type(driver, wait, keyword):
    select_el = wait.until(presence_of_element_located((By.ID, "licenseType")))
    select = Select(select_el)
    keyword_lower = keyword.lower()

    for option in select.options:
        option_text = normalize(option.text)
        if keyword_lower in option_text.lower() and option.get_attribute("value") not in ("", "0"):
            select.select_by_value(option.get_attribute("value"))
            return option_text

    raise ValueError(f"No license type option matched keyword: {keyword!r}")


def clear_previous_results(driver):
    driver.execute_script(
        """
        document.querySelectorAll(arguments[0]).forEach(card => card.remove());
        """,
        CARD_SELECTOR,
    )


def set_input_value(driver, wait, element_id, value):
    input_el = wait.until(presence_of_element_located((By.ID, element_id)))
    driver.execute_script(
        """
        const el = arguments[0];
        el.value = arguments[1];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('keyup', { bubbles: true }));
        """,
        input_el,
        value,
    )


def search_button_enabled(driver):
    for selector in ("#srchSubmitHome", "#srchSubmit"):
        try:
            button = driver.find_element(By.CSS_SELECTOR, selector)
        except NoSuchElementException:
            continue
        if button.is_displayed() and button.is_enabled():
            return selector
    return None


def wait_for_search_ready(driver, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        selector = search_button_enabled(driver)
        if selector:
            return selector

        time.sleep(0.5)

    raise TimeoutError("Search button never became enabled.")


def click_search(driver, wait):
    selector = wait_for_search_ready(driver)
    safe_click(driver, wait, By.CSS_SELECTOR, selector)
    time.sleep(POST_SEARCH_SLEEP)


def count_cards(driver):
    return driver.execute_script(
        "return document.querySelectorAll(arguments[0]).length;", CARD_SELECTOR
    )


def wait_for_results(driver, wait):
    def ready(current_driver):
        try:
            if count_cards(current_driver) > 0:
                return True
            page_text = (current_driver.page_source or "").lower()
            return "no results" in page_text or "no records" in page_text
        except WebDriverException:
            return False

    wait.until(ready)


SCROLL_STEP_SCRIPT = """
const selector = arguments[0];
const cards = document.querySelectorAll(selector);
const lastCard = cards[cards.length - 1] || null;

function findScrollableParent(node) {
    let current = node ? node.parentElement : null;
    while (current) {
        const style = window.getComputedStyle(current);
        if (/(auto|scroll|overlay)/i.test(style.overflowY || '')
            && current.scrollHeight > current.clientHeight + 5) {
            return current;
        }
        current = current.parentElement;
    }
    return null;
}

const container = findScrollableParent(lastCard);
if (container) {
    container.scrollTop = container.scrollHeight;
} else {
    window.scrollTo(0, document.documentElement.scrollHeight);
}
if (lastCard) {
    lastCard.scrollIntoView({ block: 'end', inline: 'nearest' });
}
return cards.length;
"""


def scroll_results_to_end(driver):
    """Keep pushing the lazy loader until no new cards appear for SCROLL_IDLE_TIMEOUT."""
    started = time.time()
    last_progress = started
    best_count = 0
    last_report = 0

    while True:
        try:
            count = driver.execute_script(SCROLL_STEP_SCRIPT, CARD_SELECTOR)
        except WebDriverException:
            break

        count = int(count or 0)
        if count > best_count:
            best_count = count
            last_progress = time.time()
            if count - last_report >= 250:
                last_report = count
                print(f"  ...{count} cards loaded")

        now = time.time()
        if now - last_progress >= SCROLL_IDLE_TIMEOUT:
            break
        if now - started >= SCROLL_MAX_SECONDS:
            print(f"  Scroll time limit hit at {best_count} cards")
            break

        time.sleep(SCROLL_POLL_SLEEP)

    print(f"  Finished loading: {best_count} cards in {time.time() - started:.1f}s")
    return best_count


# Pulls every card in one round-trip. Per-element Selenium calls were the bottleneck:
# ~15 calls per card over the wire versus a single script execution for the whole page.
EXTRACT_SCRIPT = """
const selector = arguments[0];
const clean = t => (t || '').replace(/\\s+/g, ' ').trim();
const after = t => clean(t.slice(t.indexOf(':') + 1));

return Array.from(document.querySelectorAll(selector)).map(card => {
    const row = {
        Name: '', 'License Number': '', 'License Type': '', 'License Status': '',
        'Expiration Date': '', 'Secondary Status': '', City: '', State: '',
        County: '', Zip: '', 'Detail URL': '', 'Public Record URL': ''
    };

    const heading = card.querySelector('h3');
    if (heading) row.Name = clean(heading.textContent);

    const detail = card.querySelector('a.newTab:not(.iconLink)');
    if (detail) {
        row['Detail URL'] = detail.href || '';
        row['License Number'] = clean(detail.textContent);
    }
    if (!row['License Number']) {
        const lic = card.querySelector("span[id^='lic']");
        if (lic) row['License Number'] = clean(lic.textContent);
    }

    const pr = card.querySelector('a.iconLink.newTab');
    if (pr) row['Public Record URL'] = pr.href || '';

    card.querySelectorAll('li').forEach(li => {
        // Hint popups live inside the status <li>; drop them before reading text.
        const copy = li.cloneNode(true);
        copy.querySelectorAll('div, img').forEach(n => n.remove());
        const text = clean(copy.textContent);
        if (!text) return;
        const lower = text.toLowerCase();

        if (lower.startsWith('license type:')) row['License Type'] = after(text);
        else if (lower.startsWith('license status:')) row['License Status'] = after(text);
        else if (lower.startsWith('expiration date:')) row['Expiration Date'] = after(text);
        else if (lower.startsWith('secondary status:')) row['Secondary Status'] = after(text);
        else if (lower.startsWith('city:')) row.City = after(text);
        else if (lower.startsWith('state:')) row.State = after(text);
        else if (lower.startsWith('county:')) row.County = after(text);
        else if (lower.startsWith('zip:')) row.Zip = after(text);
    });

    return row;
});
"""


def scrape_current_results(driver):
    return driver.execute_script(EXTRACT_SCRIPT, CARD_SELECTOR) or []


def append_csv(path, records, write_header=False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def record_key(record):
    return (
        record.get("License Number", ""),
        record.get("Name", ""),
        record.get("License Type", ""),
        record.get("License Status", ""),
        record.get("City", ""),
        record.get("Zip", ""),
    )


def run_search(driver, wait, license_type_label, name_query):
    set_input_value(driver, wait, "firstName", "")
    set_input_value(driver, wait, "lastName", name_query)
    clear_previous_results(driver)
    click_search(driver, wait)
    wait_for_results(driver, wait)
    scroll_results_to_end(driver)

    records = scrape_current_results(driver)
    for record in records:
        record["License Type Search"] = license_type_label
        record["Name Search"] = name_query
    return records


def run():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    header_written = OUTPUT_FILE.exists() and OUTPUT_FILE.stat().st_size > 0
    seen = set()
    total_rows = 0

    try:
        open_search_page(driver, wait)
        for license_keyword in LICENSE_TYPE_KEYWORDS:
            try:
                license_type_label = select_license_type(driver, wait, license_keyword)
            except Exception as exc:
                print(f"ERROR selecting license type {license_keyword!r}: {exc}")
                continue

            print(f"Selected license type: {license_type_label}")

            for name_query in NAME_QUERIES:
                label = name_query or "(all)"
                try:
                    records = run_search(driver, wait, license_type_label, name_query)
                except Exception as exc:
                    print(f"ERROR on {license_type_label} / {label}: {exc}")
                    continue

                fresh = []
                for record in records:
                    key = record_key(record)
                    if key in seen:
                        continue
                    seen.add(key)
                    fresh.append(record)

                if fresh:
                    append_csv(OUTPUT_FILE, fresh, write_header=not header_written)
                    header_written = True
                    total_rows += len(fresh)

                print(
                    f"{license_type_label} | {label}: {len(fresh)} new rows "
                    f"of {len(records)} scraped (grand total {total_rows})"
                )

    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
