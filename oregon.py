import csv
import json
import os
import re
import string
import time
from urllib.parse import urljoin
import nopecha_1
import requests
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


SEARCH_URL = "https://osbn.boardsofnursing.org/licenselookup/"
OUTPUT_FILE = "oregon.csv"
CHECKPOINT_FILE = "oregon_checkpoint.json"
COLLECTED_FILE = "oregon_collected.json"
WAIT_SECONDS = 40
PAGE_LOAD_SLEEP = 1.2
RESULT_SETTLE_SECONDS = 1.5
# How long the initial page load may block while the operator clears the
# Imperva/hCaptcha bot gate by hand (the search form only renders afterwards).
FORM_LOAD_TIMEOUT = 300
# How long to wait for the reCAPTCHA "I'm not a robot" token to appear after we
# click the checkbox -- covers both an instant auto-pass and a manual solve.
RECAPTCHA_SOLVE_TIMEOUT = 30
# Token solves that are going nowhere should fail faster than the full manual
# timeout so the scraper can fall back/retry without burning several minutes.
RECAPTCHA_AUTO_SOLVE_TIMEOUT = 60
# Audio solves are faster than token solves, but they still need enough time for
# the transcription round-trip.
RECAPTCHA_AUDIO_SOLVE_TIMEOUT = 30
RECAPTCHA_AUDIO_MAX_ATTEMPTS = 3
# How long to let the reCAPTCHA widget lazy-render before concluding a page has
# no captcha. Without this, clicking Search the instant the form appears can race
# the widget and submit the search with no token at all.
RECAPTCHA_RENDER_TIMEOUT = 15
HEADLESS = False
CHROMEDRIVER_MAJOR = 150

# The site supports license-type filtering. We read the current options from the
# page, but keep a fallback list so the scraper still works if the dropdown is
# temporarily unavailable during load.
LICENSE_TYPE_FALLBACKS = [
    "APRN-CNS",
    "APRN-CRNA",
    "APRN-NP",
    "CMA",
    "CNA",
    "DE-CNA",
    "LPN",
    "LPN-E",
    "NI",
    "RN",
    "RN-E",
]

FIELDNAMES = [
    "State",
    "Search Last Name",
    "Search First Name",
    "License Type Filter",
    "First Name",
    "Middle Initial",
    "Last Name",
    "Name on License",
    "License/Certificate Type",
    "License/Certificate Number",
    "License Status",
    "Original Issue Date",
    "Current Issue Date",
    "Current Expiration Date",
    "Detail URL",
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_key(text: str) -> str:
    return normalize(text).casefold()


def build_driver() -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--start-minimized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")

    kwargs = {"options": options, "headless": HEADLESS}
    if CHROMEDRIVER_MAJOR:
        kwargs["version_main"] = CHROMEDRIVER_MAJOR

    driver = uc.Chrome(**kwargs)
    driver.set_page_load_timeout(120)
    driver.set_script_timeout(120)
    return driver


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_page(driver):
    """Load the search page and block until the search form is actually present.

    The site sits behind an Imperva/hCaptcha bot gate that must be cleared before
    the form renders. Instead of a fixed sleep we poll for the Last Name field, so
    the operator has time to solve that gate by hand and we continue the instant
    it clears -- no arbitrary waiting."""
    driver.get(SEARCH_URL)
    print(
        "Page opened. If a bot-check / hCaptcha appears, solve it by hand -- the "
        "scraper resumes automatically once the search form loads."
    )
    form_wait = WebDriverWait(driver, FORM_LOAD_TIMEOUT)
    form_wait.until(EC.presence_of_element_located((By.ID, "LicenseSearch_NameSearchInput_LastName")))
    time.sleep(PAGE_LOAD_SLEEP)


def generate_prefixes():
    for letter in string.ascii_uppercase:
        yield letter


def set_text_input(driver, wait, element_id: str, value: str):
    last_error = None
    for _ in range(4):
        try:
            element = wait.until(EC.presence_of_element_located((By.ID, element_id)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            element.clear()
            element.send_keys(value)
            return
        except (StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error


def get_license_type_options(driver, wait):
    try:
        select = Select(wait.until(EC.presence_of_element_located((By.ID, "LicenseSearch_NameSearchInput_LicenseTypeId"))))
        options = []
        for option in select.options:
            text = normalize(option.text)
            value = (option.get_attribute("value") or "").strip()
            if not text or text.lower() == "select" or not value:
                continue
            options.append((value, text))
        return options or [(text, text) for text in LICENSE_TYPE_FALLBACKS]
    except TimeoutException:
        return [(text, text) for text in LICENSE_TYPE_FALLBACKS]


def select_license_type(driver, wait, license_type_value: str):
    last_error = None
    for _ in range(4):
        try:
            select = Select(wait.until(EC.presence_of_element_located((By.ID, "LicenseSearch_NameSearchInput_LicenseTypeId"))))
            select.select_by_value(str(license_type_value))
            time.sleep(0.3)
            return
        except (StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error:
        raise last_error


def ensure_maiden_search_unchecked(driver):
    """The 'Search on maiden or other names' checkbox must stay unchecked so it is
    never the element our search click lands on, and so results are not filtered."""
    try:
        checkbox = driver.find_element(By.ID, "LicenseSearch_NameSearchInput_SearchMaidenOther")
        if checkbox.is_selected():
            driver.execute_script("arguments[0].checked = false;", checkbox)
    except (NoSuchElementException, WebDriverException):
        pass


def recaptcha_widget_present(driver):
    """True once the reCAPTCHA v2 widget has rendered on the page (its hidden
    response textarea exists). The widget renders lazily, so this can be False
    for the first moments after load."""
    return bool(
        driver.execute_script(
            "return !!document.getElementById('g-recaptcha-response');"
        )
    )


def recaptcha_token_present(driver):
    """True when the reCAPTCHA has been passed and a response token is set."""
    return bool(
        driver.execute_script(
            "var t = document.getElementById('g-recaptcha-response');"
            "return t && t.value && t.value.length > 0;"
        )
    )


def get_recaptcha_sitekey(driver):
    """Return the page's reCAPTCHA v2 public sitekey, or "" if not found.

    Reads the ``data-sitekey`` attribute of the widget container first, then
    falls back to parsing the ``k=`` parameter out of the anchor iframe src."""
    key = driver.execute_script(
        "var el = document.querySelector('[data-sitekey]');"
        "return el ? el.getAttribute('data-sitekey') : '';"
    )
    if key:
        return key.strip()
    try:
        anchor = driver.find_element(
            By.CSS_SELECTOR, "iframe[src*='/recaptcha/'][src*='anchor']"
        )
        src = anchor.get_attribute("src") or ""
    except (NoSuchElementException, WebDriverException):
        return ""
    match = re.search(r"[?&]k=([^&]+)", src)
    return match.group(1) if match else ""


def _recaptcha_anchor_src(driver):
    try:
        anchor = driver.find_element(
            By.CSS_SELECTOR, "iframe[src*='/recaptcha/'][src*='anchor']"
        )
        return anchor.get_attribute("src") or ""
    except (NoSuchElementException, WebDriverException):
        return ""


def _first_present(driver, locators):
    """Return the first displayed element matching any (By, value) locator."""
    for by, value in locators:
        try:
            el = driver.find_element(by, value)
            if el.is_displayed():
                return el
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            continue
    return None


def _query_param(url: str, name: str) -> str:
    if not url:
        return ""
    match = re.search(rf"[?&]{re.escape(name)}=([^&]+)", url)
    return match.group(1) if match else ""


def get_recaptcha_context(driver):
    """Collect extra reCAPTCHA metadata that makes token solves more reliable."""
    anchor_src = _recaptcha_anchor_src(driver)
    data_s = driver.execute_script(
        "var el = document.querySelector('[data-s]');"
        "return el ? el.getAttribute('data-s') : '';"
    ) or ""
    if not data_s:
        data_s = _query_param(anchor_src, "s")

    theme = driver.execute_script(
        "var el = document.querySelector('[data-theme]');"
        "return el ? el.getAttribute('data-theme') : '';"
    ) or ""
    if not theme:
        theme = _query_param(anchor_src, "theme")

    enterprise = bool(
        driver.execute_script(
            "return !!document.querySelector('script[src*=\"recaptcha/enterprise.js\"], iframe[src*=\"recaptcha/enterprise/\"]');"
        )
    )

    try:
        useragent = driver.execute_script("return navigator.userAgent || '';") or ""
    except WebDriverException:
        useragent = ""

    cookies = []
    try:
        for item in driver.get_cookies():
            if not item.get("name"):
                continue
            cookie = {
                "name": item["name"],
                "value": item.get("value", ""),
                "domain": item.get("domain", ""),
                "path": item.get("path", "/"),
                "hostOnly": not str(item.get("domain", "")).startswith("."),
                "httpOnly": bool(item.get("httpOnly", False)),
                "secure": bool(item.get("secure", False)),
                "session": "expiry" not in item,
            }
            if "expiry" in item:
                cookie["expirationDate"] = int(item["expiry"])
            cookies.append(cookie)
    except WebDriverException:
        cookies = []

    data = {}
    if data_s:
        data["s"] = data_s
    if theme:
        data["theme"] = theme

    return {
        "data": data or None,
        "enterprise": enterprise,
        "useragent": useragent or None,
        "cookie": cookies or None,
    }


def inject_recaptcha_token(driver, token):
    """Write a solved reCAPTCHA token into the page and fire its callback.

    Sets the hidden ``#g-recaptcha-response`` textarea to the NopeCHA-supplied
    token and then walks ``___grecaptcha_cfg`` to invoke the widget's success
    callback, which is what most forms listen on. If the callback cannot be
    located the token in the textarea is still submitted with the form."""
    driver.execute_script(
        """
        var token = arguments[0];
        var boxes = Array.from(document.querySelectorAll(
          '#g-recaptcha-response, textarea[name="g-recaptcha-response"]'
        ));
        boxes.forEach(function(box) {
          box.value = token;
          box.innerHTML = token;
          box.dispatchEvent(new Event('input', { bubbles: true }));
          box.dispatchEvent(new Event('change', { bubbles: true }));
        });
        try {
          var cfg = window.___grecaptcha_cfg;
          if (cfg && cfg.clients) {
            for (var cid in cfg.clients) {
              var client = cfg.clients[cid];
              for (var ck in client) {
                var obj = client[ck];
                if (obj && typeof obj === 'object') {
                  for (var k in obj) {
                    var leaf = obj[k];
                    if (leaf && typeof leaf.callback === 'function') {
                      leaf.callback(token);
                    }
                    if (leaf && typeof leaf.promiseCallback === 'function') {
                      leaf.promiseCallback(token);
                    }
                  }
                }
              }
            }
          }
        } catch (e) { /* token in textarea is enough for form submit */ }
        """,
        token,
    )


def recaptcha_challenge_present(driver):
    try:
        return bool(
            _first_present(driver, [
                (By.CSS_SELECTOR, "iframe[src*='/recaptcha/'][src*='bframe']"),
                (By.CSS_SELECTOR, "iframe[title*='challenge' i]"),
            ])
        )
    except WebDriverException:
        return False


def solve_recaptcha_via_nopecha(driver):
    """Attempt to auto-solve the page's reCAPTCHA v2 through NopeCHA.

    Returns True if a token was fetched and injected (and is now present),
    False on any failure so the caller can fall back to the manual flow."""
    sitekey = get_recaptcha_sitekey(driver)
    if not sitekey:
        return False
    context = get_recaptcha_context(driver)
    print("[captcha] solving reCAPTCHA via NopeCHA...")
    try:
        token = nopecha_1.solve_recaptcha_v2(
            sitekey,
            driver.current_url,
            data=context["data"],
            enterprise=context["enterprise"],
            useragent=context["useragent"],
            cookie=context["cookie"],
            timeout=RECAPTCHA_AUTO_SOLVE_TIMEOUT,
        )
    except nopecha_1.NopechaError as exc:
        print(f"[captcha] NopeCHA failed ({exc}); falling back to manual.")
        return False
    if not token:
        return False
    inject_recaptcha_token(driver, token)
    return recaptcha_token_present(driver)


def click_recaptcha_checkbox(driver):
    """Click the 'I'm not a robot' checkbox inside the reCAPTCHA anchor iframe.

    On a trusted (warmed-profile) session this alone passes the challenge; when
    an image/audio challenge is raised instead, the operator solves it by hand
    and ``ensure_recaptcha_solved`` picks up the resulting token."""
    try:
        anchor = driver.find_element(
            By.CSS_SELECTOR, "iframe[src*='/recaptcha/'][src*='anchor']"
        )
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


def _switch_to_recaptcha_challenge_frame(driver):
    driver.switch_to.default_content()
    frame = _first_present(driver, [
        (By.CSS_SELECTOR, "iframe[src*='/recaptcha/'][src*='bframe']"),
        (By.CSS_SELECTOR, "iframe[title*='challenge' i]"),
    ])
    if frame is None:
        return False
    driver.switch_to.frame(frame)
    return True


def _fetch_recaptcha_audio_as_data_url(driver):
    return driver.execute_async_script(
        """
        const callback = arguments[arguments.length - 1];
        const audio = document.querySelector('#audio-source');
        if (!audio) { callback(null); return; }
        const src = audio.currentSrc || audio.src;
        if (!src) { callback(null); return; }
        fetch(src, {cache: 'no-store'}).then(r => r.blob()).then(blob => {
            const reader = new FileReader();
            reader.onloadend = () => callback(reader.result);
            reader.onerror = () => callback(null);
            reader.readAsDataURL(blob);
        }).catch(() => callback(null));
        """
    )


def solve_recaptcha_audio_challenge(driver):
    """Open reCAPTCHA's audio challenge, solve it via NopeCHA, and submit it."""
    if not _switch_to_recaptcha_challenge_frame(driver):
        return False
    try:
        audio_button = _first_present(driver, [
            (By.ID, "recaptcha-audio-button"),
            (By.CSS_SELECTOR, "button[id*='audio']"),
            (By.CSS_SELECTOR, "[aria-label*='audio' i]"),
        ])
        if audio_button is not None:
            try:
                audio_button.click()
            except WebDriverException:
                driver.execute_script("arguments[0].click();", audio_button)
            time.sleep(1)

        for attempt in range(1, RECAPTCHA_AUDIO_MAX_ATTEMPTS + 1):
            data_url = _fetch_recaptcha_audio_as_data_url(driver)
            if not data_url:
                reload_button = _first_present(driver, [
                    (By.ID, "recaptcha-reload-button"),
                    (By.CSS_SELECTOR, "button[id*='reload']"),
                ])
                if reload_button is not None:
                    try:
                        reload_button.click()
                    except WebDriverException:
                        driver.execute_script("arguments[0].click();", reload_button)
                    time.sleep(1)
                    continue
                return False

            print(f"[captcha] solving reCAPTCHA audio via NopeCHA (attempt {attempt})...")
            try:
                answer = nopecha_1.solve_recaptcha_audio(
                    data_url,
                    timeout=RECAPTCHA_AUDIO_SOLVE_TIMEOUT,
                )
            except nopecha_1.NopechaError as exc:
                print(f"[captcha] reCAPTCHA audio solve failed ({exc})")
                answer = ""

            if not answer:
                reload_button = _first_present(driver, [
                    (By.ID, "recaptcha-reload-button"),
                    (By.CSS_SELECTOR, "button[id*='reload']"),
                ])
                if reload_button is not None and attempt < RECAPTCHA_AUDIO_MAX_ATTEMPTS:
                    try:
                        reload_button.click()
                    except WebDriverException:
                        driver.execute_script("arguments[0].click();", reload_button)
                    time.sleep(1)
                    continue
                return False

            input_box = _first_present(driver, [
                (By.ID, "audio-response"),
                (By.CSS_SELECTOR, "input[type='text']"),
            ])
            verify_button = _first_present(driver, [
                (By.ID, "recaptcha-verify-button"),
                (By.CSS_SELECTOR, "button[id*='verify']"),
            ])
            if input_box is None or verify_button is None:
                return False

            input_box.clear()
            input_box.send_keys(answer)
            try:
                verify_button.click()
            except WebDriverException:
                driver.execute_script("arguments[0].click();", verify_button)
            time.sleep(2)

            driver.switch_to.default_content()
            if recaptcha_token_present(driver):
                return True
            if not recaptcha_challenge_present(driver):
                return recaptcha_token_present(driver)
            if attempt >= RECAPTCHA_AUDIO_MAX_ATTEMPTS:
                return False
            if not _switch_to_recaptcha_challenge_frame(driver):
                return False
        return False
    finally:
        driver.switch_to.default_content()


def ensure_recaptcha_solved(driver, timeout=RECAPTCHA_SOLVE_TIMEOUT):
    """Guarantee a reCAPTCHA token is present before the Search click.

    No-op when the page has no reCAPTCHA or it is already solved. Otherwise it
    first tries to auto-solve via NopeCHA; if that fails it clicks the checkbox
    and polls until the token appears -- covering an instant auto-pass or a
    manual challenge solve -- with no fixed sleeps. Returns True once a token
    exists, False if the timeout elapses first."""
    # The widget renders lazily; give it a moment to appear before concluding the
    # page is captcha-free, otherwise we would click Search with no token at all.
    render_deadline = time.time() + RECAPTCHA_RENDER_TIMEOUT
    while not recaptcha_widget_present(driver) and time.time() < render_deadline:
        time.sleep(0.3)
    if not recaptcha_widget_present(driver):
        return True
    if recaptcha_token_present(driver):
        return True

    click_recaptcha_checkbox(driver)

    deadline = time.time() + timeout
    audio_announced = False
    while time.time() < deadline:
        if recaptcha_token_present(driver):
            return True
        if recaptcha_challenge_present(driver):
            if not audio_announced:
                print("[captcha] reCAPTCHA challenge opened; switching to audio solve.")
                audio_announced = True
            if solve_recaptcha_audio_challenge(driver):
                return True
        time.sleep(1)
    print("[captcha] reCAPTCHA not solved within timeout")
    return False


def click_search(driver, wait):
    ensure_maiden_search_unchecked(driver)
    if not ensure_recaptcha_solved(driver):
        raise RuntimeError("reCAPTCHA was not solved; aborting submit")
    try:
        # Target the real Search button strictly by its id so we can never hit the
        # maiden-name checkbox (which also contains the word "Search").
        wait.until(EC.presence_of_element_located((By.ID, "btnSearch")))
        button = wait.until(EC.element_to_be_clickable((By.ID, "btnSearch")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
        try:
            button.click()
        except (ElementClickInterceptedException, WebDriverException):
            driver.execute_script("arguments[0].click();", button)
        time.sleep(RESULT_SETTLE_SECONDS)
    except Exception as e:
        raise RuntimeError("Failed to click Search button") from e


def wait_for_results_or_empty(driver, wait):
    def loaded(current_driver):
        try:
            report_links = current_driver.find_elements(By.CSS_SELECTOR, "a.btn.btn-primary.mb-2[href*='/Home/Report?o=']")
            if report_links:
                return True
            page_text = normalize(current_driver.find_element(By.TAG_NAME, "body").text)
            return any(
                phrase in page_text.lower()
                for phrase in [
                    "no results",
                    "no records",
                    "no matching",
                    "no license",
                ]
            )
        except WebDriverException:
            return False

    try:
        wait.until(loaded)
    except TimeoutException:
        pass
    time.sleep(0.8)


def extract_result_cards(driver):
    cards = []
    anchors = driver.find_elements(By.CSS_SELECTOR, "a.btn.btn-primary.mb-2[href*='/Home/Report?o=']")
    seen_urls = set()

    for anchor in anchors:
        try:
            href = (anchor.get_attribute("href") or "").strip()
            if not href:
                continue
            detail_url = urljoin(driver.current_url, href)
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            card = anchor.find_element(By.XPATH, "./ancestor::div[contains(@class,'card-body')][1]")
            rows = card.find_elements(By.CSS_SELECTOR, "table tbody tr")
            if not rows:
                continue

            row = rows[0]
            cells = [normalize(cell.text) for cell in row.find_elements(By.TAG_NAME, "td")]
            if len(cells) < 5:
                continue

            full_name = ""
            try:
                full_name = normalize(card.find_element(By.CSS_SELECTOR, "h3 span.fw-bold.fs-3.wrap-all-text").text)
            except NoSuchElementException:
                pass
            if full_name and " [NCSBN ID:" in full_name:
                full_name = full_name.split(" [NCSBN ID:", 1)[0].strip()

            cards.append(
                {
                    "First Name": cells[0],
                    "Middle Initial": cells[1],
                    "Last Name": cells[2],
                    "License/Certificate Type": cells[3],
                    "License/Certificate Number": cells[4],
                    "Detail URL": detail_url,
                    "Name on License": full_name,
                }
            )
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            continue

    return cards


def extract_label_value_cards(driver):
    records = []
    cards = driver.find_elements(By.CSS_SELECTOR, "div.card.mb-3")

    for card in cards:
        try:
            fields = {}
            for item in card.find_elements(By.CSS_SELECTOR, "div[role='listitem']"):
                try:
                    strong = item.find_element(By.TAG_NAME, "strong").text
                except NoSuchElementException:
                    continue

                label = normalize_key(strong).rstrip(":")
                value_parts = []
                for child in item.find_elements(By.XPATH, "./*"):
                    if child.tag_name.lower() == "strong":
                        continue
                    text = normalize(child.text)
                    if text:
                        value_parts.append(text)
                value = normalize(" ".join(value_parts))
                if label:
                    fields[label] = value

            if not fields:
                continue

            record = {
                "Name on License": fields.get("name on license", ""),
                "License/Certificate Type": fields.get("license/certificate type", ""),
                "License/Certificate Number": fields.get("license/certificate number", ""),
                "License Status": fields.get("license status", ""),
                "Original Issue Date": fields.get("original issue date", ""),
                "Current Issue Date": fields.get("current issue date", ""),
                "Current Expiration Date": fields.get("current expiration date", ""),
            }
            if any(record.values()):
                records.append(record)
        except (NoSuchElementException, StaleElementReferenceException, WebDriverException):
            continue

    return records


def collect_search_results(driver, wait, search_last, search_first, license_type_value, license_type_label):
    """Phase 1: run ONE search in place (no page reload) and return the summary
    cards. The search page is never navigated away from, so the CAPTCHA challenge
    is not re-triggered between searches."""
    set_text_input(driver, wait, "LicenseSearch_NameSearchInput_LastName", search_last)
    set_text_input(driver, wait, "LicenseSearch_NameSearchInput_FirstName", search_first)
    select_license_type(driver, wait, license_type_value)
    click_search(driver, wait)
    wait_for_results_or_empty(driver, wait)

    results = []
    for card in extract_result_cards(driver):
        card["Search Last Name"] = search_last
        card["Search First Name"] = search_first
        card["License Type Filter"] = license_type_label
        results.append(card)
    return results


def enrich_with_detail(driver, wait, result):
    """Phase 2: navigate the SAME tab to a detail page and return the full row(s).
    Falls back to the summary data if the detail page cannot be read."""
    detail_url = result["Detail URL"]
    base = {
        "State": "Oregon",
        "Search Last Name": result.get("Search Last Name", ""),
        "Search First Name": result.get("Search First Name", ""),
        "License Type Filter": result.get("License Type Filter", ""),
        "First Name": result.get("First Name", ""),
        "Middle Initial": result.get("Middle Initial", ""),
        "Last Name": result.get("Last Name", ""),
        "Detail URL": detail_url,
    }

    detail_records = [{}]
    try:
        driver.get(detail_url)
        wait_for_page_ready(driver, wait)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.card.mb-3")))
        except TimeoutException:
            pass
        time.sleep(0.6)
        detail_records = extract_label_value_cards(driver) or [{}]
    except WebDriverException:
        detail_records = [{}]

    records = []
    for detail in detail_records:
        record = dict(base)
        record.update(
            {
                "Name on License": detail.get("Name on License") or result.get("Name on License", ""),
                "License/Certificate Type": detail.get("License/Certificate Type") or result.get("License/Certificate Type", ""),
                "License/Certificate Number": detail.get("License/Certificate Number") or result.get("License/Certificate Number", ""),
                "License Status": detail.get("License Status", ""),
                "Original Issue Date": detail.get("Original Issue Date", ""),
                "Current Issue Date": detail.get("Current Issue Date", ""),
                "Current Expiration Date": detail.get("Current Expiration Date", ""),
            }
        )
        records.append(record)
    return records


def append_csv(path: str, records):
    if not records:
        return

    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def load_checkpoint():
    if not os.path.exists(CHECKPOINT_FILE):
        return {"phase": "search", "search_index": 0, "detail_index": 0}
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"phase": "search", "search_index": 0, "detail_index": 0}
    return {
        "phase": data.get("phase", "search"),
        "search_index": int(data.get("search_index", 0) or 0),
        "detail_index": int(data.get("detail_index", 0) or 0),
    }


def save_checkpoint(*, phase: str, search_index: int, detail_index: int):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "phase": phase,
                "search_index": search_index,
                "detail_index": detail_index,
            },
            fh,
            indent=2,
        )


def load_collected_results():
    if not os.path.exists(COLLECTED_FILE):
        return []
    try:
        with open(COLLECTED_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_collected_results(results):
    with open(COLLECTED_FILE, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)


def create_browser_session():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    open_search_page(driver)
    return driver, wait


def run():
    checkpoint = load_checkpoint()
    if checkpoint["phase"] == "done":
        print(
            f"Checkpoint {CHECKPOINT_FILE} shows the Oregon scrape already completed. "
            f"Delete {CHECKPOINT_FILE} and {COLLECTED_FILE} to restart from scratch."
        )
        return

    driver, wait = create_browser_session()
    collected = load_collected_results()

    try:
        license_types = get_license_type_options(driver, wait)
        prefixes = list(generate_prefixes())
        search_tasks = [
            (prefix, license_type_value, license_type_label)
            for prefix in prefixes
            for license_type_value, license_type_label in license_types
        ]

        # ----- Phase 1: all searches on the single already-loaded page -----
        # Progress is checkpointed after every search so a rerun resumes from the
        # next unfinished (prefix, license type) pair instead of starting at A.
        if checkpoint["phase"] == "search":
            start_index = min(checkpoint["search_index"], len(search_tasks))
            if start_index:
                print(f"Resuming search phase at task {start_index + 1}/{len(search_tasks)}.")
            for idx in range(start_index, len(search_tasks)):
                prefix, license_type_value, license_type_label = search_tasks[idx]
                try:
                    results = collect_search_results(
                        driver=driver,
                        wait=wait,
                        search_last=prefix,
                        search_first=prefix,
                        license_type_value=license_type_value,
                        license_type_label=license_type_label,
                    )
                    collected.extend(results)
                    save_collected_results(collected)
                    print(f"[search {prefix} | {license_type_label}] found {len(results)} results")
                except Exception as exc:
                    print(f"[search {prefix} | {license_type_label}] failed: {exc}")
                finally:
                    save_checkpoint(
                        phase="search",
                        search_index=idx + 1,
                        detail_index=0,
                    )

            checkpoint = {"phase": "detail", "search_index": len(search_tasks), "detail_index": 0}
            save_checkpoint(
                phase="detail",
                search_index=len(search_tasks),
                detail_index=0,
            )

        print(f"Phase 1 complete: {len(collected)} results collected. Fetching detail pages...")

        # ----- Phase 2: visit each detail page in the SAME tab and write rows -----
        start_detail_index = min(checkpoint.get("detail_index", 0), len(collected))
        if start_detail_index:
            print(f"Resuming detail phase at record {start_detail_index + 1}/{len(collected)}.")
        seen_urls = {
            result.get("Detail URL", "")
            for result in collected[:start_detail_index]
            if result.get("Detail URL")
        }
        total = len(collected)
        for idx in range(start_detail_index, total):
            result = collected[idx]
            detail_url = result.get("Detail URL", "")
            if not detail_url or detail_url in seen_urls:
                save_checkpoint(
                    phase="detail",
                    search_index=len(search_tasks),
                    detail_index=idx + 1,
                )
                continue
            seen_urls.add(detail_url)

            try:
                records = enrich_with_detail(driver, wait, result)
                append_csv(OUTPUT_FILE, records)
                print(f"[detail {idx + 1}/{total}] saved {len(records)} rows")
            except Exception as exc:
                print(f"[detail {idx + 1}/{total}] failed: {exc}")
            finally:
                save_checkpoint(
                    phase="detail",
                    search_index=len(search_tasks),
                    detail_index=idx + 1,
                )
        save_checkpoint(
            phase="done",
            search_index=len(search_tasks),
            detail_index=total,
        )
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
