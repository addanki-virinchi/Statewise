import csv
import os
import re
import time

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


LANDING_URL = "https://www.pals.pa.gov/#!/page/search"
WAIT_SECONDS = 45
PAGE_LOAD_SLEEP = 1.8
RESULT_WAIT_SECONDS = 40
RESULT_SETTLE_SECONDS = 3.0
EMPTY_STABLE_SECONDS = 12.0
HEADLESS = False
OUTPUT_FILE = "Pennsylvania.csv"

KEYWORDS = [
    "State Board of Dentistry",
    "State Board of Medicine",
    "State Board of Nursing",
    "State Board of Occupational Therapy",
    "State Board of Optometry",
    "State Board of Osteopathic Medicine",
    "State Board of Pharmacy",
    "State Board of Physical Therapy",
    "State Board of Psychology",
    "Radiology Personnel",
    "State Board of Examiners in Speech-Language Pathology and Audiology",
]

STATES = [
    "AL", "AK", "AS", "AZ", "AR", "CA", "CO", "CT", "DE", "DC",
    "FL", "GA", "GU", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
    "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "MP", "OH", "OK",
    "OR", "PA", "PR", "RI", "SC", "SD", "TN", "TX", "VI", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "OTHER"
]

FIELDNAMES = [
    "Searched Board/Commission",
    "Searched Country",
    "Searched State",
    "Full Name",
    "License Number",
    "Board/Commission",
    "License Type",
    "Status",
    "Address",
]


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().upper()


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    if HEADLESS:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_page(driver, wait):
    driver.get(LANDING_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "selBoardOrCommission")))


def get_select(driver, locator):
    return Select(driver.find_element(*locator))


def select_board(driver, board_name):
    wait = WebDriverWait(driver, WAIT_SECONDS)
    wait.until(
        lambda d: len(
            get_select(d, (By.ID, "selBoardOrCommission")).options
        ) > 1
    )
    last_error = None
    for _ in range(3):
        try:
            select = get_select(driver, (By.ID, "selBoardOrCommission"))
            select.select_by_visible_text(board_name)
            time.sleep(0.8)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def select_country_united_states(driver):
    last_error = None
    for _ in range(3):
        try:
            select = get_select(driver, (By.ID, "selFacCountry"))
            select.select_by_visible_text("United States")
            time.sleep(0.8)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def select_state(driver, state_code):
    last_error = None
    for _ in range(3):
        try:
            select = get_select(driver, (By.ID, "selfacilitystate"))
            select.select_by_visible_text(state_code)
            time.sleep(0.8)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def click_search(driver, wait):
    last_error = None
    for _ in range(3):
        try:
            button = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' and contains(., 'Search')]"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            try:
                button.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", button)
            time.sleep(RESULT_SETTLE_SECONDS)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def set_page_size_100(driver, wait):
    wait.until(EC.presence_of_element_located((By.NAME, "DataTables_Table_3_length")))
    last_error = None
    for _ in range(3):
        try:
            select = Select(driver.find_element(By.NAME, "DataTables_Table_3_length"))
            select.select_by_value("100")
            time.sleep(1.2)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def get_table_state(driver):
    return driver.execute_script(
        """
        var table = document.querySelector('#DataTables_Table_3');
        var info = document.querySelector('#DataTables_Table_3_info');
        var processing = document.querySelector('#DataTables_Table_3_processing');
        var result = {
            tablePresent: !!table,
            processing: !!processing && processing.offsetParent !== null,
            empty: false,
            rows: [],
            infoText: info ? info.innerText.trim() : ''
        };
        if (!table) return result;

        var trs = table.querySelectorAll('tbody tr');
        for (var tr of trs) {
            if (tr.querySelector('td.dataTables_empty')) {
                result.empty = true;
                continue;
            }
            var tds = Array.from(tr.querySelectorAll('td')).map(function(td) {
                return td.innerText.trim();
            });
            if (tds.length >= 6 && tds[0]) {
                result.rows.push({
                    fullName: tds[0],
                    licenseNumber: tds[1],
                    boardCommission: tds[2],
                    licenseType: tds[3],
                    status: tds[4],
                    address: tds[5]
                });
            }
        }
        return result;
        """
    )


def get_data_rows(driver):
    return get_table_state(driver).get("rows", [])


def wait_for_results_or_empty(driver):
    end_time = time.time() + RESULT_WAIT_SECONDS
    empty_since = None
    while time.time() < end_time:
        try:
            state = get_table_state(driver)
            if state.get("processing"):
                empty_since = None
                time.sleep(0.5)
                continue

            rows = state.get("rows") or []
            if rows:
                return True

            if state.get("empty") or "No matching records" in (state.get("infoText") or ""):
                if empty_since is None:
                    empty_since = time.time()
                elif time.time() - empty_since >= EMPTY_STABLE_SECONDS:
                    return False
            else:
                empty_since = None
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        time.sleep(0.5)
    return False


def parse_row(row):
    return {
        "Full Name": row.get("fullName", ""),
        "License Number": row.get("licenseNumber", ""),
        "Board/Commission": row.get("boardCommission", ""),
        "License Type": row.get("licenseType", ""),
        "Status": row.get("status", ""),
        "Address": row.get("address", ""),
    }


def extract_current_page_records(driver):
    for _ in range(5):
        try:
            return [parse_row(row) for row in get_data_rows(driver)]
        except StaleElementReferenceException:
            time.sleep(0.5)
    return []


def get_next_page_index(driver):
    return driver.execute_script(
        """
        var next = document.querySelector('#DataTables_Table_3_next');
        if (!next || next.classList.contains('disabled')) return null;
        return next.getAttribute('data-dt-idx');
        """
    )


def click_next_page_by_index(driver, page_index):
    return driver.execute_script(
        """
        var idx = arguments[0];
        var next = document.querySelector('#DataTables_Table_3_next');
        if (!next || next.classList.contains('disabled')) return false;
        if (idx && next.getAttribute('data-dt-idx') !== idx) return false;
        next.click();
        return true;
        """,
        page_index,
    )


def click_next_page(driver, wait):
    page_index = get_next_page_index(driver)
    if page_index is None:
        return False

    before = get_table_state(driver)
    before_key = "|".join(
        row.get("licenseNumber", "") for row in (before.get("rows") or [])[:5]
    )

    last_error = None
    for _ in range(3):
        try:
            if click_next_page_by_index(driver, page_index):
                break
            return False
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    else:
        if last_error:
            raise last_error

    try:
        wait.until(
            lambda d: "|".join(
                row.get("licenseNumber", "")
                for row in (get_table_state(d).get("rows") or [])[:5]
            ) != before_key
            or get_next_page_index(d) is None
        )
    except TimeoutException:
        pass

    time.sleep(1.2)
    return True


def scrape_all_pages(driver, wait):
    records = []
    seen = set()

    while True:
        page_records = extract_current_page_records(driver)
        for record in page_records:
            key = (record["License Number"], record["Full Name"], record["License Type"], record["Address"])
            if key not in seen:
                seen.add(key)
                records.append(record)

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


def run_search(driver, wait, board_name, state_code):
    open_search_page(driver, wait)
    select_board(driver, board_name)
    select_country_united_states(driver)
    select_state(driver, state_code)
    click_search(driver, wait)

    if not wait_for_results_or_empty(driver):
        return []

    set_page_size_100(driver, wait)
    time.sleep(1.0)
    return scrape_all_pages(driver, wait)


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    header_written = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
    total_rows = 0

    try:
        for board_name in KEYWORDS:
            for state_code in STATES:
                try:
                    records = run_search(driver, wait, board_name, state_code)
                except Exception as exc:
                    print(f"ERROR on {board_name} / {state_code}: {exc}")
                    records = []

                if records:
                    output_rows = []
                    for record in records:
                        row = dict(record)
                        row["Searched Board/Commission"] = board_name
                        row["Searched Country"] = "United States"
                        row["Searched State"] = state_code
                        output_rows.append(row)

                    append_csv(OUTPUT_FILE, output_rows, write_header=not header_written)
                    header_written = True
                    total_rows += len(records)

                print(f"{board_name} | {state_code}: {len(records)} rows (total {total_rows})")
    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
