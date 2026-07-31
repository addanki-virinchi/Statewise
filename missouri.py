import csv
import time
from typing import Sequence

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager



SEARCH_URL = "https://mopro.mo.gov/license/s/license-search"
OUTPUT_FILE = "missouri.csv"
WAIT_SECONDS = 45
PAGE_LOAD_SLEEP = 1.2
HEADLESS = False

KEYWORDS = [
    "Anesthesiologist Assistant",
    "Anesthesiologist Assistant Temp",
    "Assistant Behavior Analyst",
    "Assistant Physician",
    "Audiologist",
    "Audiologist Aide",
    "Audiologist Provisional",
    # "Behavior Analyst",
    # "Chiropractic Physician",
    # "Clinical Social Worker",
    # "Dental Assistant Nitrous Permit",
    # "Dental Hygienist",
    # "Dental Specialist",
    # "Dentist",
    # "Dietitian",
    # "EF-Orthodontics",
    # "Hearing Instrument Specialist",
    # "Intern Pharmacist"
]

FIELDNAMES = [
    "Selected Keywords",
    "S.NO.",
    "Licensee Name",
    "Profession Name",
    "City",
    "State",
    "Action ID",
]


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    if HEADLESS:
        options.add_argument("--headless=new")

    try:
        return webdriver.Chrome(options=options)
    except Exception:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def safe_click(driver, wait, by, value, retries=4, sleep_seconds=0.6):
    last_error = None
    for _ in range(retries):
        try:
            element = wait.until(EC.element_to_be_clickable((by, value)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            try:
                element.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", element)
            return element
        except (TimeoutException, StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(sleep_seconds)
    raise last_error


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)


def select_active_licensee_search(driver, wait):
    radio_xpath = (
        "//label[contains(@class,'slds-radio__label')"
        " and .//span[normalize-space()='Active Licensee Search']]"
    )
    safe_click(driver, wait, By.XPATH, radio_xpath)
    wait.until(lambda d: d.find_elements(By.XPATH, "//span[normalize-space()='Available']"))
    time.sleep(0.5)


def click_available_keyword(driver, wait, keyword):
    option_xpath = (
        "//div[@role='option' and @data-type and "
        ".//span[@title={0} or normalize-space()={0}]]"
    ).format(repr(keyword))
    safe_click(driver, wait, By.XPATH, option_xpath)


def move_selection_to_selected(driver, wait):
    button_xpath = "//button[@title='Move selection to Selected']"
    safe_click(driver, wait, By.XPATH, button_xpath)
    time.sleep(0.5)


def select_keywords(driver, wait, keywords: Sequence[str]):
    for keyword in keywords:
        click_available_keyword(driver, wait, keyword)
        move_selection_to_selected(driver, wait)


def select_none_option(driver, wait):
    none_xpath = "//label[contains(@class,'slds-radio__label') and .//span[normalize-space()='None']]"
    safe_click(driver, wait, By.XPATH, none_xpath)
    time.sleep(0.5)


def click_submit(driver, wait):
    safe_click(driver, wait, By.XPATH, "//button[@title='Submit']")
    time.sleep(PAGE_LOAD_SLEEP)


# The results table and pagination controls are rendered inside a native
# Shadow DOM (Salesforce Lightning Web Component). Selenium's CSS-selector and
# document.querySelector lookups cannot pierce shadow roots, so every selector
# below uses a recursive, shadow-piercing querySelectorAll executed in the page.
_DEEP_QSA = """
function deepQSA(sel, root) {
    root = root || document;
    let out = Array.from(root.querySelectorAll(sel));
    for (const el of root.querySelectorAll('*')) {
        if (el.shadowRoot) out = out.concat(deepQSA(sel, el.shadowRoot));
    }
    return out;
}
"""

_FETCH_ROWS_JS = _DEEP_QSA + """
const tables = deepQSA('table.licensee-table');
if (!tables.length) return null;                 // table not rendered yet
const table = tables[0];
const rows = [];
for (const tr of table.querySelectorAll('tbody tr')) {
    if (tr.querySelector('td.dataTables_empty')) return [];   // results ready, but empty
    const tds = tr.querySelectorAll('td');
    if (tds.length < 5) continue;
    let actionId = '';
    const btn = tr.querySelector('lightning-button[data-id]');
    if (btn) actionId = btn.getAttribute('data-id') || '';
    rows.push({
        cells: Array.from(tds).map(td => (td.textContent || '').trim()),
        actionId: actionId,
    });
}
return rows;
"""

_NEXT_AVAILABLE_JS = _DEEP_QSA + """
const btns = deepQSA('button[title="next"]');
if (!btns.length) return false;
const b = btns[0];
const disabled = b.disabled || (b.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
return !disabled;
"""

_CLICK_NEXT_JS = _DEEP_QSA + """
const btns = deepQSA('button[title="next"]');
if (!btns.length) return false;
const b = btns[0];
const disabled = b.disabled || (b.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
if (disabled) return false;
b.scrollIntoView({block: 'center'});
b.click();
return true;
"""


def fetch_page_rows(driver):
    """Return None if the results table has not rendered yet, otherwise the
    list of row dicts for the current page (possibly empty)."""
    try:
        return driver.execute_script(_FETCH_ROWS_JS)
    except WebDriverException:
        return None


def get_table_rows(driver):
    return fetch_page_rows(driver) or []


def wait_for_results(driver, wait):
    try:
        # Ready as soon as the table exists, whether it has rows or is empty.
        wait.until(lambda d: fetch_page_rows(d) is not None)
        return True
    except TimeoutException:
        return False


def parse_current_page(driver, selected_keywords: Sequence[str]):
    records = []
    for row in get_table_rows(driver):
        cells = row.get("cells") or []
        if len(cells) < 5:
            continue
        records.append(
            {
                "Selected Keywords": " | ".join(selected_keywords),
                "S.NO.": cells[0],
                "Licensee Name": cells[1],
                "Profession Name": cells[2],
                "City": cells[3],
                "State": cells[4],
                "Action ID": row.get("actionId", ""),
            }
        )
    return records


def get_first_row_signature(driver):
    rows = get_table_rows(driver)
    if not rows:
        return None
    first = rows[0].get("cells") or []
    return tuple(first[:5])


def next_page_available(driver):
    try:
        return bool(driver.execute_script(_NEXT_AVAILABLE_JS))
    except WebDriverException:
        return False


def click_next_page(driver, wait):
    before = get_first_row_signature(driver)
    if before is None:
        return False

    try:
        if not driver.execute_script(_CLICK_NEXT_JS):
            return False
    except WebDriverException:
        return False

    try:
        wait.until(lambda d: get_first_row_signature(d) not in (None, before))
    except TimeoutException:
        return False

    time.sleep(PAGE_LOAD_SLEEP)
    return True


def scrape_all_pages(driver, wait, selected_keywords: Sequence[str]):
    records = []
    seen = set()

    while True:
        page_rows = parse_current_page(driver, selected_keywords)
        for row in page_rows:
            key = (
                row.get("S.NO.", ""),
                row.get("Licensee Name", ""),
                row.get("Profession Name", ""),
                row.get("City", ""),
                row.get("State", ""),
                row.get("Action ID", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(row)

        if not next_page_available(driver):
            break
        if not click_next_page(driver, wait):
            break

    return records


def append_csv(path, records, write_header=False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def run_search(driver, wait, keyword: str):
    """Run a fresh Active Licensee Search for a single keyword and return all
    of its result rows. Reloading the page each time keeps the selection state
    clean so one keyword's results never leak into the next."""
    open_search_page(driver, wait)
    select_active_licensee_search(driver, wait)
    select_keywords(driver, wait, [keyword])
    select_none_option(driver, wait)
    click_submit(driver, wait)

    if not wait_for_results(driver, wait):
        return []

    return scrape_all_pages(driver, wait, [keyword])


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    total_rows = 0
    header_written = False

    try:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig"):
            pass

        for keyword in KEYWORDS:
            try:
                records = run_search(driver, wait, keyword)
            except Exception as exc:  # don't let one keyword abort the whole run
                print(f"Active Licensee Search | {keyword}: ERROR {exc!r}")
                continue

            if records:
                append_csv(OUTPUT_FILE, records, write_header=not header_written)
                header_written = True
                total_rows += len(records)

            print(f"Active Licensee Search | {keyword}: {len(records)} rows (total {total_rows})")
    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
