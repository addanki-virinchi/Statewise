"""Count profile URLs on a Doximity specialty listing, page by page.

Standalone companion to doximity_ser.py. Give it a specialty listing URL
(e.g. cardiology) and it walks the pagination, recording how many profile
URLs each page yields and the running total. Results are written to both a
JSON file (structured, per-page) and a TXT file (human-readable + the flat
list of profile URLs).

Usage:
    python doximity_count.py
    python doximity_count.py "https://www.doximity.com/directory/md/specialty/cardiology"
    python doximity_count.py --url ".../cardiology" --out cardiology_count
"""

import argparse
import json
import os
import sys
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://www.doximity.com"
DEFAULT_URL = "https://www.doximity.com/directory/md/specialty/cardiology"
WAIT_SECONDS = 30
HEADLESS = True
FAST_LISTING_FETCH = True   # pull listing pages via in-page fetch() instead of navigating
PAGE_PAUSE = 0.5            # polite pause between listing pages (seconds)

CHROMEDRIVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chromedriver.exe")


def canonical_profile_url(url: str) -> str:
    """Stable identity for a profile URL -- must match doximity_ser.py.

    Collapses tracking query params, trailing slashes, host casing, and the
    /cv/ vs /pub/ prefix so the count reflects unique doctors, not URL strings.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if path.startswith("/cv/"):
        path = "/pub/" + path[len("/cv/"):]
    if netloc:
        return f"{parsed.scheme.lower()}://{netloc}{path}"
    return path


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
    """Return (html, used_fast_fetch); in-page fetch first, navigation fallback."""
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
    """Return (profile_urls_on_page, next_page_url_or_None)."""
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


def count_specialty(driver, start_url: str):
    """Walk every listing page, returning per-page stats and the unique URL list."""
    pages = []
    all_urls = []
    seen = set()

    next_url = start_url
    visited = set()
    page_number = 0

    while next_url and next_url not in visited:
        visited.add(next_url)
        current_url = next_url
        page_number += 1

        html, used_fast_fetch = fetch_listing_html(driver, current_url)
        page_urls, next_url = parse_listing_page(html)
        if next_url is None and used_fast_fetch:
            # A partial fetch() can drop the pagination block, which looks
            # exactly like the last page. Confirm with a real navigation before
            # ending the walk (this is what caused the earlier 142 vs 291 gap).
            driver.get(current_url)
            wait_for_page_ready(driver)
            page_urls, next_url = parse_listing_page(driver.page_source)

        raw_on_page = len(page_urls)
        new_on_page = 0
        for url in page_urls:
            canon = canonical_profile_url(url)
            if canon in seen:
                continue
            seen.add(canon)
            all_urls.append(url)
            new_on_page += 1

        pages.append(
            {
                "page": page_number,
                "page_url": current_url,
                "urls_on_page": raw_on_page,
                "new_urls_on_page": new_on_page,
                "cumulative_unique": len(all_urls),
                "next_page_url": next_url,
            }
        )
        print(
            f"  page {page_number:>4}: {raw_on_page:>3} urls "
            f"({new_on_page} new)  -> total {len(all_urls)}"
        )

        if PAGE_PAUSE:
            time.sleep(PAGE_PAUSE)

    return pages, all_urls


def main():
    parser = argparse.ArgumentParser(description="Count Doximity specialty profile URLs by page.")
    parser.add_argument("url", nargs="?", default=None, help="Specialty listing URL")
    parser.add_argument("--url", dest="url_flag", default=None, help="Specialty listing URL (flag form)")
    parser.add_argument("--out", default=None, help="Output basename (default derived from URL)")
    args = parser.parse_args()

    start_url = args.url or args.url_flag or DEFAULT_URL

    slug = os.path.basename(urlparse(start_url).path) or "specialty"
    out_base = args.out or f"doximity_count_{slug}"
    json_path = out_base + ".json"
    txt_path = out_base + ".txt"

    print(f"Counting profiles for: {start_url}")
    started = time.time()

    driver = build_driver()
    try:
        pages, all_urls = count_specialty(driver, start_url)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    elapsed = time.time() - started
    summary = {
        "start_url": start_url,
        "slug": slug,
        "total_pages": len(pages),
        "total_profile_urls": len(all_urls),
        "elapsed_seconds": round(elapsed, 1),
        "pages": pages,
    }

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    with open(txt_path, "w", encoding="utf-8") as handle:
        handle.write(f"Specialty listing : {start_url}\n")
        handle.write(f"Total pages       : {len(pages)}\n")
        handle.write(f"Total profile URLs: {len(all_urls)}\n")
        handle.write(f"Elapsed seconds   : {round(elapsed, 1)}\n")
        handle.write("\nPer-page breakdown:\n")
        handle.write(f"{'page':>6}  {'urls':>5}  {'new':>5}  {'cumulative':>10}\n")
        for entry in pages:
            handle.write(
                f"{entry['page']:>6}  {entry['urls_on_page']:>5}  "
                f"{entry['new_urls_on_page']:>5}  {entry['cumulative_unique']:>10}\n"
            )
        handle.write("\nProfile URLs:\n")
        for url in all_urls:
            handle.write(url + "\n")

    print()
    print(f"Total pages       : {len(pages)}")
    print(f"Total profile URLs: {len(all_urls)}")
    print(f"Elapsed           : {elapsed:.1f}s")
    print(f"Wrote {json_path} and {txt_path}")


if __name__ == "__main__":
    main()
