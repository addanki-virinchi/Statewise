import csv
import logging
import os
import re
import shutil
import tempfile
import time

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import undetected_chromedriver as uc
from webdriver_manager.chrome import ChromeDriverManager


URLS_FILE = "dhp_detail_urls.txt"
OUTPUT_FILE = "dhp_details.csv"
LOG_FILE = "dhp2.log"

WAIT_SECONDS = 180
PAGE_LOAD_SLEEP = 1.0
HEADLESS = False
PAGE_LOAD_TIMEOUT = 300
CHROME_VERSION_MAIN = 148
CHROME_DRIVER_VERSION = "148.0.7778.179"

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
    logger = logging.getLogger("dhp2_scraper")
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
    profile_dir = tempfile.mkdtemp(prefix="dhp2_profile_")
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disk-cache-size=0")
    options.add_argument("--media-cache-size=0")
    options.add_argument("--disable-cache")
    options.add_argument(f"--user-data-dir={profile_dir}")
    #options.add_experimental_option("excludeSwitches", ["enable-automation"])
    #options.add_experimental_option("useAutomationExtension", False)
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
    driver._dhp2_profile_dir = profile_dir
    return driver


def close_driver(driver):
    profile_dir = getattr(driver, "_dhp2_profile_dir", None)
    try:
        driver.quit()
    finally:
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def read_lines(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


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
        close_driver(driver)
    return records


def main():
    if not os.path.exists(URLS_FILE):
        LOGGER.info("No URL file found at %s; skipping detail scrape.", URLS_FILE)
        return

    urls = read_lines(URLS_FILE)
    if not urls:
        LOGGER.info("No detail URLs collected; skipping detail scrape.")
        return

    initialize_csv(OUTPUT_FILE)
    records = scrape_details(urls)
    if records:
        write_csv(OUTPUT_FILE, records, append=True)
    LOGGER.info("Saved %s rows to %s", len(records), OUTPUT_FILE)


if __name__ == "__main__":
    main()
