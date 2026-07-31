import base64
import csv
import json
import logging
import os
import queue
import random
import re
import threading
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
INPUT_FILE = "doximity_2.csv"
OUTPUT_FILE = "doximity_output_2.csv"
DETAILS_OUTPUT_FILE = "doximity_output_details_2.csv"

SCREENSHOT_ROOT_DIR = r"C:\Users\Orange\OneDrive - Radixsol\Doximity_server_3"

ERROR_LOG_FILE = "error.log"

WAIT_SECONDS = 30
HEADLESS = True

# --- resume / persistence -------------------------------------------------
# Profile URLs are appended to disk the moment a listing page is parsed, and
# pagination progress is checkpointed after every page. A restart therefore
# replays zero listing pages for specialties that already finished, and picks
# up mid-pagination for the one that was interrupted.
STATE_DIR = "doximity_state"
PROFILE_URL_DIR = os.path.join(STATE_DIR, "profile_urls")
LISTING_STATE_FILE = os.path.join(STATE_DIR, "listing_progress.json")

# --- throughput -----------------------------------------------------------
PROFILE_WORKERS = 2            # parallel Chrome instances scraping profile pages
DRIVER_RECYCLE_EVERY = 250     # rebuild a worker's browser after this many profiles
PROFILE_ATTEMPTS = 3           # attempts per profile before it is recorded as failed
RETRY_FAILED_PROFILES = True   # True -> re-attempt profiles previously recorded as errors
RETRY_ROUNDS = 2               # extra passes over this run's failures, after all specialties
RETRY_ROUND_PAUSE = 30         # seconds to idle before a retry round (lets throttling decay)
FAST_LISTING_FETCH = True      # pull listing pages via in-page fetch() instead of navigating

# --- politeness / rate limiting ------------------------------------------
# Requests are spaced out GLOBALLY (across all workers) so the site sees a
# steady trickle instead of a burst, which is what triggers the "not
# responding" / throttling errors. Every profile load waits for a shared slot.
REQUEST_MIN_DELAY = 1.5        # minimum seconds between any two profile loads
REQUEST_JITTER = 1.5          # extra random 0..this seconds, breaks up robotic timing
RETRY_BACKOFF_BASE = 2.0      # first retry waits this long, doubling each attempt
RETRY_BACKOFF_MAX = 10.0       # cap on the per-attempt backoff

# --- listing pagination pacing -------------------------------------------
# Walking the "next page" links is where results were being lost: firing the
# pages back-to-back gets throttled, and a throttled response still carries
# profile cards while silently dropping the pagination block -- which the
# parser reads as "last page", so a specialty stops after only a few pages.
# The cure is to page slowly and never trust an empty / paginationless page
# until it has been re-fetched a few times with growing backoff.
LISTING_PAGE_MIN_DELAY = 2.0   # minimum seconds between two listing-page requests
LISTING_PAGE_JITTER = 2.0      # extra random 0..this seconds on top of the minimum
LISTING_MAX_RETRIES =2        # re-fetches for a throttled/empty listing page
LISTING_RETRY_BACKOFF_BASE = 3.0   # first listing retry waits this long, then doubles
LISTING_RETRY_BACKOFF_MAX = 30.0   # cap on the per-attempt listing backoff


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


def canonical_profile_url(url: str) -> str:
    """Return a stable identity for a profile URL.

    The same doctor surfaces as several different strings -- with tracking
    query params, a trailing slash, mixed host casing, or as a /cv/ link
    instead of /pub/. Collapse all of those to one canonical form so
    profile_url can actually be used as a unique key. The value returned is
    only ever used for comparison/dedup; the original URL is what gets
    navigated, so rewriting /cv/ -> /pub/ here never breaks scraping.
    """
    url = normalize_text(url)
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    # Path case is left untouched (slugs are case-sensitive); only the tail
    # slash and the /cv/ vs /pub/ prefix are normalized.
    path = parsed.path.rstrip("/")
    if path.startswith("/cv/"):
        path = "/pub/" + path[len("/cv/"):]
    # Query + fragment carry per-page tracking noise; drop them entirely.
    if netloc:
        return f"{parsed.scheme.lower()}://{netloc}{path}"
    return path


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
    """Return (done_urls, failed_urls) already recorded in the output CSV."""
    done_urls = set()
    failed_urls = set()
    if not os.path.exists(path):
        return done_urls, failed_urls

    try:
        with open(path, newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                profile_url = canonical_profile_url(row.get("profile_url") or "")
                if not profile_url:
                    continue
                if normalize_text(row.get("scrape_status") or "") == "ok":
                    done_urls.add(profile_url)
                else:
                    failed_urls.add(profile_url)
    except Exception:
        logging.getLogger("doximity").exception("Failed to read existing output file %s", path)

    failed_urls -= done_urls
    return done_urls, failed_urls


def specialty_key(source_row) -> str:
    name = safe_filename(source_row.get("name") or "")
    slug = safe_filename(os.path.basename(urlparse(source_row["url"]).path))
    return f"{name}__{slug}" if name else slug


def profile_url_path(key: str) -> str:
    return os.path.join(PROFILE_URL_DIR, f"{key}.txt")


def load_listing_state() -> dict:
    if not os.path.exists(LISTING_STATE_FILE):
        return {}
    try:
        with open(LISTING_STATE_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        logging.getLogger("doximity").exception(
            "Unreadable listing state %s; starting listings fresh", LISTING_STATE_FILE
        )
        return {}


def save_listing_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp_path = LISTING_STATE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(tmp_path, LISTING_STATE_FILE)


def load_saved_profile_urls(key: str):
    path = profile_url_path(key)
    urls = []
    seen = set()
    if not os.path.exists(path):
        return urls, seen
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            url = line.strip()
            if not url:
                continue
            canon = canonical_profile_url(url)
            if canon in seen:
                continue
            seen.add(canon)
            urls.append(url)
    return urls, seen


def build_driver():
    options = ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--no-first-run")
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
    driver.set_script_timeout(60)
    return driver


def quit_driver(driver):
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


_throttle_lock = threading.Lock()
_next_request_time = [0.0]


def throttle_request():
    """Block until this thread is allowed to issue the next profile request.

    A single shared clock hands out evenly spaced slots to all workers, so no
    matter how many threads run, the site only ever sees one request roughly
    every REQUEST_MIN_DELAY (+jitter) seconds. The slot is reserved under the
    lock, then the actual wait happens outside it so workers don't block each
    other while sleeping.
    """
    with _throttle_lock:
        now = time.monotonic()
        slot = max(now, _next_request_time[0])
        _next_request_time[0] = slot + REQUEST_MIN_DELAY + random.uniform(0, REQUEST_JITTER)
    wait = slot - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def wait_for_page_ready(driver):
    WebDriverWait(driver, WAIT_SECONDS).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )


FETCH_PAGE_SCRIPT = """
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {credentials: 'include', headers: {'Accept': 'text/html'}})
  .then(r => (r.ok ? r.text() : ''))
  .then(t => done(t))
  .catch(() => done(''));
"""


def fetch_listing_html(driver, url: str):
    """Return (html, used_fast_fetch) for a listing page.

    Doximity rejects plain HTTP clients (403), so the request has to come from
    the browser. An in-page fetch() reuses the same cookies and TLS session but
    skips navigation, rendering and subresource loading, which is much cheaper
    than a driver.get() per page. Falls back to a real navigation if it fails.

    The second return value tells the caller whether the fast path was used, so
    it can double-check a suspicious "no next page" result: a throttled/partial
    fetch() response can carry profile cards but drop the pagination block,
    which otherwise looks exactly like the end of the listing.
    """
    same_origin = urlparse(driver.current_url).netloc == urlparse(BASE_URL).netloc
    if FAST_LISTING_FETCH and same_origin:
        try:
            html = driver.execute_async_script(FETCH_PAGE_SCRIPT, url)
            if html and "list-4-col" in html:
                return html, True
        except WebDriverException:
            pass

    driver.get(url)
    wait_for_page_ready(driver)
    return driver.page_source, False


def parse_listing_page(html: str):
    soup = BeautifulSoup(html, "html.parser")

    page_urls = []
    for anchor in soup.select("ul.list-4-col a[href]"):
        href = anchor.get("href", "").strip()
        if not href:
            continue
        if not (href.startswith("/pub/") or href.startswith("/cv/")):
            continue
        page_urls.append(urljoin(BASE_URL, href))

    next_link = soup.select_one("div.pagination a.next_page[href]")
    next_url = urljoin(BASE_URL, next_link["href"]) if next_link else None
    return page_urls, next_url


def load_listing_page(driver, url: str, logger):
    """Fetch one listing page reliably, retrying throttled/partial responses.

    Returns (page_urls, next_url). Two failure modes are handled so pagination
    never stops early:

    * A response with *no* profile cards is treated as throttling ("not
      responding" / partial fetch) and re-fetched with growing backoff rather
      than accepted as an empty end-of-list.
    * A response that has cards but no pagination is only believed once it has
      been confirmed by a *real* navigation (a partial in-page fetch() can drop
      the pagination block while keeping the cards, which is indistinguishable
      from the true last page).
    """
    for attempt in range(1, LISTING_MAX_RETRIES + 1):
        html, used_fast_fetch = fetch_listing_html(driver, url)
        page_urls, next_url = parse_listing_page(html)

        if page_urls:
            if next_url is None and used_fast_fetch:
                # Cards but no "next": re-load for real before trusting the end.
                driver.get(url)
                wait_for_page_ready(driver)
                page_urls, next_url = parse_listing_page(driver.page_source)
            return page_urls, next_url

        # No cards -> almost always throttling. Wait longer and try again.
        backoff = min(
            LISTING_RETRY_BACKOFF_MAX,
            LISTING_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)),
        ) + random.uniform(0, 1.5)
        logger.warning(
            "Listing page %s came back empty (attempt %d/%d); backing off %.1fs",
            url, attempt, LISTING_MAX_RETRIES, backoff,
        )
        time.sleep(backoff)

    # Retries exhausted: one last real navigation so a genuinely empty page is
    # reported honestly instead of looping forever.
    driver.get(url)
    wait_for_page_ready(driver)
    return parse_listing_page(driver.page_source)


def collect_profile_urls(driver, source_row, logger):
    """Return every profile URL for one specialty, resuming from disk when possible."""
    key = specialty_key(source_row)
    state = load_listing_state()
    entry = state.get(key) or {}
    urls, seen = load_saved_profile_urls(key)

    if entry.get("complete"):
        logger.info("Listing cached for %s (%d profiles, 0 pages refetched)", source_row["name"], len(urls))
        return urls

    next_url = entry.get("next_page") or source_row["url"]
    pages_done = int(entry.get("pages") or 0)
    if pages_done:
        logger.info(
            "Resuming listing for %s at page %d (%d profiles already cached)",
            source_row["name"],
            pages_done + 1,
            len(urls),
        )

    os.makedirs(PROFILE_URL_DIR, exist_ok=True)
    visited = set()

    def checkpoint(pending_url, complete):
        state[key] = {
            "name": source_row["name"],
            "next_page": pending_url,
            "pages": pages_done,
            "complete": complete,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_listing_state(state)

    first_page = True
    with open(profile_url_path(key), "a", encoding="utf-8") as url_file:
        while next_url and next_url not in visited:
            visited.add(next_url)
            current_url = next_url
            # Space out pagination so the site never sees back-to-back "next"
            # hits -- that burst is what triggers the throttling that quietly
            # truncates a specialty. The very first page needs no pre-delay.
            if not first_page:
                time.sleep(LISTING_PAGE_MIN_DELAY + random.uniform(0, LISTING_PAGE_JITTER))
            first_page = False
            try:
                page_urls, next_url = load_listing_page(driver, current_url, logger)
            except Exception:
                # Checkpoint so the next run restarts on this exact page.
                checkpoint(current_url, False)
                logger.exception("Failed to load listing page %s", current_url)
                raise

            for url in page_urls:
                canon = canonical_profile_url(url)
                if canon in seen:
                    continue
                seen.add(canon)
                urls.append(url)
                url_file.write(url + "\n")
            url_file.flush()
            os.fsync(url_file.fileno())

            pages_done += 1
            checkpoint(next_url, next_url is None)

    return urls


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


class ResultWriter:
    """Thread-safe append-and-flush writer for both output CSVs."""

    def __init__(self, csv_file, csv_writer, details_file, details_writer, seen_urls=None):
        self._csv_file = csv_file
        self._csv_writer = csv_writer
        self._details_file = details_file
        self._details_writer = details_writer
        self._lock = threading.Lock()
        # Canonical profile URLs already written (this run or a prior one). This
        # is the last line of defence against duplicate rows: even if the same
        # profile reaches the queue twice, it is only ever persisted once.
        self._seen_urls = set(seen_urls or ())

    def write(self, output_row):
        # Only successful records are ever persisted. Failed/error rows are
        # dropped entirely so the CSV never needs cleaning afterwards.
        if output_row.get("scrape_status") != "ok":
            return
        canon = canonical_profile_url(output_row.get("profile_url") or "")
        with self._lock:
            if canon and canon in self._seen_urls:
                return
            if canon:
                self._seen_urls.add(canon)
            self._csv_writer.writerow(output_row)
            self._csv_file.flush()
            self._details_writer.writerow(
                {field: output_row.get(field, "") for field in DETAIL_FIELDNAMES}
            )
            self._details_file.flush()


def profile_worker(work_queue, source_row, writer, processed_urls, lock, counter, logger):
    driver = None
    handled = 0
    try:
        while True:
            try:
                index, total, profile_url = work_queue.get_nowait()
            except queue.Empty:
                return

            try:
                for attempt in range(1, PROFILE_ATTEMPTS + 1):
                    if driver is None or handled >= DRIVER_RECYCLE_EVERY:
                        quit_driver(driver)
                        driver = build_driver()
                        handled = 0
                    handled += 1
                    # Wait for a globally spaced slot before touching the site so
                    # we never fire requests back-to-back.
                    throttle_request()
                    try:
                        row_data = scrape_profile(driver, profile_url, source_row["name"])
                        output_row = build_output_row(row_data, source_row, profile_url)
                        writer.write(output_row)
                        with lock:
                            processed_urls.add(canonical_profile_url(profile_url))
                            counter["ok"] += 1
                        print(f"  [{index}/{total}] ok  {profile_url}")
                        break
                    except Exception as exc:
                        if isinstance(exc, WebDriverException) and not isinstance(exc, TimeoutException):
                            # Browser is likely unhealthy; drop it and start clean.
                            quit_driver(driver)
                            driver = None
                        if attempt == PROFILE_ATTEMPTS:
                            raise
                        # Exponential backoff with jitter: give the site room to
                        # recover before retrying instead of hammering it.
                        backoff = min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
                        backoff += random.uniform(0, 1.0)
                        logger.warning(
                            "Attempt %d/%d failed for %s: %s (retry in %.1fs)",
                            attempt, PROFILE_ATTEMPTS, profile_url, exc, backoff,
                        )
                        time.sleep(backoff)
            except Exception:
                logger.exception("Giving up on %s", profile_url)
                # Do NOT write failed profiles to the CSV. They are left unsaved
                # so the next run re-attempts them (nothing to clean up later).
                with lock:
                    counter["failed"] += 1
                    counter["failed_urls"].append(profile_url)
                print(f"  [{index}/{total}] ERR {profile_url} (not saved)")
    finally:
        quit_driver(driver)


def scrape_profiles_parallel(profile_urls, source_row, writer, processed_urls, logger):
    work_queue = queue.Queue()
    total = len(profile_urls)
    for index, profile_url in enumerate(profile_urls, start=1):
        work_queue.put((index, total, profile_url))

    lock = threading.Lock()
    counter = {"ok": 0, "failed": 0, "failed_urls": []}
    worker_count = max(1, min(PROFILE_WORKERS, total))
    threads = [
        threading.Thread(
            target=profile_worker,
            args=(work_queue, source_row, writer, processed_urls, lock, counter, logger),
            name=f"profile-{i + 1}",
            daemon=True,
        )
        for i in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return counter


def consolidate_output(logger):
    """Collapse duplicate rows per profile_url, keeping the successful one.

    Retries append a fresh row rather than seeking into the CSV, so a profile
    that failed and later succeeded appears twice. Rewrite the file keeping one
    row per URL -- the 'ok' row when there is one, otherwise the last attempt --
    so the error placeholder is replaced by the real values. The details file is
    regenerated from the consolidated rows for the same reason.
    """
    if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
        return

    rows_by_url = {}
    order = []
    total_rows = 0
    try:
        with open(OUTPUT_FILE, newline="", encoding="utf-8-sig") as csv_file:
            for row in csv.DictReader(csv_file):
                profile_url = canonical_profile_url(row.get("profile_url") or "")
                if not profile_url:
                    continue
                total_rows += 1
                existing = rows_by_url.get(profile_url)
                if existing is None:
                    order.append(profile_url)
                    rows_by_url[profile_url] = row
                elif row.get("scrape_status") == "ok" or existing.get("scrape_status") != "ok":
                    # Prefer a success; between two failures keep the newest note.
                    rows_by_url[profile_url] = row
    except Exception:
        logger.exception("Could not consolidate %s; leaving it untouched", OUTPUT_FILE)
        return

    if len(order) == total_rows:
        return

    tmp_path = OUTPUT_FILE + ".tmp"
    details_tmp_path = DETAILS_OUTPUT_FILE + ".tmp"
    try:
        with open(tmp_path, "w", newline="", encoding="utf-8-sig") as csv_file, open(
            details_tmp_path, "w", newline="", encoding="utf-8-sig"
        ) as details_file:
            csv_writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
            details_writer = csv.DictWriter(details_file, fieldnames=DETAIL_FIELDNAMES)
            csv_writer.writeheader()
            details_writer.writeheader()
            for profile_url in order:
                row = rows_by_url[profile_url]
                csv_writer.writerow({field: row.get(field, "") for field in FIELDNAMES})
                if row.get("scrape_status") == "ok":
                    details_writer.writerow({field: row.get(field, "") for field in DETAIL_FIELDNAMES})
        os.replace(tmp_path, OUTPUT_FILE)
        os.replace(details_tmp_path, DETAILS_OUTPUT_FILE)
    except Exception:
        logger.exception("Failed to rewrite %s; original left in place", OUTPUT_FILE)
        for path in (tmp_path, details_tmp_path):
            try:
                os.remove(path)
            except OSError:
                pass
        return

    logger.info(
        "Consolidated %s: %d rows -> %d unique profiles", OUTPUT_FILE, total_rows, len(order)
    )


def retry_failed_profiles(pending_failures, writer, processed_profile_urls, logger):
    """Re-attempt this run's failures, newest browser each time, until they stick."""
    for round_number in range(1, RETRY_ROUNDS + 1):
        if not pending_failures:
            break

        print(
            f"Retry round {round_number}/{RETRY_ROUNDS}: {len(pending_failures)} failed profiles"
        )
        logger.info(
            "Retry round %d/%d over %d failed profiles", round_number, RETRY_ROUNDS, len(pending_failures)
        )
        if RETRY_ROUND_PAUSE:
            time.sleep(RETRY_ROUND_PAUSE)

        still_failing = {}
        for source_key, entry in pending_failures.items():
            source_row = entry["source_row"]
            urls = entry["urls"]
            print(f"  retrying {len(urls)} under {source_row['name']}")
            counter = scrape_profiles_parallel(urls, source_row, writer, processed_profile_urls, logger)
            print(f"  round {round_number}: {counter['ok']} recovered, {counter['failed']} still failing")
            if counter["failed_urls"]:
                still_failing[source_key] = {
                    "source_row": source_row,
                    "urls": counter["failed_urls"],
                }

        pending_failures = still_failing

    remaining = sum(len(entry["urls"]) for entry in pending_failures.values())
    if remaining:
        logger.warning("%d profiles still failing after %d retry rounds", remaining, RETRY_ROUNDS)
        print(f"{remaining} profiles still failing; re-run the script to try them again")


def main():
    logger = setup_logging()
    os.makedirs(PROFILE_URL_DIR, exist_ok=True)

    try:
        input_rows = read_input_rows(INPUT_FILE)
    except Exception:
        logger.exception("Failed to read input file %s", INPUT_FILE)
        return

    if not input_rows:
        logger.error("No input rows found in %s", INPUT_FILE)
        return

    processed_profile_urls, failed_profile_urls = load_existing_profile_urls(OUTPUT_FILE)
    if processed_profile_urls or failed_profile_urls:
        logger.info(
            "Resuming: %d profiles already scraped, %d previously failed (%s)",
            len(processed_profile_urls),
            len(failed_profile_urls),
            "will retry" if RETRY_FAILED_PROFILES else "will skip",
        )

    skip_urls = set(processed_profile_urls)
    if not RETRY_FAILED_PROFILES:
        skip_urls |= failed_profile_urls

    try:
        listing_driver = build_driver()
    except Exception:
        logger.exception("Failed to initialize Chrome/WebDriver")
        return

    started_at = time.time()
    try:
        output_exists = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
        details_exists = os.path.exists(DETAILS_OUTPUT_FILE) and os.path.getsize(DETAILS_OUTPUT_FILE) > 0

        with open(OUTPUT_FILE, "a", newline="", encoding="utf-8-sig") as csv_file, open(
            DETAILS_OUTPUT_FILE, "a", newline="", encoding="utf-8-sig"
        ) as details_file:
            csv_writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
            details_writer = csv.DictWriter(details_file, fieldnames=DETAIL_FIELDNAMES)
            if not output_exists:
                csv_writer.writeheader()
            if not details_exists:
                details_writer.writeheader()
            # Seed the writer with everything already on disk so a resumed run
            # can never re-append a profile that a previous run persisted.
            writer = ResultWriter(
                csv_file, csv_writer, details_file, details_writer,
                seen_urls=processed_profile_urls,
            )
            run_failures = {}

            for source_row in input_rows:
                listing_url = source_row["url"]
                source_name = source_row["name"]
                print(f"Listing: {source_name} -> {listing_url}")
                try:
                    profile_urls = collect_profile_urls(listing_driver, source_row, logger)
                except Exception as exc:
                    print(f"  failed to load listing pages: {exc}")
                    continue

                pending = [url for url in profile_urls if canonical_profile_url(url) not in skip_urls]
                print(f"  {len(profile_urls)} profiles known, {len(pending)} pending")
                if not pending:
                    continue

                counter = scrape_profiles_parallel(pending, source_row, writer, processed_profile_urls, logger)
                # Everything attempted this round is now recorded in the CSV, so it
                # must not be attempted again later in the same run either. Failures
                # are held back for the retry pass instead of being re-queued here.
                skip_urls |= processed_profile_urls
                skip_urls.update(canonical_profile_url(url) for url in pending)
                if counter["failed_urls"]:
                    run_failures[specialty_key(source_row)] = {
                        "source_row": source_row,
                        "urls": counter["failed_urls"],
                    }
                print(f"  done: {counter['ok']} ok, {counter['failed']} failed")

            if run_failures:
                retry_failed_profiles(run_failures, writer, processed_profile_urls, logger)
    except Exception:
        logger.exception("Failed while writing output file %s", OUTPUT_FILE)
    finally:
        quit_driver(listing_driver)
        # Runs even after a crash: whatever succeeded should still replace its
        # earlier error row on disk.
        consolidate_output(logger)
        logger.info("Run finished in %.1f minutes", (time.time() - started_at) / 60.0)


if __name__ == "__main__":
    main()

# Infectious Disease,https://www.doximity.com/directory/md/specialty/infectious-disease
# Internal Medicine,https://www.doximity.com/directory/md/specialty/internal-medicine
# Interventional Radiology,https://www.doximity.com/directory/md/specialty/interventional-radiology
# Medical Genetics,https://www.doximity.com/directory/md/specialty/medical-genetics
# Medicine/Pediatrics,https://www.doximity.com/directory/md/specialty/medicine%2Fpediatrics
# Neonat/Perinatology,https://www.doximity.com/directory/md/specialty/neonat%2Fperinatology
# Nephrology,https://www.doximity.com/directory/md/specialty/nephrology
# Neurology,https://www.doximity.com/directory/md/specialty/neurology
# Neurosurgery,https://www.doximity.com/directory/md/specialty/neurosurgery
# Nuclear Medicine,https://www.doximity.com/directory/md/specialty/nuclear-medicine
# Obstetrics & Gynecology,https://www.doximity.com/directory/md/specialty/obstetrics-gynecology
# Occupational Medicine,https://www.doximity.com/directory/md/specialty/occupational-medicine
# Oncology,https://www.doximity.com/directory/md/specialty/oncology
# Ophthalmology,https://www.doximity.com/directory/md/specialty/ophthalmology