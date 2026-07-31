import csv
import os
import re
import shutil
import threading
import time
import tempfile

#The scraper will search for the following licensing boards across all U.S. states, territories, and jurisdictions: State Board of Dentistry, State Board of Medicine, State Board of Nursing, State Board of Occupational Therapy, State Board of Optometry, State Board of Osteopathic Medicine, State Board of Pharmacy, State Board of Physical Therapy, State Board of Psychology, Radiology Personnel, and State Board of Examiners in Speech-Language Pathology and Audiology. Searches are performed for each ZIP code listed in zip_codes.csv (column ZIP_Code).


from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.options import Options as ChromeOptions
import undetected_chromedriver as uc


LANDING_URL = "https://www.pals.pa.gov/#!/page/search"
WAIT_SECONDS = 45
PAGE_LOAD_SLEEP = 1.5
RESULT_WAIT_SECONDS = 40
RESULT_SETTLE_SECONDS = 2.0
EMPTY_STABLE_SECONDS = 6.0
# Interval between DOM polls — 1.0 s halves JS executions vs. the old 0.5 s.
POLL_INTERVAL = 1.0
HEADLESS = True           # Saves GPU/render CPU on headless servers.
OUTPUT_FILE = "IN_Database.csv"
ERROR_FILE = "scrape_errors.csv"
ZIP_CSV = "zip_codes_in.csv"
NUM_THREADS = 1

# Serializes appends to the shared CSV across worker threads.
CSV_LOCK = threading.Lock()
ERROR_LOCK = threading.Lock()

KEYWORDS = [
    #  "State Board of Dentistry",
    #  "State Board of Medicine",
    #  "State Board of Nursing",
    # "State Board of Occupational Therapy",
    # "State Board of Optometry",
    # "State Board of Osteopathic Medicine",
    # "State Board of Pharmacy",
    # "State Board of Physical Therapy",
    # "State Board of Psychology",
    "Radiology Personnel",
   #"State Board of Examiners in Speech-Language Pathology and Audiology",
]

FIELDNAMES = [
    "Searched Board/Commission",
    "Searched Zip",
    "Full Name",
    "License Number",
    "Board/Commission",
    "License Type",
    "Status",
    "Address",
]

ERROR_FIELDNAMES = [
    "Searched Board/Commission",
    "Searched Zip",
    "Error Type",
    "Error Message",
]


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().upper()


def load_zip_codes(path):
    zips = []
    seen = set()
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            value = (row.get("ZIP_Code") or "").strip()
            if not value:
                continue
            # Normalize to 5-digit zip; keep leading zeros.
            value = value.split(".")[0].zfill(5)[:5]
            if value not in seen:
                seen.add(value)
                zips.append(value)
    return zips


def build_driver():
    profile_dir = tempfile.mkdtemp(prefix="pa_2nd_uc_")
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--incognito")

    # Server / low-CPU flags
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")   # avoids /dev/shm exhaustion
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--disable-default-apps")
    options.add_argument("--mute-audio")
    options.add_argument("--log-level=3")              # suppress Chrome console noise

    # Block images — reduces bandwidth and render work for a data-only scraper.
    options.add_experimental_option(
        "prefs",
        {"profile.default_content_setting_values.images": 2},
    )

    if HEADLESS:
        options.add_argument("--headless=new")

    try:
        driver = uc.Chrome(options=options, use_subprocess=False)
    except Exception:
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise
    return driver, profile_dir


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


def enter_zip(driver, wait, zip_code):
    last_error = None
    for _ in range(3):
        try:
            field = wait.until(EC.presence_of_element_located((By.ID, "zip")))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", field)
            field.clear()
            field.send_keys(zip_code)
            # Sync AngularJS model with the typed value.
            driver.execute_script(
                """
                var el = arguments[0];
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                """,
                field,
            )
            time.sleep(0.6)
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
                time.sleep(POLL_INTERVAL)
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
        time.sleep(POLL_INTERVAL)
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


def append_csv(path, records):
    if not records:
        return
    # Lock so concurrent threads never interleave rows or both write a header.
    with CSV_LOCK:
        write_header = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(records)


def append_error_csv(path, board_name, zip_code, exc):
    row = {
        "Searched Board/Commission": board_name,
        "Searched Zip": zip_code,
        "Error Type": type(exc).__name__,
        "Error Message": str(exc),
    }
    with ERROR_LOCK:
        write_header = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=ERROR_FIELDNAMES, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(row)


def run_search(driver, wait, board_name, zip_code):
    open_search_page(driver, wait)
    select_board(driver, board_name)
    enter_zip(driver, wait, zip_code)
    click_search(driver, wait)

    if not wait_for_results_or_empty(driver):
        return []

    set_page_size_100(driver, wait)
    time.sleep(1.0)
    return scrape_all_pages(driver, wait)


def split_keywords(keywords, num_threads):
    """Distribute keywords round-robin across the worker threads."""
    groups = [[] for _ in range(num_threads)]
    for index, keyword in enumerate(keywords):
        groups[index % num_threads].append(keyword)
    return [group for group in groups if group]


def worker(thread_name, keywords, zip_codes, stats, stats_lock):
    driver, profile_dir = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    try:
        for board_name in keywords:
            for zip_code in zip_codes:
                try:
                    records = run_search(driver, wait, board_name, zip_code)
                except Exception as exc:
                    print(f"[{thread_name}] ERROR on {board_name} / {zip_code}: {exc}")
                    append_error_csv(ERROR_FILE, board_name, zip_code, exc)
                    records = []

                if records:
                    output_rows = []
                    for record in records:
                        row = dict(record)
                        row["Searched Board/Commission"] = board_name
                        row["Searched Zip"] = zip_code
                        output_rows.append(row)

                    append_csv(OUTPUT_FILE, output_rows)
                    with stats_lock:
                        stats["total"] += len(records)

                with stats_lock:
                    running_total = stats["total"]
                    print(f"[{thread_name}] {board_name} | {zip_code}: {len(records)} rows (total {running_total})")
    finally:
        try:
            driver.quit()
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)


def main():
    zip_codes = load_zip_codes(ZIP_CSV)
    print(f"Loaded {len(zip_codes)} ZIP codes from {ZIP_CSV}")

    keyword_groups = split_keywords(KEYWORDS, NUM_THREADS)
    print(f"Launching {len(keyword_groups)} threads:")
    for index, group in enumerate(keyword_groups, start=1):
        print(f"  Thread-{index}: {', '.join(group)}")

    stats = {"total": 0}
    stats_lock = threading.Lock()

    threads = []
    for index, group in enumerate(keyword_groups, start=1):
        thread = threading.Thread(
            target=worker,
            args=(f"Thread-{index}", group, zip_codes, stats, stats_lock),
            name=f"Thread-{index}",
        )
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    print(f"Done. Saved {stats['total']} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
