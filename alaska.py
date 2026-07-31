import csv
import atexit
import json
import random
import shutil
import time
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import undetected_chromedriver as uc
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait


SEARCH_URL = "https://www.commerce.alaska.gov/cbp/main/Search/Professional"
OUTPUT_FILE = "alaska.csv"
CHROME_VERSION_MAIN = 149
PROXY_FILE = Path(__file__).resolve().with_name("Webshare 10 proxies.txt")

WAIT_SECONDS = 45
PAGE_LOAD_SLEEP = 1.0
HEADLESS = False
USE_PROXIES = True

_PROXY_EXTENSION_DIRS: List[Path] = []

PROGRAM_KEYWORDS = ["Audiologists and Hearing Aid Dealers","Behavior Analysts", "Dental","Dental Radiological Equipment","Dental Radiological Inspector","Medical","Nurse Aides","Nursing","Nursing Home Administrators","Optometry","Pharmacy","Physical and Occupational Therapy","Physician Assistants","Psychology","Speech-Language Pathology"]

FIELDNAMES = [
    "Program",
    "License Number",
    "DBA",
    "Owners",
    "Status",
    "License Expiration",
]


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def normalize_key(text: str) -> str:
    return normalize_text(text).lower()


def _cleanup_proxy_extensions():
    for path in _PROXY_EXTENSION_DIRS:
        shutil.rmtree(path, ignore_errors=True)


atexit.register(_cleanup_proxy_extensions)


def load_proxy_pool() -> List[Dict[str, str]]:
    if not PROXY_FILE.exists():
        return []

    proxies = []
    with PROXY_FILE.open("r", encoding="utf-8-sig") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = normalize_text(raw_line)
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split(":")]
            if len(parts) != 4:
                raise ValueError(
                    f"Invalid proxy entry on line {line_number} in {PROXY_FILE.name}: {raw_line.strip()!r}"
                )

            host, port, username, password = parts
            proxies.append(
                {
                    "host": host,
                    "port": port,
                    "username": username,
                    "password": password,
                }
            )

    return proxies


def build_proxy_extension(proxy: Dict[str, str]) -> str:
    extension_dir = Path(tempfile.mkdtemp(prefix="webshare_proxy_"))
    manifest = {
        "manifest_version": 3,
        "name": "Webshare Auth Proxy",
        "version": "1.0.0",
        "permissions": ["webRequest", "webRequestAuthProvider"],
        "host_permissions": ["<all_urls>"],
        "background": {"service_worker": "background.js"},
    }
    background_js = f"""
const proxyCredentials = {{
  username: {json.dumps(proxy["username"])},
  password: {json.dumps(proxy["password"])},
}};

chrome.webRequest.onAuthRequired.addListener(
  (details, callback) => {{
    callback({{authCredentials: proxyCredentials}});
  }},
  {{urls: ["<all_urls>"]}},
  ["asyncBlocking"]
);
""".strip()

    (extension_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    (extension_dir / "background.js").write_text(background_js, encoding="utf-8")

    _PROXY_EXTENSION_DIRS.append(extension_dir)
    return str(extension_dir)


def build_driver():
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")

    if USE_PROXIES:
        proxies = load_proxy_pool()
        if not proxies:
            raise RuntimeError(f"No valid proxies found in {PROXY_FILE}")

        proxy = random.choice(proxies)
        proxy_url = f"http://{proxy['host']}:{proxy['port']}"
        extension_dir = build_proxy_extension(proxy)
        options.add_argument(f"--proxy-server={proxy_url}")
        options.add_argument(f"--disable-extensions-except={extension_dir}")
        options.add_argument(f"--load-extension={extension_dir}")
        print(f"Using proxy {proxy['host']}:{proxy['port']}")

    if HEADLESS:
        options.add_argument("--headless=new")

    return uc.Chrome(
        options=options,
        use_subprocess=True,
        version_main=CHROME_VERSION_MAIN,
    )


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "ProgramId")))
    wait.until(EC.presence_of_element_located((By.ID, "LicenseTypeId")))


def get_program_select(driver, wait):
    return Select(wait.until(EC.presence_of_element_located((By.ID, "ProgramId"))))


def get_license_type_select(driver, wait):
    return Select(wait.until(EC.presence_of_element_located((By.ID, "LicenseTypeId"))))


def find_best_option(keyword: str, option_texts: Sequence[str]) -> Optional[str]:
    target = normalize_key(keyword)
    exact_match = None
    contains_matches = []

    for option_text in option_texts:
        normalized = normalize_key(option_text)
        if normalized == target:
            exact_match = option_text
            break
        if target in normalized or normalized in target:
            contains_matches.append(option_text)

    if exact_match:
        return exact_match
    if contains_matches:
        return sorted(contains_matches, key=len)[0]

    tokens = set(target.split())
    scored = []
    for option_text in option_texts:
        option_tokens = set(normalize_key(option_text).split())
        overlap = len(tokens & option_tokens)
        if overlap:
            scored.append((overlap, len(option_tokens), option_text))

    if scored:
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return scored[0][2]

    return None


def select_option_by_text_or_value(select: Select, target: str):
    normalized_target = normalize_key(target)

    for option in select.options:
        value = normalize_text(option.get_attribute("value") or "")
        text = normalize_text(option.text)
        if normalize_key(value) == normalized_target or normalize_key(text) == normalized_target:
            option.click()
            return

    try:
        select.select_by_visible_text(target)
        return
    except NoSuchElementException:
        pass

    raise NoSuchElementException(f"Could not find option '{target}'")


def wait_for_license_types_to_load(driver, wait):
    wait.until(
        lambda d: len(
            [
                option
                for option in Select(d.find_element(By.ID, "LicenseTypeId")).options
                if normalize_text(option.get_attribute("value") or "") not in {"", "0"}
                and normalize_text(option.text)
                and "(not selected)" not in normalize_key(option.text)
            ]
        )
        > 0
    )


def get_available_programs(driver, wait) -> List[Dict[str, str]]:
    program_select = get_program_select(driver, wait)
    programs = []

    for option in program_select.options:
        value = normalize_text(option.get_attribute("value") or "")
        text = normalize_text(option.text)
        if not value or value == "0" or not text or "(not selected)" in normalize_key(text):
            continue
        programs.append({"value": value, "text": text})

    return programs


def resolve_program_targets(driver, wait) -> List[Dict[str, str]]:
    programs = get_available_programs(driver, wait)
    available_texts = [item["text"] for item in programs]
    targets = []

    for keyword in PROGRAM_KEYWORDS:
        best_text = find_best_option(keyword, available_texts)
        if not best_text:
            continue
        for program in programs:
            if normalize_key(program["text"]) == normalize_key(best_text):
                targets.append({"keyword": keyword, "value": program["value"], "text": program["text"]})
                break

    return targets


def select_program(driver, wait, program_value: str):
    program_select = get_program_select(driver, wait)
    select_option_by_text_or_value(program_select, program_value)
    time.sleep(0.75)
    wait_for_license_types_to_load(driver, wait)


def collect_license_type_options(driver, wait) -> List[Dict[str, str]]:
    license_select = get_license_type_select(driver, wait)
    options = []

    for option in license_select.options:
        value = normalize_text(option.get_attribute("value") or "")
        text = normalize_text(option.text)
        if not value or value == "0" or not text or "(not selected)" in normalize_key(text):
            continue
        options.append({"value": value, "text": text})

    return options


def click_search(driver, wait):
    search_button = wait.until(EC.element_to_be_clickable((By.ID, "search")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_button)
    try:
        search_button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", search_button)
    time.sleep(PAGE_LOAD_SLEEP)


def page_has_empty_results_message(driver):
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except NoSuchElementException:
        return False

    text = normalize_key(body_text)
    empty_markers = [
        "no records",
        "no record found",
        "no results",
        "no data",
        "no matching",
    ]
    return any(marker in text for marker in empty_markers)


def get_result_table(driver):
    try:
        return driver.find_element(By.CSS_SELECTOR, "table.deptGridView")
    except NoSuchElementException:
        return None


def wait_for_results_or_empty(driver, wait):
    try:
        wait.until(lambda d: get_result_table(d) or page_has_empty_results_message(d))
    except TimeoutException:
        return False
    return bool(get_result_table(driver))


def get_result_rows(driver):
    table = get_result_table(driver)
    if not table:
        return []

    rows = []
    for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) >= 6:
            rows.append(row)
    return rows


def parse_row(row):
    cells = [normalize_text(cell.text) for cell in row.find_elements(By.TAG_NAME, "td")]
    if len(cells) < 6:
        return None

    return {
        "Program": cells[0],
        "License Number": cells[1],
        "DBA": cells[2],
        "Owners": cells[3],
        "Status": cells[4],
        "License Expiration": cells[5],
    }


def parse_current_page(driver):
    records = []
    for row in get_result_rows(driver):
        record = parse_row(row)
        if record:
            records.append(record)
    return records


def get_current_page_number(driver) -> Optional[str]:
    try:
        select = Select(driver.find_element(By.CSS_SELECTOR, "select[aria-label='Current Page Number']"))
        return normalize_text(select.first_selected_option.get_attribute("value") or select.first_selected_option.text)
    except NoSuchElementException:
        return None


def get_current_page_select(driver):
    try:
        return Select(driver.find_element(By.CSS_SELECTOR, "select[aria-label='Current Page Number']"))
    except NoSuchElementException:
        return None


def get_page_numbers(driver) -> List[str]:
    select = get_current_page_select(driver)
    if not select:
        return ["1"]

    page_numbers = []
    for option in select.options:
        value = normalize_text(option.get_attribute("value") or "")
        text = normalize_text(option.text)
        candidate = value or text
        if not candidate:
            continue
        if candidate.isdigit():
            page_numbers.append(candidate)

    # Preserve the page order from the dropdown while removing duplicates.
    seen = set()
    ordered_pages = []
    for page in page_numbers:
        if page in seen:
            continue
        seen.add(page)
        ordered_pages.append(page)
    return ordered_pages or ["1"]


def get_first_row_signature(driver):
    rows = get_result_rows(driver)
    if not rows:
        return None
    first_cells = [normalize_text(cell.text) for cell in rows[0].find_elements(By.TAG_NAME, "td")]
    return tuple(first_cells[:6])


def select_page_number(driver, wait, page_number: str) -> bool:
    current_page = get_current_page_number(driver)
    if current_page == page_number:
        return True

    select_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[aria-label='Current Page Number']")))

    try:
        Select(select_el).select_by_value(page_number)
    except NoSuchElementException:
        return False
    except StaleElementReferenceException:
        select_el = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[aria-label='Current Page Number']")))
        Select(select_el).select_by_value(page_number)

    try:
        wait.until(lambda d: get_current_page_number(d) == page_number and bool(get_result_rows(d)))
    except TimeoutException:
        return False

    time.sleep(PAGE_LOAD_SLEEP)
    return get_current_page_number(driver) == page_number


def scrape_all_pages(driver, wait):
    records = []
    seen = set()
    page_numbers = get_page_numbers(driver)

    for index, page_number in enumerate(page_numbers):
        if index > 0:
            if not select_page_number(driver, wait, page_number):
                break

        page_records = parse_current_page(driver)
        for record in page_records:
            key = (
                record.get("Program", ""),
                record.get("License Number", ""),
                record.get("DBA", ""),
                record.get("Owners", ""),
                record.get("Status", ""),
                record.get("License Expiration", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(record)

    return records


def append_csv(path: str, records: Sequence[dict], write_header: bool = False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def run_search(driver, wait, program_value: str, license_type_value: str):
    open_search_page(driver, wait)
    select_program(driver, wait, program_value)
    select_option_by_text_or_value(get_license_type_select(driver, wait), license_type_value)
    time.sleep(0.5)
    click_search(driver, wait)

    if not wait_for_results_or_empty(driver, wait):
        return []

    return scrape_all_pages(driver, wait)


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    total_rows = 0
    header_written = False

    try:
        with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig"):
            pass

        open_search_page(driver, wait)
        program_targets = resolve_program_targets(driver, wait)

        if not program_targets:
            raise RuntimeError("No matching programs found for the configured keywords.")

        for program in program_targets:
            print(f"Program selected: {program['text']} (keyword: {program['keyword']})")

            open_search_page(driver, wait)
            select_program(driver, wait, program["value"])
            license_types = collect_license_type_options(driver, wait)

            if not license_types:
                print(f"  No license types found for {program['text']}")
                continue

            for license_type in license_types:
                try:
                    records = run_search(driver, wait, program["value"], license_type["value"])
                except Exception as exc:
                    print(
                        f"  {program['text']} | {license_type['text']}: ERROR {exc!r}"
                    )
                    continue

                if records:
                    append_csv(OUTPUT_FILE, records, write_header=not header_written)
                    header_written = True
                    total_rows += len(records)

                print(
                    f"  {program['text']} | {license_type['text']}: "
                    f"{len(records)} rows (total {total_rows})"
                )

    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
