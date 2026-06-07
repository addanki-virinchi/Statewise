import csv
import re
import time
from collections import defaultdict

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


LANDING_URL = "https://www.michigan.gov/lara/i-need-to/find-or-verify-a-licensed-professional-or-business"
CHROMEDRIVER_VERSION = "148.0.7778.179"
WAIT_SECONDS = 30
PAGE_LOAD_SLEEP = 1.5
HEADLESS = False

KEYWORDS = [
    # "Registered Nurse",
    # "Medical Doctor",
    # "Osteopathic Physician",
    # "Physicians Assistant",
    # "Pharmacist",
    # "Pharmacy Technician",
    "Marriage and Family Therapist",
    "Medical Doctor Limited",
]

# These are the landing-page buttons worth checking for the supplied healthcare keywords.
PROFESSION_LINK_TEXTS = [
    "Nursing",
    "Pharmacy",
    "Physician Assistant",
    "Osteopathic Medicine & Surgery",
    "Medicine",
]

# Manual fallbacks for keywords that may need a broader license-type search.
KEYWORD_OPTION_OVERRIDES = {
    "Medical Doctor": ["Medical Doctor", "Physician", "Medicine"],
    "Osteopathic Physician": ["Osteopathic Physician", "Physician", "Osteopathic"],
    "Physicians Assistant": ["Physicians Assistant", "Physician Assistant"],
}

KEYWORD_PROFESSION_HINTS = {
    "Registered Nurse": ["Nursing"],
    "Medical Doctor": ["Medicine"],
    "Osteopathic Physician": ["Osteopathic Medicine & Surgery"],
    "Physicians Assistant": ["Physician Assistant"],
    "Pharmacist": ["Pharmacy"],
    "Pharmacy Technician": ["Pharmacy"],
}


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slugify(text):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return slug or "keyword"


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


def click_profession_link(driver, wait, link_text):
    driver.get(LANDING_URL)
    wait_for_page_ready(driver, wait)

    original_handles = set(driver.window_handles)
    link = wait.until(
        EC.element_to_be_clickable(
            (
                By.XPATH,
                f"//a[normalize-space()='{link_text}']",
            )
        )
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
    try:
        link.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", link)

    wait.until(lambda d: len(set(d.window_handles) - original_handles) >= 1 or d.current_url != LANDING_URL)
    new_handles = list(set(driver.window_handles) - original_handles)
    if new_handles:
        driver.switch_to.window(new_handles[-1])
    wait_for_page_ready(driver, wait)


def get_license_type_select(wait):
    element = wait.until(
        EC.presence_of_element_located(
            (By.ID, "ctl00_PlaceHolderMain_refLicenseeSearchForm_ddlLicenseType")
        )
    )
    return Select(element)


def find_best_option(keyword, available_options):
    normalized_keyword = normalize(keyword)
    exact_match = None
    contains_matches = []

    for option_text in available_options:
        normalized_option = normalize(option_text)
        if normalized_option == normalized_keyword:
            exact_match = option_text
            break

        if normalized_keyword in normalized_option or normalized_option in normalized_keyword:
            contains_matches.append(option_text)

    if exact_match:
        return exact_match

    if contains_matches:
        return sorted(contains_matches, key=len)[0]

    override_terms = KEYWORD_OPTION_OVERRIDES.get(keyword, [])
    if override_terms:
        for option_text in available_options:
            normalized_option = normalize(option_text)
            if any(normalize(term) in normalized_option for term in override_terms):
                return option_text

    keyword_tokens = set(normalized_keyword.split())
    scored = []
    for option_text in available_options:
        option_tokens = set(normalize(option_text).split())
        overlap = len(keyword_tokens & option_tokens)
        if overlap:
            scored.append((overlap, len(option_tokens), option_text))

    if scored:
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return scored[0][2]

    return None


def select_license_type(wait, option_text):
    select = get_license_type_select(wait)
    select.select_by_visible_text(option_text)
    time.sleep(0.5)


def click_search(driver, wait):
    candidate_locators = [
        (By.ID, "ctl00_PlaceHolderMain_btnSearch"),
        (By.ID, "ctl00_PlaceHolderMain_refLicenseeSearchForm_btnSearch"),
        (By.XPATH, "//input[@type='submit' and contains(@value, 'Search')]"),
        (By.XPATH, "//a[normalize-space()='Search']"),
        (By.XPATH, "//button[normalize-space()='Search']"),
    ]

    last_error = None
    for by, value in candidate_locators:
        try:
            button = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, value)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            button.click()
            return
        except Exception as exc:
            last_error = exc

    raise RuntimeError("Could not locate the Search button.") from last_error


def wait_for_results(wait):
    wait.until(
        EC.presence_of_element_located(
            (By.ID, "ctl00_PlaceHolderMain_refLicenseeList_gdvRefLicenseeList")
        )
    )


def get_result_rows(driver):
    table = driver.find_element(By.ID, "ctl00_PlaceHolderMain_refLicenseeList_gdvRefLicenseeList")
    rows = table.find_elements(By.XPATH, ".//tr")
    data_rows = []
    for row in rows:
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 9:
                continue
            if not row.find_elements(By.XPATH, ".//a[contains(@id,'lnkLicenseRefNumber')]"):
                continue
            data_rows.append(row)
        except StaleElementReferenceException:
            continue
    return data_rows


def parse_row(row):
    cells = row.find_elements(By.TAG_NAME, "td")
    values = []
    for cell in cells[:9]:
        values.append(cell.text.strip())
    return {
        "License Type": values[0],
        "License Number": values[1],
        "First Name": values[2],
        "Middle Initial": values[3],
        "Last Name": values[4],
        "Organization Name": values[5],
        "DBA/Trade Name": values[6],
        "License Status": values[7],
        "License Expiration Date": values[8],
    }


def extract_current_page_records(driver):
    records = []
    table = driver.find_element(By.ID, "ctl00_PlaceHolderMain_refLicenseeList_gdvRefLicenseeList")
    row_xpath = (
        ".//tr[td and .//a[contains(@id,'lnkLicenseRefNumber')] and count(td) >= 9]"
    )
    row_count = len(table.find_elements(By.XPATH, row_xpath))

    for index in range(1, row_count + 1):
        attempts = 0
        while attempts < 3:
            try:
                table = driver.find_element(By.ID, "ctl00_PlaceHolderMain_refLicenseeList_gdvRefLicenseeList")
                row = table.find_element(By.XPATH, f"({row_xpath})[{index}]")
                records.append(parse_row(row))
                break
            except StaleElementReferenceException:
                attempts += 1
                time.sleep(0.5)

    return records


def click_next_page(driver, wait, next_page_number):
    next_link = None
    candidate_locators = [
        (
            By.XPATH,
            f"//td[contains(@class,'aca_pagination_td')]/a[normalize-space()='{next_page_number}']",
        ),
        (
            By.XPATH,
            f"//a[normalize-space()='{next_page_number}' and contains(@href,'__doPostBack')]",
        ),
    ]

    for by, value in candidate_locators:
        try:
            next_link = driver.find_element(by, value)
            break
        except NoSuchElementException:
            continue

    if next_link is None:
        return False

    current_first_row = get_result_rows(driver)[0]
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_link)
    next_link.click()
    wait.until(EC.staleness_of(current_first_row))
    wait_for_results(wait)
    return True


def scrape_current_search(driver, wait):
    wait_for_results(wait)
    records = []
    seen_license_numbers = set()
    page_number = 1

    while True:
        page_records = extract_current_page_records(driver)
        if not page_records:
            break
        for record in page_records:
            license_number = record["License Number"]
            if license_number and license_number not in seen_license_numbers:
                seen_license_numbers.add(license_number)
                records.append(record)

        if not click_next_page(driver, wait, page_number + 1):
            break
        page_number += 1

    return records


def save_csv(keyword, records):
    output_name = f"Michigan_{slugify(keyword)}.csv"
    fieldnames = [
        "Keyword",
        "Matched License Type",
        "License Type",
        "License Number",
        "First Name",
        "Middle Initial",
        "Last Name",
        "Organization Name",
        "DBA/Trade Name",
        "License Status",
        "License Expiration Date",
    ]

    with open(output_name, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"Saved {len(records)} rows to {output_name}")


def collect_available_options(driver, wait, profession_link_texts):
    options_by_profession = {}
    for link_text in profession_link_texts:
        try:
            click_profession_link(driver, wait, link_text)
            option_texts = [
                option.text.strip()
                for option in get_license_type_select(wait).options
                if option.text.strip() and option.text.strip() != "--Select--"
            ]
            options_by_profession[link_text] = option_texts
            if len(driver.window_handles) > 1:
                driver.close()
                driver.switch_to.window(driver.window_handles[0])
        except TimeoutException:
            print(f"Skipping profession link '{link_text}' because it did not load in time.")
        except Exception as exc:
            print(f"Skipping profession link '{link_text}' due to error: {exc}")
            if len(driver.window_handles) > 1:
                driver.close()
                driver.switch_to.window(driver.window_handles[0])
    return options_by_profession


def resolve_keyword_targets(options_by_profession, keywords):
    targets = {}
    unmatched = []

    for keyword in keywords:
        match = None
        preferred_professions = KEYWORD_PROFESSION_HINTS.get(keyword, [])
        ordered_professions = []

        for profession_name in preferred_professions:
            if profession_name in options_by_profession:
                ordered_professions.append((profession_name, options_by_profession[profession_name]))

        for profession_name, option_texts in options_by_profession.items():
            if profession_name not in preferred_professions:
                ordered_professions.append((profession_name, option_texts))

        for profession_name, option_texts in ordered_professions:
            best_option = find_best_option(keyword, option_texts)
            if best_option:
                match = (profession_name, best_option)
                break

        if match:
            targets[keyword] = match
        else:
            unmatched.append(keyword)

    return targets, unmatched


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)

    try:
        options_by_profession = collect_available_options(driver, wait, PROFESSION_LINK_TEXTS)
        if not options_by_profession:
            raise RuntimeError("No profession pages were loaded successfully.")

        print("Available profession pages loaded:")
        for profession_name, option_texts in options_by_profession.items():
            print(f"  - {profession_name}: {len(option_texts)} license type options")

        targets, unmatched_keywords = resolve_keyword_targets(options_by_profession, KEYWORDS)
        grouped_targets = defaultdict(list)
        for keyword, target in targets.items():
            grouped_targets[target].append(keyword)

        print(f"Matched {len(targets)} keywords. Unmatched keywords: {len(unmatched_keywords)}")
        for keyword in unmatched_keywords:
            print(f"  Unmatched: {keyword}")

        for (profession_name, license_type), keyword_group in grouped_targets.items():
            print(f"Processing {profession_name} -> {license_type} for {len(keyword_group)} keyword(s)")
            click_profession_link(driver, wait, profession_name)
            select_license_type(wait, license_type)
            click_search(driver, wait)
            scraped_records = scrape_current_search(driver, wait)

            for keyword in keyword_group:
                keyword_records = []
                for record in scraped_records:
                    row = dict(record)
                    row["Keyword"] = keyword
                    row["Matched License Type"] = license_type
                    keyword_records.append(row)
                save_csv(keyword, keyword_records)

            if len(driver.window_handles) > 1:
                driver.close()
                driver.switch_to.window(driver.window_handles[0])

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
