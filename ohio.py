import csv
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
WAIT_SECONDS = 30
PAGE_LOAD_SLEEP = 1.2
HEADLESS = False

#TARGET_STATES = ["OH", "KY", "VT", "NY", "LA", "NH", "CA", "IN"]
TARGET_STATES = [
    "OH", "AA", "AB", "AE", "AK", "AL", "AP", "AR", "AZ",
    "BC", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "GU",
    "HI", "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA",
    "MB", "MD", "ME", "MI", "MN", "MO", "MS", "MT", "NB",
    "NC", "ND", "NE", "NH", "NJ", "NL", "NM", "NS", "NT",
    "NU", "NV", "NY", "OK", "ON", "OR", "PA", "PE", "PR",
    "QC", "RI", "SC", "SD", "SK", "TN", "TX", "UT", "VA",
    "VT", "WA", "WI", "WV", "WY", "YT", "AS", "MP", "VI",
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
        except NoSuchElementException:
            continue
    return len(get_result_rows(driver)) == 0


def get_result_rows(driver):
    try:
        table = driver.find_element(By.ID, "results")
    except NoSuchElementException:
        return []

    rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
    valid_rows = []
    for row in rows:
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) >= 13 and "dataTables_empty" not in (row.get_attribute("class") or ""):
            valid_rows.append(row)
    return valid_rows


def parse_current_page(driver, state_code: str, board_name: str, license_type_name: str):
    records = []
    for row in get_result_rows(driver):
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 14:
                continue

            view_links = row.find_elements(By.CSS_SELECTOR, "a.btn-view-more")
            detail_url = view_links[0].get_attribute("href").strip() if view_links else ""

            records.append(
                {
                    "Filter State": state_code,
                    "Filter Board": board_name,
                    "Filter License Type": license_type_name,
                    "Name": cells[0].text.strip(),
                    "Board": cells[1].text.strip(),
                    "Application/License/Endorsement #": cells[2].text.strip(),
                    "Type": cells[3].text.strip(),
                    "Status": cells[4].text.strip(),
                    "Sub Status": cells[5].text.strip(),
                    "Sub Category": cells[6].text.strip(),
                    "Board Action?": cells[7].text.strip(),
                    "City": cells[8].text.strip(),
                    "State": cells[9].text.strip(),
                    "County": cells[10].text.strip(),
                    "Zip Code": cells[11].text.strip(),
                    "Compact/Multi-State Eligible": cells[12].text.strip(),
                    "Detail URL": detail_url,
                }
            )
        except StaleElementReferenceException:
            continue
    return records


def next_page_available(driver) -> bool:
    try:
        next_button = driver.find_element(By.ID, "results_next")
    except NoSuchElementException:
        return False

    class_name = (next_button.get_attribute("class") or "").lower()
    aria_disabled = (next_button.get_attribute("aria-disabled") or "").lower()
    return "disabled" not in class_name and aria_disabled != "true"


def click_next_page(driver, wait):
    before = capture_results_signature(driver)
    safe_click(driver, wait, By.ID, "results_next")
    wait.until(lambda d: capture_results_signature(d) != before)
    wait_for_ajax_idle(driver, wait)


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

        click_next_page(driver, wait)

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

    print(f"    Skipping license type '{license_type_name}' (not found after refresh)")
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


def write_csv(path: str, records: Sequence[dict]):
    fieldnames = [
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
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def append_csv(path: str, records: Sequence[dict], write_header: bool):
    fieldnames = [
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
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def run_scraper(
    target_states: Optional[Sequence[str]] = None,
    target_boards: Optional[Sequence[str]] = None,
    fixed_license_types: Optional[Sequence[str]] = None,
    output_file: str = OUTPUT_FILE,
):
    target_states = list(target_states or TARGET_STATES)
    target_boards = list(target_boards or TARGET_BOARDS)
    fixed_license_types = list(fixed_license_types or [])

    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    all_records = []
    header_written = False

    try:
        click_continue_if_present(driver, wait)

        for state_code in target_states:
            print(f"Selecting state: {state_code}")
            set_state(driver, wait, state_code)

            board_options = list_select_options(driver, wait, get_board_select)
            boards_to_run = find_matching_options(target_boards, board_options)
            if not boards_to_run:
                print(f"No matching boards found for state {state_code}.")
                continue

            for board_name in boards_to_run:
                print(f"  Board: {board_name}")
                set_board(driver, wait, board_name)

                available_license_types = list_select_options(driver, wait, get_license_type_select)
                if fixed_license_types:
                    license_types_to_run = find_matching_options(fixed_license_types, available_license_types)
                else:
                    license_types_to_run = available_license_types

                if not license_types_to_run:
                    print(f"    No matching license types for board {board_name}.")
                    continue

                for license_type_name in license_types_to_run:
                    print(f"    License type: {license_type_name}")
                    if not set_license_type(driver, wait, license_type_name):
                        continue
                    click_search(driver, wait)
                    records = scrape_all_pages(driver, wait, state_code, board_name, license_type_name)
                    print(f"      Records collected: {len(records)}")
                    if records:
                        append_csv(output_file, records, write_header=not header_written)
                        header_written = True
                        all_records.extend(records)

        if all_records:
            print(f"Finished. Wrote {len(all_records)} rows to {output_file}")
        else:
            print("Finished. No rows collected.")
    finally:
        driver.quit()


if __name__ == "__main__":
    run_scraper()
