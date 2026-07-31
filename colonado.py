import csv
import json
import os
import random
import re
import sys
import time

# Force line-buffered stdout so progress prints always show up immediately.
sys.stdout.reconfigure(line_buffering=True)

import undetected_chromedriver as uc
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

import nopecha_1

URL = "https://apps2.colorado.gov/dora/licensing/Lookup/LicenseLookup.aspx"
WAIT_SECONDS = 40
OUTPUT_CSV = "Colorado_results_new.csv"
ZIP_CODES_CSV = "zip_codes_colo.csv"
CHECKPOINT_FILE = "colorado_checkpoint.json"
CHROME_VERSION_MAIN = 149

# --- Rate limiting / backoff ------------------------------------------------
SEARCH_PAUSE_MIN_SECONDS = 1.5
SEARCH_PAUSE_MAX_SECONDS = 3.5
RATE_LIMIT_BASE_DELAY_SECONDS = 10
RATE_LIMIT_MAX_DELAY_SECONDS = 300
RATE_LIMIT_JITTER_SECONDS = 5
RATE_LIMIT_MAX_RETRIES = 5
_rate_limit_failures = 0
_rate_limit_block_until = 0.0

# --- Captcha automation -----------------------------------------------------
# When True, captchas are solved automatically through NopeCHA. Failures do not
# fall back to a manual prompt; they trigger a browser restart for the same lead.
AUTO_CAPTCHA = True
CAPTCHA_MAX_ROUNDS = 2  # maximum solve attempts before restarting the browser

# NopeCHA can sit on an unsolvable/garbled image until the poll deadline, so use
# a shorter timeout for the FormShield image than the module default and stop
# auto-solving it after a couple of consecutive failures (each failure otherwise
# blocks for the full timeout before the current lead is restarted).
FORMSHIELD_SOLVE_TIMEOUT = 60

# How many times to (re)enter the captcha code and click Submit before giving up
# on a single search. A wrong/expired code re-shows the form instead of results.
SUBMIT_MAX_ATTEMPTS = 2
SEARCH_OUTCOME_GRACE_SECONDS = 8
POSTBACK_WAIT_SECONDS = 10

# Return values for the individual captcha solvers.
CAPTCHA_NONE = "none"      # no captcha of this kind on the page
CAPTCHA_SOLVED = "solved"  # auto-solved and the form resubmitted

LICENSE_TYPE_ID = "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_lbMultipleCredentialTypePrefix"
STATE_ID = "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_ddStates"
ZIP_CODE_ID = "ctl00_MainContentPlaceHolder_ucLicenseLookup_ctl03_tbZipCode_ContactAddress"
RESULTS_TABLE_ID = "ctl00_MainContentPlaceHolder_ucLicenseLookup_gvSearchResults"

KEYWORDS = [
    # "Audiologists",
    # "Dental",
    # "Hearing Aid Providers",
    # "Marriage and Family Therapists",
    # "Medical",
    # "Natural Medicine",
    "Nursing",
    # "Nursing Home Administrators",
    # "Occupational Therapy",
    # "Pharmacy",
    # "Physical Therapy",
    # "Psychologists",
    # "Speech-Language Pathology",
    # "Surgical Assistant and Surgical Technologist",
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


def checkpoint_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CHECKPOINT_FILE)


def load_checkpoint():
    path = checkpoint_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if {"keyword_index", "state_index", "zip_index"} <= set(data):
            data["keyword_index"] = max(0, int(data.get("keyword_index", 0)))
            data["state_index"] = max(0, int(data.get("state_index", 0)))
            data["zip_index"] = max(0, int(data.get("zip_index", 0)))
            data["page"] = max(1, int(data.get("page", 1)))
            return data
    except Exception as exc:
        print(f"[checkpoint] Could not read {CHECKPOINT_FILE}: {exc}")
    return {}


def save_checkpoint(keyword_index, state_index, zip_index, page=1, reason="in_progress"):
    data = {
        "keyword_index": max(0, int(keyword_index)),
        "state_index": max(0, int(state_index)),
        "zip_index": max(0, int(zip_index)),
        "page": max(1, int(page or 1)),
        "reason": reason,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(checkpoint_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def clear_checkpoint():
    path = checkpoint_path()
    if os.path.exists(path):
        os.remove(path)


class CaptchaRestartRequested(RuntimeError):
    """Raised when a captcha cannot be cleared within the allowed attempts."""


def _page_looks_rate_limited(driver):
    try:
        haystack = " ".join([
            driver.title or "",
            driver.current_url or "",
            driver.page_source or "",
        ]).lower()
    except Exception:
        return False
    return any(token in haystack for token in [
        "too many requests",
        "rate limit",
        "rate limited",
        "request blocked",
        "temporarily unavailable",
    ])


def _rate_limit_delay():
    delay = RATE_LIMIT_BASE_DELAY_SECONDS * (2 ** max(_rate_limit_failures - 1, 0))
    delay = min(delay, RATE_LIMIT_MAX_DELAY_SECONDS)
    delay += random.uniform(0, RATE_LIMIT_JITTER_SECONDS)
    return delay


def _record_rate_limit(context=""):
    global _rate_limit_failures, _rate_limit_block_until
    _rate_limit_failures += 1
    delay = _rate_limit_delay()
    _rate_limit_block_until = time.time() + delay
    label = f" ({context})" if context else ""
    print(f"  [429]{label} Rate limit detected. Backing off for {delay:.1f}s.")


def _reset_rate_limit_state():
    global _rate_limit_failures, _rate_limit_block_until
    _rate_limit_failures = 0
    _rate_limit_block_until = 0.0


def _maybe_sleep_for_rate_limit(context=""):
    if _rate_limit_block_until <= 0:
        return
    remaining = _rate_limit_block_until - time.time()
    if remaining <= 0:
        return
    label = f" ({context})" if context else ""
    print(f"  [429]{label} Cooling down for {remaining:.1f}s before retrying.")
    time.sleep(remaining)


def _search_pause():
    time.sleep(random.uniform(SEARCH_PAUSE_MIN_SECONDS, SEARCH_PAUSE_MAX_SECONDS))


def _is_retryable_rate_limit():
    if _rate_limit_failures >= RATE_LIMIT_MAX_RETRIES:
        print(f"  [429] Hit {_rate_limit_failures} consecutive rate limits; stopping retries for this step.")
        return False
    return True


def _wait_for_page_health(driver, wait, context=""):
    _maybe_sleep_for_rate_limit(context)
    wait_for_page_settle(driver)
    if _page_looks_rate_limited(driver):
        _record_rate_limit(context)
        return False
    _reset_rate_limit_state()
    return True


def _open_url_with_backoff(driver, wait, url, context=""):
    for _ in range(RATE_LIMIT_MAX_RETRIES):
        _maybe_sleep_for_rate_limit(context)
        driver.get(url)
        if _wait_for_page_health(driver, wait, context):
            return True
        if not _is_retryable_rate_limit():
            return False
    return False


def build_driver():
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")

    return uc.Chrome(
        options=options,
        use_subprocess=True,
        version_main=CHROME_VERSION_MAIN,
    )


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
        # ("CU", "Cancun"),
        #("CO", "Colorado"),
        # ("CT", "Connecticut"),
        # ("DE", "Delaware"),
        # ("DC", "District of Columbia"),#---
        # ("EM", "East Midlands"),
        # ("EE", "East of England"),
        # ("FL", "Florida"),
        # ("FC", "Foreign Country"),
        # ("GA", "Georgia"),
        # ("GU", "Guam"),
        # ("HI", "Hawaii"),
        # ("ID", "Idaho"),
        # ("IL", "Illinois"),
        # ("IN", "Indiana"),
        # ("IA", "Iowa"),
        # ("KS", "Kansas"),
        # ("KY", "Kentucky"),
        # ("LO", "London"),
        # ("LA", "Louisiana"),
        # ("ME", "Maine"),
        # ("MB", "Manitoba"),
        # ("MD", "Maryland"),
        # ("MA", "Massachusetts"),
        # ("MX", "Mexico"),
        # ("MC", "Mexico City"),
        # ("MI", "Michigan"),
        # ("MN", "Minnesota"),
        # ("MS", "Mississippi"),
        # ("MO", "Missouri"),
        # ("MT", "Montana"),
        # ("NE", "Nebraska"),
        # ("NV", "Nevada"),
        # ("NB", "New Brunswick"),
        # ("NH", "New Hampshire"),
        # ("NJ", "New Jersey"),
        # ("NM", "New Mexico"),
        # ("NY", "New York"),
        # ("NF", "Newfoundland"),
        # ("NC", "North Carolina"),
        # ("ND", "North Dakota"),
        # ("NO", "North East"),
        # ("NW", "North West"),
        # ("MP", "Northern Mariana Island"),
        # ("NT", "Northwest Territories"),
        # ("NS", "Nova Scotia"),
        # ("NN", "Nunavut"),
        # ("OH", "Ohio"),
        # ("OK", "Oklahoma"),
        # ("ON", "Ontario"),
        # ("OR", "Oregon"),
        # ("OT", "Ottawa"),
        # ("PA", "Pennsylvania"),
        # ("PE", "Prince Edward Island"),
        # ("PR", "Puerto Rico"),
        # ("PQ", "Quebec"),
        # ("RI", "Rhode Island"),
        # ("SK", "Saskatchewan"),
        # ("SC", "South Carolina"),
        # ("SD", "South Dakota"),
        # ("SE", "South East"),
        # ("SW", "South West"),
        # ("TN", "Tennessee"),
        # ("TX", "Texas"),
        # ("TR", "U.S. Territory"),
        # ("UK", "Unknown"),
        # ("UT", "Utah"),
        # ("VT", "Vermont"),
        # ("VI", "Virgin Islands"),
        # ("VA", "Virginia"),
        # ("WA", "Washington"),
        # ("WM", "West Midlands"),
        # ("WV", "West Virginia"),
        # ("WI", "Wisconsin"),
        # ("WY", "Wyoming"),
        # ("YH", "Yorkshire and the Humber"),
        # ("YT", "Yukon"),
    ]

def select_state(driver, value):
    Select(driver.find_element(By.ID, STATE_ID)).select_by_value(value)


def load_zip_codes(path=ZIP_CODES_CSV):
    zip_codes = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            zip_code = ""
            for key in ("Zip_code", "ZIP_Code", "zip_code", "ZIP_CODE"):
                if key in row and row[key]:
                    zip_code = row[key].strip()
                    break
            if zip_code:
                zip_codes.append(zip_code)

    # Keep the CSV order but avoid repeated searches for duplicate ZIPs.
    seen = set()
    unique_zip_codes = []
    for zip_code in zip_codes:
        if zip_code in seen:
            continue
        seen.add(zip_code)
        unique_zip_codes.append(zip_code)
    return unique_zip_codes


def fill_zip_code(driver, zip_code):
    box = _first_present(driver, [
        (By.ID, ZIP_CODE_ID),
        (By.CSS_SELECTOR, "input[id*='tbZipCode_ContactAddress']"),
        (By.CSS_SELECTOR, "input[name$='tbZipCode_ContactAddress']"),
    ])
    if box is None:
        return False
    box.clear()
    box.send_keys(zip_code)
    return True


def parse_current_page(driver):
    records = []
    row_xpath = "//table[contains(@id,'gvSearchResults')]/tbody/tr[td//a[contains(@href,'DisplayLicenceDetail')]]"
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


def _current_page(driver):
    """Number of the currently-active page in the CavuGrid pager, or None."""
    els = driver.find_elements(
        By.XPATH,
        "//ul[contains(@class,'pagination')]//li[contains(@class,'active')]",
    )
    for el in els:
        try:
            return int(el.text.strip())
        except (ValueError, StaleElementReferenceException):
            continue
    return None


def _next_page_link(driver):
    """Return the next pagination anchor and its page number, if present."""
    current = _current_page(driver)
    candidates = []
    for el in driver.find_elements(By.XPATH, "//ul[contains(@class,'pagination')]//a"):
        try:
            href = el.get_attribute("href") or ""
            text = el.text.strip()
        except StaleElementReferenceException:
            continue

        m = re.search(r"__doPostBack\(\s*'([^']+)'\s*,\s*'Page\$(\d+)'\s*\)", href)
        if not m:
            continue

        page_num = int(m.group(2))
        if current is None or page_num > current:
            candidates.append((page_num, el))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][0], candidates[0][1]


def _click_pager_link(driver, link):
    """Click a pager link using the site-generated __doPostBack href."""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
    driver.execute_script(
        "arguments[0].click();",
        link,
    )


def _advance_to_page(driver, target_page):
    """Advance sequentially until the requested result page is active."""
    target_page = max(1, int(target_page or 1))
    current_page = _current_page(driver) or 1
    while current_page < target_page:
        next_page, next_link = _next_page_link(driver)
        if next_link is None:
            print(f"    resume target page {target_page} was not reachable; stopped at page {current_page}.")
            return current_page
        _click_pager_link(driver, next_link)
        expected = next_page or (current_page + 1)
        try:
            WebDriverWait(driver, WAIT_SECONDS).until(
                lambda d: _current_page(d) == expected
            )
        except TimeoutException:
            print(f"    resume jump to page {expected} timed out; stopped at page {current_page}.")
            return current_page
        current_page = expected
    return current_page


def scrape_and_save(driver, wait, keyword, state_name, keyword_index, state_index, zip_index, start_page=1):
    """Parse every result page and save each page to CSV as it is scraped.

    Saving per page (rather than only at the end) means a crash or captcha
    mid-pagination never loses rows already collected. Returns the total count.
    """
    total = 0
    page = _advance_to_page(driver, start_page) if start_page > 1 else 1
    while True:
        records = parse_current_page(driver)
        for record in records:
            record["Keyword"] = keyword
            record["State"] = state_name
        if records:
            save_rows(records)
        total += len(records)
        print(f"    page {page}: saved {len(records)} rows (total {total}).")

        next_page, next_link = _next_page_link(driver)
        if next_link is None:
            break
        save_checkpoint(keyword_index, state_index, zip_index, next_page or (page + 1))

        # Click the next pager link directly so ASP.NET runs its own postback.
        _click_pager_link(driver, next_link)

        # Wait for the new page to become active (re-queried fresh each poll, so
        # nothing here can go stale).
        expected = next_page or (page + 1)
        try:
            WebDriverWait(driver, WAIT_SECONDS).until(
                lambda d: _current_page(d) == expected
            )
        except TimeoutException:
            print(f"    page {page + 1}: pager did not confirm the next page in time; stopping pagination.")
            break
        page += 1
    return total


def close_modal(driver):
    try:
        driver.execute_script(
            "$('.bs-example-modal-lg').hide().remove();"
            "if(window.reCaptchaReloadOnModalClose){reCaptchaReloadOnModalClose();}"
        )
    except Exception:
        pass


# --- Captcha handling -------------------------------------------------------

def _first_present(driver, locators):
    """Return the first displayed element matching any (By, value) locator."""
    for by, value in locators:
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                return el
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return None


def _request_captcha_restart(message):
    raise CaptchaRestartRequested(message)


def _fetch_image_as_data_url(driver, image_el):
    """Download the captcha image via its own ``src`` and return it as a data URL.

    The FormShield image is same-origin (``FormShield.aspx?...``), so fetching it
    in-page yields the exact PNG the server generated. This is far more reliable
    than an element screenshot, which can come back blank when the element is not
    scrolled into view -- and a blank image is unsolvable, so NopeCHA just spins
    until it times out.
    """
    try:
        src = image_el.get_attribute("currentSrc") or image_el.get_attribute("src")
    except StaleElementReferenceException:
        src = None
    if not src:
        return None
    try:
        return driver.execute_async_script(
            """
            const src = arguments[0];
            const callback = arguments[arguments.length - 1];
            fetch(src, {cache: 'no-store'})
                .then(r => r.blob())
                .then(blob => {
                    const reader = new FileReader();
                    reader.onloadend = () => callback(reader.result);
                    reader.onerror = () => callback(null);
                    reader.readAsDataURL(blob);
                })
                .catch(() => callback(null));
            """,
            src,
        )
    except Exception:
        return None


def _formshield_image_data_url(driver, image_el):
    """Best-effort base64 PNG data URL for the FormShield captcha image."""
    data_url = _fetch_image_as_data_url(driver, image_el)
    if data_url and data_url.startswith("data:image"):
        return data_url
    # Fallback: scroll into view first so the element screenshot isn't blank.
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", image_el)
        time.sleep(0.3)
        return f"data:image/png;base64,{image_el.screenshot_as_base64}"
    except Exception:
        return None


def _find_formshield_image(driver):
    return _first_present(driver, [
        (By.ID, "FormShield1_Image"),
        (By.CSS_SELECTOR, "img[src*='FormShield.aspx']"),
        (By.CSS_SELECTOR, "img[alt='Protected by FormShield']"),
    ])


def formshield_present(driver):
    return _find_formshield_image(driver) is not None


def enter_formshield_answer(driver):
    """Put an answer into the FormShield captcha box (does NOT submit).

    The FormShield security code lives on the search form itself, so the correct
    flow is: fill the fields, enter this code, then click Submit once. Submitting
    is left to the caller so a single postback carries both the search and the
    captcha answer.

    Returns ``CAPTCHA_NONE`` (no captcha) or ``CAPTCHA_SOLVED`` (auto-filled).
    Raises ``CaptchaRestartRequested`` when the captcha cannot be attempted.
    """
    image = _find_formshield_image(driver)
    if image is None:
        return CAPTCHA_NONE

    # The answer text box is the ASP.NET CaptchaSecurity control's txtCAPTCHA
    # field. Its ClientID is prefixed by the containing naming container, so
    # match on the stable suffix rather than the full id.
    answer_box = _first_present(driver, [
        (By.ID, "ctl00_MainContentPlaceHolder_ucLicenseLookup_CaptchaSecurity1_txtCAPTCHA"),
        (By.CSS_SELECTOR, "input[id$='_txtCAPTCHA']"),
        (By.CSS_SELECTOR, "input[id*='CaptchaSecurity'][type='text']"),
    ])
    if answer_box is None:
        for candidate in driver.find_elements(By.CSS_SELECTOR, "input[type='text']"):
            try:
                if candidate.is_displayed() and not candidate.get_attribute("value"):
                    answer_box = candidate
                    break
            except StaleElementReferenceException:
                continue

    if answer_box is None:
        _request_captcha_restart("FormShield captcha shown, but no answer field was found.")
    if not AUTO_CAPTCHA:
        _request_captcha_restart("FormShield captcha shown, but automatic solving is disabled.")

    data_url = _formshield_image_data_url(driver, image)
    if not data_url:
        _request_captcha_restart("Could not capture the FormShield captcha image.")

    print("  [FormShield] Image captcha detected, solving via NopeCHA...")
    try:
        answer = nopecha_1.solve_textcaptcha(data_url, timeout=FORMSHIELD_SOLVE_TIMEOUT)
    except nopecha_1.NopechaError as exc:
        _request_captcha_restart(f"FormShield NopeCHA solve failed: {exc}")

    print(f"  [FormShield] Solved as '{answer}'; entering code.")
    answer_box.clear()
    answer_box.send_keys(answer)
    return CAPTCHA_SOLVED


_AUDIO_TOGGLE_SELECTORS = [
    (By.CSS_SELECTOR, "[aria-label*='audio' i]"),
    (By.CSS_SELECTOR, "button[id*='audio' i]"),
    (By.CSS_SELECTOR, "a[id*='audio' i]"),
    (By.CSS_SELECTOR, "[class*='audio' i][role='button']"),
    (By.CSS_SELECTOR, "img[alt*='audio' i]"),
]
_AUDIO_ANSWER_SELECTORS = [
    (By.CSS_SELECTOR, "input[id*='audio' i][type='text']"),
    (By.CSS_SELECTOR, "input[aria-label*='answer' i]"),
    (By.CSS_SELECTOR, "input[type='text']"),
]
_AUDIO_SUBMIT_SELECTORS = [
    (By.CSS_SELECTOR, "button[id*='verify' i]"),
    (By.CSS_SELECTOR, "button[type='submit']"),
    (By.XPATH, "//button[contains(translate(normalize-space(),'SUBMIT','submit'),'submit')]"),
]


def _switch_into_captcha_frame(driver):
    """If the AWS WAF widget renders inside an iframe, switch into it.

    Returns True if it switched into a frame (caller should switch back to
    default content when done), False if it stayed on the top-level document.
    """
    for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
        ident = f"{iframe.get_attribute('id') or ''} {iframe.get_attribute('src') or ''}".lower()
        if any(token in ident for token in ("captcha", "waf", "amzn")):
            driver.switch_to.frame(iframe)
            return True
    return False


def _fetch_audio_as_data_url(driver):
    """Return the currently-loaded <audio> element's src as a base64 data URL."""
    return driver.execute_async_script(
        """
        const callback = arguments[arguments.length - 1];
        const audio = document.querySelector('audio');
        if (!audio) { callback(null); return; }
        const src = audio.currentSrc || audio.src;
        if (!src) { callback(null); return; }
        fetch(src).then(r => r.blob()).then(blob => {
            const reader = new FileReader();
            reader.onloadend = () => callback(reader.result);
            reader.onerror = () => callback(null);
            reader.readAsDataURL(blob);
        }).catch(() => callback(null));
        """
    )


def solve_aws_waf(driver, wait):
    """Handle the AWS WAF "Begin" challenge via its audio accessibility option.

    NopeCHA has no visual solver for AWS WAF's puzzle-piece challenge, only a
    speech-to-text solver for its audio alternative, so this: clicks Begin,
    switches to the audio challenge, downloads the clip, solves it via
    NopeCHA, and types the answer back in.

    Returns one of ``CAPTCHA_NONE`` / ``CAPTCHA_SOLVED``.
    Raises ``CaptchaRestartRequested`` when the captcha cannot be cleared.
    """
    begin = _first_present(driver, [
        (By.ID, "amzn-captcha-verify-button"),
        (By.CSS_SELECTOR, "button.amzn-captcha-verify-button"),
        (By.CSS_SELECTOR, ".amzn-captcha-state-container button"),
    ])
    if begin is None:
        return CAPTCHA_NONE

    print("  [AWS WAF] Challenge detected, clicking Begin...")

    if not AUTO_CAPTCHA:
        _request_captcha_restart("AWS WAF captcha shown, but automatic solving is disabled.")

    try:
        begin.click()
    except Exception:
        pass
    time.sleep(2)

    switched = _switch_into_captcha_frame(driver)
    try:
        audio_toggle = _first_present(driver, _AUDIO_TOGGLE_SELECTORS)
        if audio_toggle is None:
            _request_captcha_restart("AWS WAF widget opened, but no audio-challenge toggle was found.")

        try:
            audio_toggle.click()
        except Exception:
            driver.execute_script("arguments[0].click();", audio_toggle)
        time.sleep(1.5)

        data_url = _fetch_audio_as_data_url(driver)
        if not data_url:
            _request_captcha_restart("AWS WAF audio challenge opened, but no audio source was found.")

        print("  [AWS WAF] Audio challenge captured, solving via NopeCHA (can take up to a few minutes)...")
        try:
            answer = nopecha_1.solve_awscaptcha_audio(data_url)
        except nopecha_1.NopechaError as exc:
            _request_captcha_restart(f"AWS WAF NopeCHA solve failed: {exc}")

        answer_box = _first_present(driver, _AUDIO_ANSWER_SELECTORS)
        submit_button = _first_present(driver, _AUDIO_SUBMIT_SELECTORS)
        if answer_box is None or submit_button is None:
            _request_captcha_restart(
                f"AWS WAF solved audio as '{answer}', but the answer field/submit button was not found."
            )

        print(f"  [AWS WAF] Solved audio challenge as '{answer}'.")
        answer_box.clear()
        answer_box.send_keys(answer)
        try:
            submit_button.click()
        except Exception:
            driver.execute_script("arguments[0].click();", submit_button)
        time.sleep(2)
        return CAPTCHA_SOLVED
    finally:
        if switched:
            driver.switch_to.default_content()


def wait_for_page_settle(driver, timeout=12):
    """Wait until the page reaches a stable state after a navigation/postback:
    either a captcha challenge shows up or the results table renders.

    ASP.NET postbacks (e.g. after clicking Search) can take a couple of
    seconds to swap in the CAPTCHA panel, so checking for it immediately
    can race the render and wrongly conclude nothing is there.
    """
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: _first_present(d, [
                (By.ID, "amzn-captcha-verify-button"),
                (By.ID, "FormShield1_Image"),
                (By.ID, RESULTS_TABLE_ID),
            ]) is not None
        )
    except TimeoutException:
        pass


def wait_for_rate_limit_cooldown(driver, context=""):
    _maybe_sleep_for_rate_limit(context)
    if _page_looks_rate_limited(driver):
        _record_rate_limit(context)
        _maybe_sleep_for_rate_limit(context)
        return False
    return True


def click_search(driver):
    """Click the license-lookup Submit button. Returns True if clicked.

    The real control is ``<input type="submit" id="...btnLookup" value="Submit">``
    -- its onclick runs ``ClickSearchLicenses(0)`` which validates the captcha
    and posts back. Older selectors looked for the word "Search" in the id/value
    and never matched this button, so the search was never actually submitted.
    """
    button = _first_present(driver, [
        (By.CSS_SELECTOR, "input[id$='btnLookup']"),
        (By.CSS_SELECTOR, "input[name$='btnLookup']"),
        (By.CSS_SELECTOR, "input[id*='btnLookup']"),
        (By.CSS_SELECTOR, "input[type='submit'][value='Submit']"),
        (By.CSS_SELECTOR, "input[id*='btnNewSearch']"),
        (By.XPATH, "//input[@type='submit' and (contains(@value,'Submit') or contains(@value,'Search'))]"),
    ])
    if button is None:
        return False
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
    try:
        button.click()
    except Exception:
        driver.execute_script("arguments[0].click();", button)
    return True


def _find_results_table(driver):
    for by, value in [
        (By.ID, RESULTS_TABLE_ID),
        (By.CSS_SELECTOR, "table[id$='gvSearchResults']"),
        (By.CSS_SELECTOR, "table[id*='gvSearchResults']"),
    ]:
        try:
            return driver.find_element(by, value)
        except NoSuchElementException:
            continue
    return None


def results_present(driver):
    """True once the search results table/rows have rendered.

    The search form can keep showing a fresh FormShield image after a successful
    submit, so captcha presence alone is not a failure signal. Prefer result
    markup, even if Selenium does not consider the table displayed yet.
    """
    if parse_current_page(driver):
        return True
    return _find_results_table(driver) is not None


def no_results_message_present(driver):
    try:
        text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except (NoSuchElementException, StaleElementReferenceException):
        return False
    return any(token in text for token in [
        "no records",
        "no results",
        "no matching",
        "no data",
        "0 records",
    ])


def _is_stale(element):
    try:
        element.is_enabled()
        return False
    except StaleElementReferenceException:
        return True


def _wait_for_postback(driver, marker):
    if marker is None:
        time.sleep(1)
        return
    try:
        WebDriverWait(driver, POSTBACK_WAIT_SECONDS).until(
            lambda d: _is_stale(marker) or results_present(d) or no_results_message_present(d)
        )
    except TimeoutException:
        # Some ASP.NET paths update in place. Continue to outcome polling.
        pass


def wait_for_search_outcome(driver):
    """Return 'results', 'empty', or 'captcha' after a submit settles."""
    deadline = time.time() + SEARCH_OUTCOME_GRACE_SECONDS
    saw_formshield = False
    while time.time() < deadline:
        if results_present(driver):
            return "results"
        if no_results_message_present(driver):
            return "empty"
        if formshield_present(driver):
            saw_formshield = True
        else:
            return "empty"
        time.sleep(0.5)

    if results_present(driver):
        return "results"
    if no_results_message_present(driver):
        return "empty"
    return "captcha" if saw_formshield else "empty"


def _submit_marker(driver):
    return _find_formshield_image(driver) or _find_results_table(driver)


def clear_aws_waf(driver, wait, context=""):
    """Solve AWS WAF interstitials until none remain.

    The WAF widget carries its own verify button and reloads the page itself,
    so this only needs to keep solving while one is present.
    """
    label = f" ({context})" if context else ""
    last_error = None
    for attempt in range(1, CAPTCHA_MAX_ROUNDS + 1):
        try:
            if solve_aws_waf(driver, wait) == CAPTCHA_NONE:
                return
        except CaptchaRestartRequested as exc:
            last_error = exc
            if attempt >= CAPTCHA_MAX_ROUNDS:
                break
            print(f"  [AWS WAF]{label} Captcha attempt {attempt} failed ({exc}); retrying.")
            wait_for_page_settle(driver)
            continue
        wait_for_page_settle(driver)
        if attempt < CAPTCHA_MAX_ROUNDS:
            print(f"  [AWS WAF]{label} Challenge still present after attempt {attempt}; retrying.")
    detail = f": {last_error}" if last_error else ""
    _request_captcha_restart(f"AWS WAF challenge was not cleared after {CAPTCHA_MAX_ROUNDS} attempts{label}{detail}.")


def submit_search(driver, wait, context=""):
    """Enter the FormShield code (if shown), click Submit, and keep
    re-entering + resubmitting until the results table appears.

    This is the correct order for this form: the security code lives on the
    search form, so we fill it, then a single Submit carries both the search and
    the answer. A wrong/expired code re-shows the form (no results), so we retry.

    Returns True if the results table is present afterwards.
    """
    label = f" ({context})" if context else ""
    last_error = None
    for attempt in range(1, SUBMIT_MAX_ATTEMPTS + 1):
        if not wait_for_rate_limit_cooldown(driver, context):
            if not _is_retryable_rate_limit():
                return False
            continue
        try:
            enter_formshield_answer(driver)      # fill the code on the form (no submit)
        except CaptchaRestartRequested as exc:
            last_error = exc
            if attempt >= SUBMIT_MAX_ATTEMPTS:
                break
            print(f"  [submit]{label} Captcha attempt {attempt} failed ({exc}); retrying.")
            wait_for_page_settle(driver)
            continue
        marker = _submit_marker(driver)
        if not click_search(driver):
            _request_captcha_restart(f"Submit button not found{label}.")
        _wait_for_postback(driver, marker)
        if not _wait_for_page_health(driver, wait, context):
            if not _is_retryable_rate_limit():
                return False
            continue
        clear_aws_waf(driver, wait, context)     # AWS WAF interstitial after submit

        outcome = wait_for_search_outcome(driver)
        if outcome == "results":
            _reset_rate_limit_state()
            return True
        if outcome == "empty":
            _reset_rate_limit_state()
            return False
        if attempt < SUBMIT_MAX_ATTEMPTS:
            print(f"  [submit]{label} No result table after captcha attempt {attempt}; retrying.")
    if results_present(driver):
        return True
    detail = f": {last_error}" if last_error else ""
    _request_captcha_restart(f"Captcha was not solved after {SUBMIT_MAX_ATTEMPTS} attempts{label}{detail}.")


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
    zip_codes = load_zip_codes()
    if not zip_codes:
        raise RuntimeError(f"Could not load any ZIP codes from {ZIP_CODES_CSV}.")

    checkpoint = load_checkpoint()
    keyword_index = checkpoint.get("keyword_index", 0)
    state_index = checkpoint.get("state_index", 0)
    zip_index = checkpoint.get("zip_index", 0)
    resume_page = checkpoint.get("page", 1)
    states = state_options(None)

    if checkpoint:
        print(
            "[checkpoint] Resuming at "
            f"keyword #{keyword_index}, state #{state_index}, zip #{zip_index}, page {resume_page}."
        )

    while keyword_index < len(KEYWORDS):
        driver = build_driver()
        wait = WebDriverWait(driver, WAIT_SECONDS)
        try:
            if not _open_url_with_backoff(driver, wait, URL, context="initial load"):
                raise RuntimeError("Could not load the Colorado search page without hitting rate limits.")

            # Clear the AWS WAF challenge guarding the initial page load.
            clear_aws_waf(driver, wait, context="initial load")

            while keyword_index < len(KEYWORDS):
                keyword = KEYWORDS[keyword_index]
                if state_index >= len(states):
                    keyword_index += 1
                    state_index = 0
                    zip_index = 0
                    continue

                value, state_name = states[state_index]
                if not select_keyword(driver, keyword):
                    print(f"No license type option matched '{keyword}', skipping.")
                    keyword_index += 1
                    state_index = 0
                    zip_index = 0
                    resume_page = 1
                    continue
                select_state(driver, value)

                while zip_index < len(zip_codes):
                    zip_code = zip_codes[zip_index]
                    start_page = resume_page if resume_page > 1 else 1
                    save_checkpoint(keyword_index, state_index, zip_index, start_page)
                    _search_pause()
                    if not fill_zip_code(driver, zip_code):
                        print(f"Could not find the ZIP code field for {keyword} | {state_name} | {zip_code}, skipping.")
                        zip_index += 1
                        resume_page = 1
                        continue

                    # Correct order: fields are set, now enter the security code and
                    # click Submit (submit_search handles the captcha + resubmits).
                    if start_page > 1:
                        print(f"[{keyword} | {state_name} | {zip_code}] resuming from page {start_page}...")
                    else:
                        print(f"[{keyword} | {state_name} | {zip_code}] searching...")
                    if not submit_search(driver, wait, context=f"{keyword} | {state_name} | {zip_code}"):
                        print(f"  No results for {keyword} | {state_name} | {zip_code}.")
                        close_modal(driver)
                        zip_index += 1
                        resume_page = 1
                        continue

                    # Scrape + save page by page so nothing is lost mid-pagination.
                    total = scrape_and_save(
                        driver,
                        wait,
                        keyword,
                        state_name,
                        keyword_index,
                        state_index,
                        zip_index,
                        start_page=start_page,
                    )
                    print(f"  Done {keyword} | {state_name} | {zip_code}: {total} rows saved.")

                    close_modal(driver)
                    zip_index += 1
                    resume_page = 1
                    if zip_index < len(zip_codes):
                        save_checkpoint(keyword_index, state_index, zip_index, 1, reason="next_zip")
                    else:
                        clear_checkpoint()

                state_index += 1
                zip_index = 0
                resume_page = 1
                if state_index < len(states):
                    save_checkpoint(keyword_index, state_index, zip_index, 1, reason="next_state")
                else:
                    clear_checkpoint()
        except CaptchaRestartRequested as exc:
            if keyword_index < len(KEYWORDS) and state_index < len(states) and zip_index < len(zip_codes):
                current = f"{KEYWORDS[keyword_index]} | {states[state_index][1]} | {zip_codes[zip_index]}"
            else:
                current = "current lead"
            print(f"  [captcha] {exc}")
            print(f"  [restart] Restarting browser and resuming at {current}.")
        finally:
            driver.quit()


if __name__ == "__main__":
    main()
