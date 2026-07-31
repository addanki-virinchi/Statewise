import csv
import itertools
import os
import re
import string
import time

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
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


SEARCH_URL = "https://secure.utah.gov/llv/search/index.html"
OUTPUT_FILE = "utah_manual.csv"
WAIT_SECONDS = 30
SEARCH_RESULT_WAIT_SECONDS = 60
CAPTCHA_RETRIES = 3
HEADLESS = False
START_PREFIX = "ab"

TARGET_PROFESSIONS = [
    "Certified Public Accountant",
    "Nurse",
]

FIELDNAMES = [
    "Search Prefix",
    "Name",
    "City",
    "Profession",
    "License #",
    "Status",
]


def normalize(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    if HEADLESS:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(120)
    driver.set_script_timeout(120)
    return driver


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait.until(EC.presence_of_element_located((By.ID, "fullName")))


SELECT_PROFESSIONS_JS = """
var keywords = arguments[0].map(function (k) {
    return k.replace(/\\s+/g, ' ').trim().toLowerCase();
});
var matched = [];

function tick(box) {
    if (!box.checked) {
        box.checked = true;
        box.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

document.querySelectorAll('#searchByNameForm input.licenseType').forEach(function (box) {
    var label = document.querySelector('label[for="' + box.id + '"]');
    if (!label) { return; }
    var text = label.textContent.replace(/\\s+/g, ' ').trim().toLowerCase();
    var hit = keywords.some(function (kw) {
        return text === kw || text.indexOf(kw) !== -1 || kw.indexOf(text) !== -1;
    });
    if (!hit) { return; }
    tick(box);
    matched.push(label.textContent.replace(/\\s+/g, ' ').trim());
    var li = box.closest('li');
    if (li) {
        li.querySelectorAll('ul input.licenseType').forEach(function (child) {
            tick(child);
            var childLabel = document.querySelector('label[for="' + child.id + '"]');
            if (childLabel) {
                matched.push(childLabel.textContent.replace(/\\s+/g, ' ').trim());
            }
        });
    }
});
return matched;
"""


APPEND_PROFESSIONS_JS = """
var form = document.getElementById('searchByNameForm');
form.querySelectorAll('input[name="professions"]').forEach(function (el) { el.remove(); });
form.querySelectorAll('input.licenseType:checked').forEach(function (box) {
    var input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'professions';
    input.value = box.value;
    form.appendChild(input);
});
return form.querySelectorAll('input[name="professions"]').length;
"""


def prepare_form(driver, prefix, verbose=False):
    """Fill the Utah search form.

    The order is:
    1. choose the "beginning" radio
    2. type the two-letter prefix
    3. tick the target profession checkboxes
    """
    driver.execute_script(
        "var r = document.getElementById('beginning'); if (r && !r.checked) { r.click(); }"
    )

    name_input = driver.find_element(By.ID, "fullName")
    driver.execute_script(
        """
        arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """,
        name_input,
        prefix,
    )

    matched = driver.execute_script(SELECT_PROFESSIONS_JS, TARGET_PROFESSIONS)
    count = driver.execute_script(APPEND_PROFESSIONS_JS)
    if verbose:
        print(f"[form] {count} profession filters selected: {matched}")
    if not matched:
        print(f"[form] WARNING: no checkbox matched {TARGET_PROFESSIONS}")
    return matched


def click_search(driver, wait):
    button = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                "//form[@id='searchByNameForm']//input[@type='submit' and normalize-space(@value)='Search']",
            )
        )
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    try:
        button.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", button)


def get_v2_sitekey(driver):
    key = driver.execute_script(
        "var el = document.querySelector('.g-recaptcha[data-sitekey]');"
        "return el ? el.getAttribute('data-sitekey') : '';"
    )
    return (key or "").strip()


def v2_challenge_present(driver):
    try:
        return bool(get_v2_sitekey(driver))
    except WebDriverException:
        return False


def wait_for_manual_captcha_clear(driver, wait, prefix):
    """Pause until the person at the keyboard solves the CAPTCHA."""
    if not v2_challenge_present(driver):
        return False

    print(
        f"[captcha] reCAPTCHA challenge detected for {prefix}. "
        "Solve it in the browser window, then press Enter here to continue."
    )
    try:
        input("[captcha] Press Enter after you finish the challenge...")
    except EOFError:
        pass

    def cleared(current_driver):
        try:
            if get_results_table(current_driver) is not None:
                return True
            if no_records_present(current_driver):
                return True
            return not v2_challenge_present(current_driver)
        except WebDriverException:
            return False

    try:
        wait.until(cleared)
    except TimeoutException:
        return False
    return True


def get_results_table(driver):
    xpaths = [
        "//th[normalize-space()='Licensee Name']/ancestor::table[1]",
        "//th[contains(normalize-space(), 'Name')]/ancestor::table[1]",
    ]
    for xpath in xpaths:
        try:
            return driver.find_element(By.XPATH, xpath)
        except NoSuchElementException:
            continue
    return None


def no_records_present(driver):
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except WebDriverException:
        return False
    return any(
        marker in body_text
        for marker in ("no records", "no results", "returned no", "0 records")
    )


def wait_for_search_outcome(driver, wait):
    """Classify what happened after clicking Search."""

    def outcome(current_driver):
        try:
            if v2_challenge_present(current_driver):
                return "challenge"
            url = current_driver.current_url
            if "message=error.captcha" in url:
                return "captcha"
            if get_results_table(current_driver) is not None:
                return "results"
            if no_records_present(current_driver):
                return "empty"
        except WebDriverException:
            pass
        return False

    return wait.until(outcome)


def get_result_rows(driver):
    table = get_results_table(driver)
    if table is None:
        return []

    rows = table.find_elements(By.XPATH, ".//tr")
    valid_rows = []
    for row in rows:
        cells = row.find_elements(By.XPATH, "./td")
        if len(cells) >= 5:
            valid_rows.append(row)
    return valid_rows


def parse_row(row, prefix):
    cells = row.find_elements(By.XPATH, "./td")
    name = cells[0].text.strip()
    city = cells[1].text.strip()
    profession_parts = [
        part.text.strip()
        for part in cells[2].find_elements(By.CSS_SELECTOR, "p")
        if part.text.strip()
    ]
    profession = " | ".join(profession_parts) if profession_parts else cells[2].text.strip()
    license_number = cells[3].text.strip()
    status = cells[4].text.strip()

    return {
        "Search Prefix": prefix,
        "Name": name,
        "City": city,
        "Profession": profession,
        "License #": license_number,
        "Status": status,
    }


def get_next_page_link(driver):
    candidates = [
        (By.ID, "pagination-next"),
        (By.CSS_SELECTOR, "a[rel='next']"),
        (By.XPATH, "//a[starts-with(normalize-space(), 'Next')]"),
        (By.XPATH, "//a[normalize-space()='>' or normalize-space()='>>']"),
    ]
    for by, selector in candidates:
        try:
            link = driver.find_element(by, selector)
        except NoSuchElementException:
            continue
        class_name = (link.get_attribute("class") or "").lower()
        aria_disabled = (link.get_attribute("aria-disabled") or "").lower()
        if "disabled" in class_name or aria_disabled == "true":
            return None
        return link
    return None


def get_first_row_signature(driver):
    rows = get_result_rows(driver)
    if not rows:
        return None
    cells = rows[0].find_elements(By.XPATH, "./td")
    if len(cells) < 5:
        return None
    return tuple(cell.text.strip() for cell in cells[:5])


def wait_for_result_change(driver, wait, previous_signature):
    def page_changed(current_driver):
        try:
            signature = get_first_row_signature(current_driver)
            return signature is not None and signature != previous_signature
        except WebDriverException:
            return False

    wait.until(page_changed)


def scrape_all_pages(driver, wait, prefix):
    records = []
    seen = set()

    while True:
        current_rows = get_result_rows(driver)
        if not current_rows:
            break

        for row in current_rows:
            try:
                record = parse_row(row, prefix)
            except StaleElementReferenceException:
                continue
            key = (
                record["Name"],
                record["City"],
                record["Profession"],
                record["License #"],
                record["Status"],
            )
            if key not in seen:
                seen.add(key)
                records.append(record)

        next_link = get_next_page_link(driver)
        if next_link is None:
            break

        previous_signature = get_first_row_signature(driver)
        if previous_signature is None:
            break

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_link)
        try:
            next_link.click()
        except WebDriverException:
            driver.execute_script("arguments[0].click();", next_link)

        try:
            wait_for_result_change(driver, wait, previous_signature)
        except TimeoutException:
            break

        time.sleep(0.2)

    return records


def write_csv(path, records):
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def generate_prefixes():
    letters = string.ascii_lowercase
    return [
        f"{a}{b}"
        for a, b in itertools.product(letters, repeat=2)
        if f"{a}{b}" >= START_PREFIX
    ]


def run_search(driver, wait, result_wait, prefix, first_run):
    """Run one prefix search without any automated captcha service.

    Flow:
    1. Open the Utah search page.
    2. Select the beginning radio and enter the two-letter prefix.
    3. Tick the target profession boxes.
    4. Click Search.
    5. If the site shows a CAPTCHA, solve it manually in the browser.
    """
    for attempt in range(1, CAPTCHA_RETRIES + 1):
        open_search_page(driver, wait)
        prepare_form(driver, prefix, verbose=first_run and attempt == 1)
        click_search(driver, wait)

        try:
            outcome = wait_for_search_outcome(driver, result_wait)
        except TimeoutException:
            print(f"[{prefix}] no response after search (attempt {attempt}), retrying")
            continue

        if outcome == "challenge":
            if not wait_for_manual_captcha_clear(driver, result_wait, prefix):
                print(f"[{prefix}] captcha was not cleared (attempt {attempt}), retrying")
                time.sleep(2 * attempt)
                continue
            try:
                outcome = wait_for_search_outcome(driver, result_wait)
            except TimeoutException:
                print(f"[{prefix}] no response after captcha solve (attempt {attempt}), retrying")
                continue

        if outcome in ("results", "empty"):
            return outcome

        print(f"[{prefix}] challenge not cleared (attempt {attempt}), retrying")
        time.sleep(2 * attempt)

    return "failed"


def run():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.25)
    result_wait = WebDriverWait(driver, SEARCH_RESULT_WAIT_SECONDS, poll_frequency=0.5)
    total = 0

    try:
        prefixes = generate_prefixes()
        for index, prefix in enumerate(prefixes):
            print(f"Running prefix: {prefix}")
            outcome = run_search(driver, wait, result_wait, prefix, first_run=index == 0)

            if outcome == "results":
                records = scrape_all_pages(driver, wait, prefix)
                print(f"Collected {len(records)} rows for {prefix}")
                if records:
                    write_csv(OUTPUT_FILE, records)
                    total += len(records)
            elif outcome == "empty":
                print(f"No records for {prefix}")
            else:
                print(f"[{prefix}] giving up after {CAPTCHA_RETRIES} attempts")

        print(f"Saved {total} rows to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
