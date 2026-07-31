import csv
import logging
import os
import re
import time
from typing import Iterable, List, Optional, Sequence

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
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


SEARCH_URL = "https://elicense.ohio.gov/oh_verifylicense"
OUTPUT_FILE = "ohio_license_search.csv"
PROGRESS_FILE = "ohio_progress.txt"
LOG_FILE = "ohio.log"
WAIT_SECONDS = 30
PAGE_LOAD_SLEEP = 1.2
HEADLESS = False

CSV_FIELDNAMES = [
    "Filter State",
    "Filter Board",
    "Filter License Type",
    "Name",
    "Board",
    "Application/License/Endorsement #",
    "Type",
    "Status",
    "Sub Status",
    "Sub Category",
    "Board Action?",
    "City",
    "State",
    "County",
    "Zip Code",
    "Compact/Multi-State Eligible",
    "Detail URL",
]

#TARGET_STATES = ["OH", "KY", "VT", "NY", "LA", "NH", "CA", "IN"]
TARGET_STATES = [
    "OH", "AA", "AB", "AE", "AK", "AL", "AP", "AR", "AZ",
    "BC", "CA", 
    "CO", "CT", "DC", "DE", "FL", "GA", "GU",
    "HI", "IA", "ID",
      "IL", "IN", "KS", "KY", "LA", "MA",
    "MB", "MD", "ME", "MI", 
    "MN", "MO", "MS", 
    "MT", "NB",
    "NC", "ND", "NE", "NH", "NJ", "NL", 
    "NM", "NS", "NT",
    "NU", "NV", "NY", "OK", "ON", "OR", "PA", "PE", "PR",
    "QC", "RI", "SC", "SD", "SK", "TN", "TX", "UT", 
    "VA","VT", "WA", "WI", "WV", 
    "WY", "YT", "AS", "MP", "VI",
    "MH", "NF", "PQ", "UK"
]
TARGET_BOARDS = [
    "Medical Board",
    "Nursing Board",
    "Psychology Board",
    "Speech and Hearing Professionals Board",
    "Board of Pharmacy",
    "Department of Behavioral Health",
]


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("ohio_scraper")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


LOGGER = setup_logger()


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


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


def wait_for_ajax_idle(driver, wait, extra_sleep: float = 0.6):
    def ajax_idle(current_driver):
        script = """
            const processing = document.querySelector('#results_processing');
            const visible = processing && processing.offsetParent !== null;
            const jqueryIdle = (typeof window.jQuery === 'undefined') || window.jQuery.active === 0;
            return jqueryIdle && !visible;
        """
        try:
            return bool(current_driver.execute_script(script))
        except WebDriverException:
            return False

    try:
        wait.until(ajax_idle)
    except TimeoutException:
        pass
    time.sleep(extra_sleep)


def safe_click(driver, wait, by, value, timeout: int = WAIT_SECONDS):
    element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    try:
        element.click()
    except (ElementClickInterceptedException, WebDriverException):
        driver.execute_script("arguments[0].click();", element)


def get_labeled_select(driver, wait, label_text: str, preferred_id: Optional[str] = None) -> Select:
    if preferred_id:
        try:
            element = WebDriverWait(driver, 4).until(
                EC.presence_of_element_located((By.ID, preferred_id))
            )
            return Select(element)
        except TimeoutException:
            pass

    xpath_candidates = [
        f"//label[normalize-space()='{label_text}']/following::select[1]",
        f"//label[contains(normalize-space(),'{label_text}')]/following::select[1]",
    ]
    last_error = None
    for xpath in xpath_candidates:
        try:
            element = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            return Select(element)
        except TimeoutException as exc:
            last_error = exc
    raise last_error or RuntimeError(f"Could not locate select for label: {label_text}")


def get_state_select(driver, wait) -> Select:
    return get_labeled_select(driver, wait, "State", "j_id0:j_id122:state")


def get_board_select(driver, wait) -> Select:
    return get_labeled_select(driver, wait, "Board")


def get_license_type_select(driver, wait) -> Select:
    return get_labeled_select(driver, wait, "License Type", "j_id0:j_id122:licenseType")


def list_select_options(
    driver,
    wait,
    select_getter,
    excluded_texts: Optional[Sequence[str]] = None,
    retries: int = 5,
) -> List[str]:
    excluded = set(excluded_texts or {"--None--", "---Select--"})
    last_error = None

    for attempt in range(retries):
        try:
            select = select_getter(driver, wait)
            option_texts = driver.execute_script(
                """
                const select = arguments[0];
                return Array.from(select.options).map(option => (option.textContent || '').trim());
                """,
                select._el,
            )
            cleaned = [text for text in option_texts if text and text not in excluded]
            if cleaned:
                return cleaned
            if attempt < retries - 1:
                time.sleep(0.6)
                continue
            return cleaned
        except (StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.6)

    if last_error:
        raise last_error
    return []


def find_matching_options(targets: Sequence[str], available_options: Iterable[str]) -> List[str]:
    matched = []
    normalized_available = {option: normalize(option) for option in available_options}

    for target in targets:
        normalized_target = normalize(target)
        exact = [option for option, value in normalized_available.items() if value == normalized_target]
        if exact:
            matched.extend(exact[:1])
            continue

        contains = [
            option
            for option, value in normalized_available.items()
            if normalized_target in value or value in normalized_target
        ]
        if contains:
            matched.extend(sorted(contains, key=len)[:1])

    deduped = []
    seen = set()
    for option in matched:
        if option not in seen:
            seen.add(option)
            deduped.append(option)
    return deduped


def select_by_value(select: Select, value: str):
    select.select_by_value(value)
    time.sleep(0.4)


def select_by_visible_text(select: Select, text: str):
    select.select_by_visible_text(text)
    time.sleep(0.4)


def click_continue_if_present(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)

    continue_locators = [
        (By.ID, "j_id0:j_id115:j_id117"),
        (By.XPATH, "//input[@type='submit' and translate(@value,'CONTINUE','continue')='continue']"),
    ]

    for by, value in continue_locators:
        try:
            safe_click(driver, wait, by, value, timeout=6)
            wait.until(EC.presence_of_element_located((By.ID, "j_id0:j_id122:state")))
            wait_for_ajax_idle(driver, wait)
            return
        except TimeoutException:
            continue

    wait.until(EC.presence_of_element_located((By.ID, "j_id0:j_id122:state")))


def capture_results_signature(driver, retries: int = 3) -> str:
    last_error = None
    for _ in range(retries):
        try:
            try:
                info = driver.find_element(By.ID, "results_info").text.strip()
            except NoSuchElementException:
                info = ""

            try:
                tbody_text = driver.find_element(By.CSS_SELECTOR, "#results tbody").text.strip()
            except NoSuchElementException:
                tbody_text = ""

            return f"{info}||{tbody_text[:300]}"
        except (StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.3)

    if last_error:
        raise last_error
    return ""


def click_search(driver, wait):
    before = capture_results_signature(driver)
    safe_click(
        driver,
        wait,
        By.XPATH,
        "//input[contains(@class,'searchButton') and translate(@value,'SEARCH','search')='search']",
    )

    try:
        wait.until(lambda d: capture_results_signature(d) != before)
    except TimeoutException:
        pass

    try:
        wait.until(EC.presence_of_element_located((By.ID, "results_wrapper")))
    except TimeoutException:
        pass

    wait_for_ajax_idle(driver, wait)


def has_no_results(driver) -> bool:
    empty_locators = [
        (By.CSS_SELECTOR, "#results tbody td.dataTables_empty"),
        (By.XPATH, "//td[contains(normalize-space(),'No matching records found')]"),
        (By.XPATH, "//td[contains(normalize-space(),'No records')]"),
    ]
    for by, value in empty_locators:
        try:
            if driver.find_element(by, value).is_displayed():
                return True
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return len(_read_page_rows(driver)) == 0


def _read_page_rows(driver, retries: int = 4):
    # Extract every row of the current page in a single JS call. Doing the read
    # atomically avoids StaleElementReferenceException when DataTables redraws
    # the table between locating a row and reading its cells.
    script = """
        const table = document.getElementById('results');
        if (!table) return [];
        const rows = table.querySelectorAll('tbody tr');
        const out = [];
        rows.forEach(row => {
            if (row.className && row.className.indexOf('dataTables_empty') !== -1) return;
            const cells = row.querySelectorAll('td');
            if (cells.length < 13) return;
            const texts = Array.from(cells).map(c => (c.textContent || '').trim());
            const link = row.querySelector('a.btn-view-more');
            out.push({cells: texts, detail: link ? (link.getAttribute('href') || '').trim() : ''});
        });
        return out;
    """
    for _ in range(retries):
        try:
            return driver.execute_script(script) or []
        except (StaleElementReferenceException, WebDriverException):
            time.sleep(0.3)
    return []


def parse_current_page(driver, state_code: str, board_name: str, license_type_name: str):
    records = []
    for row in _read_page_rows(driver):
        cells = row.get("cells") or []
        if len(cells) < 13:
            continue

        records.append(
            {
                "Filter State": state_code,
                "Filter Board": board_name,
                "Filter License Type": license_type_name,
                "Name": cells[0],
                "Board": cells[1],
                "Application/License/Endorsement #": cells[2],
                "Type": cells[3],
                "Status": cells[4],
                "Sub Status": cells[5],
                "Sub Category": cells[6],
                "Board Action?": cells[7],
                "City": cells[8],
                "State": cells[9],
                "County": cells[10],
                "Zip Code": cells[11],
                "Compact/Multi-State Eligible": cells[12],
                "Detail URL": row.get("detail", ""),
            }
        )
    return records


def next_page_available(driver, retries: int = 4) -> bool:
    # Read the button's state in a single JS call so the element cannot go
    # stale between locating it and reading its attributes (DataTables redraws
    # the pagination controls via AJAX, which invalidates cached references).
    script = """
        const btn = document.getElementById('results_next');
        if (!btn) return null;
        const cls = (btn.className || '').toLowerCase();
        const aria = (btn.getAttribute('aria-disabled') || '').toLowerCase();
        return cls.indexOf('disabled') === -1 && aria !== 'true';
    """
    for _ in range(retries):
        try:
            result = driver.execute_script(script)
            if result is None:
                return False
            return bool(result)
        except (StaleElementReferenceException, WebDriverException):
            time.sleep(0.3)
    return False


def click_next_page(driver, wait):
    before = capture_results_signature(driver)
    try:
        safe_click(driver, wait, By.ID, "results_next", timeout=10)
    except TimeoutException:
        # On the last page the "next" button is disabled and never becomes
        # clickable. Treat this as "no more pages" instead of crashing.
        return False

    try:
        wait.until(lambda d: capture_results_signature(d) != before)
    except TimeoutException:
        # The signature never changed: we were already on the last page.
        return False

    wait_for_ajax_idle(driver, wait)
    return True


def scrape_all_pages(driver, wait, state_code: str, board_name: str, license_type_name: str):
    seen = set()
    records = []

    while True:
        if has_no_results(driver):
            break

        for record in parse_current_page(driver, state_code, board_name, license_type_name):
            unique_key = (
                record["Application/License/Endorsement #"],
                record["Name"],
                record["Type"],
                record["City"],
                record["State"],
            )
            if unique_key not in seen:
                seen.add(unique_key)
                records.append(record)

        if not next_page_available(driver):
            break

        if not click_next_page(driver, wait):
            break

    return records


def set_state(driver, wait, state_code: str):
    state_select = get_state_select(driver, wait)
    select_by_value(state_select, state_code)
    wait_for_ajax_idle(driver, wait)


def set_board(driver, wait, board_name: str):
    board_select = get_board_select(driver, wait)
    select_by_visible_text(board_select, board_name)
    wait_for_ajax_idle(driver, wait)
    wait_for_license_types_refresh(driver, wait)


def set_license_type(driver, wait, license_type_name: str, retries: int = 3) -> bool:
    last_error = None
    for _ in range(retries):
        try:
            license_select = get_license_type_select(driver, wait)
            select_by_visible_text(license_select, license_type_name)
            wait_for_ajax_idle(driver, wait)
            return True
        except (NoSuchElementException, StaleElementReferenceException, TimeoutException, WebDriverException) as exc:
            last_error = exc
            wait_for_license_types_refresh(driver, wait, retries=2)
            time.sleep(0.6)

    LOGGER.warning("    Skipping license type '%s' (not found after refresh)", license_type_name)
    if last_error:
        return False
    return False


def wait_for_license_types_refresh(driver, wait, retries: int = 5):
    last_error = None
    for _ in range(retries):
        try:
            wait_for_ajax_idle(driver, wait, extra_sleep=0.4)
            get_license_type_select(driver, wait)
            return
        except (StaleElementReferenceException, TimeoutException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.6)

    if last_error:
        raise last_error


def append_csv(path: str, records: Sequence[dict]):
    """Append records to the CSV, writing the header only when the file is new.

    Always opens in append mode so reruns preserve previously collected rows.
    The header is emitted only when the file does not yet exist (or is empty).
    """
    if not records:
        return

    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def progress_key(board_name: str, state_code: str) -> str:
    return f"{board_name}||{state_code}"


def load_completed_keys(path: str) -> set:
    """Return the set of (board, state) jobs already finished in prior runs."""
    completed = set()
    if not os.path.exists(path):
        return completed
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                completed.add(line)
    return completed


def mark_completed(path: str, key: str):
    """Record a finished (board, state) job so a rerun can skip it."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(key + "\n")
        handle.flush()


def chunked(items: Sequence[str], size: int) -> List[List[str]]:
    return [list(items[index : index + size]) for index in range(0, len(items), size)]


def process_board_states(
    board_name: str,
    states: Sequence[str],
    output_file: str,
    completed_keys: set,
    progress_file: str,
) -> int:
    written = 0
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    try:
        click_continue_if_present(driver, wait)

        for state_code in states:
            key = progress_key(board_name, state_code)
            if key in completed_keys:
                LOGGER.info("Skipping already-completed %s / %s", board_name, state_code)
                continue

            try:
                LOGGER.info("Selecting state: %s", state_code)
                set_state(driver, wait, state_code)
                set_board(driver, wait, board_name)

                click_search(driver, wait)
                records = scrape_all_pages(driver, wait, state_code, board_name, "")
                LOGGER.info("      Records collected: %s", len(records))

                # Persist immediately so a crash later in the batch never loses
                # this state's data, then mark the job done so reruns skip it.
                append_csv(output_file, records)
                mark_completed(progress_file, key)
                completed_keys.add(key)
                written += len(records)
            except Exception as exc:  # noqa: BLE001 - keep the run going
                LOGGER.exception(
                    "Failed on %s / %s (will retry on next run): %s",
                    board_name,
                    state_code,
                    exc,
                )
                # Reset the page so a transient failure doesn't poison the
                # remaining states in this batch. Rebuild the driver if the
                # page can no longer be reset.
                try:
                    click_continue_if_present(driver, wait)
                except Exception:
                    LOGGER.exception("Could not reset page; rebuilding driver")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = build_driver()
                    wait = WebDriverWait(driver, WAIT_SECONDS)
                    click_continue_if_present(driver, wait)

        return written
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def run_scraper(
    target_states: Optional[Sequence[str]] = None,
    target_boards: Optional[Sequence[str]] = None,
    output_file: str = OUTPUT_FILE,
    progress_file: str = PROGRESS_FILE,
):
    target_states = list(target_states or TARGET_STATES)
    target_boards = list(target_boards or TARGET_BOARDS)

    completed_keys = load_completed_keys(progress_file)
    if completed_keys:
        LOGGER.info("Resuming: %s (board, state) jobs already completed will be skipped", len(completed_keys))

    total_written = 0
    for board_name in target_boards:
        board_states = chunked(target_states, 4)
        LOGGER.info("  Board: %s", board_name)
        for batch_number, state_batch in enumerate(board_states, start=1):
            LOGGER.info("Processing states batch %s for %s: %s", batch_number, board_name, ", ".join(state_batch))
            total_written += process_board_states(
                board_name=board_name,
                states=state_batch,
                output_file=output_file,
                completed_keys=completed_keys,
                progress_file=progress_file,
            )

    LOGGER.info("Finished. Wrote %s new rows to %s this run.", total_written, output_file)


if __name__ == "__main__":
    run_scraper()
