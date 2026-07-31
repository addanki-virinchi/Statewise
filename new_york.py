import csv
import os
import re
import string
import time

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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


SEARCH_URL = "https://eservices.nysed.gov/professions/verification-search"
OUTPUT_FILE = "new_york.csv"
WAIT_SECONDS = 15
PAGE_LOAD_SLEEP = 1.2
RESULT_WAIT_SECONDS = 15
RESULT_SETTLE_SECONDS = 1.0
HEADLESS = False

SEARCH_MODE = "Licensee Name"

PROFESSIONS = [
    #"Certified Behavior Analyst Assistant (078)",
    "Registered Dental Assistant (049)",
    "Certified Dietitian/Nutritionist (048)",
    "Clinical Laboratory Technician (094)",
    "Clinical Laboratory Technologist (092)",
    "Clinical Nurse Specialist (CN)",
    "Dental Assistant, Certified (049)",
    "Dental Hygienist (051)",
    "Dentist (050)",
    "Dietitian/Nutritionist, Certified (048)",
    "Doctor (Physician) (060)",
    "Licensed Behavior Analyst (071)",
    "Licensed Clinical Social Worker (073)",
    "Licensed Practical Nurse (010)",
    "Medical Physicist, Diagnostic Radiology (009)",
    "Medical Physicist, Medical Health (011)",
    "Medical Physicist, Medical Nuclear (012)",
    "Medical Physicist, Therapeutic Radiology (013)",
    "Nurse Practitioner - All Specialties (029)",
    "Occupational Therapy Assistant (064)",
    "Occupational Therapist (063)",
    "Pharmacist (020)",
    "Registered Pharmacy Technician (002)",
    "Physical Therapist (062)",
    "Physical Therapist Assistant (066)",
    "Physician Assistant (023)",
    "Physician (060)",
    "Registered Physician Assistant (023)",
    "Registered Professional Nurse (022)",
    "Respiratory Therapist (052)",
    "Respiratory Therapy Technician (053)"
]
PREFIXES = list(string.ascii_uppercase)

FIELDNAMES = [
    "Searched Profession",
    "Search Prefix",
    "License #",
    "Name",
    "Profession",
    "Address",
    "Date of Licensure",
]


def normalize(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def quote_xpath(value):
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in parts) + ")"


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


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "goButton")))


def safe_click(driver, wait, by, value, retries=4):
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
            time.sleep(0.5)
    if last_error:
        raise last_error
    raise RuntimeError(f"Could not click element: {by}={value}")


def _vue_inputs(driver):
    return driver.find_elements(By.CSS_SELECTOR, "div.vue-select input[autocomplete='off']")


def click_visible_vue_option(driver, option_text):
    xpath = (
        "//li[contains(@class,'vue-dropdown-item') and @role='option'"
        f" and .//span[normalize-space()={quote_xpath(option_text)}]]"
    )
    matches = driver.find_elements(By.XPATH, xpath)
    for match in matches:
        try:
            if match.is_displayed():
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", match)
                try:
                    match.click()
                except ElementClickInterceptedException:
                    driver.execute_script("arguments[0].click();", match)
                time.sleep(0.4)
                return True
        except WebDriverException:
            continue
    return False


def set_vue_select_by_index(driver, wait, index, value):
    last_error = None
    for _ in range(5):
        try:
            wait.until(lambda d: len(_vue_inputs(d)) > index)
            input_el = _vue_inputs(driver)[index]
            current = normalize(
                input_el.get_attribute("placeholder")
                or input_el.get_attribute("value")
                or ""
            )
            if normalize(current) == normalize(value):
                return

            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", input_el)
            try:
                input_el.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", input_el)
            time.sleep(0.2)

            try:
                input_el.send_keys(Keys.CONTROL, "a")
                input_el.send_keys(Keys.BACKSPACE)
            except WebDriverException:
                pass
            input_el.send_keys(value)
            time.sleep(0.5)

            if click_visible_vue_option(driver, value):
                return

            input_el.send_keys(Keys.ENTER)
            time.sleep(0.5)
            if click_visible_vue_option(driver, value):
                return
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error
    raise RuntimeError(f"Could not select vue option: {value}")


def select_search_mode(driver, wait):
    set_vue_select_by_index(driver, wait, 0, SEARCH_MODE)
    time.sleep(0.4)


def select_profession(driver, wait, profession):
    set_vue_select_by_index(driver, wait, 1, profession)
    time.sleep(0.4)


def set_search_prefix(driver, prefix):
    search_input = driver.find_element(By.ID, "searchInput")
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_input)
    try:
        search_input.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", search_input)
    try:
        search_input.send_keys(Keys.CONTROL, "a")
        search_input.send_keys(Keys.BACKSPACE)
    except WebDriverException:
        driver.execute_script("arguments[0].value = '';", search_input)
    driver.execute_script(
        """
        arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """,
        search_input,
        prefix,
    )


def click_go(driver, wait):
    safe_click(driver, wait, By.ID, "goButton")
    time.sleep(RESULT_SETTLE_SECONDS)


def get_table_state(driver):
    return driver.execute_script(
        """
        var table = document.querySelector('#searchTable');
        var loading = document.querySelector('.fixed-table-loading');
        var result = {
            tablePresent: !!table,
            loading: !!loading && loading.offsetParent !== null,
            empty: false,
            rows: []
        };
        if (!table) return result;

        var emptyRow = table.querySelector('tbody tr.no-records-found');
        if (emptyRow) result.empty = true;

        var trs = table.querySelectorAll('tbody tr[data-index]');
        for (var tr of trs) {
            var tds = tr.querySelectorAll('td');
            if (!tds || tds.length < 5) continue;
            var cells = Array.from(tds).map(function (td) {
                return (td.innerText || '').replace(/\\s+/g, ' ').trim();
            });
            if (!cells[0] && !cells[1]) continue;
            result.rows.push({
                licenseNumber: cells[0] || '',
                name: cells[1] || '',
                profession: cells[2] || '',
                address: cells[3] || '',
                dateOfLicensure: cells[4] || ''
            });
        }
        return result;
        """
    )


def get_data_rows(driver):
    return get_table_state(driver).get("rows", [])


def wait_for_results_or_empty(driver, wait):
    end_time = time.time() + RESULT_WAIT_SECONDS
    empty_since = None

    while time.time() < end_time:
        try:
            state = get_table_state(driver)
            if state.get("loading"):
                empty_since = None
                time.sleep(0.3)
                continue

            rows = state.get("rows") or []
            if rows:
                return True

            if state.get("empty"):
                if empty_since is None:
                    empty_since = time.time()
                elif time.time() - empty_since >= 1.5:
                    return False
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            pass
        time.sleep(0.4)
    return False


def wait_for_result_refresh(driver, wait, before_signature):
    end_time = time.time() + RESULT_WAIT_SECONDS
    empty_since = None

    while time.time() < end_time:
        try:
            state = get_table_state(driver)
            if state.get("loading"):
                empty_since = None
                time.sleep(0.3)
                continue

            signature = get_first_row_signature(driver)
            if signature is None and state.get("empty"):
                if empty_since is None:
                    empty_since = time.time()
                elif time.time() - empty_since >= 1.0:
                    return False
            elif signature is not None and signature != before_signature:
                return True
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            pass
        time.sleep(0.4)

    return bool(get_first_row_signature(driver))


def parse_row(row):
    return {
        "License #": row.get("licenseNumber", ""),
        "Name": row.get("name", ""),
        "Profession": row.get("profession", ""),
        "Address": row.get("address", ""),
        "Date of Licensure": row.get("dateOfLicensure", ""),
    }


def extract_current_page_records(driver):
    for _ in range(4):
        try:
            return [parse_row(row) for row in get_data_rows(driver)]
        except StaleElementReferenceException:
            time.sleep(0.3)
    return []


def get_total_results(driver):
    """Read the 'Showing X to Y of N rows' text and return N (total result count)."""
    return driver.execute_script(
        """
        var info = document.querySelector('.pagination-info');
        if (!info) return null;
        var m = (info.textContent || '').replace(/,/g, '').match(/of\\s+(\\d+)\\s+rows/i);
        return m ? parseInt(m[1], 10) : null;
        """
    )


def get_page_size_info(driver):
    """Return available page-size options, the current size, and the control kind."""
    return driver.execute_script(
        """
        var result = { options: [], current: null, kind: null };

        var span = document.querySelector('.page-list .page-size');
        if (span) {
            var c = parseInt((span.textContent || '').replace(/\\D/g, ''), 10);
            if (c) result.current = c;
        }

        var items = document.querySelectorAll('.page-list .dropdown-menu .dropdown-item');
        if (items.length) {
            result.kind = 'dropdown';
            items.forEach(function (it) {
                var n = parseInt((it.textContent || '').replace(/\\D/g, ''), 10);
                if (n) result.options.push(n);
            });
        }

        if (!result.options.length) {
            var sel = document.querySelector('.page-list select, select.page-size');
            if (sel) {
                result.kind = 'select';
                Array.from(sel.options).forEach(function (o) {
                    var n = parseInt((o.textContent || o.value || '').replace(/\\D/g, ''), 10);
                    if (n) result.options.push(n);
                });
                var cur = parseInt((sel.value || '').replace(/\\D/g, ''), 10);
                if (cur) result.current = cur;
            }
        }

        return result;
        """
    )


def choose_page_size(options, total):
    """Pick the smallest option that fits all results on one page; else the largest."""
    if not options:
        return None
    options = sorted(set(options))
    if total:
        for opt in options:
            if opt >= total:
                return opt
    return options[-1]


def set_page_size(driver, wait):
    """Select the page size based on the total number of results.

    Returns (total, page_size) so the caller knows how many pages to expect.
    """
    total = get_total_results(driver)
    info = get_page_size_info(driver)
    options = info.get("options") or []
    target = choose_page_size(options, total)
    if not target:
        return total, info.get("current")

    if info.get("current") == target:
        return total, target

    before = get_first_row_signature(driver)
    changed = False

    if info.get("kind") == "dropdown":
        changed = bool(
            driver.execute_script(
                """
                var target = arguments[0];
                var toggle = document.querySelector('.page-list .dropdown-toggle');
                if (toggle) { toggle.click(); }
                var items = document.querySelectorAll('.page-list .dropdown-menu .dropdown-item');
                for (var it of items) {
                    var n = parseInt((it.textContent || '').replace(/\\D/g, ''), 10);
                    if (n === target) {
                        it.scrollIntoView({block: 'center'});
                        it.click();
                        return true;
                    }
                }
                return false;
                """,
                target,
            )
        )
    else:
        try:
            for select_el in driver.find_elements(By.CSS_SELECTOR, ".page-list select, select.page-size"):
                select = Select(select_el)
                for option in select.options:
                    digits = re.sub(r"\D", "", option.get_attribute("value") or option.text or "")
                    if digits and int(digits) == target:
                        select.select_by_value(option.get_attribute("value"))
                        changed = True
                        break
                if changed:
                    break
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            changed = False

    if changed:
        try:
            wait.until(lambda d: get_first_row_signature(d) not in (None, before))
        except TimeoutException:
            pass
        time.sleep(0.8)

    return total, target


def get_first_row_signature(driver):
    rows = get_data_rows(driver)
    if not rows:
        return None
    first = rows[0]
    return (
        first.get("licenseNumber", ""),
        first.get("name", ""),
        first.get("profession", ""),
        first.get("address", ""),
        first.get("dateOfLicensure", ""),
    )


def get_active_page_number(driver):
    return driver.execute_script(
        """
        var active = document.querySelector('li.page-item.active a.page-link, li.active a.page-link');
        if (!active) return null;
        var n = parseInt((active.textContent || '').replace(/\\D/g, ''), 10);
        return isNaN(n) ? null : n;
        """
    )


def go_to_page(driver, wait, page_number):
    """Click the explicit 'to page N' link. Returns True only if the rows changed.

    Does not rely on reading the active page number from the DOM, and searches
    every 'a.page-link' on the page (not just those under a '.pagination' ul) so
    it works regardless of the pagination container's class.
    """
    before_sig = get_first_row_signature(driver)
    if before_sig is None:
        return False

    clicked = driver.execute_script(
        """
        function norm(text) { return (text || '').replace(/\\s+/g, ' ').trim(); }
        var target = String(arguments[0]);
        var links = Array.from(document.querySelectorAll('a.page-link, .pagination a, .page-list ~ * a'));
        for (var link of links) {
            var label = norm(link.getAttribute('aria-label'));
            var text = norm(link.textContent);
            if (label === 'to page ' + target || text === target) {
                var item = link.closest('li') || link;
                if (item.classList && item.classList.contains('disabled')) return false;
                if (item.classList && item.classList.contains('active')) return 'already';
                item.scrollIntoView({block: 'center'});
                link.click();
                return true;
            }
        }
        return false;
        """,
        page_number,
    )

    if clicked == "already":
        return True
    if not clicked:
        return False

    try:
        wait.until(lambda d: get_first_row_signature(d) not in (None, before_sig))
    except TimeoutException:
        pass

    time.sleep(0.5)
    return get_first_row_signature(driver) not in (None, before_sig)


def scrape_all_pages(driver, wait, total=None, page_size=None):
    records = []
    seen = set()
    visited_pages = set()

    # Expected number of pages, e.g. 196 results @ 100/page -> 2 pages.
    max_pages = None
    if total and page_size:
        max_pages = (total + page_size - 1) // page_size

    page = 1
    last_sig = None
    while True:
        # Wrap-around guard: if the same first row reappears, we did not really
        # advance (the site bounced us back), so stop.
        sig = get_first_row_signature(driver)
        if sig is not None and sig == last_sig:
            break
        last_sig = sig
        visited_pages.add(page)

        for row in extract_current_page_records(driver):
            key = (
                row.get("License #", ""),
                row.get("Name", ""),
                row.get("Profession", ""),
                row.get("Date of Licensure", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(row)

        # Stop conditions, any of which guarantees we are done:
        #   1. We collected every result the site reported.
        if total and len(records) >= total:
            break
        #   2. We reached the expected last page.
        if max_pages and page >= max_pages:
            break
        #   3. There is no genuine next page (last page, or a wrap).
        if not go_to_page(driver, wait, page + 1):
            break
        page += 1

    return records


def append_csv(path, records, write_header=False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def run_search(driver, wait, profession, prefix):
    before_signature = get_first_row_signature(driver)
    set_search_prefix(driver, prefix)
    click_go(driver, wait)
    if not wait_for_result_refresh(driver, wait, before_signature):
        return []

    total, page_size = set_page_size(driver, wait)
    time.sleep(0.4)
    print(f"  detected total={total}, page_size={page_size}")
    return scrape_all_pages(driver, wait, total, page_size)


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    header_written = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
    total_rows = 0

    try:
        open_search_page(driver, wait)
        select_search_mode(driver, wait)

        for profession in PROFESSIONS:
            try:
                select_profession(driver, wait, profession)
            except Exception as exc:
                print(f"ERROR selecting profession '{profession}': {exc}")
                continue

            for prefix in PREFIXES:
                try:
                    records = run_search(driver, wait, profession, prefix)
                except Exception as exc:
                    print(f"ERROR on {profession} / {prefix}: {exc}")
                    records = []

                if records:
                    output_rows = []
                    for record in records:
                        row = dict(record)
                        row["Searched Profession"] = profession
                        row["Search Prefix"] = prefix
                        output_rows.append(row)

                    append_csv(OUTPUT_FILE, output_rows, write_header=not header_written)
                    header_written = True
                    total_rows += len(records)

                print(f"{profession} | {prefix}: {len(records)} rows (total {total_rows})")
    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
