import csv
import os
import re
import time
from urllib.parse import urljoin

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


BASE_URL = "https://dhp.virginiainteractive.org"
SEARCH_URL = "https://dhp.virginiainteractive.org/Lookup/Index"
URLS_FILE = "dhp_detail_urls.txt"
OUTPUT_FILE = "dhp_details.csv"

WAIT_SECONDS = 30
PAGE_LOAD_SLEEP = 1.0
RESULT_WAIT_SECONDS = 20
HEADLESS = False

KEYWORDS = [
    "Pharmacy",
    "Pharmacy Intern",
    "Pharmacy Technician",
    "Radiologist Assistant",
    "Registered Nurse",
    "Respiratory Therapist",
    "Licensed Clinical Social Worker",
    "Speech-Language Pathologist",
    "Polysomnographic Technologist",
    "Provisional Audiologist",
    "Provisional Speech-Language Pathologist",
    "Qualified Mental Health Professional",
    "Licensed Surgical Assistant",
    "Physical Therapist Assistant",
    "Clinical Nurse Specialist",
    "Clinical Psychologist",
    "Physician Assistant",
    "Audiologist",
]

STATES = [
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "District of Columbia",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
]

FIELDNAMES = [
    "Detail URL",
    "License Number",
    "Occupation",
    "Name",
    "Address",
    "Initial License Date",
    "Expire Date",
    "License Status",
    "Additional Public Information",
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
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(120)
    return driver


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "OccupationId")))


def get_select(driver, element_id):
    return Select(driver.find_element(By.ID, element_id))


def select_option_by_text(driver, element_id, target_text):
    target = normalize(target_text)
    select = get_select(driver, element_id)
    for option in select.options:
        if normalize(option.text) == target and (option.get_attribute("value") or "").strip():
            option.click()
            time.sleep(0.4)
            return
    select.select_by_visible_text(target_text)
    time.sleep(0.4)


def select_occupation(driver, occupation_name):
    select_option_by_text(driver, "OccupationId", occupation_name)


def select_state(driver, state_name):
    select_option_by_text(driver, "State", state_name)


def select_current_licensees(driver):
    select_option_by_text(driver, "LicStatus", "Current Licensees")


def click_search(driver, wait):
    button = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//input[@type='submit' and @name='submitBtn' and @value='Search']")
        )
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    try:
        button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", button)


def get_result_table(driver):
    try:
        return driver.find_element(
            By.CSS_SELECTOR, "table.table.table-responsive.table-striped"
        )
    except NoSuchElementException:
        return None


def get_result_rows(driver):
    table = get_result_table(driver)
    if not table:
        return []

    rows = []
    for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
        if row.find_elements(By.CSS_SELECTOR, "td.OccupationHeader"):
            continue
        if row.find_elements(By.CSS_SELECTOR, "a[href*='/Lookup/Detail/']"):
            rows.append(row)
    return rows


def page_has_empty_results_message(driver):
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except NoSuchElementException:
        return False

    text = normalize(body_text)
    empty_markers = [
        "NO RECORDS",
        "NO RECORD FOUND",
        "NO RESULTS",
        "NO DATA",
        "NO MATCHING",
    ]
    return any(marker in text for marker in empty_markers)


def wait_for_results_or_empty(driver):
    end_time = time.time() + RESULT_WAIT_SECONDS
    while time.time() < end_time:
        if get_result_rows(driver):
            return True
        if page_has_empty_results_message(driver):
            return False
        time.sleep(0.5)
    return False


def extract_urls_from_page(driver):
    urls = []
    for row in get_result_rows(driver):
        for link in row.find_elements(By.CSS_SELECTOR, "a[href*='/Lookup/Detail/']"):
            href = (link.get_attribute("href") or "").strip()
            if href:
                urls.append(urljoin(BASE_URL, href))
    return urls


def has_next_page(driver):
    candidates = [
        "//a[normalize-space()='Next']",
        "//button[normalize-space()='Next']",
        "//a[contains(normalize-space(),'Next')]",
        "//button[contains(normalize-space(),'Next')]",
        "//a[normalize-space()='>']",
    ]
    for xpath in candidates:
        try:
            element = driver.find_element(By.XPATH, xpath)
        except NoSuchElementException:
            continue
        classes = (element.get_attribute("class") or "").lower()
        aria_disabled = (element.get_attribute("aria-disabled") or "").lower()
        if "disabled" in classes or aria_disabled == "true":
            continue
        return element
    return None


def click_next_page(driver, wait):
    next_button = has_next_page(driver)
    if not next_button:
        return False

    rows = get_result_rows(driver)
    anchor = rows[0] if rows else None

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
    try:
        next_button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", next_button)

    if anchor is not None:
        try:
            wait.until(EC.staleness_of(anchor))
        except TimeoutException:
            pass
    time.sleep(0.8)
    return True


def scrape_all_urls(driver, wait):
    seen = set()
    urls = []

    while True:
        page_urls = extract_urls_from_page(driver)
        for url in page_urls:
            if url not in seen:
                seen.add(url)
                urls.append(url)

        if not click_next_page(driver, wait):
            break

    return urls


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line.rstrip() + "\n")


def read_lines(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def collect_urls():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    all_urls = []
    seen = set()

    try:
        for occupation in KEYWORDS:
            for state_name in STATES:
                try:
                    open_search_page(driver, wait)
                    select_occupation(driver, occupation)
                    select_state(driver, state_name)
                    select_current_licensees(driver)
                    click_search(driver, wait)

                    if not wait_for_results_or_empty(driver):
                        print(f"{occupation} | {state_name}: 0 urls")
                        continue

                    page_urls = scrape_all_urls(driver, wait)
                    new_urls = 0
                    for url in page_urls:
                        if url not in seen:
                            seen.add(url)
                            all_urls.append(url)
                            new_urls += 1

                    print(f"{occupation} | {state_name}: {new_urls} new urls")
                except Exception as exc:
                    print(f"ERROR on {occupation} / {state_name}: {exc}")

        write_lines(URLS_FILE, all_urls)
        print(f"Saved {len(all_urls)} unique urls to {URLS_FILE}")
    finally:
        driver.quit()

    return all_urls


def get_text(cell):
    return re.sub(r"\s+", " ", cell.text or "").strip()


def parse_detail_page(driver, url):
    driver.get(url)
    wait = WebDriverWait(driver, WAIT_SECONDS)
    wait_for_page_ready(driver, wait)
    wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "table.table.table-responsive.borderless")
        )
    )

    data = {field: "" for field in FIELDNAMES}
    data["Detail URL"] = url

    table = driver.find_element(By.CSS_SELECTOR, "table.table.table-responsive.borderless")
    for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
        headers = row.find_elements(By.TAG_NAME, "th")
        cells = row.find_elements(By.TAG_NAME, "td")
        if not headers or not cells:
            continue

        key = normalize(get_text(headers[0]))
        value = get_text(cells[0])

        if key == "LICENSE NUMBER":
            data["License Number"] = value
        elif key == "OCCUPATION":
            data["Occupation"] = value
        elif key == "NAME":
            data["Name"] = value
        elif key == "ADDRESS":
            data["Address"] = value
        elif key == "INITIAL LICENSE DATE":
            data["Initial License Date"] = value
        elif key == "EXPIRE DATE":
            data["Expire Date"] = value
        elif key == "LICENSE STATUS":
            data["License Status"] = value
        elif key.startswith("ADDITIONAL PUBLIC INFORMATION"):
            data["Additional Public Information"] = value

    return data


def scrape_details(urls):
    driver = build_driver()
    records = []
    try:
        for index, url in enumerate(urls, start=1):
            try:
                record = parse_detail_page(driver, url)
                records.append(record)
                print(f"{index}/{len(urls)} scraped: {url}")
            except Exception as exc:
                print(f"ERROR on detail page {url}: {exc}")
    finally:
        driver.quit()
    return records


def write_csv(path, records):
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main():
    urls = collect_urls()
    if not urls and os.path.exists(URLS_FILE):
        urls = read_lines(URLS_FILE)

    if not urls:
        print("No detail URLs collected; skipping detail scrape.")
        return

    records = scrape_details(urls)
    write_csv(OUTPUT_FILE, records)
    print(f"Saved {len(records)} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
