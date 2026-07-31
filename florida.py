import csv
import os
import queue
import re
import threading
import time
from urllib.parse import urljoin, urlparse, parse_qs

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
from selenium.webdriver.support.ui import Select, WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


LANDING_URL = "https://mqa-internet.doh.state.fl.us/MQASearchServices/HealthCareProviders"
WAIT_SECONDS = 45
PAGE_LOAD_SLEEP = 1.5
RESULT_WAIT_SECONDS = 30
RESULT_SETTLE_SECONDS = 2.0
HEADLESS = False
WORKER_COUNT = 4

INPUT_FILE = "zip_codes_FD.csv"
OUTPUT_FILE = "florida_database.csv"

KEYWORDS = [
    #"BOARD OF DENTISTRY",
    "BOARD OF MASSAGE THERAPY",
    "BOARD OF MEDICINE",
    "BOARD OF NATUROPATHIC MEDICINE",
    "BOARD OF NURSING",
    "BOARD OF OCCUPATIONAL THERAPY PRACTICE",
    "BOARD OF OPTICIANRY",
    "BOARD OF OPTOMETRY",
    "BOARD OF ORTHOTISTS AND PROSTHETISTS",
    "BOARD OF OSTEOPATHIC MEDICINE",
    "BOARD OF PHARMACY",
    "BOARD OF PHYSICAL THERAPY PRACTICE",
    "BOARD OF PODIATRIC MEDICINE",
    "BOARD OF PSYCHOLOGY",
    "DIETETICS AND NUTRITION PRACTICE COUNCIL",
    "CERTIFIED SOCIAL WORKERS",
    "SPEECH-LANGUAGE PATHOLOGY AND AUDIOLOGY",
    "OUT-OF-STATE TELEHEALTH PROVIDER",
]
FIELDNAMES = [
    "Searched Board",
    "Searched Zip Code",
    "License",
    "Name",
    "Profession",
    "City",
    "License Status",
]


def normalize_text(text):
    return re.sub(r"\s+", " ", text or "").strip().upper()


def extract_zip_code(value):
    text = str(value or "").strip()
    if not text:
        return ""

    match = re.search(r"\d{5}", text)
    if match:
        return match.group(0)

    return text


def read_unique_zip_codes(path):
    zip_codes = []
    seen = set()

    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            return zip_codes

        zip_field = None
        for field in reader.fieldnames:
            if field and field.strip().lower() in {"zip_code", "zip code", "zipcode", "zip"}:
                zip_field = field
                break

        if zip_field is None:
            raise ValueError(f"Could not find a ZIP column in {path}")

        for row in reader:
            zip_code = extract_zip_code(row.get(zip_field))
            if not zip_code or zip_code in seen:
                continue
            seen.add(zip_code)
            zip_codes.append(zip_code)

    return zip_codes


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
    wait.until(EC.presence_of_element_located((By.ID, "BoardDD")))


def select_board(driver, board_name):
    last_error = None
    for _ in range(4):
        try:
            select = Select(driver.find_element(By.ID, "BoardDD"))
            target = normalize_text(board_name)
            for option in select.options:
                if normalize_text(option.text) == target:
                    select.select_by_value(option.get_attribute("value"))
                    time.sleep(0.8)
                    return
            raise NoSuchElementException(f"Board option not found: {board_name}")
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def enter_zip_code(driver, zip_code):
    last_error = None
    for _ in range(4):
        try:
            input_box = driver.find_element(By.ID, "SearchDto_ZipCode")
            input_box.clear()
            input_box.send_keys(zip_code)
            time.sleep(0.4)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def select_license_status(driver, status_text):
    last_error = None
    for _ in range(4):
        try:
            select = Select(driver.find_element(By.ID, "SearchDto_LicenseStatus"))
            target = normalize_text(status_text)
            for option in select.options:
                if normalize_text(option.text) == target:
                    select.select_by_value(option.get_attribute("value"))
                    time.sleep(0.8)
                    return
            raise NoSuchElementException(f"License status option not found: {status_text}")
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def click_search(driver, wait):
    last_error = None
    for _ in range(4):
        try:
            button = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "input[type='submit'][value='Search']")
                )
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


def get_result_state(driver):
    return driver.execute_script(
        """
        var table = document.querySelector('table.table.table-striped.table-condensed.table-hover');
        var caption = table && table.querySelector('caption') ? table.querySelector('caption').innerText.trim() : '';
        var result = {
            tablePresent: !!table,
            captionText: caption,
            rows: [],
            empty: false
        };
        if (!table) return result;

        var trs = table.querySelectorAll('tbody tr');
        for (var tr of trs) {
            var emptyCell = tr.querySelector('td.dataTables_empty');
            if (emptyCell) {
                result.empty = true;
                continue;
            }

            var tds = tr.querySelectorAll('td');
            if (!tds || tds.length < 5) continue;

            result.rows.push({
                license: (tds[0].innerText || '').trim(),
                name: (tds[1].innerText || '').trim(),
                profession: (tds[2].innerText || '').trim(),
                city: (tds[3].innerText || '').trim(),
                status: (tds[4].innerText || '').trim()
            });
        }

        return result;
        """
    )


def wait_for_results_or_empty(driver):
    end_time = time.time() + RESULT_WAIT_SECONDS
    while time.time() < end_time:
        try:
            state = get_result_state(driver)
            if state.get("rows"):
                return True
            caption = state.get("captionText") or ""
            if state.get("tablePresent") and ("Search Results Total" in caption or state.get("empty")):
                return False
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        time.sleep(0.5)
    return False


def extract_current_page_records(driver):
    for _ in range(5):
        try:
            return get_result_state(driver).get("rows", [])
        except StaleElementReferenceException:
            time.sleep(0.5)
    return []


def parse_page_number_from_href(href):
    try:
        parsed = urlparse(href)
        page_values = parse_qs(parsed.query).get("page", [])
        if not page_values:
            return None
        return int(page_values[0])
    except (ValueError, TypeError):
        return None


def get_current_page_number(driver):
    current_url = driver.current_url or ""
    page_values = parse_qs(urlparse(current_url).query).get("page", [])
    if page_values:
        try:
            return int(page_values[0])
        except ValueError:
            return 1
    return 1


def get_next_page_href(driver):
    current_page = get_current_page_number(driver)
    candidates = driver.execute_script(
        """
        var links = Array.from(document.querySelectorAll("a[href*='IndexPaged?page=']"));
        return links.map(function(link) {
            return { href: link.href, text: (link.innerText || '').trim() };
        });
        """
    )

    numeric_candidates = []
    next_candidate = None
    for item in candidates or []:
        href = item.get("href") or ""
        text = (item.get("text") or "").strip().lower()
        page_number = parse_page_number_from_href(href)
        if page_number and page_number > current_page:
            numeric_candidates.append((page_number, href))
        elif text in {"next", ">", ">>"}:
            next_candidate = href

    if numeric_candidates:
        numeric_candidates.sort(key=lambda item: item[0])
        return numeric_candidates[0][1]
    return next_candidate


def go_to_next_page(driver, wait):
    next_href = get_next_page_href(driver)
    if not next_href:
        return False

    before = extract_current_page_records(driver)
    before_key = tuple(row.get("license", "") for row in before[:3])

    driver.get(urljoin(driver.current_url, next_href))
    wait_for_page_ready(driver, wait)

    try:
        wait.until(
            lambda d: tuple(row.get("license", "") for row in extract_current_page_records(d)[:3]) != before_key
        )
    except TimeoutException:
        pass

    time.sleep(1.0)
    return True


def scrape_all_pages(driver, wait):
    records = []
    seen = set()

    while True:
        page_rows = extract_current_page_records(driver)
        for row in page_rows:
            key = (
                row.get("license", ""),
                row.get("name", ""),
                row.get("profession", ""),
                row.get("city", ""),
                row.get("status", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(row)

        if not go_to_next_page(driver, wait):
            break

    return records


def append_csv(path, records, write_header=False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def run_search(driver, wait, board_name, zip_code):
    open_search_page(driver, wait)
    select_board(driver, board_name)
    enter_zip_code(driver, zip_code)
    select_license_status(
        driver,
        "Practicing statuses only (i.e. Clear, Military, Obligations, Probation)",
    )
    click_search(driver, wait)

    if not wait_for_results_or_empty(driver):
        return []

    return scrape_all_pages(driver, wait)


class ResultWriter:
    """Thread-safe CSV writer that lazily writes the header once."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.header_written = False
        self.total_rows = 0

    def write(self, rows):
        if not rows:
            return
        with self.lock:
            append_csv(self.path, rows, write_header=not self.header_written)
            self.header_written = True
            self.total_rows += len(rows)
            return self.total_rows


def worker(task_queue, writer, worker_id):
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)

    try:
        while True:
            try:
                board_name, zip_code = task_queue.get_nowait()
            except queue.Empty:
                break

            try:
                try:
                    records = run_search(driver, wait, board_name, zip_code)
                except Exception as exc:
                    print(f"[w{worker_id}] ERROR on {board_name} / {zip_code}: {exc}")
                    records = []

                output_rows = [
                    {
                        "Searched Board": board_name,
                        "Searched Zip Code": zip_code,
                        "License": record.get("license", ""),
                        "Name": record.get("name", ""),
                        "Profession": record.get("profession", ""),
                        "City": record.get("city", ""),
                        "License Status": record.get("status", ""),
                    }
                    for record in records
                ]

                total = writer.write(output_rows)
                total_str = f" (total {total})" if total is not None else ""
                print(f"[w{worker_id}] {board_name} | {zip_code}: {len(records)} rows{total_str}")
            finally:
                task_queue.task_done()
    finally:
        driver.quit()


def main():
    zip_codes = read_unique_zip_codes(INPUT_FILE)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig"):
        pass

    task_queue = queue.Queue()
    for board_name in KEYWORDS:
        for zip_code in zip_codes:
            task_queue.put((board_name, zip_code))

    writer = ResultWriter(OUTPUT_FILE)
    worker_count = max(1, min(WORKER_COUNT, task_queue.qsize()))

    threads = []
    for worker_id in range(worker_count):
        thread = threading.Thread(
            target=worker, args=(task_queue, writer, worker_id), daemon=True
        )
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    print(f"Done. Saved {writer.total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
