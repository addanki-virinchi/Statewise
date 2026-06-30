import base64
import csv
import logging
import os
import re
import time
from datetime import datetime
from io import BytesIO
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from PIL import Image
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = "https://www.doximity.com"
INPUT_FILE = "doximity_1.csv"
OUTPUT_FILE = "doximity_output.csv"
DETAILS_OUTPUT_FILE = "doximity_output_details.csv"
SCREENSHOT_ROOT_DIR = r"C:\Users\Administrator\Downloads\D_screenshots"
ERROR_LOG_FILE = "error.log"
WAIT_SECONDS = 30
PAGE_SLEEP = 0.5
HEADLESS = True


CHROMEDRIVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chromedriver.exe")

FIELDNAMES = [
    "input_name",
    "input_url",
    "profile_name",
    "first_name",
    "last_name",
    "profile_url",
    "secondary_occupation",
    "sub_speciality_occupations",
    "main_primary_occupation",
    "job_title",
    "alternative_job_title",
    "address",
    "city",
    "state",
    "active_certifications_licenses",
    "other_licenses",
    "board_certification",
    "screenshot_file",
    "scrape_status",
    "scrape_notes",
]

DETAIL_FIELDNAMES = [
    "first_name",
    "last_name",
    "city",
    "state",
]

INVISIBLE_CHARS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
NAME_SUFFIX_RE = re.compile(
    r"\s+(?=(?:MD|M\.D\.|DO|D\.O\.|PhD|Ph\.D\.|MBA|MPH|MS|MA|RN|NP|PA|DNP|FNP|APRN|DDS|DMD|OD|PsyD)\b)",
    flags=re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    text = INVISIBLE_CHARS_RE.sub("", text or "")
    return re.sub(r"\s+", " ", text).strip()


def unique_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        value = normalize_text(value)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def clean_profile_name(text: str) -> str:
    cleaned = normalize_text(text)
    cleaned = re.sub(r"\s*\([^)]*\)", " ", cleaned)
    cleaned = re.split(r"\s*,\s*", cleaned, maxsplit=1)[0]
    cleaned = NAME_SUFFIX_RE.split(cleaned, maxsplit=1)[0]
    cleaned = normalize_text(cleaned)
    return cleaned


def split_profile_name(text: str):
    cleaned = clean_profile_name(text)
    parts = cleaned.split()
    if not parts:
        return "", "", ""
    first_name = parts[0]
    last_name = parts[-1] if len(parts) > 1 else ""
    return first_name, last_name, cleaned


def extract_city_state(address: str):
    cleaned = normalize_text(address)
    if not cleaned:
        return "", ""

    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if len(parts) < 2:
        return "", ""

    city = parts[-2]
    state_match = re.search(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b", parts[-1])
    state = state_match.group(1) if state_match else ""
    return city, state


def safe_filename(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("._") or "profile"


def build_screenshot_dir(specialty_name: str) -> str:
    # Group every screenshot under one folder per specialty from the input CSV.
    folder_name = safe_filename(specialty_name) or "unknown_specialty"
    return os.path.join(SCREENSHOT_ROOT_DIR, folder_name)


def setup_logging():
    logger = logging.getLogger("doximity")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(ERROR_LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.propagate = False
    return logger


def read_input_rows(path: str):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if not reader.fieldnames:
            return rows
        for row in reader:
            name = normalize_text(row.get("name") or row.get("Name") or "")
            url = normalize_text(row.get("url") or row.get("URL") or "")
            if not url:
                continue
            rows.append({"name": name, "url": url})
    return rows


def load_existing_profile_urls(path: str):
    existing_urls = set()
    if not os.path.exists(path):
        return existing_urls

    try:
        with open(path, newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                profile_url = normalize_text(row.get("profile_url") or "")
                if profile_url:
                    existing_urls.add(profile_url)
    except Exception:
        logging.getLogger("doximity").exception("Failed to read existing output file %s", path)

    return existing_urls


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-infobars")
    options.page_load_strategy = "eager"
    options.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 2,
            "profile.managed_default_content_settings.notifications": 2,
        },
    )
    if HEADLESS:
        options.add_argument("--headless=new")

    if not os.path.exists(CHROMEDRIVER_PATH):
        raise FileNotFoundError(f"Missing chromedriver.exe at {CHROMEDRIVER_PATH}")

    service = Service(CHROMEDRIVER_PATH)

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(90)
    return driver


def wait_for_page_ready(driver):
    WebDriverWait(driver, WAIT_SECONDS).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    time.sleep(PAGE_SLEEP)


def scrape_listing_pages(driver, start_url: str):
    profile_urls = []
    seen = set()
    current_url = start_url
    visited_pages = set()

    while current_url and current_url not in visited_pages:
        visited_pages.add(current_url)
        driver.get(current_url)
        wait_for_page_ready(driver)
        soup = BeautifulSoup(driver.page_source, "html.parser")

        for anchor in soup.select("ul.list-4-col a[href]"):
            href = anchor.get("href", "").strip()
            if not href:
                continue
            if not (href.startswith("/pub/") or href.startswith("/cv/")):
                continue
            absolute = urljoin(BASE_URL, href)
            if absolute in seen:
                continue
            seen.add(absolute)
            profile_urls.append(absolute)

        next_link = soup.select_one("div.pagination a.next_page[href]")
        if next_link:
            current_url = urljoin(BASE_URL, next_link["href"])
        else:
            current_url = None

    return profile_urls


def get_text_list(elements):
    return unique_preserve_order([normalize_text(el.get_text(" ", strip=True)) for el in elements])


def parse_year_range(text: str):
    match = re.search(
        r"(?P<start>\d{4}|Present|Current|Now)?\s*-\s*(?P<end>\d{4}|Present|Current|Now)",
        text or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return None, None
    start = match.group("start")
    end = match.group("end")
    return start, end


def end_year_is_active(end_value: str, current_year: int) -> bool:
    if not end_value:
        return False
    lowered = end_value.strip().lower()
    if lowered in {"present", "current", "now"}:
        return True
    if lowered.isdigit():
        return int(lowered) >= current_year
    return False


BOARD_CERT_PATTERN = re.compile(r"American Board[^,]+", flags=re.IGNORECASE)


def extract_board_certifications(text: str):
    matches = unique_preserve_order(BOARD_CERT_PATTERN.findall(text or ""))
    if not matches:
        return "", normalize_text(text)

    cleaned_text = BOARD_CERT_PATTERN.sub("", text or "")
    cleaned_text = re.sub(r"(?:\s*,\s*){2,}", ", ", cleaned_text)
    cleaned_text = re.sub(r"\s*,\s*$", "", cleaned_text)
    cleaned_text = re.sub(r"^\s*,\s*", "", cleaned_text)
    cleaned_text = normalize_text(cleaned_text)
    cleaned_text = re.sub(r"\s*,\s*,\s*", ", ", cleaned_text)
    cleaned_text = cleaned_text.strip(" ,")
    return ", ".join(matches), cleaned_text


def normalize_job_title(text: str) -> str:
    raw = normalize_text(text)
    if re.search(r"\b(physician|fellow)\b", raw, flags=re.IGNORECASE):
        return "MD"
    return raw


def parse_profile_html(html: str, profile_url: str):
    soup = BeautifulSoup(html, "html.parser")
    current_year = datetime.now().year

    name = ""
    name_node = soup.select_one("h1.profile-overview-user-name")
    if name_node:
        name = normalize_text(name_node.get_text(" ", strip=True))

    secondary_occupations = []
    for anchor in soup.select("div.profile-overview-subheading a.profile-overview-subheading-link[href]"):
        href = anchor.get("href", "")
        text = normalize_text(anchor.get_text(" ", strip=True))
        if not text:
            continue
        if "/directory/md/specialty/" in href:
            secondary_occupations.append(text)
    secondary_occupations = unique_preserve_order(secondary_occupations)

    sub_speciality_occupations = []
    job_titles = []
    for container in soup.select("div.profile-overview-info-line-container"):
        for paragraph in container.select("p.profile-overview-info-line"):
            text = normalize_text(paragraph.get_text(" ", strip=True))
            if not text:
                continue
            classes = " ".join(paragraph.get("class", [])).lower()
            itemprop = (paragraph.get("itemprop") or "").lower()
            if "jobtitle" in classes or itemprop == "jobtitle":
                job_titles.append(text)
            else:
                sub_speciality_occupations.append(text)

    sub_speciality_occupations = unique_preserve_order(sub_speciality_occupations)
    job_titles = unique_preserve_order(job_titles)

    address = ""
    contact_container = soup.select_one("p.profile-overview-contact-list-item-text-container")
    if contact_container:
        address_lines = [
            normalize_text(span.get_text(" ", strip=True))
            for span in contact_container.select("span.profile-overview-contact-list-item-text-line")
        ]
        address_lines = [line for line in address_lines if line]
        address = ", ".join(address_lines)

    active_credentials = []
    other_licenses = []
    for block in soup.select("div.profile-section-wrapper-text"):
        spans = [
            normalize_text(span.get_text(" ", strip=True))
            for span in block.select("span")
        ]
        spans = [span for span in spans if span]
        if not spans:
            continue

        label = spans[0]
        detail = " - ".join(spans[1:]).strip()

        if len(spans) > 1:
            start_value, end_value = parse_year_range(detail)
            if start_value is not None or end_value is not None:
                if end_year_is_active(end_value, current_year):
                    active_credentials.append(f"{label} [{detail}]")
                continue
            other_licenses.append(f"{label} - {detail}")
        else:
            other_licenses.append(label)

    active_credentials = unique_preserve_order(active_credentials)
    other_licenses = unique_preserve_order(other_licenses)
    board_certifications = []
    cleaned_other_licenses = []
    for text in other_licenses:
        board_certification, cleaned_text = extract_board_certifications(text)
        if board_certification:
            board_certifications.extend([part for part in board_certification.split(", ") if part])
        if cleaned_text:
            cleaned_other_licenses.append(cleaned_text)

    board_certifications = unique_preserve_order(board_certifications)
    cleaned_other_licenses = unique_preserve_order(cleaned_other_licenses)

    raw_job_title = job_titles[-1] if job_titles else ""
    alternative_job_title = raw_job_title
    job_title = normalize_job_title(raw_job_title)
    first_name, last_name, cleaned_name = split_profile_name(name)
    city, state = extract_city_state(address)

    return {
        "profile_name": cleaned_name,
        "first_name": first_name,
        "last_name": last_name,
        "secondary_occupation": ", ".join(secondary_occupations),
        "sub_speciality_occupations": ", ".join(sub_speciality_occupations),
        "main_primary_occupation": sub_speciality_occupations[-1] if sub_speciality_occupations else "",
        "job_title": job_title,
        "alternative_job_title": alternative_job_title,
        "address": address,
        "city": city,
        "state": state,
        "active_certifications_licenses": ", ".join(active_credentials),
        "other_licenses": ", ".join(cleaned_other_licenses),
        "board_certification": ", ".join(board_certifications) if board_certifications else "NULL",
        "scrape_notes": "",
    }


def capture_profile_screenshot(driver, output_path: str):
    screenshot_dir = os.path.dirname(output_path)
    if screenshot_dir:
        os.makedirs(screenshot_dir, exist_ok=True)

    metrics = driver.execute_script(
        """
        const target = document.querySelector('input.find-my-profile-btn, input[name="commit"][value="Find my profile"], .find-my-profile-btn');
        if (!target) return null;
        const rect = target.getBoundingClientRect();
        return {
          top: rect.top + window.scrollY,
          dpr: window.devicePixelRatio || 1
        };
        """
    )

    raw_png = driver.execute_cdp_cmd(
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True, "captureBeyondViewport": True},
    )["data"]

    image = Image.open(BytesIO(base64.b64decode(raw_png)))
    if metrics and metrics.get("top") is not None:
        crop_bottom = int(float(metrics["top"]) * float(metrics.get("dpr") or 1.0)) - 32
        crop_bottom = max(1, min(image.height, crop_bottom))
        if crop_bottom < image.height:
            image = image.crop((0, 0, image.width, crop_bottom))

    image.save(output_path)


def scrape_profile(driver, profile_url: str, source_name: str):
    driver.get(profile_url)
    wait_for_page_ready(driver)
    WebDriverWait(driver, WAIT_SECONDS).until(
        lambda d: d.find_elements(By.CSS_SELECTOR, "h1.profile-overview-user-name")
    )

    profile_data = parse_profile_html(driver.page_source, profile_url)
    display_name = " ".join(part for part in [profile_data.get("first_name", ""), profile_data.get("last_name", "")] if part)
    profile_name = display_name or source_name or os.path.basename(urlparse(profile_url).path).strip("/")
    screenshot_name = safe_filename(profile_name) + "_" + safe_filename(os.path.basename(urlparse(profile_url).path))
    screenshot_dir = build_screenshot_dir(source_name)
    screenshot_path = os.path.join(screenshot_dir, f"{screenshot_name}.png")
    capture_profile_screenshot(driver, screenshot_path)

    profile_data.update(
        {
            "profile_url": profile_url,
            "screenshot_file": screenshot_path,
            "scrape_status": "ok",
        }
    )
    return profile_data


def build_output_row(row_data, source_row, profile_url):
    return {
        "input_name": source_row["name"],
        "input_url": source_row["url"],
        "profile_name": row_data.get("profile_name", ""),
        "first_name": row_data.get("first_name", ""),
        "last_name": row_data.get("last_name", ""),
        "profile_url": profile_url,
        "secondary_occupation": row_data.get("secondary_occupation", ""),
        "sub_speciality_occupations": row_data.get("sub_speciality_occupations", ""),
        "main_primary_occupation": row_data.get("main_primary_occupation", ""),
        "job_title": row_data.get("job_title", ""),
        "alternative_job_title": row_data.get("alternative_job_title", ""),
        "address": row_data.get("address", ""),
        "city": row_data.get("city", ""),
        "state": row_data.get("state", ""),
        "active_certifications_licenses": row_data.get("active_certifications_licenses", ""),
        "other_licenses": row_data.get("other_licenses", ""),
        "board_certification": row_data.get("board_certification", "NULL"),
        "screenshot_file": row_data.get("screenshot_file", ""),
        "scrape_status": row_data.get("scrape_status", ""),
        "scrape_notes": row_data.get("scrape_notes", ""),
    }


def main():
    logger = setup_logging()
    try:
        input_rows = read_input_rows(INPUT_FILE)
    except Exception:
        logger.exception("Failed to read input file %s", INPUT_FILE)
        return

    if not input_rows:
        logger.error("No input rows found in %s", INPUT_FILE)
        return

    try:
        driver = build_driver()
    except Exception:
        logger.exception("Failed to initialize Chrome/WebDriver")
        return

    processed_profile_urls = load_existing_profile_urls(OUTPUT_FILE)
    if processed_profile_urls:
        logger.info("Loaded %d existing profile URLs from %s", len(processed_profile_urls), OUTPUT_FILE)

    try:
        output_exists = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
        details_exists = os.path.exists(DETAILS_OUTPUT_FILE) and os.path.getsize(DETAILS_OUTPUT_FILE) > 0

        with open(OUTPUT_FILE, "a", newline="", encoding="utf-8-sig") as csv_file, open(
            DETAILS_OUTPUT_FILE, "a", newline="", encoding="utf-8-sig"
        ) as details_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
            details_writer = csv.DictWriter(details_file, fieldnames=DETAIL_FIELDNAMES)
            if not output_exists:
                writer.writeheader()
            if not details_exists:
                details_writer.writeheader()

            for source_row in input_rows:
                listing_url = source_row["url"]
                source_name = source_row["name"]
                print(f"Listing: {source_name} -> {listing_url}")
                try:
                    profile_urls = scrape_listing_pages(driver, listing_url)
                except Exception as exc:
                    print(f"  failed to load listing page: {exc}")
                    logger.exception("Failed to load listing page: %s", listing_url)
                    continue

                print(f"  found {len(profile_urls)} profiles")
                for index, profile_url in enumerate(profile_urls, start=1):
                    if profile_url in processed_profile_urls:
                        print(f"  profile {index}/{len(profile_urls)}: {profile_url} [skip duplicate]")
                        continue
                    print(f"  profile {index}/{len(profile_urls)}: {profile_url}")
                    try:
                        row_data = scrape_profile(driver, profile_url, source_name)
                        output_row = build_output_row(row_data, source_row, profile_url)
                        writer.writerow(output_row)
                        details_writer.writerow({field: output_row.get(field, "") for field in DETAIL_FIELDNAMES})
                        csv_file.flush()
                        details_file.flush()
                        processed_profile_urls.add(profile_url)
                    except TimeoutException as exc:
                        print(f"    timeout: {exc}")
                        logger.exception("Timeout while scraping %s", profile_url)
                    except WebDriverException as exc:
                        print(f"    webdriver error: {exc}")
                        logger.exception("WebDriver error while scraping %s", profile_url)
                    except Exception as exc:
                        print(f"    scrape error: {exc}")
                        logger.exception("Unexpected scrape error for %s", profile_url)
    except Exception:
        logger.exception("Failed while writing output file %s", OUTPUT_FILE)
    finally:
        try:
            driver.quit()
        except Exception:
            logger.exception("Failed to close the driver cleanly")


if __name__ == "__main__":
    main()
