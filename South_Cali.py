import csv
import os
import re
import threading
import time
from pathlib import Path

import pandas as pd
import nopecha_1
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


BASE_DIR = Path(__file__).resolve().parent
SEARCH_URL = "https://verify.llronline.com/LicLookup/LookupMain.aspx"
OUTPUT_FILE = BASE_DIR / "South_Cali.csv"
WAIT_SECONDS = 30
PAGE_LOAD_SLEEP = 0.5
RECAPTCHA_SOLVE_TIMEOUT = 120
# A reCAPTCHA v2 token is single-use and expires after ~2 minutes. Keep our own
# shelf life a bit shorter so a prefetched token is never handed over stale.
RECAPTCHA_TOKEN_TTL = 100
HEADLESS = False

KEYWORDS = [
    #"Chiropractic",
    "Dentistry",
    "Dietetics",
    #"Genetic Counselors",
    "Massage Therapy",
    "Medical Board",
    "Nursing",
    "Occupational Therapy",
    "Opticians",
    "Optometry",
    "Pharmacy",
    "Physical Therapy",
    #"Podiatry",
    #"Professional Counselors",
    "Psychology",
    #"Social Worker",
    "Speech-Language Pathology and Audiology",
]
CITY_SOURCE_FILES = [
    "zip_codes_sc.csv"
]

FIELDNAMES = [
    "Keyword",
    "City Searched",
    "Lic #",
    "Last",
    "First",
    "Middle",
    "City",
    "State",
    "Type",
]


def normalize(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def build_driver():
    options = ChromeOptions()
    options.page_load_strategy = "eager"
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
    wait.until(lambda d: d.execute_script("return document.readyState") != "loading")
    time.sleep(PAGE_LOAD_SLEEP)


def load_zip_codes_sc():
    for filename in CITY_SOURCE_FILES:
        path = BASE_DIR / filename
        if not path.exists():
            continue

        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        if "City" not in df.columns:
            continue

        df = df.copy()
        df["City"] = df["City"].fillna("").astype(str).map(normalize)
        df = df[df["City"] != ""]
        if not df.empty:
            return df.drop_duplicates(subset=["City"]).reset_index(drop=True)

    raise FileNotFoundError(
        "Could not find a city source CSV with a 'City' column. "
        f"Looked for: {', '.join(CITY_SOURCE_FILES)}"
    )


zip_codes_sc = load_zip_codes_sc()


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder1_DropDownList1")))


def safe_click(driver, wait, by, value, retries=4):
    last_error = None
    for _ in range(retries):
        try:
            element = wait.until(EC.element_to_be_clickable((by, value)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            try:
                element.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", element)
            return element
        except (TimeoutException, StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error
    raise RuntimeError(f"Could not click element: {by}={value}")


def select_keyword(driver, wait, keyword):
    select_el = wait.until(
        EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder1_DropDownList1"))
    )
    select = Select(select_el)
    select.select_by_visible_text(keyword)


def click_select_button(driver, wait):
    safe_click(driver, wait, By.ID, "ctl00_ContentPlaceHolder1_btn_select")
    wait.until(EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder1_UserInputGen_txt_city")))


def set_city(driver, wait, city):
    city_input = wait.until(
        EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder1_UserInputGen_txt_city"))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", city_input)
    try:
        city_input.clear()
    except WebDriverException:
        pass

    driver.execute_script(
        """
        arguments[0].value = arguments[1];
        arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
        arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
        """,
        city_input,
        city,
    )


def click_find(driver, wait):
    safe_click(driver, wait, By.XPATH, "//button[normalize-space()='Find']")
    time.sleep(0.5)
    ensure_recaptcha_solved(driver, wait)


def recaptcha_widget_present(driver):
    try:
        return bool(
            driver.execute_script(
                """
                return !!document.getElementById('g-recaptcha-response') ||
                       !!document.querySelector('iframe[src*="recaptcha"]') ||
                       !!document.querySelector('[data-sitekey]');
                """
            )
        )
    except WebDriverException:
        return False


def recaptcha_token_present(driver):
    try:
        return bool(
            driver.execute_script(
                """
                var t = document.getElementById('g-recaptcha-response') ||
                        document.querySelector('textarea[name="g-recaptcha-response"]');
                return !!(t && t.value && t.value.length > 0);
                """
            )
        )
    except WebDriverException:
        return False


def recaptcha_sitekey(driver):
    try:
        return normalize(
            driver.execute_script(
                """
                var el = document.querySelector('[data-sitekey]');
                if (el && el.getAttribute('data-sitekey')) {
                    return el.getAttribute('data-sitekey');
                }
                var frames = Array.from(document.querySelectorAll('iframe[src*="recaptcha"]'));
                for (var frame of frames) {
                    try {
                        var url = new URL(frame.src, window.location.href);
                        var key = url.searchParams.get('k');
                        if (key) {
                            return key;
                        }
                    } catch (err) {}
                }
                return '';
                """
            )
        )
    except WebDriverException:
        return ""


def inject_recaptcha_token(driver, token):
    try:
        return bool(
            driver.execute_script(
                """
                var token = arguments[0];
                var el = document.getElementById('g-recaptcha-response') ||
                         document.querySelector('textarea[name="g-recaptcha-response"]');
                if (!el) {
                    return false;
                }
                el.value = token;
                el.innerHTML = token;
                el.textContent = token;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
                """,
                token,
            )
        )
    except WebDriverException:
        return False


def click_recaptcha_checkbox(driver):
    try:
        anchor = driver.find_element(
            By.CSS_SELECTOR, "iframe[src*='/recaptcha/'][src*='anchor']"
        )
    except NoSuchElementException:
        try:
            anchor = driver.find_element(By.CSS_SELECTOR, "iframe[src*='recaptcha']")
        except NoSuchElementException:
            return False

    try:
        driver.switch_to.frame(anchor)
        box = driver.find_element(By.ID, "recaptcha-anchor")
        if box.get_attribute("aria-checked") != "true":
            box.click()
        return True
    except (NoSuchElementException, WebDriverException):
        return False
    finally:
        driver.switch_to.default_content()


def _solve_recaptcha_token(driver):
    sitekey = recaptcha_sitekey(driver)
    if not sitekey:
        return False

    try:
        token = nopecha_1.solve_recaptcha_v2(
            sitekey,
            driver.current_url,
            timeout=RECAPTCHA_SOLVE_TIMEOUT,
        )
    except nopecha_1.NopechaError as exc:
        print(f"[captcha] recaptcha token solve failed: {exc}")
        return False

    if not token:
        return False

    if not inject_recaptcha_token(driver, token):
        return False

    time.sleep(0.5)
    return recaptcha_token_present(driver)


def ensure_recaptcha_solved(driver, wait):
    if recaptcha_token_present(driver):
        return True

    widget_deadline = time.time() + 5
    while time.time() < widget_deadline:
        if recaptcha_widget_present(driver):
            break
        time.sleep(0.25)

    if not recaptcha_widget_present(driver):
        return True

    if _solve_recaptcha_token(driver) and recaptcha_token_present(driver):
        return True

    click_recaptcha_checkbox(driver)

    deadline = time.time() + RECAPTCHA_SOLVE_TIMEOUT
    while time.time() < deadline:
        if recaptcha_token_present(driver):
            return True
        time.sleep(0.25)

    print("[captcha] reCAPTCHA not solved within timeout")
    return False


def get_result_rows(driver):
    try:
        table = driver.find_element(By.ID, "ctl00_ContentPlaceHolder2_gv_results")
    except NoSuchElementException:
        return []

    rows = table.find_elements(By.XPATH, "./tbody/tr")
    valid_rows = []
    for row in rows:
        try:
            cells = row.find_elements(By.XPATH, "./td")
            if len(cells) == 7:
                valid_rows.append(row)
        except StaleElementReferenceException:
            continue
    return valid_rows


def parse_row(row):
    cells = row.find_elements(By.XPATH, "./td")
    return {
        "Lic #": normalize(cells[0].text),
        "Last": normalize(cells[1].text),
        "First": normalize(cells[2].text),
        "Middle": normalize(cells[3].text),
        "City": normalize(cells[4].text),
        "State": normalize(cells[5].text),
        "Type": normalize(cells[6].text),
    }


def get_first_row_signature(driver):
    rows = get_result_rows(driver)
    if not rows:
        return None
    first = rows[0].find_elements(By.XPATH, "./td")
    if len(first) < 7:
        return None
    return tuple(normalize(cell.text) for cell in first[:7])


def wait_for_results(driver, wait):
    def results_ready(current_driver):
        try:
            if get_result_rows(current_driver):
                return True
            page_text = current_driver.page_source.lower()
            return "no records" in page_text or "no results" in page_text
        except WebDriverException:
            return False

    wait.until(results_ready)


def wait_for_page_change(driver, wait, before_signature):
    def changed(current_driver):
        try:
            after = get_first_row_signature(current_driver)
            return after is not None and after != before_signature
        except WebDriverException:
            return False

    wait.until(changed)


def go_to_page(driver, wait, page_number):
    before_signature = get_first_row_signature(driver)
    if before_signature is None:
        return False

    clicked = driver.execute_script(
        """
        function norm(text) { return (text || '').replace(/\\s+/g, ' ').trim(); }
        var target = String(arguments[0]);
        var links = Array.from(document.querySelectorAll('#ctl00_ContentPlaceHolder2_gv_results a'));
        for (var link of links) {
            var text = norm(link.textContent);
            var href = link.getAttribute('href') || '';
            if (text === target || href.indexOf('Page$' + target) >= 0) {
                link.scrollIntoView({block: 'center'});
                link.click();
                return true;
            }
        }
        return false;
        """,
        page_number,
    )

    if not clicked:
        return False

    try:
        wait_for_page_change(driver, wait, before_signature)
    except TimeoutException:
        pass
    time.sleep(0.2)
    return get_first_row_signature(driver) not in (None, before_signature)


def scrape_current_page(driver):
    records = []
    for row in get_result_rows(driver):
        try:
            records.append(parse_row(row))
        except StaleElementReferenceException:
            continue
    return records


def scrape_all_pages(driver, wait):
    records = []
    seen = set()
    page_number = 1
    last_signature = None

    while True:
        signature = get_first_row_signature(driver)
        if signature is not None and signature == last_signature:
            break
        last_signature = signature

        for record in scrape_current_page(driver):
            key = (
                record["Lic #"],
                record["Last"],
                record["First"],
                record["Middle"],
                record["City"],
                record["State"],
                record["Type"],
            )
            if key in seen:
                continue
            seen.add(key)
            records.append(record)

        next_page = page_number + 1
        if not go_to_page(driver, wait, next_page):
            break
        page_number = next_page

    return records


def append_csv(path, records, write_header=False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def run():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.25)
    header_written = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
    total_rows = 0

    try:
        open_search_page(driver, wait)

        for keyword in KEYWORDS:
            try:
                select_keyword(driver, wait, keyword)
                click_select_button(driver, wait)
            except Exception as exc:
                print(f"ERROR selecting keyword '{keyword}': {exc}")
                continue

            for city in zip_codes_sc["City"].tolist():
                try:
                    set_city(driver, wait, city)
                    click_find(driver, wait)
                    wait_for_results(driver, wait)
                    records = scrape_all_pages(driver, wait)
                except Exception as exc:
                    print(f"ERROR on {keyword} / {city}: {exc}")
                    records = []

                if records:
                    output_rows = []
                    for record in records:
                        row = dict(record)
                        row["Keyword"] = keyword
                        row["City Searched"] = city
                        output_rows.append(row)

                    append_csv(OUTPUT_FILE, output_rows, write_header=not header_written)
                    header_written = True
                    total_rows += len(output_rows)

                print(f"{keyword} | {city}: {len(records)} rows (total {total_rows})")

    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
