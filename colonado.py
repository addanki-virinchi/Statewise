import csv
import time

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

URL = "https://apps2.colorado.gov/dora/licensing/Lookup/LicenseLookup.aspx"
WAIT_SECONDS = 30
OUTPUT_CSV = "Colorado_results.csv"

LICENSE_TYPE_ID = "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_lbMultipleCredentialTypePrefix"
STATE_ID = "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_ddStates"
RESULTS_TABLE_ID = "ctl00_MainContentPlaceHolder_ucLicenseLookup_gvSearchResults"

KEYWORDS = [
    #"Audiologists",
    "Dental",
    "Hearing Aid Providers",
    "Marriage and Family Therapists",
    "Medical",
    "Natural Medicine",
    "Nursing",
    "Nursing Home Administrators",
    "Occupational Therapy",
    "Pharmacy",
    "Physical Therapy",
    "Psychologists",
    "Speech-Language Pathology",
    "Surgical Assistant and Surgical Technologist",
]

FIELDNAMES = [
    "Keyword",
    "State",
    "Name",
    "License Number",
    "License Status",
    "Contact Type",
    "City",
    "Result State",
    "Zip Code",
]


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def select_keyword(driver, keyword):
    select = Select(driver.find_element(By.ID, LICENSE_TYPE_ID))
    select.deselect_all()
    matched = False
    for option in select.options:
        if keyword.lower() in option.text.strip().lower():
            select.select_by_visible_text(option.text.strip())
            matched = True
    return matched


# def state_options(driver):
#     select = Select(driver.find_element(By.ID, STATE_ID))
#     return [(o.get_attribute("value"), o.text.strip()) for o in select.options if o.get_attribute("value")]
def state_options(driver):
    return [
        # ("AL", "Alabama"),
        # ("AK", "Alaska"),
        # ("AB", "Alberta"),
        # ("AS", "American Samoa"),
        # ("AZ", "Arizona"),
        # ("AR", "Arkansas"),
        # ("AA", "Armed Forces America"),
        # ("AE", "Armed Forces Over Seas"),
        # ("AP", "Armed Forces Pacific"),
        # ("BC", "British Columbia"),
        # ("CG", "Cairo Governorate"),
        # ("CA", "California"),
        ("CU", "Cancun"),
        ("CO", "Colorado"),
        ("CT", "Connecticut"),
        ("DE", "Delaware"),
        ("DC", "District of Columbia"),#---
        ("EM", "East Midlands"),
        ("EE", "East of England"),
        ("FL", "Florida"),
        ("FC", "Foreign Country"),
        ("GA", "Georgia"),
        ("GU", "Guam"),
        ("HI", "Hawaii"),
        ("ID", "Idaho"),
        ("IL", "Illinois"),
        ("IN", "Indiana"),
        ("IA", "Iowa"),
        ("KS", "Kansas"),
        ("KY", "Kentucky"),
        ("LO", "London"),
        ("LA", "Louisiana"),
        ("ME", "Maine"),
        ("MB", "Manitoba"),
        ("MD", "Maryland"),
        ("MA", "Massachusetts"),
        ("MX", "Mexico"),
        ("MC", "Mexico City"),
        ("MI", "Michigan"),
        ("MN", "Minnesota"),
        ("MS", "Mississippi"),
        ("MO", "Missouri"),
        ("MT", "Montana"),
        ("NE", "Nebraska"),
        ("NV", "Nevada"),
        ("NB", "New Brunswick"),
        ("NH", "New Hampshire"),
        ("NJ", "New Jersey"),
        ("NM", "New Mexico"),
        ("NY", "New York"),
        ("NF", "Newfoundland"),
        ("NC", "North Carolina"),
        ("ND", "North Dakota"),
        ("NO", "North East"),
        ("NW", "North West"),
        ("MP", "Northern Mariana Island"),
        ("NT", "Northwest Territories"),
        ("NS", "Nova Scotia"),
        ("NN", "Nunavut"),
        ("OH", "Ohio"),
        ("OK", "Oklahoma"),
        ("ON", "Ontario"),
        ("OR", "Oregon"),
        ("OT", "Ottawa"),
        ("PA", "Pennsylvania"),
        ("PE", "Prince Edward Island"),
        ("PR", "Puerto Rico"),
        ("PQ", "Quebec"),
        ("RI", "Rhode Island"),
        ("SK", "Saskatchewan"),
        ("SC", "South Carolina"),
        ("SD", "South Dakota"),
        ("SE", "South East"),
        ("SW", "South West"),
        ("TN", "Tennessee"),
        ("TX", "Texas"),
        ("TR", "U.S. Territory"),
        ("UK", "Unknown"),
        ("UT", "Utah"),
        ("VT", "Vermont"),
        ("VI", "Virgin Islands"),
        ("VA", "Virginia"),
        ("WA", "Washington"),
        ("WM", "West Midlands"),
        ("WV", "West Virginia"),
        ("WI", "Wisconsin"),
        ("WY", "Wyoming"),
        ("YH", "Yorkshire and the Humber"),
        ("YT", "Yukon"),
    ]

def select_state(driver, value):
    Select(driver.find_element(By.ID, STATE_ID)).select_by_value(value)


def parse_current_page(driver):
    records = []
    row_xpath = f"//table[@id='{RESULTS_TABLE_ID}']/tbody/tr[td//a[contains(@href,'DisplayLicenceDetail')]]"
    rows = driver.find_elements(By.XPATH, row_xpath)
    for row in rows:
        try:
            cells = [c.text.strip() for c in row.find_elements(By.TAG_NAME, "td")]
        except StaleElementReferenceException:
            continue
        if len(cells) < 8:
            continue
        records.append({
            "Name": cells[1],
            "License Number": cells[2],
            "License Status": cells[3],
            "Contact Type": cells[4],
            "City": cells[5],
            "Result State": cells[6],
            "Zip Code": cells[7],
        })
    return records


def scrape_results(driver, wait):
    records = []
    page = 1
    while True:
        records.extend(parse_current_page(driver))
        next_num = page + 1
        next_xpath = (
            f"//table[@id='{RESULTS_TABLE_ID}']//ul[contains(@class,'pagination')]"
            f"/li/a[normalize-space()='{next_num}']"
        )
        try:
            link = driver.find_element(By.XPATH, next_xpath)
        except NoSuchElementException:
            break
        first_row = driver.find_element(
            By.XPATH, f"//table[@id='{RESULTS_TABLE_ID}']/tbody/tr[1]"
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        link.click()
        try:
            wait.until(EC.staleness_of(first_row))
        except Exception:
            time.sleep(1.5)
        page += 1
    return records


def close_modal(driver):
    try:
        driver.execute_script(
            "$('.bs-example-modal-lg').hide().remove();"
            "if(window.reCaptchaReloadOnModalClose){reCaptchaReloadOnModalClose();}"
        )
    except Exception:
        pass


def save_rows(rows):
    write_header = False
    try:
        with open(OUTPUT_CSV, "r", encoding="utf-8-sig"):
            pass
    except FileNotFoundError:
        write_header = True

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    driver.get(URL)

    input("Complete the captcha in the browser, then press Enter to start...")

    states = state_options(driver)

    try:
        for keyword in KEYWORDS:
            for value, state_name in states:
                if not select_keyword(driver, keyword):
                    print(f"No license type option matched '{keyword}', skipping.")
                    break
                select_state(driver, value)
                input(
                    f"[{keyword} | {state_name}] Solve captcha if shown, click Search, "
                    "then press Enter here..."
                )

                try:
                    wait.until(EC.presence_of_element_located((By.ID, RESULTS_TABLE_ID)))
                except Exception:
                    print(f"  No results table for {keyword} | {state_name}.")
                    close_modal(driver)
                    continue

                records = scrape_results(driver, wait)
                for record in records:
                    record["Keyword"] = keyword
                    record["State"] = state_name
                save_rows(records)
                print(f"  Saved {len(records)} rows for {keyword} | {state_name}.")

                close_modal(driver)
                time.sleep(1)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
