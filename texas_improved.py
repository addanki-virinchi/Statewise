import csv
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Sequence

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


SEARCH_URL = "https://profile.tmb.state.tx.us/Search.aspx?135a91a3-a770-49e6-a164-294696a6ff9f"
MUNICIPALITIES_FILE = "texas_municipalities.csv"
OUTPUT_FILE = "Texas_DB.csv"

WAIT_SECONDS = 45

# --- SPEED KNOBS -----------------------------------------------------------
# 1) Fixed sleeps are the single biggest silent time-sink when multiplied by
#    thousands of iterations. We keep them, but much shorter, since the
#    explicit WebDriverWait calls already guarantee the page/element is ready.
PAGE_LOAD_SLEEP = 0.3          # was 1.0
POST_SEARCH_SLEEP = 0.2        # small buffer after clicking search/next

# 2) Headless = no rendering work in a visible window. This alone is
#    typically a 20-40% speedup with Chrome, plus lower memory use so you can
#    safely run more parallel workers.
HEADLESS = True

# 3) Run N municipalities concurrently, each with its own Chrome instance.
#    This is the single biggest lever: total wall-clock time roughly divides
#    by MAX_WORKERS (network/site permitting). Start conservative (3-5) and
#    raise it only if the site doesn't start throwing errors/timeouts, since
#    TMB's server may rate-limit or slow down under heavy concurrent load.
MAX_WORKERS = 4
# ---------------------------------------------------------------------------

LICENSE_TYPES = ["PHY-ADMIN", "PHY-CONCDEM", "PHY-PBHLTH", "PHY-TELE", "PIT"]

FIELDNAMES = [
    "Search Municipality",
    "Search License Type",
    "Search Specialty",
    "Name",
    "License",
    "Type",
    "Address",
    "City",
    "Board Actions",
]

RESULT_TABLE_XPATH = (
    "//table[.//th[normalize-space()='Name'] "
    "and .//th[normalize-space()='License'] "
    "and .//th[normalize-space()='Type'] "
    "and .//th[normalize-space()='Address'] "
    "and .//th[normalize-space()='City']]"
)

# Shared across worker threads: license_type -> list of specialty dicts.
# Speed win #2: each license type's specialty list is fetched ONCE total
# (not once per municipality), and shared by every worker thread.
_specialty_cache: Dict[str, List[dict]] = {}
_specialty_cache_lock = threading.Lock()

# Thread-safe CSV writer so all workers can append to one output file.
_csv_lock = threading.Lock()
_header_written = False


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
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


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "BodyContent_ddLicenseType")))
    wait.until(EC.presence_of_element_located((By.ID, "BodyContent_tbCity")))


def select_license_type(driver, wait, license_type: str):
    select = Select(wait.until(EC.presence_of_element_located((By.ID, "BodyContent_ddLicenseType"))))
    target = license_type.strip().upper()

    try:
        select.select_by_value(target)
    except NoSuchElementException:
        for option in select.options:
            value = (option.get_attribute("value") or "").strip().upper()
            text = normalize_text(option.text).upper()
            if value == target or text == target:
                option.click()
                break
        else:
            raise

    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "BodyContent_ddLicenseType")))


def get_specialty_select(driver, wait):
    return Select(wait.until(EC.presence_of_element_located((By.ID, "BodyContent_ddSpecialty"))))


def get_specialty_options(driver, wait):
    specialty_select = get_specialty_select(driver, wait)
    options = []
    for option in specialty_select.options:
        value = (option.get_attribute("value") or "").strip()
        text = normalize_text(option.text)
        if not value or value.upper() == "ALL":
            continue
        options.append({"value": value, "text": text})
    return options


def get_specialty_options_cached(driver, wait, license_type: str) -> List[dict]:
    """Fetch specialty options for a license type once, ever, and reuse the
    result for every municipality/worker afterwards. Specialty lists don't
    depend on the city being searched, so recomputing them per-municipality
    (as the original script did) was pure wasted work."""
    with _specialty_cache_lock:
        cached = _specialty_cache.get(license_type)
    if cached is not None:
        return cached

    open_search_page(driver, wait)
    select_license_type(driver, wait, license_type)
    options = get_specialty_options(driver, wait)

    with _specialty_cache_lock:
        _specialty_cache[license_type] = options
    return options


def select_specialty(driver, wait, specialty_value: str):
    specialty_select = get_specialty_select(driver, wait)
    target = specialty_value.strip()

    try:
        specialty_select.select_by_value(target)
    except NoSuchElementException:
        for option in specialty_select.options:
            value = (option.get_attribute("value") or "").strip()
            text = normalize_text(option.text)
            if value == target or text == target:
                option.click()
                break
        else:
            raise

    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "BodyContent_ddSpecialty")))


def set_active_only(driver, wait):
    checkbox = wait.until(EC.presence_of_element_located((By.ID, "BodyContent_cbActiveLicensesOnly")))
    if not checkbox.is_selected():
        driver.execute_script("arguments[0].click();", checkbox)
        time.sleep(0.2)


def set_city(driver, wait, city: str):
    city_box = wait.until(EC.presence_of_element_located((By.ID, "BodyContent_tbCity")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", city_box)
    try:
        city_box.clear()
    except WebDriverException:
        driver.execute_script("arguments[0].value = '';", city_box)
    city_box.send_keys(city.strip())


def click_search(driver, wait):
    search_button = wait.until(EC.element_to_be_clickable((By.ID, "BodyContent_btnSearch")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_button)
    try:
        search_button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", search_button)
    time.sleep(POST_SEARCH_SLEEP)


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def get_result_table(driver):
    try:
        return driver.find_element(By.XPATH, RESULT_TABLE_XPATH)
    except NoSuchElementException:
        return None


def page_has_empty_results_message(driver):
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except NoSuchElementException:
        return False

    text = normalize_text(body_text).upper()
    empty_markers = ["NO RECORDS", "NO RECORD FOUND", "NO RESULTS", "NO DATA", "NO MATCHING"]
    return any(marker in text for marker in empty_markers)


def wait_for_results_or_empty(driver):
    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        if get_result_table(driver):
            return True
        if page_has_empty_results_message(driver):
            return False
        time.sleep(0.3)
    return False


def get_result_rows(driver):
    table = get_result_table(driver)
    if not table:
        return []

    rows = []
    for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) >= 5:
            rows.append(row)
    return rows


def parse_row(row, search_municipality: str, search_license_type: str, search_specialty: str):
    cells = [normalize_text(cell.text) for cell in row.find_elements(By.TAG_NAME, "td")]
    if len(cells) < 5:
        return None

    board_actions = ""
    if len(cells) > 5:
        board_actions = " ".join(cells[5:]).strip()

    return {
        "Search Municipality": search_municipality,
        "Search License Type": search_license_type,
        "Search Specialty": search_specialty,
        "Name": cells[0],
        "License": cells[1],
        "Type": cells[2],
        "Address": cells[3],
        "City": cells[4],
        "Board Actions": board_actions,
    }


def parse_current_page(driver, search_municipality: str, search_license_type: str, search_specialty: str):
    records = []
    for row in get_result_rows(driver):
        record = parse_row(row, search_municipality, search_license_type, search_specialty)
        if record:
            records.append(record)
    return records


def get_first_row_signature(driver):
    rows = get_result_rows(driver)
    if not rows:
        return None
    first_cells = [normalize_text(cell.text) for cell in rows[0].find_elements(By.TAG_NAME, "td")]
    return tuple(first_cells[:6])


def get_next_button(driver):
    candidates = [
        "//a[normalize-space()='Next']",
        "//button[normalize-space()='Next']",
        "//input[@type='submit' and contains(@value,'Next')]",
        "//a[contains(@title,'Next')]",
        "//button[contains(@title,'Next')]",
        "//a[normalize-space()='>']",
    ]
    for xpath in candidates:
        elements = driver.find_elements(By.XPATH, xpath)
        for element in elements:
            classes = (element.get_attribute("class") or "").lower()
            aria_disabled = (element.get_attribute("aria-disabled") or "").lower()
            disabled = element.get_attribute("disabled")
            if "disabled" in classes or aria_disabled == "true" or disabled:
                continue
            return element
    return None


def click_next_page(driver, wait):
    before = get_first_row_signature(driver)
    if before is None:
        return False

    next_button = get_next_button(driver)
    if not next_button:
        return False

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
    try:
        next_button.click()
    except (ElementClickInterceptedException, StaleElementReferenceException):
        driver.execute_script("arguments[0].click();", next_button)

    try:
        wait.until(lambda d: get_first_row_signature(d) not in (None, before))
    except TimeoutException:
        return False

    time.sleep(POST_SEARCH_SLEEP)
    return True


def scrape_all_pages(driver, wait, search_municipality: str, search_license_type: str, search_specialty: str):
    records = []
    seen = set()

    while True:
        page_rows = parse_current_page(driver, search_municipality, search_license_type, search_specialty)
        for row in page_rows:
            key = tuple(row.values())
            if key in seen:
                continue
            seen.add(key)
            records.append(row)

        if not click_next_page(driver, wait):
            break

    return records


def read_municipalities(path: str) -> List[str]:
    municipalities = []
    seen = set()

    with open(path, "r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            municipality = normalize_text(row.get("Municipality", ""))
            if not municipality or municipality in seen:
                continue
            seen.add(municipality)
            municipalities.append(municipality)

    return municipalities


def append_csv(path: str, records: Sequence[dict]):
    """Thread-safe append. Any worker thread can call this concurrently."""
    global _header_written
    if not records:
        return
    with _csv_lock:
        write_header = not _header_written
        mode = "w" if write_header else "a"
        with open(path, mode, newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(records)
        _header_written = True


def run_search_for_specialty(driver, wait, municipality, license_type, specialty_value, specialty_text):
    """Run ONE search assuming the search form is already on screen with the
    correct license type selected. Only changes specialty + city, instead of
    reloading the whole page (speed win #1)."""
    select_specialty(driver, wait, specialty_value)
    set_active_only(driver, wait)
    set_city(driver, wait, municipality)
    click_search(driver, wait)

    if not wait_for_results_or_empty(driver):
        return []

    return scrape_all_pages(driver, wait, municipality, license_type, specialty_text)


def process_municipality(municipality: str, worker_id: int):
    """One municipality, run on its own Chrome instance. This function is
    what gets parallelized across MAX_WORKERS threads."""
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    total_rows = 0

    try:
        for license_type in LICENSE_TYPES:
            try:
                specialty_options = get_specialty_options_cached(driver, wait, license_type)
            except Exception as exc:
                print(f"[w{worker_id}] {municipality} | {license_type}: specialty fetch ERROR {exc!r}")
                continue

            if not specialty_options:
                print(f"[w{worker_id}] {municipality} | {license_type}: 0 specialties")
                continue

            # Open the search form ONCE per (municipality, license_type),
            # then reuse it for every specialty in that license type,
            # instead of a full page reload per specialty.
            try:
                open_search_page(driver, wait)
                select_license_type(driver, wait, license_type)
            except Exception as exc:
                print(f"[w{worker_id}] {municipality} | {license_type}: page open ERROR {exc!r}")
                continue

            for specialty in specialty_options:
                try:
                    records = run_search_for_specialty(
                        driver, wait, municipality, license_type,
                        specialty["value"], specialty["text"],
                    )
                except Exception as exc:
                    # If the form got into a bad state (e.g. postback replaced
                    # the page unexpectedly), reload fresh and retry once
                    # before giving up on this specialty.
                    print(
                        f"[w{worker_id}] {municipality} | {license_type} | "
                        f"{specialty['text']}: ERROR {exc!r} — reloading and retrying"
                    )
                    try:
                        open_search_page(driver, wait)
                        select_license_type(driver, wait, license_type)
                        records = run_search_for_specialty(
                            driver, wait, municipality, license_type,
                            specialty["value"], specialty["text"],
                        )
                    except Exception as exc2:
                        print(
                            f"[w{worker_id}] {municipality} | {license_type} | "
                            f"{specialty['text']}: retry failed {exc2!r}"
                        )
                        continue

                if records:
                    append_csv(OUTPUT_FILE, records)
                    total_rows += len(records)

                print(
                    f"[w{worker_id}] {municipality} | {license_type} | "
                    f"{specialty['text']}: {len(records)} rows (worker total {total_rows})"
                )
    finally:
        driver.quit()

    return municipality, total_rows


def main():
    municipalities = read_municipalities(MUNICIPALITIES_FILE)

    # Reset output file up front.
    global _header_written
    _header_written = False
    open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig").close()

    grand_total = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_municipality, muni, i % MAX_WORKERS): muni
            for i, muni in enumerate(municipalities)
        }
        for future in as_completed(futures):
            muni = futures[future]
            try:
                _, rows = future.result()
                grand_total += rows
                print(f"=== Done: {muni} ({rows} rows) ===")
            except Exception as exc:
                print(f"=== FAILED: {muni}: {exc!r} ===")

    print(f"Done. Saved {grand_total} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()