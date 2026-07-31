import csv
import os
import re
import time
from typing import Iterable, List, Optional

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
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


LANDING_URL = "https://delpros.delaware.gov/OH_VerifyLicense"
OUTPUT_FILE = "delaware.csv"
CITY_INPUT_FILE = "delaware_db.csv"
WAIT_SECONDS = 45
PAGE_LOAD_SLEEP = 1.5
RESULT_WAIT_SECONDS = 35
EMPTY_STABLE_SECONDS = 10.0
HEADLESS = False

# Only search these profession keywords. The matcher is intentionally forgiving
# so "Behaviour Analyst" can still match a dropdown option such as
# "Behavior Analyst" if the site uses the US spelling.
PROFESSION_KEYWORDS = [
    "Dentistry",
    "Dietitians/Nutritionists",
    "Massage Bodywork",
    "Medical Practice",
    "Mental Health",
    "Nursing",
    "Nursing Home Administrators",
    "Occupational Therapy",
    "Optometry",
    "Pharmacy",
    "Physical Therapy/Athletic Trg",
    "Psychology",
    "Speech and Hearing"
]

# STATES = [
#     "AA", "AE", "AK", "AL", "AP", "AR", "AZ", "CA", "CO", "CT", "DC", "DE",
#     "FL", "GA", "HI", "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD",
#     "ME", "MI", "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM",
#     "NV", "NY", "OH", "OK", "OR", "PA", "PR", "RI", "SC", "SD", "TN", "TX",
#     "UT", "VA", "VT", "WA", "WI", "WV", "WY",
# ]
STATES = [
    "DE",]

STATE_SELECT_ID = "j_id0:j_id111:state"
CITY_INPUT_ID = "j_id0:j_id111:city"
PROFESSION_SELECT_ID = "customprofessionselectIndividual"
LICENSE_TYPE_SELECT_ID = "customLicenseTypeselectIndividual"
SEARCH_BUTTON_SELECTOR = "input.searchButton[type='button']"
RESULT_TABLE_SELECTOR = "#results"
RESULT_INFO_SELECTOR = "#results_info"
RESULT_PROCESSING_SELECTOR = "#results_processing"
RESULT_NEXT_SELECTOR = "#results_next"

FIELDNAMES = [
    "Searched State",
    "Searched City",
    "Searched Profession",
    "Searched License Type",
    "Name",
    "Profession",
    "License/Approval Number",
    "Type",
    "Application Type",
    "Status",
    "Discipline",
    "Street Address",
    "City",
    "State",
    "Zip Code",
]


def normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = text.replace("behaviour", "behavior")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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
    driver.get(LANDING_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, STATE_SELECT_ID)))


def safe_select_by_visible_text(select: Select, text: str):
    last_error = None
    for _ in range(4):
        try:
            select.select_by_visible_text(text)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error


def get_select(driver, element_id: str) -> Select:
    return Select(driver.find_element(By.ID, element_id))


def collect_option_texts(driver, element_id: str) -> List[str]:
    try:
        select = get_select(driver, element_id)
    except NoSuchElementException:
        return []

    texts = []
    for option in select.options:
        value = (option.get_attribute("value") or "").strip()
        text = (option.text or "").strip()
        if not text:
            continue
        if value in {"", "none", "None"} or text in {"--None--", "---Select--"}:
            continue
        texts.append(text)
    return texts


def read_city_values(path: str) -> List[str]:
    if not os.path.exists(path):
        return []

    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        seen = set()
        cities = []
        for row in reader:
            city = (row.get("City") or "").strip()
            if not city:
                continue
            normalized_city = normalize(city)
            if normalized_city in seen:
                continue
            seen.add(normalized_city)
            cities.append(city)
    return cities


def find_matching_option(keyword: str, available_options: Iterable[str]) -> Optional[str]:
    normalized_keyword = normalize(keyword)
    if not normalized_keyword:
        return None

    exact_matches = []
    contains_matches = []
    keyword_tokens = set(normalized_keyword.split())

    for option_text in available_options:
        normalized_option = normalize(option_text)
        if normalized_option == normalized_keyword:
            exact_matches.append(option_text)
            continue
        if normalized_keyword in normalized_option or normalized_option in normalized_keyword:
            contains_matches.append(option_text)
            continue

        option_tokens = set(normalized_option.split())
        overlap = len(keyword_tokens & option_tokens)
        if overlap:
            contains_matches.append(option_text)

    if exact_matches:
        return exact_matches[0]
    if contains_matches:
        contains_matches.sort(key=lambda item: (len(item), item))
        return contains_matches[0]
    return None


def select_state(driver, state_code: str):
    last_error = None
    for _ in range(4):
        try:
            select = get_select(driver, STATE_SELECT_ID)
            safe_select_by_visible_text(select, state_code)
            time.sleep(1.0)
            return
        except (StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def enter_city(driver, city: str):
    field = driver.find_element(By.ID, CITY_INPUT_ID)
    field.clear()
    if city:
        field.send_keys(city)
    time.sleep(0.5)


def select_profession(driver, profession_text: str):
    last_error = None
    for _ in range(4):
        try:
            select = get_select(driver, PROFESSION_SELECT_ID)
            safe_select_by_visible_text(select, profession_text)
            time.sleep(1.0)
            return
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def select_license_type(driver, license_type_text: str):
    last_error = None
    for _ in range(4):
        try:
            select = get_select(driver, LICENSE_TYPE_SELECT_ID)
            safe_select_by_visible_text(select, license_type_text)
            time.sleep(0.8)
            return
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def click_search(driver, wait):
    button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, SEARCH_BUTTON_SELECTOR)))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    try:
        button.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", button)
    time.sleep(1.2)


def get_table_state(driver):
    return driver.execute_script(
        """
        var table = document.querySelector('#results');
        var info = document.querySelector('#results_info');
        var processing = document.querySelector('#results_processing');
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
            if (tds.length >= 11 && tds[0]) {
                result.rows.push({
                    name: tds[0],
                    profession: tds[1],
                    licenseNumber: tds[2],
                    type: tds[3],
                    applicationType: tds[4],
                    status: tds[5],
                    discipline: tds[6],
                    streetAddress: tds[7],
                    city: tds[8],
                    state: tds[9],
                    zipCode: tds[10]
                });
            }
        }
        return result;
        """
    )


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

            info_text = (state.get("infoText") or "").lower()
            if state.get("empty") or "no matching records" in info_text or "showing 0 to 0 of 0" in info_text:
                if empty_since is None:
                    empty_since = time.time()
                elif time.time() - empty_since >= EMPTY_STABLE_SECONDS:
                    return False
            else:
                empty_since = None
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            pass
        time.sleep(0.5)
    return False


def parse_row(row: dict) -> dict:
    return {
        "Name": row.get("name", ""),
        "Profession": row.get("profession", ""),
        "License/Approval Number": row.get("licenseNumber", ""),
        "Type": row.get("type", ""),
        "Application Type": row.get("applicationType", ""),
        "Status": row.get("status", ""),
        "Discipline": row.get("discipline", ""),
        "Street Address": row.get("streetAddress", ""),
        "City": row.get("city", ""),
        "State": row.get("state", ""),
        "Zip Code": row.get("zipCode", ""),
    }


def extract_current_page_records(driver):
    for _ in range(5):
        try:
            return [parse_row(row) for row in (get_table_state(driver).get("rows") or [])]
        except StaleElementReferenceException:
            time.sleep(0.5)
    return []


def get_next_button(driver):
    candidates = [
        (By.ID, "results_next"),
        (By.CSS_SELECTOR, "#results_next"),
        (By.CSS_SELECTOR, "a.paginate_button.next"),
        (By.XPATH, "//a[contains(@class,'paginate_button') and normalize-space()='Next']"),
    ]
    for by, value in candidates:
        try:
            element = driver.find_element(by, value)
            classes = (element.get_attribute("class") or "").lower()
            aria_disabled = (element.get_attribute("aria-disabled") or "").lower()
            if "disabled" in classes or aria_disabled == "true":
                return None
            return element
        except NoSuchElementException:
            continue
    return None


def get_first_result_signature(driver):
    rows = get_table_state(driver).get("rows") or []
    if not rows:
        return None
    first = rows[0]
    return tuple(first.get(key, "") for key in (
        "name",
        "profession",
        "licenseNumber",
        "type",
        "applicationType",
        "status",
        "city",
        "state",
        "zipCode",
    ))


def click_next_page(driver, wait):
    next_button = get_next_button(driver)
    if not next_button:
        return False

    previous_signature = get_first_result_signature(driver)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
    try:
        next_button.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", next_button)

    try:
        wait.until(
            lambda d: get_first_result_signature(d) != previous_signature
            or get_next_button(d) is None
        )
    except TimeoutException:
        pass

    time.sleep(1.0)
    return True


def scrape_all_pages(driver, wait):
    records = []
    seen = set()

    while True:
        page_records = extract_current_page_records(driver)
        for record in page_records:
            key = (
                record["Name"],
                record["License/Approval Number"],
                record["Type"],
                record["Application Type"],
                record["City"],
                record["State"],
                record["Zip Code"],
            )
            if key not in seen:
                seen.add(key)
                records.append(record)

        if not click_next_page(driver, wait):
            break

    return records


def append_csv(path: str, records: List[dict], write_header: bool = False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def wait_for_dropdown_population(driver, select_id: str, min_options: int = 2):
    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        try:
            options = collect_option_texts(driver, select_id)
            if len(options) >= min_options:
                return options
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            pass
        time.sleep(0.4)
    return collect_option_texts(driver, select_id)


def run_search(driver, wait, state_code: str, profession_keyword: str, license_type_text: str):
    open_search_page(driver, wait)
    select_state(driver, state_code)

    # The profession select is updated dynamically after the state changes.
    wait_for_dropdown_population(driver, PROFESSION_SELECT_ID)
    available_professions = collect_option_texts(driver, PROFESSION_SELECT_ID)
    matched_profession = find_matching_option(profession_keyword, available_professions)
    if not matched_profession:
        return None, []

    select_profession(driver, matched_profession)

    # License types can change after the profession changes, so wait for the
    # refreshed dropdown before selecting the requested type.
    wait_for_dropdown_population(driver, LICENSE_TYPE_SELECT_ID)
    available_license_types = collect_option_texts(driver, LICENSE_TYPE_SELECT_ID)
    matched_license_type = find_matching_option(license_type_text, available_license_types)
    if not matched_license_type:
        return matched_profession, []

    select_license_type(driver, matched_license_type)
    click_search(driver, wait)

    if not wait_for_results_or_empty(driver):
        return matched_profession, []

    records = scrape_all_pages(driver, wait)
    return matched_profession, [
        {
            **record,
            "Searched State": state_code,
            "Searched Profession": matched_profession,
            "Searched License Type": matched_license_type,
        }
        for record in records
    ]


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    header_written = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
    total_rows = 0
    cities = read_city_values(CITY_INPUT_FILE)

    if not cities:
        print(f"No city values found in {CITY_INPUT_FILE}")
        driver.quit()
        return

    try:
        for city in cities:
            for state_code in STATES:
                for profession_keyword in PROFESSION_KEYWORDS:
                    try:
                        open_search_page(driver, wait)
                        select_state(driver, state_code)
                        enter_city(driver, city)
                        wait_for_dropdown_population(driver, PROFESSION_SELECT_ID)
                        available_professions = collect_option_texts(driver, PROFESSION_SELECT_ID)
                        matched_profession = find_matching_option(profession_keyword, available_professions)
                        if not matched_profession:
                            print(f"Skipping {city} / {state_code} / {profession_keyword}: no profession match")
                            continue

                        select_profession(driver, matched_profession)
                        wait_for_dropdown_population(driver, LICENSE_TYPE_SELECT_ID)
                        available_license_types = collect_option_texts(driver, LICENSE_TYPE_SELECT_ID)

                        if not available_license_types:
                            print(f"Skipping {city} / {state_code} / {matched_profession}: no license types")
                            continue

                        for license_type_text in available_license_types:
                            try:
                                open_search_page(driver, wait)
                                select_state(driver, state_code)
                                enter_city(driver, city)
                                wait_for_dropdown_population(driver, PROFESSION_SELECT_ID)
                                available_professions = collect_option_texts(driver, PROFESSION_SELECT_ID)
                                matched_profession = find_matching_option(profession_keyword, available_professions)
                                if not matched_profession:
                                    continue

                                select_profession(driver, matched_profession)
                                wait_for_dropdown_population(driver, LICENSE_TYPE_SELECT_ID)
                                available_license_types = collect_option_texts(driver, LICENSE_TYPE_SELECT_ID)
                                matched_license_type = find_matching_option(license_type_text, available_license_types)
                                if not matched_license_type:
                                    continue

                                select_license_type(driver, matched_license_type)
                                click_search(driver, wait)

                                if not wait_for_results_or_empty(driver):
                                    print(f"{city} | {state_code} | {matched_profession} | {matched_license_type}: 0 rows")
                                    continue

                                records = scrape_all_pages(driver, wait)
                                if records:
                                    records = [
                                        {
                                            **record,
                                            "Searched State": state_code,
                                            "Searched City": city,
                                            "Searched Profession": matched_profession,
                                            "Searched License Type": matched_license_type,
                                        }
                                        for record in records
                                    ]
                                    append_csv(
                                        OUTPUT_FILE,
                                        records,
                                        write_header=not header_written,
                                    )
                                    header_written = True
                                    total_rows += len(records)

                                print(
                                    f"{city} | {state_code} | {matched_profession} | {matched_license_type}: "
                                    f"{len(records)} rows (total {total_rows})"
                                )
                            except Exception as exc:
                                print(
                                    f"ERROR on {city} / {state_code} / {profession_keyword} / {license_type_text}: {exc}"
                                )
                    except Exception as exc:
                        print(f"ERROR on {city} / {state_code} / {profession_keyword}: {exc}")
    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
