import csv
import html
import re
import string
import time
from urllib.parse import urljoin
from typing import Dict, List, Optional, Tuple

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
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


SEARCH_URL = "https://forms.nh.gov/licenseverification/Search.aspx"
OUTPUT_FILE = "new_haphire.csv"
WAIT_SECONDS = 20
PAGE_SLEEP = 1.0
HEADLESS = False
PROFESSIONS = [
    "Dental",
    "Dietitian",
    "Health Facilities",
    "Hearing Care Providers",
    "Massage Therapy",
    "Med Imaging Radiation Therapy",
    "Medical Technicians",
    "Medicine",
    "Mental Health",
    "Naturopathic Examiners",
    "Nurse Agencies",
    "Nursing",
    "Nursing Assistants",
    "Nursing Home Administrators",
    "Optometry",
    "Pharmacy",
    "Psychology",
]

#PROFESSIONS = ["Acupuncture", "Dentist"]
PREFIXES = list(string.ascii_uppercase)

FIELDNAMES = [
    "Search Profession",
    "Search Prefix",
    "Full Name",
    "Profession",
    "License Type",
    "License Number",
    "License Status",
    "Details URL",
]


def normalize(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


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
    time.sleep(PAGE_SLEEP)


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC_presence_of_search_controls)


def EC_presence_of_search_controls(driver):
    try:
        driver.find_element(By.ID, "t_web_lookup__profession_name")
        driver.find_element(By.ID, "t_web_lookup__license_type_name")
        driver.find_element(By.ID, "t_web_lookup__first_name")
        driver.find_element(By.ID, "sch_button")
        return True
    except NoSuchElementException:
        return False


def set_select_value(driver, element_id: str, wanted: str):
    normalized = normalize(wanted).lower()
    return driver.execute_script(
        """
        var select = document.getElementById(arguments[0]);
        var wanted = (arguments[1] || '').trim().toLowerCase();
        if (!select) return false;
        for (var i = 0; i < select.options.length; i++) {
            var opt = select.options[i];
            var text = (opt.text || '').trim().toLowerCase();
            var value = (opt.value || '').trim().toLowerCase();
            if (text === wanted || value === wanted) {
                select.selectedIndex = i;
                return true;
            }
        }
        return false;
        """,
        element_id,
        normalized,
    )


def set_first_name(driver, prefix: str):
    field = driver.find_element(By.ID, "t_web_lookup__first_name")
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", field)
    try:
        field.clear()
    except WebDriverException:
        pass
    driver.execute_script(
        """
        arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """,
        field,
        prefix,
    )


def click_search(driver, wait):
    button = wait.until(lambda d: d.find_element(By.ID, "sch_button"))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    try:
        button.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", button)
    wait_for_page_ready(driver, wait)
    try:
        wait.until(lambda d: d.find_element(By.ID, "datagrid_results"))
    except TimeoutException:
        pass


def clean_cell_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return normalize(text)


def extract_result_rows(driver) -> List[Dict[str, str]]:
    source = driver.page_source
    table_match = re.search(
        r'<table[^>]*id="datagrid_results"[^>]*>(.*?)</table>',
        source,
        flags=re.I | re.S,
    )
    if not table_match:
        return []

    table_html = table_match.group(1)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.I | re.S)
    records: List[Dict[str, str]] = []

    for row_html in rows:
        lower = row_html.lower()
        if "<th" in lower or "colspan" in lower:
            continue

        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.I | re.S)
        if len(cells) < 5:
            continue

        name_cell = cells[0]
        name_match = re.search(r'href="([^"]+)"', name_cell, flags=re.I)
        details_url = urljoin(SEARCH_URL, name_match.group(1).strip()) if name_match else ""

        record = {
            "Full Name": clean_cell_html(cells[0]),
            "Profession": clean_cell_html(cells[1]),
            "License Type": clean_cell_html(cells[2]),
            "License Number": clean_cell_html(cells[3]),
            "License Status": clean_cell_html(cells[4]),
            "Details URL": details_url,
        }

        if not record["Full Name"]:
            continue

        records.append(record)

    return records


def get_first_row_signature(driver) -> Optional[Tuple[str, str, str, str, str]]:
    rows = extract_result_rows(driver)
    if not rows:
        return None
    first = rows[0]
    return (
        first.get("Full Name", ""),
        first.get("Profession", ""),
        first.get("License Type", ""),
        first.get("License Number", ""),
        first.get("License Status", ""),
    )


def record_key(record: Dict[str, str]) -> Tuple[str, str, str, str, str, str]:
    return (
        record.get("Full Name", ""),
        record.get("License Number", ""),
        record.get("Profession", ""),
        record.get("License Type", ""),
        record.get("Search Profession", ""),
        record.get("Search Prefix", ""),
    )


def get_visible_page_numbers(driver) -> List[int]:
    numbers = []
    try:
        anchors = driver.find_elements(By.XPATH, "//table[@id='datagrid_results']//a")
    except WebDriverException:
        return numbers

    for anchor in anchors:
        try:
            text = normalize(anchor.text)
        except WebDriverException:
            continue
        if text.isdigit():
            numbers.append(int(text))
    return sorted(set(numbers))


def click_page_number(driver, wait, page_number: int) -> bool:
    before = get_first_row_signature(driver)
    xpath = f"//table[@id='datagrid_results']//a[normalize-space()='{page_number}']"
    try:
        link = wait.until(lambda d: d.find_element(By.XPATH, xpath))
    except TimeoutException:
        return False

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
    try:
        link.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", link)

    end_time = time.time() + WAIT_SECONDS
    while time.time() < end_time:
        try:
            after = get_first_row_signature(driver)
            if after and after != before:
                return True
        except (StaleElementReferenceException, WebDriverException):
            pass
        time.sleep(0.4)
    return False


def search_current_filters(driver, wait, profession: str, prefix: str) -> List[Dict[str, str]]:
    open_search_page(driver, wait)
    set_select_value(driver, "t_web_lookup__profession_name", profession)
    set_select_value(driver, "t_web_lookup__license_type_name", "All")
    set_first_name(driver, prefix)
    click_search(driver, wait)

    records = []
    seen_pages = set()
    current_page = 1

    while True:
        if current_page not in seen_pages:
            seen_pages.add(current_page)
            for record in extract_result_rows(driver):
                record["Search Profession"] = profession
                record["Search Prefix"] = prefix
                records.append(record)

        visible_pages = get_visible_page_numbers(driver)
        next_pages = [page for page in visible_pages if page > current_page]
        if not next_pages:
            break

        next_page = min(next_pages)
        if next_page in seen_pages:
            break

        moved = click_page_number(driver, wait, next_page)
        if not moved:
            break
        current_page = next_page

    return records


def open_csv_writer(path: str):
    handle = open(path, "w", newline="", encoding="utf-8-sig")
    writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
    writer.writeheader()
    handle.flush()
    return handle, writer


def write_records(writer: csv.DictWriter, records: List[Dict[str, str]]) -> int:
    written = 0
    for record in records:
        writer.writerow({field: record.get(field, "") for field in FIELDNAMES})
        written += 1
    return written


def run():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    seen_keys = set()
    total_written = 0
    csv_handle = None
    csv_writer = None

    try:
        csv_handle, csv_writer = open_csv_writer(OUTPUT_FILE)
        for profession in PROFESSIONS:
            for prefix in PREFIXES:
                print(f"Scraping {profession} / {prefix}")
                try:
                    records = search_current_filters(driver, wait, profession, prefix)
                    unique_records = []
                    for record in records:
                        key = record_key(record)
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        unique_records.append(record)

                    written = write_records(csv_writer, unique_records)
                    total_written += written
                    csv_handle.flush()
                    print(f"Saved {written} new rows for {profession} / {prefix}")
                except Exception as exc:
                    print(f"Failed for {profession} / {prefix}: {exc}")
                    continue
    finally:
        try:
            if csv_handle is not None:
                csv_handle.close()
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass

    print(f"Wrote {total_written} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
