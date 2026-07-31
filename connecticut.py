import csv
import os
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

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


SEARCH_URL = "https://www.elicense.ct.gov/Lookup/LicenseLookup.aspx"
OUTPUT_FILE = "connecticut.csv"
WAIT_SECONDS = 40
PAGE_LOAD_SLEEP = 1.0
RESULT_SETTLE_SLEEP = 1.0
HEADLESS = False

LICENSE_TYPE_KEYWORDS = [
    #"Acupuncturist",
    "Advanced Emergency Medical Technician",
    "Advanced Practice Registered Nurse",
    # "Ambulatory Surgical Center",
    # "Assisted Living Service Agency",
    # "Athletic Trainer",
    "Audiologist",
    "Behavior Analyst",
    # "Blood Collection Facility",
    # "Certified Alcohol and Drug Counselor",
    # "Certified EMS Organization",
    # "Children's Hospital",
    "Chiropractic Physician",
    "Clinical Laboratory",
    "Community Health Worker",
   # "CONTROLLED SUBSTANCE REGISTRATION FOR PRACTITIONER",
    "Dental Hygienist",
    "Dentist",
    "Dietitian/Nutritionist",
    # "Doula",
    # "Emergency Ambulance",
    # "Emergency Medical Responder",
    # "Emergency Medical Services - Instructor",
    # "Emergency Medical Technician",
    # "Extended Care Facility",
    # # "Family Planning",
    # "General Hospital",
    # "Genetic Counselor",
    "Hearing Instrument Specialist",
    # "Hemodialysis",
    # "Home Health Care",
    # "Homemaker Home Health Care",
    "Homeopathic Physician",
    # "Hospice",
    # "Hospitals for Mentally Ill Persons",
    # "Infirmary Operated by an Educational Institution",
    # "Lactation Consultant",
    # "Licensed Alcohol and Drug Counselor",
    # "Licensed Clinical Social Worker",
    "Licensed Nurse Midwife",
    "Licensed Practical Nurse",
    "Marital and Family Therapist",
    "Massage Therapist",
    "Medical Response Technician - CSP",
    # "Medical School Faculty License",
    # "Medication Administration Certification",
   # "Mental Health Community Residence",
    "Mental Health Day Treatment",
    "Mental Health Intermediate Treatment",
    #"Mental Health Residential Living",
    "Music Therapist",
    "Naturopathic Physician",
    "Nurses Aide",
    "Nursing Home Administrator",
    "Occupational Therapist",
    "Occupational Therapist Assistant",
    "Optician",
    # "Optometrist",
    # "Outpatient Clinic",
    # "Paramedic",
    # "Perfusionist",
    "Pharmacist",
    "Pharmacy",
    "Pharmacy Intern",
    "Pharmacy Technician",
    "Physical Therapist",
    "Physical Therapist Assistant",
    "Physician / Surgeon",
    "Physician Assistant",
    # "Podiatrist",
    # "Professional Counselor",
    # "Provisional Faculty Dentist",
    # "Psychiatric Hospital",
    # "Psychiatric Outpatient Clinic",
    # "Psychiatric Residential Treatment Facility",
    "Psychologist",
    "Radiographer",
    "Registered Nurse",
    "Registered Sanitarian",
    "Respiratory Care Practitioner",
    "Speech and Language Pathologist",
    # "Substance Abuse",
    "Temporary Nursing Services Agency",
    # "Veterinarian",
    # "Well Child Clinic"
]

#LICENSE_TYPE_KEYWORDS = ["Behaviour analyst", "nursing"]
STATUSES = ["ACTIVE"]
STATES = ["AL", "AK"]

CSV_SOURCE_CANDIDATES = ["zip_codes_co.csv", "zip_codes_CO.csv", "zip_codes.csv"]

FIELDNAMES = [
    "searched_keyword",
    "searched_state",
    "searched_city",
    "name",
    "credential",
    "credential_description",
    "status",
    "status_reason",
    "city",
    "dba",
    "detail_url",
]


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def normalize_key(text: str) -> str:
    normalized = normalize_text(text).casefold()
    normalized = normalized.replace("behaviour", "behavior")
    return normalized


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
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
    wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_lbMultipleCredentialTypePrefix")))
    wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_ddStatus")))
    wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_ddStates")))
    wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_tbCity_ContactAddress")))


def find_best_option_text(keyword: str, option_texts: Sequence[str]) -> Optional[str]:
    target = normalize_key(keyword)
    best_exact = None
    contains_matches = []
    token_matches = []
    target_tokens = {token for token in target.split() if token}

    for option_text in option_texts:
        normalized = normalize_key(option_text)
        if normalized == target:
            best_exact = option_text
            break
        if target in normalized or normalized in target:
            contains_matches.append(option_text)
            continue
        option_tokens = {token for token in normalized.split() if token}
        overlap = len(target_tokens & option_tokens)
        if overlap:
            token_matches.append((overlap, len(option_tokens), option_text))

    if best_exact:
        return best_exact
    if contains_matches:
        return sorted(contains_matches, key=len)[0]
    if token_matches:
        token_matches.sort(key=lambda item: (-item[0], item[1], item[2]))
        return token_matches[0][2]
    return None


def select_single_option(select: Select, target: str):
    normalized_target = normalize_key(target)
    for option in select.options:
        value = normalize_key(option.get_attribute("value") or "")
        text = normalize_key(option.text)
        if value == normalized_target or text == normalized_target:
            option.click()
            return
    select.select_by_visible_text(target)


def select_multiple_options(select: Select, targets: Sequence[str]):
    if not select.is_multiple:
        raise RuntimeError("License type select is not multi-select.")

    available_texts = [normalize_text(option.text) for option in select.options if normalize_text(option.text)]
    matched_texts = []
    for keyword in targets:
        best = find_best_option_text(keyword, available_texts)
        if best:
            matched_texts.append(best)

    if not matched_texts:
        raise NoSuchElementException(f"Could not match any license type options for: {targets}")

    select.deselect_all()
    for option_text in matched_texts:
        for option in select.options:
            if normalize_key(option.text) == normalize_key(option_text):
                option.click()
                break


def set_text_input(driver, wait, element_id: str, value: str):
    last_error = None
    for _ in range(4):
        try:
            element = wait.until(EC.presence_of_element_located((By.ID, element_id)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.clear()
            element.send_keys(value)
            return
        except (StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error


def select_status(driver, wait, status: str):
    select = Select(wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_ddStatus"))))
    select_single_option(select, status)
    time.sleep(0.3)


def select_state(driver, wait, state: str):
    select = Select(wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_ddStates"))))
    select_single_option(select, state)
    time.sleep(0.3)


def select_license_types(driver, wait, keywords: Sequence[str]):
    select = Select(wait.until(EC.presence_of_element_located((By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_lbMultipleCredentialTypePrefix"))))
    select_multiple_options(select, keywords)
    time.sleep(0.5)


def click_search(driver, wait):
    search_button = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_btnLookup")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_button)
    try:
        search_button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", search_button)
    time.sleep(RESULT_SETTLE_SLEEP)


def normalize_credential(text: str) -> str:
    normalized = normalize_text(text)
    digits = re.findall(r"\d+", normalized)
    if not digits:
        return normalized[-4:]
    joined = "".join(digits)
    return joined[-4:].zfill(4)


def get_result_rows(driver):
    return driver.find_elements(By.CSS_SELECTOR, "tr.forge-table-row")


def parse_card_row(row, searched_keyword: str, searched_state: str, searched_city: str) -> Optional[Dict[str, str]]:
    try:
        name = normalize_text(row.find_element(By.CSS_SELECTOR, "b.forge-typography--headline6").text)
    except NoSuchElementException:
        name = ""

    fields: Dict[str, str] = {
        "searched_keyword": searched_keyword,
        "searched_state": searched_state,
        "searched_city": searched_city,
        "name": name,
        "credential": "",
        "credential_description": "",
        "status": "",
        "status_reason": "",
        "city": "",
        "dba": "",
        "detail_url": "",
    }

    for col in row.find_elements(By.CSS_SELECTOR, "div.col.col-md-4"):
        try:
            label = normalize_key(col.find_element(By.CSS_SELECTOR, "b").text)
        except NoSuchElementException:
            continue
        try:
            value = normalize_text(col.find_element(By.CSS_SELECTOR, "p").text)
        except NoSuchElementException:
            value = ""

        if label == "credential":
            fields["credential"] = normalize_credential(value)
        elif label == "credential description":
            fields["credential_description"] = value
        elif label == "status":
            fields["status"] = value
        elif label == "status reason":
            fields["status_reason"] = value
        elif label == "city":
            fields["city"] = value
        elif label == "dba":
            fields["dba"] = value

    try:
        detail_link = row.find_element(By.CSS_SELECTOR, "a.btn.btn-outline-primary")
        fields["detail_url"] = detail_link.get_attribute("href") or detail_link.get_attribute("onclick") or ""
    except NoSuchElementException:
        pass

    return fields


def page_has_results(driver) -> bool:
    return len(get_result_rows(driver)) > 0


def page_has_no_results(driver) -> bool:
    try:
        body_text = normalize_key(driver.find_element(By.TAG_NAME, "body").text)
    except NoSuchElementException:
        return False
    markers = ["no results", "no record", "no records", "no data", "no matches", "no matching"]
    return any(marker in body_text for marker in markers)


def wait_for_results(driver, wait):
    try:
        wait.until(lambda d: page_has_results(d) or page_has_no_results(d))
    except TimeoutException:
        return False
    return page_has_results(driver)


def get_next_button(driver):
    candidates = [
        (By.XPATH, "//a[contains(normalize-space(),'Next')]"),
        (By.XPATH, "//button[contains(normalize-space(),'Next')]"),
        (By.XPATH, "//input[@type='submit' and contains(@value,'Next')]"),
    ]
    for by, value in candidates:
        try:
            element = driver.find_element(by, value)
            classes = normalize_key(element.get_attribute("class") or "")
            aria_disabled = normalize_key(element.get_attribute("aria-disabled") or "")
            if "disabled" in classes or aria_disabled == "true":
                return None
            return element
        except NoSuchElementException:
            continue
    return None


def scrape_current_results(driver, searched_keyword: str, searched_state: str, searched_city: str) -> List[Dict[str, str]]:
    records = []
    seen = set()
    for row in get_result_rows(driver):
        record = parse_card_row(row, searched_keyword, searched_state, searched_city)
        if not record:
            continue
        key = (
            record["name"],
            record["credential"],
            record["status"],
            record["city"],
            record["searched_keyword"],
            record["searched_state"],
            record["searched_city"],
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def scrape_all_pages(driver, wait, searched_keyword: str, searched_state: str, searched_city: str) -> List[Dict[str, str]]:
    all_records: List[Dict[str, str]] = []
    seen = set()

    while True:
        current_records = scrape_current_results(driver, searched_keyword, searched_state, searched_city)
        for record in current_records:
            key = (
                record["name"],
                record["credential"],
                record["status"],
                record["city"],
                record["detail_url"],
                record["searched_keyword"],
                record["searched_state"],
                record["searched_city"],
            )
            if key not in seen:
                seen.add(key)
                all_records.append(record)

        next_button = get_next_button(driver)
        if not next_button:
            break

        previous_first = current_records[0]["detail_url"] if current_records else ""
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
        try:
            next_button.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", next_button)

        try:
            wait.until(lambda d: scrape_current_results(d, searched_keyword, searched_state, searched_city) and scrape_current_results(d, searched_keyword, searched_state, searched_city)[0]["detail_url"] != previous_first)
        except TimeoutException:
            break

        time.sleep(RESULT_SETTLE_SLEEP)

    return all_records


def read_unique_cities() -> List[str]:
    for candidate in CSV_SOURCE_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "city" not in [name.lower() for name in reader.fieldnames if name]:
                continue
            cities = []
            seen = set()
            for row in reader:
                raw_city = normalize_text(row.get("city", ""))
                if not raw_city:
                    continue
                key = raw_city.casefold()
                if key in seen:
                    continue
                seen.add(key)
                cities.append(raw_city)
            if cities:
                return cities
    raise FileNotFoundError("Could not find a city CSV with a 'city' column.")


def ensure_output_header(path: str):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()


def append_records(path: str, records: List[Dict[str, str]]):
    if not records:
        return
    ensure_output_header(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writerows(records)


def run_search(driver, wait, keyword: str, state: str, city: str) -> List[Dict[str, str]]:
    open_search_page(driver, wait)
    select_license_types(driver, wait, [keyword])
    select_status(driver, wait, "ACTIVE")
    select_state(driver, wait, state)
    set_text_input(driver, wait, "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_tbCity_ContactAddress", city)
    click_search(driver, wait)
    wait_for_results(driver, wait)
    return scrape_all_pages(driver, wait, keyword, state, city)


def run():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    cities = read_unique_cities()
    total_records = 0

    try:
        for keyword in LICENSE_TYPE_KEYWORDS:
            for state in STATES:
                for city in cities:
                    print(f"Searching keyword={keyword!r}, state={state!r}, city={city!r}")
                    try:
                        records = run_search(driver, wait, keyword, state, city)
                    except Exception as exc:
                        print(f"Search failed for keyword={keyword!r}, state={state!r}, city={city!r}: {exc}")
                        continue

                    if not records:
                        continue

                    append_records(OUTPUT_FILE, records)
                    total_records += len(records)
                    print(f"Collected {len(records)} records")

        print(f"Saved {total_records} records to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
