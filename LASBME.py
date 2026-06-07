import csv
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


LANDING_URL = "https://online.lasbme.org/#/verifylicense"
CHROMEDRIVER_VERSION = "148.0.7778.179"
WAIT_SECONDS = 30
PAGE_LOAD_SLEEP = 1.5
RESULT_WAIT_SECONDS = 20
HEADLESS = False
OUTPUT_FILE = "LASBME.csv"

# Practitioner types to search (must match the dropdown option text, ignoring whitespace).
KEYWORDS = [
    "LICENSED RESPIRATORY THERAPIST",
    "LICENSED RESPIRATORY THERAPIST -RRT",
    "LICENSED RESPIRATORY THERAPIST TEMPORARY PERMIT",
    "LICENSED RESPIRATORY THERAPIST -CRT",
    "MEDICAL PSYCHOLOGIST",
    "LABORATORY ASSISTANT",
    "CLS - PHLEBOTOMY",
    "PHYSICIAN & SURGEON - DO",
    "PHYSICIAN & SURGEON - MD",
    "PHYSICIAN & SURGEON COMPACT LICENSE",
    "PHYSICIAN ASSISTANT",
    "PRIVATE RADIOLOGICAL TECHNOLOGY",
    "OCCUPATIONAL THERAPIST",
    "OCCUPATIONAL THERAPIST COMPACT LICENSE",
]

STATES = [
    "Alabama",
    "Alaska",
    "Alberta",
    "American Samoa",
    "Arizona",
    "Arkansas",
    "British Columbia",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "District of Columbia",
    "Federated States of Micronesia",
    "Florida",
    "Foreign",
    "Georgia",
    "Guam",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Manitoba",
    "Marshall Islands",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Brunswick",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "Newfoundland and Labrador",
    "North Carolina",
    "North Dakota",
    "Northern Mariana Islands",
    "Northwest Territories",
    "Nova Scotia",
    "Nunavut",
    "Ohio",
    "Oklahoma",
    "Ontario",
    "Oregon",
    "Palau",
    "Pennsylvania",
    "Prince Edward Island",
    "Puerto Rico",
    "Quebec",
    "Rhode Island",
    "Saskatchewan",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virgin Islands",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
    "Yukon",
]

FIELDNAMES = [
    "Searched Practitioner Type",
    "Type Value",
    "Searched State",
    "Name",
    "Credential Number",
    "Current Status",
    "Practitioner Type",
    "City",
    "State",
    "Zip Code",
]


def normalize(text):
    return re.sub(r"\s+", " ", text).strip().upper()


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    if HEADLESS:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager(driver_version=CHROMEDRIVER_VERSION).install())
    return webdriver.Chrome(service=service, options=options)


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_form(driver, wait):
    driver.get(LANDING_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.NAME, "PractitionerType")))


def wait_for_practitioner_type_options(driver, wait):
    """Wait until the PractitionerType dropdown has populated options."""
    def has_options(d):
        try:
            select = get_select(d, "PractitionerType")
            options = [
                option.text.strip()
                for option in select.options
                if option.text.strip() and option.get_attribute("value")
            ]
            return bool(options)
        except NoSuchElementException:
            return False

    wait.until(has_options)


def get_select(driver, name):
    return Select(driver.find_element(By.NAME, name))


def build_practitioner_value_map(driver):
    """Map normalized option text -> list of option values.

    The dropdown contains duplicate labels with different values
    (e.g. "CLS - PHLEBOTOMY TEMPORARY PERMIT" is both 83 and 113), so a
    single label can resolve to several searchable values.
    """
    wait_for_practitioner_type_options(driver, WebDriverWait(driver, WAIT_SECONDS))
    select = get_select(driver, "PractitionerType")
    value_map = {}
    for option in select.options:
        value = option.get_attribute("value")
        text = normalize(option.text)
        if value and text:
            values = value_map.setdefault(text, [])
            if value not in values:
                values.append(value)
    return value_map


def select_practitioner(driver, value):
    get_select(driver, "PractitionerType").select_by_value(value)
    time.sleep(0.3)


def select_active_status(driver):
    select = get_select(driver, "licenseStatus")
    select.select_by_visible_text("Active")
    time.sleep(0.3)


def select_state(driver, state_name):
    select = get_select(driver, "StateCode")
    select.select_by_visible_text(state_name)
    time.sleep(0.3)


def click_search(driver, wait):
    button = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//input[@type='submit' and @value='Search']")
        )
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    try:
        button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", button)


def set_page_size_100(driver):
    try:
        select = Select(driver.find_element(By.XPATH, "//select[@title='Show']"))
        select.select_by_value("100")
        time.sleep(1.0)
        return True
    except (NoSuchElementException, StaleElementReferenceException):
        return False


def get_data_rows(driver):
    try:
        table = driver.find_element(By.XPATH, "//table[contains(@class,'verify-table')]")
    except NoSuchElementException:
        return []
    rows = []
    for row in table.find_elements(By.XPATH, ".//tbody/tr"):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) >= 7 and cells[0].text.strip():
            rows.append(row)
    return rows


def wait_for_results_or_empty(driver):
    """Return True if data rows appeared, False if the search returned nothing."""
    end_time = time.time() + RESULT_WAIT_SECONDS
    while time.time() < end_time:
        rows = get_data_rows(driver)
        if rows:
            return True
        time.sleep(0.5)
    return False


def parse_row(row):
    cells = row.find_elements(By.TAG_NAME, "td")
    values = [cell.text.strip() for cell in cells[:7]]
    while len(values) < 7:
        values.append("")
    return {
        "Name": values[0],
        "Credential Number": values[1],
        "Current Status": values[2],
        "Practitioner Type": values[3],
        "City": values[4],
        "State": values[5],
        "Zip Code": values[6],
    }


def extract_current_page_records(driver):
    records = []
    row_count = len(get_data_rows(driver))
    for index in range(row_count):
        attempts = 0
        while attempts < 3:
            try:
                rows = get_data_rows(driver)
                if index >= len(rows):
                    break
                records.append(parse_row(rows[index]))
                break
            except StaleElementReferenceException:
                attempts += 1
                time.sleep(0.3)
    return records


def click_next_page(driver, wait):
    try:
        next_link = driver.find_element(
            By.XPATH, "//a[normalize-space()='Next' and contains(@ng-click,'selectPage')]"
        )
    except NoSuchElementException:
        return False

    if next_link.get_attribute("disabled") or "disabled" in (
        next_link.get_attribute("class") or ""
    ):
        return False

    first_rows = get_data_rows(driver)
    anchor = first_rows[0] if first_rows else None

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_link)
    try:
        next_link.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", next_link)

    if anchor is not None:
        try:
            wait.until(EC.staleness_of(anchor))
        except TimeoutException:
            pass
    time.sleep(0.8)
    return True


def scrape_all_pages(driver, wait):
    records = []
    seen = set()
    while True:
        page_records = extract_current_page_records(driver)
        new_on_page = 0
        for record in page_records:
            key = (record["Credential Number"], record["Name"])
            if key not in seen:
                seen.add(key)
                records.append(record)
                new_on_page += 1

        if not click_next_page(driver, wait):
            break
        # Guard against a Next button that does not advance the list.
        if new_on_page == 0:
            break
    return records


def run_search(driver, wait, value, state_name):
    open_search_form(driver, wait)
    select_practitioner(driver, value)
    select_active_status(driver)
    select_state(driver, state_name)
    click_search(driver, wait)

    if not wait_for_results_or_empty(driver):
        return []

    set_page_size_100(driver)
    time.sleep(0.5)
    return scrape_all_pages(driver, wait)


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)

    total_rows = 0
    try:
        open_search_form(driver, wait)
        value_map = build_practitioner_value_map(driver)

        # Resolve each keyword to its dropdown option value(s) up front. A
        # label may map to multiple values, each of which is searched.
        resolved = []
        for keyword in KEYWORDS:
            values = value_map.get(normalize(keyword))
            if values:
                for value in values:
                    resolved.append((keyword, value))
                if len(values) > 1:
                    print(f"NOTE: '{keyword}' maps to {len(values)} values: {values}")
            else:
                print(f"WARNING: practitioner type not found in dropdown: {keyword}")

        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
            writer.writeheader()

            for keyword, value in resolved:
                for state_name in STATES:
                    try:
                        records = run_search(driver, wait, value, state_name)
                    except Exception as exc:
                        print(f"ERROR on {keyword} / {state_name}: {exc}")
                        records = []

                    for record in records:
                        row = dict(record)
                        row["Searched Practitioner Type"] = keyword
                        row["Type Value"] = value
                        row["Searched State"] = state_name
                        writer.writerow(row)

                    csv_file.flush()
                    total_rows += len(records)
                    print(f"{keyword} [{value}] | {state_name}: {len(records)} rows (total {total_rows})")

    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
