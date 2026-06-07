import csv
import re
import time

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


SEARCH_URL = "https://gateway.msbml.ms.gov/Verification/search.aspx"
OUTPUT_FILE = "Mississippi-db.csv"
WAIT_SECONDS = 30
CAPTCHA_WAIT_SECONDS = 15
PAGE_LOAD_SLEEP = 1.0
HEADLESS = False

KEYWORDS = [
    "Limited X-Ray Operator",
    "Radiologist Assistant",
    "Physician Assistant",
    "Physician Assistant - Certified",
    "Physician Assistant Temporary",
    "Physician Assistant Volunteer",
]


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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


def get_license_type_select(wait):
    return Select(wait.until(EC.presence_of_element_located((By.ID, "ddLicType"))))


def get_status_select(wait):
    return Select(wait.until(EC.presence_of_element_located((By.ID, "ddLicStatus"))))


def collect_license_type_options(wait):
    select = get_license_type_select(wait)
    return [option.text.strip() for option in select.options if option.text.strip()]


def find_matching_option(keyword, available_options):
    normalized_keyword = normalize(keyword)

    for option_text in available_options:
        if normalize(option_text) == normalized_keyword:
            return option_text

    contains_matches = []
    keyword_tokens = set(normalized_keyword.split())
    scored_matches = []

    for option_text in available_options:
        normalized_option = normalize(option_text)
        if normalized_keyword in normalized_option or normalized_option in normalized_keyword:
            contains_matches.append(option_text)

        option_tokens = set(normalized_option.split())
        overlap = len(keyword_tokens & option_tokens)
        if overlap:
            scored_matches.append((overlap, len(option_tokens), option_text))

    if contains_matches:
        contains_matches.sort(key=len)
        return contains_matches[0]

    if scored_matches:
        scored_matches.sort(key=lambda item: (-item[0], item[1], item[2]))
        return scored_matches[0][2]

    return None


def ensure_active_status(wait):
    status_select = get_status_select(wait)
    if status_select.first_selected_option.get_attribute("value") != "A":
        status_select.select_by_value("A")
        time.sleep(0.3)


def select_license_type(wait, option_text):
    select = get_license_type_select(wait)
    select.select_by_visible_text(option_text)
    time.sleep(0.5)


def click_search(driver, wait):
    button = wait.until(EC.element_to_be_clickable((By.ID, "btnSubmit")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    button.click()


def wait_for_results(driver, wait):
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table.table.table-hover tbody tr")))
    wait_for_page_ready(driver, wait)


def get_result_rows(driver):
    table = driver.find_element(By.CSS_SELECTOR, "table.table.table-hover")
    return table.find_elements(By.CSS_SELECTOR, "tbody tr")


def parse_current_page(driver, keyword, matched_license_type):
    records = []
    for row in get_result_rows(driver):
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 7:
                continue

            name_link = cells[0].find_elements(By.TAG_NAME, "a")
            detail_url = ""
            if name_link:
                href = name_link[0].get_attribute("href") or ""
                detail_url = href

            records.append(
                {
                    "Keyword": keyword,
                    "Matched License Type": matched_license_type,
                    "Name": cells[0].text.strip(),
                    "License Number": cells[1].text.strip(),
                    "License Type": cells[2].text.strip(),
                    "License Status": cells[3].text.strip(),
                    "Public Record": cells[4].text.strip(),
                    "City": cells[5].text.strip(),
                    "State": cells[6].text.strip(),
                    "Detail URL": detail_url,
                }
            )
        except StaleElementReferenceException:
            continue
    return records


def find_next_button(driver):
    candidates = [
        (By.XPATH, "//a[normalize-space()='Next']"),
        (By.XPATH, "//li[not(contains(@class,'disabled'))]/a[@aria-label='Next']"),
        (By.XPATH, "//a[contains(@href,'Page$Next')]"),
    ]
    for by, value in candidates:
        try:
            return driver.find_element(by, value)
        except NoSuchElementException:
            continue
    return None


def scrape_all_pages(driver, wait, keyword, matched_license_type):
    records = []
    seen_rows = set()

    while True:
        current_page_records = parse_current_page(driver, keyword, matched_license_type)
        for record in current_page_records:
            unique_key = (
                record["License Number"],
                record["Name"],
                record["License Type"],
                record["City"],
                record["State"],
            )
            if unique_key not in seen_rows:
                seen_rows.add(unique_key)
                records.append(record)

        next_button = find_next_button(driver)
        if next_button is None:
            break

        if "disabled" in (next_button.get_attribute("class") or "").lower():
            break

        first_row = get_result_rows(driver)[0]
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
        next_button.click()
        wait.until(EC.staleness_of(first_row))
        wait_for_results(driver, wait)

    return records


def reset_to_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    ensure_active_status(wait)


def save_csv(records):
    fieldnames = [
        "Keyword",
        "Matched License Type",
        "Name",
        "License Number",
        "License Type",
        "License Status",
        "Public Record",
        "City",
        "State",
        "Detail URL",
    ]

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"Saved {len(records)} rows to {OUTPUT_FILE}")


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    all_records = []

    try:
        driver.get(SEARCH_URL)
        wait_for_page_ready(driver, wait)

        available_options = collect_license_type_options(wait)
        print(f"Loaded {len(available_options)} license type options.")

        for keyword in KEYWORDS:
            matched_option = find_matching_option(keyword, available_options)
            if not matched_option:
                print(f"Skipping '{keyword}' because no matching dropdown option was found.")
                continue

            print(f"Preparing search for '{keyword}' using '{matched_option}'.")
            reset_to_search_page(driver, wait)
            select_license_type(wait, matched_option)
            ensure_active_status(wait)

            print(
                f"Waiting {CAPTCHA_WAIT_SECONDS} seconds before search so the CAPTCHA can be completed manually."
            )
            time.sleep(CAPTCHA_WAIT_SECONDS)

            click_search(driver, wait)

            try:
                wait_for_results(driver, wait)
            except TimeoutException:
                print(f"No results table found for '{keyword}'.")
                continue

            keyword_records = scrape_all_pages(driver, wait, keyword, matched_option)
            print(f"Collected {len(keyword_records)} rows for '{keyword}'.")
            all_records.extend(keyword_records)

        save_csv(all_records)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
