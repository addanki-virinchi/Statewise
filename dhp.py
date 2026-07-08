import csv
import logging
import os
import random
import re
import shutil
import time
import tempfile
from string import ascii_uppercase
from urllib.parse import urljoin

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc
from webdriver_manager.chrome import ChromeDriverManager


BASE_URL = "https://dhp.virginiainteractive.org"
SEARCH_URL = "https://dhp.virginiainteractive.org/Lookup/Index"
URLS_FILE = "dhp_detail_urls.txt"
OUTPUT_FILE = "dhp_details.csv"
LOG_FILE = "dhp.log"

WAIT_SECONDS = 180
PAGE_LOAD_SLEEP = 1.0
RESULT_WAIT_SECONDS = 20
HEADLESS = False
PAGE_LOAD_TIMEOUT = 300
SEARCH_BUTTON_WAIT_SECONDS = 180
CHROME_VERSION_MAIN = 148
CHROME_DRIVER_VERSION = "148.0.7778.179"

# Human-mimic timing ranges (seconds). Kept randomized/jittered rather than
# fixed so request cadence doesn't look robotic.
TYPE_DELAY_RANGE = (0.06, 0.22)
CLICK_SETTLE_RANGE = (0.25, 0.7)
BETWEEN_SEARCH_RANGE = (0.9, 2.4)
BETWEEN_PAGE_RANGE = (0.6, 1.5)
LONG_BREAK_EVERY = 40          # take a longer pause every N searches
LONG_BREAK_RANGE = (5.0, 12.0)

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
    # "Alabama",
    # "Alaska",
    # "Arizona",
    # "Arkansas",
    # "California",
    # "Colorado",
    # "Connecticut",
    # "Delaware",
    # "District of Columbia",
    # "Florida",
    # "Georgia",
    # "Hawaii",
    # "Idaho",
    # "Illinois",
    # "Indiana",
    # "Iowa",
    # "Kansas",
    # "Kentucky",
    # "Louisiana",
    # "Maine",
    # "Maryland",
    # "Massachusetts",
    # "Michigan",
    # "Minnesota",
    # "Mississippi",
    # "Missouri",
    # "Montana",
    # "Nebraska",
    # "Nevada",
    # "New Hampshire",
    # "New Jersey",
    # "New Mexico",
    # "New York",
    # "North Carolina",
    # "North Dakota",
    # "Ohio",
    # "Oklahoma",
    # "Oregon",
    # "Pennsylvania",
    # "Rhode Island",
    # "South Carolina",
    # "South Dakota",
    # "Tennessee",
    # "Texas",
    # "Utah",
    # "Vermont",
    "Virginia",
    # "Washington",
    # "West Virginia",
    # "Wisconsin",
    # "Wyoming",
]

FIRST_NAME_PREFIXES = [
    f"{first}{second}"
    for first in ascii_uppercase
    for second in ascii_uppercase
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


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("dhp_scraper")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


LOGGER = setup_logger()


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip().upper()


def build_driver():
    profile_dir = tempfile.mkdtemp(prefix="dhp_profile_")
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disk-cache-size=0")
    options.add_argument("--media-cache-size=0")
    options.add_argument("--disable-cache")
    options.add_argument(f"--user-data-dir={profile_dir}")
    # options.add_experimental_option("excludeSwitches", ["enable-automation"])
    # options.add_experimental_option("useAutomationExtension", False)
    if HEADLESS:
        options.add_argument("--headless=new")

    driver = uc.Chrome(
        options=options,
        driver_executable_path=ChromeDriverManager(
            driver_version=CHROME_DRIVER_VERSION
        ).install(),
        version_main=CHROME_VERSION_MAIN,
        use_subprocess=True,
    )
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver._dhp_profile_dir = profile_dir
    return driver


def close_driver(driver):
    profile_dir = getattr(driver, "_dhp_profile_dir", None)
    try:
        driver.quit()
    finally:
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)


def human_sleep(min_seconds, max_seconds):
    time.sleep(random.uniform(min_seconds, max_seconds))


def human_mouse_move(driver, element=None):
    """Move the mouse toward an element (with a small random offset) or to a
    random point on the page, so cursor motion isn't a straight teleport."""
    try:
        actions = ActionChains(driver)
        if element is not None:
            actions.move_to_element_with_offset(
                element, random.randint(-4, 4), random.randint(-4, 4)
            )
        else:
            actions.move_by_offset(random.randint(-120, 120), random.randint(-120, 120))
        actions.perform()
    except Exception:
        pass


def human_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    human_sleep(0.2, 0.5)
    human_mouse_move(driver, element)
    human_sleep(0.1, 0.35)
    try:
        element.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", element)
    human_sleep(*CLICK_SETTLE_RANGE)


def human_type(element, text):
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(*TYPE_DELAY_RANGE))


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    human_sleep(PAGE_LOAD_SLEEP, PAGE_LOAD_SLEEP + 0.6)


def open_search_page(driver, wait):
    try:
        driver.get(SEARCH_URL)
    except TimeoutException:
        # The site can take a long time to finish loading. Stop the navigation
        # and continue once the form is reachable in the DOM.
        driver.execute_script("window.stop();")
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.ID, "OccupationId")))


def get_select(driver, element_id):
    return Select(driver.find_element(By.ID, element_id))


def select_option_by_text(driver, element_id, target_text):
    target = normalize(target_text)
    select = get_select(driver, element_id)
    for option in select.options:
        if normalize(option.text) == target and (option.get_attribute("value") or "").strip():
            human_click(driver, option)
            human_sleep(0.3, 0.7)
            return
    select.select_by_visible_text(target_text)
    human_sleep(0.3, 0.7)


def select_occupation(driver, occupation_name):
    select_option_by_text(driver, "OccupationId", occupation_name)


def select_state(driver, state_name):
    select_option_by_text(driver, "State", state_name)


def set_text_input(driver, element_id, value):
    element = WebDriverWait(driver, WAIT_SECONDS).until(
        EC.presence_of_element_located((By.ID, element_id))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    human_mouse_move(driver, element)
    human_sleep(0.15, 0.4)
    try:
        element.click()
    except Exception:
        pass

    try:
        element.clear()
    except Exception:
        pass

    try:
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.DELETE)
        human_type(element, value)
    except Exception:
        driver.execute_script(
            """
            const el = arguments[0];
            const value = arguments[1];
            el.value = value;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            """,
            element,
            value,
        )
    human_sleep(0.25, 0.55)


def set_first_name_prefix(driver, prefix):
    set_text_input(driver, "FName", prefix)


def select_current_licensees(driver):
    select_option_by_text(driver, "LicStatus", "Current Licensees")


def click_search(driver, wait):
    search_xpath = "(//input[@type='submit' and @name='submitBtn' and @value='Search'])[3]"
    try:
        button = WebDriverWait(driver, SEARCH_BUTTON_WAIT_SECONDS).until(
            EC.presence_of_element_located((By.XPATH, search_xpath))
        )
        WebDriverWait(driver, SEARCH_BUTTON_WAIT_SECONDS).until(
            EC.element_to_be_clickable((By.XPATH, search_xpath))
        )
        human_click(driver, button)
        return True
    except TimeoutException:
        LOGGER.warning(
            "Automatic Search click was not reliable on this page load. "
            "Please click Search manually in the browser, then press Enter here."
        )
        try:
            input()
        except EOFError:
            return False
        return True
    except Exception as exc:
        LOGGER.error("Automatic Search click failed: %s", exc)
        LOGGER.warning("Please click Search manually in the browser, then press Enter here.")
        try:
            input()
        except EOFError:
            return False
        return True


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

    human_click(driver, next_button)

    if anchor is not None:
        try:
            wait.until(EC.staleness_of(anchor))
        except TimeoutException:
            pass
    human_sleep(*BETWEEN_PAGE_RANGE)
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


def collect_urls_by_keyword():
    all_urls = []
    seen = set()
    keyword_batches = []
    search_count = 0

    for occupation in KEYWORDS:
        driver = build_driver()
        wait = WebDriverWait(driver, WAIT_SECONDS)
        keyword_urls = []
        try:
            for state_name in STATES:
                for prefix in FIRST_NAME_PREFIXES:
                    try:
                        open_search_page(driver, wait)
                        human_mouse_move(driver)
                        select_occupation(driver, occupation)
                        set_first_name_prefix(driver, prefix)
                        select_state(driver, state_name)
                        select_current_licensees(driver)
                        if not click_search(driver, wait):
                            LOGGER.warning(
                                "%s | %s | %s: skipped because Search could not be submitted",
                                occupation,
                                state_name,
                                prefix,
                            )
                            continue

                        if not wait_for_results_or_empty(driver):
                            LOGGER.info("%s | %s | %s: 0 urls", occupation, state_name, prefix)
                            write_lines(URLS_FILE, all_urls)
                            continue

                        page_urls = scrape_all_urls(driver, wait)
                        new_urls = 0
                        for url in page_urls:
                            if url not in seen:
                                seen.add(url)
                                all_urls.append(url)
                                keyword_urls.append(url)
                                new_urls += 1

                        # Persist after every single search so progress
                        # survives a crash/restart instead of only being
                        # written once the whole occupation/state finishes.
                        write_lines(URLS_FILE, all_urls)
                        LOGGER.info(
                            "%s | %s | %s: %s new urls (%s total saved to %s)",
                            occupation,
                            state_name,
                            prefix,
                            new_urls,
                            len(all_urls),
                            URLS_FILE,
                        )
                    except Exception as exc:
                        LOGGER.error(
                            "ERROR on %s / %s / %s: %s",
                            occupation,
                            state_name,
                            prefix,
                            exc,
                        )
                        write_lines(URLS_FILE, all_urls)
                    finally:
                        search_count += 1
                        if search_count % LONG_BREAK_EVERY == 0:
                            human_sleep(*LONG_BREAK_RANGE)
                        else:
                            human_sleep(*BETWEEN_SEARCH_RANGE)
        finally:
            close_driver(driver)

        LOGGER.info(
            "%s: finished, %s unique urls saved so far in %s",
            occupation,
            len(all_urls),
            URLS_FILE,
        )
        if keyword_urls:
            keyword_batches.append((occupation, keyword_urls))

    return keyword_batches


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
                LOGGER.info("%s/%s scraped: %s", index, len(urls), url)
            except Exception as exc:
                LOGGER.error("ERROR on detail page %s: %s", url, exc)
            finally:
                if index % LONG_BREAK_EVERY == 0:
                    human_sleep(*LONG_BREAK_RANGE)
                else:
                    human_sleep(*BETWEEN_SEARCH_RANGE)
    finally:
        close_driver(driver)
    return records


def write_csv(path, records, append=False):
    file_exists = os.path.exists(path)
    mode = "a" if append else "w"
    with open(path, mode, newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        if not append or not file_exists:
            writer.writeheader()
        writer.writerows(records)


def initialize_csv(path):
    write_csv(path, [], append=False)


def main():
    collect_urls_by_keyword()
    LOGGER.info("Phase 1 complete. URL list written to %s", URLS_FILE)


if __name__ == "__main__":
    main()
