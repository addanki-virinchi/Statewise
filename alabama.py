import csv
import os
import time
from typing import List

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


SEARCH_URL = "https://dashboard.albme.gov/Verification/search.aspx"
OUTPUT_FILE = "alabama.csv"
WAIT_SECONDS = 30
PAGE_SLEEP = 1.0
HEADLESS = False
LICENSE_TYPES = [
    # "AA",
    # "ACSC",
    # "AMCP",
    # "CP",
    # "CPP",
    # "DO",
    # "L",
    # "LPSP",
    # "MD",
    # "PA",
    # "QACSC",
    # "QACSCNP",
    "RA",
]


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    if HEADLESS:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(120)
    return driver


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_SLEEP)


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "ddLicType")))


def select_license_type(driver, license_type: str):
    select = Select(driver.find_element(By.ID, "ddLicType"))
    target = license_type.strip()

    for option in select.options:
        value = (option.get_attribute("value") or "").strip()
        text = (option.text or "").strip()
        if value == target or text == target:
            option.click()
            time.sleep(0.5)
            return

    select.select_by_visible_text(target)
    time.sleep(0.5)


def wait_for_manual_captcha(license_type: str):
    input(
        f"Solve the 'I am not a robot' captcha for license type '{license_type}' in the browser, then press Enter here to continue..."
    )


def click_search(driver, wait):
    search_button = wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_button)
    search_button.click()
    try:
        wait.until(
            lambda d: d.find_elements(By.CSS_SELECTOR, "table.table.table-hover tbody tr")
            or d.find_elements(By.XPATH, "//*[contains(normalize-space(),'No records')]")
            or d.find_elements(By.XPATH, "//*[contains(normalize-space(),'No results')]")
        )
    except TimeoutException:
        pass
    time.sleep(1)


def get_result_rows(driver):
    table = driver.find_element(By.CSS_SELECTOR, "table.table.table-hover")
    rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
    valid_rows = []
    for row in rows:
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) >= 7:
            valid_rows.append(row)
    return valid_rows


def parse_current_page(driver) -> List[dict]:
    records = []
    for row in get_result_rows(driver):
        cells = row.find_elements(By.TAG_NAME, "td")
        records.append(
            {
                "Name": cells[0].text.strip(),
                "License #": cells[1].text.strip(),
                "License Type": cells[2].text.strip(),
                "License Status": cells[3].text.strip(),
                "Issue Date": cells[4].text.strip(),
                "Expiration Date": cells[5].text.strip(),
                "Practice Location(s)": cells[6].text.strip(),
            }
        )
    return records


def get_next_button(driver):
    candidates = [
        (By.XPATH, "//a[contains(normalize-space(),'Next')]"),
        (By.XPATH, "//input[@type='submit' and contains(@value,'Next')]"),
        (By.XPATH, "//button[contains(normalize-space(),'Next')]"),
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


def scrape_all_pages(driver, wait) -> List[dict]:
    all_records = []
    seen = set()

    while True:
        current_page_records = parse_current_page(driver)
        for record in current_page_records:
            key = (
                record["Name"],
                record["License #"],
                record["License Type"],
                record["Issue Date"],
                record["Expiration Date"],
            )
            if key not in seen:
                seen.add(key)
                all_records.append(record)

        next_button = get_next_button(driver)
        if not next_button:
            break

        previous_first_row = current_page_records[0] if current_page_records else None
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
        next_button.click()

        try:
            wait.until(lambda d: parse_current_page(d) and parse_current_page(d)[0] != previous_first_row)
        except TimeoutException:
            break

        time.sleep(1)

    return all_records


def write_csv(path: str, records: List[dict]):
    fieldnames = [
        "Name",
        "License #",
        "License Type",
        "License Status",
        "Issue Date",
        "Expiration Date",
        "Practice Location(s)",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def append_csv(path: str, records: List[dict]):
    fieldnames = [
        "Name",
        "License #",
        "License Type",
        "License Status",
        "Issue Date",
        "Expiration Date",
        "Practice Location(s)",
    ]
    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def run():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    all_records: List[dict] = []

    try:
        for license_type in LICENSE_TYPES:
            print(f"Running license type: {license_type}")
            open_search_page(driver, wait)
            select_license_type(driver, license_type)
            wait_for_manual_captcha(license_type)
            try:
                click_search(driver, wait)
            except TimeoutException:
                pass
            records = scrape_all_pages(driver, wait)
            print(f"Collected {len(records)} rows for {license_type}")
            if records:
                append_csv(OUTPUT_FILE, records)
                all_records.extend(records)

        print(f"Saved {len(all_records)} rows to {OUTPUT_FILE}")
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
