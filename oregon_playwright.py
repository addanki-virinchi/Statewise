"""
Oregon Board of Nursing license lookup scraper — Playwright edition.

Ported from oregon.py (undetected-chromedriver/Selenium) to Playwright:

  * Real Chrome (channel="chrome") launched through p.chromium.launch(proxy=...)
    with a Webshare proxy from proxy_pool, round-robined across runs.
  * Same two-phase flow (collect search-result cards, then enrich each with
    its detail page) with the same checkpoint/collected-results JSON files,
    so a scrape in progress under the old script and this one share state.
  * reCAPTCHA v2 handling (NopeCHA token solve -> manual checkbox -> NopeCHA
    audio-challenge solve) ported to Playwright's Frame API in place of
    Selenium's switch_to.frame.
  * Typing/clicking go through playwright_stealth_utils' human_type/human_click.
  * After repeated per-task failures (usually a bot wall), the browser is
    torn down and rebuilt with the next proxy in the pool instead of grinding
    on the same blocked IP.
"""

import csv
import json
import os
import re
import string
import time
from urllib.parse import urljoin

import nopecha_1
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

import proxy_pool
from playwright_stealth_utils import human_pause, human_type, new_stealth_browser

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
RECAPTCHA_SOLVE_TIMEOUT = 30
RECAPTCHA_AUTO_SOLVE_TIMEOUT = 60
RECAPTCHA_AUDIO_SOLVE_TIMEOUT = 30
RECAPTCHA_AUDIO_MAX_ATTEMPTS = 3
RECAPTCHA_RENDER_TIMEOUT = 15
HEADLESS = False

USE_PROXY = True
CONSECUTIVE_FAILURE_ROTATE_THRESHOLD = 3

LICENSE_TYPE_FALLBACKS = [
    "APRN-CNS", "APRN-CRNA", "APRN-NP", "CMA", "CNA", "DE-CNA",
    "LPN", "LPN-E", "NI", "RN", "RN-E",
]

FIELDNAMES = [
    "State", "Search Last Name", "Search First Name", "License Type Filter",
    "First Name", "Middle Initial", "Last Name", "Name on License",
    "License/Certificate Type", "License/Certificate Number", "License Status",
    "Original Issue Date", "Current Issue Date", "Current Expiration Date",
    "Detail URL",
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def normalize_key(text: str) -> str:
    return normalize(text).casefold()


def generate_prefixes():
    for letter in string.ascii_uppercase:
        yield letter


def _query_param(url: str, name: str) -> str:
    if not url:
        return ""
    match = re.search(rf"[?&]{re.escape(name)}=([^&]+)", url)
    return match.group(1) if match else ""


# ─── PAGE / FORM HELPERS ──────────────────────────────────────────────────────

def open_search_page(page):
    """Load the search page and block until the search form is actually present.

    The site sits behind an Imperva/hCaptcha bot gate that must be cleared
    before the form renders. Instead of a fixed sleep we poll for the Last
    Name field, so the operator has time to solve that gate by hand and the
    scraper continues the instant it clears."""
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    print(
        "Page opened. If a bot-check / hCaptcha appears, solve it by hand -- the "
        "scraper resumes automatically once the search form loads."
    )
    page.wait_for_selector(
        "#LicenseSearch_NameSearchInput_LastName", timeout=FORM_LOAD_TIMEOUT * 1000
    )
    human_pause(PAGE_LOAD_SLEEP, PAGE_LOAD_SLEEP + 0.4)


def set_text_input(page, element_id: str, value: str):
    locator = page.locator(f"#{element_id}")
    locator.wait_for(state="visible", timeout=15000)
    locator.scroll_into_view_if_needed()
    locator.click()
    locator.fill("")
    human_type(page, value)


def get_license_type_options(page):
    try:
        page.wait_for_selector("#LicenseSearch_NameSearchInput_LicenseTypeId", timeout=WAIT_SECONDS * 1000)
    except PWTimeoutError:
        return [(text, text) for text in LICENSE_TYPE_FALLBACKS]
    raw = page.eval_on_selector_all(
        "#LicenseSearch_NameSearchInput_LicenseTypeId option",
        "els => els.map(o => ({value: o.value, text: o.textContent}))",
    ) or []
    options = []
    for opt in raw:
        text = normalize(opt.get("text", ""))
        value = (opt.get("value") or "").strip()
        if not text or text.lower() == "select" or not value:
            continue
        options.append((value, text))
    return options or [(text, text) for text in LICENSE_TYPE_FALLBACKS]


def select_license_type(page, license_type_value: str):
    page.locator("#LicenseSearch_NameSearchInput_LicenseTypeId").select_option(value=str(license_type_value))
    time.sleep(0.3)


def ensure_maiden_search_unchecked(page):
    """The 'Search on maiden or other names' checkbox must stay unchecked so it
    never filters results and is never accidentally the click target."""
    try:
        checkbox = page.locator("#LicenseSearch_NameSearchInput_SearchMaidenOther")
        if checkbox.count() and checkbox.is_checked():
            checkbox.uncheck()
    except Exception:
        pass


# ─── RECAPTCHA HANDLING ───────────────────────────────────────────────────────

def recaptcha_widget_present(page) -> bool:
    return bool(page.evaluate("() => !!document.getElementById('g-recaptcha-response')"))


def recaptcha_token_present(page) -> bool:
    return bool(page.evaluate(
        "() => { const t = document.getElementById('g-recaptcha-response');"
        "return !!(t && t.value && t.value.length > 0); }"
    ))


def get_recaptcha_sitekey(page) -> str:
    key = page.evaluate(
        "() => { const el = document.querySelector('[data-sitekey]');"
        "return el ? el.getAttribute('data-sitekey') : ''; }"
    )
    if key:
        return key.strip()
    try:
        src = page.locator("iframe[src*='/recaptcha/'][src*='anchor']").first.get_attribute("src") or ""
    except Exception:
        return ""
    match = re.search(r"[?&]k=([^&]+)", src)
    return match.group(1) if match else ""


def _recaptcha_anchor_src(page) -> str:
    try:
        return page.locator("iframe[src*='/recaptcha/'][src*='anchor']").first.get_attribute("src") or ""
    except Exception:
        return ""


def get_recaptcha_context(page) -> dict:
    anchor_src = _recaptcha_anchor_src(page)
    data_s = page.evaluate(
        "() => { const el = document.querySelector('[data-s]');"
        "return el ? el.getAttribute('data-s') : ''; }"
    ) or ""
    if not data_s:
        data_s = _query_param(anchor_src, "s")

    theme = page.evaluate(
        "() => { const el = document.querySelector('[data-theme]');"
        "return el ? el.getAttribute('data-theme') : ''; }"
    ) or ""
    if not theme:
        theme = _query_param(anchor_src, "theme")

    enterprise = bool(page.evaluate(
        "() => !!document.querySelector('script[src*=\"recaptcha/enterprise.js\"], "
        "iframe[src*=\"recaptcha/enterprise/\"]')"
    ))

    try:
        useragent = page.evaluate("() => navigator.userAgent || ''") or ""
    except Exception:
        useragent = ""

    cookies = []
    try:
        for item in page.context.cookies():
            if not item.get("name"):
                continue
            expires = item.get("expires", -1)
            cookie = {
                "name": item["name"],
                "value": item.get("value", ""),
                "domain": item.get("domain", ""),
                "path": item.get("path", "/"),
                "hostOnly": not str(item.get("domain", "")).startswith("."),
                "httpOnly": bool(item.get("httpOnly", False)),
                "secure": bool(item.get("secure", False)),
                "session": expires in (-1, None),
            }
            if expires not in (-1, None):
                cookie["expirationDate"] = int(expires)
            cookies.append(cookie)
    except Exception:
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


def inject_recaptcha_token(page, token: str):
    """Write a solved reCAPTCHA token into the page and fire its callback."""
    page.evaluate(
        """
        (token) => {
          const boxes = Array.from(document.querySelectorAll(
            '#g-recaptcha-response, textarea[name="g-recaptcha-response"]'
          ));
          boxes.forEach((box) => {
            box.value = token;
            box.innerHTML = token;
            box.dispatchEvent(new Event('input', { bubbles: true }));
            box.dispatchEvent(new Event('change', { bubbles: true }));
          });
          try {
            const cfg = window.___grecaptcha_cfg;
            if (cfg && cfg.clients) {
              for (const cid in cfg.clients) {
                const client = cfg.clients[cid];
                for (const ck in client) {
                  const obj = client[ck];
                  if (obj && typeof obj === 'object') {
                    for (const k in obj) {
                      const leaf = obj[k];
                      if (leaf && typeof leaf.callback === 'function') leaf.callback(token);
                      if (leaf && typeof leaf.promiseCallback === 'function') leaf.promiseCallback(token);
                    }
                  }
                }
              }
            }
          } catch (e) { /* token in textarea is enough for form submit */ }
        }
        """,
        token,
    )


def recaptcha_anchor_frame(page):
    for frame in page.frames:
        url = frame.url or ""
        if "/recaptcha/" in url and "anchor" in url:
            return frame
    return None


def recaptcha_challenge_frame(page):
    for frame in page.frames:
        url = frame.url or ""
        if "/recaptcha/" in url and "bframe" in url:
            return frame
    for frame in page.frames:
        try:
            title = (frame.title() or "").lower()
        except Exception:
            title = ""
        if "challenge" in title:
            return frame
    return None


def recaptcha_challenge_present(page) -> bool:
    try:
        return recaptcha_challenge_frame(page) is not None
    except Exception:
        return False


def solve_recaptcha_via_nopecha(page) -> bool:
    """Attempt to auto-solve the page's reCAPTCHA v2 through NopeCHA.

    Not currently wired into the polling flow below (mirrors the original
    script, where this path exists but ensure_recaptcha_solved always uses
    the checkbox + audio-challenge flow instead)."""
    sitekey = get_recaptcha_sitekey(page)
    if not sitekey:
        return False
    context_info = get_recaptcha_context(page)
    print("[captcha] solving reCAPTCHA via NopeCHA...")
    try:
        token = nopecha_1.solve_recaptcha_v2(
            sitekey,
            page.url,
            data=context_info["data"],
            enterprise=context_info["enterprise"],
            useragent=context_info["useragent"],
            cookie=context_info["cookie"],
            timeout=RECAPTCHA_AUTO_SOLVE_TIMEOUT,
        )
    except nopecha_1.NopechaError as exc:
        print(f"[captcha] NopeCHA failed ({exc}); falling back to manual.")
        return False
    if not token:
        return False
    inject_recaptcha_token(page, token)
    return recaptcha_token_present(page)


def click_recaptcha_checkbox(page) -> bool:
    """Click the 'I'm not a robot' checkbox inside the reCAPTCHA anchor iframe."""
    frame = recaptcha_anchor_frame(page)
    if frame is None:
        return False
    try:
        box = frame.locator("#recaptcha-anchor")
        box.wait_for(state="visible", timeout=5000)
        if (box.get_attribute("aria-checked") or "") != "true":
            box.click()
        return True
    except Exception:
        return False


def _fetch_recaptcha_audio_as_data_url(frame):
    try:
        return frame.evaluate(
            """
            () => new Promise((resolve) => {
              const audio = document.querySelector('#audio-source');
              if (!audio) { resolve(null); return; }
              const src = audio.currentSrc || audio.src;
              if (!src) { resolve(null); return; }
              fetch(src, { cache: 'no-store' }).then(r => r.blob()).then(blob => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(reader.result);
                reader.onerror = () => resolve(null);
                reader.readAsDataURL(blob);
              }).catch(() => resolve(null));
            })
            """
        )
    except Exception:
        return None


def solve_recaptcha_audio_challenge(page) -> bool:
    """Open reCAPTCHA's audio challenge, solve it via NopeCHA, and submit it."""
    frame = recaptcha_challenge_frame(page)
    if frame is None:
        return False

    audio_button = frame.locator("#recaptcha-audio-button, button[id*='audio'], [aria-label*='audio' i]").first
    if audio_button.count():
        try:
            audio_button.click(timeout=3000)
        except Exception:
            pass
        time.sleep(1)

    for attempt in range(1, RECAPTCHA_AUDIO_MAX_ATTEMPTS + 1):
        frame = recaptcha_challenge_frame(page) or frame
        data_url = _fetch_recaptcha_audio_as_data_url(frame)
        if not data_url:
            reload_button = frame.locator("#recaptcha-reload-button, button[id*='reload']").first
            if reload_button.count():
                try:
                    reload_button.click(timeout=3000)
                except Exception:
                    pass
                time.sleep(1)
                continue
            return False

        print(f"[captcha] solving reCAPTCHA audio via NopeCHA (attempt {attempt})...")
        try:
            answer = nopecha_1.solve_recaptcha_audio(data_url, timeout=RECAPTCHA_AUDIO_SOLVE_TIMEOUT)
        except nopecha_1.NopechaError as exc:
            print(f"[captcha] reCAPTCHA audio solve failed ({exc})")
            answer = ""

        if not answer:
            reload_button = frame.locator("#recaptcha-reload-button, button[id*='reload']").first
            if reload_button.count() and attempt < RECAPTCHA_AUDIO_MAX_ATTEMPTS:
                try:
                    reload_button.click(timeout=3000)
                except Exception:
                    pass
                time.sleep(1)
                continue
            return False

        input_box = frame.locator("#audio-response, input[type='text']").first
        verify_button = frame.locator("#recaptcha-verify-button, button[id*='verify']").first
        if input_box.count() == 0 or verify_button.count() == 0:
            return False

        try:
            input_box.fill("")
            input_box.click()
            human_type(page, answer)
            verify_button.click()
        except Exception:
            try:
                verify_button.click()
            except Exception:
                return False
        time.sleep(2)

        if recaptcha_token_present(page):
            return True
        if not recaptcha_challenge_present(page):
            return recaptcha_token_present(page)
        if attempt >= RECAPTCHA_AUDIO_MAX_ATTEMPTS:
            return False
        frame = recaptcha_challenge_frame(page)
        if frame is None:
            return False
    return False


def ensure_recaptcha_solved(page, timeout=RECAPTCHA_SOLVE_TIMEOUT) -> bool:
    """Guarantee a reCAPTCHA token is present before the Search click.

    No-op when the page has no reCAPTCHA or it is already solved. Otherwise
    clicks the checkbox and polls until the token appears -- covering an
    instant auto-pass or a manual/audio challenge solve -- with no fixed
    sleeps. Returns True once a token exists, False if the timeout elapses."""
    render_deadline = time.time() + RECAPTCHA_RENDER_TIMEOUT
    while not recaptcha_widget_present(page) and time.time() < render_deadline:
        time.sleep(0.3)
    if not recaptcha_widget_present(page):
        return True
    if recaptcha_token_present(page):
        return True

    click_recaptcha_checkbox(page)

    deadline = time.time() + timeout
    audio_announced = False
    while time.time() < deadline:
        if recaptcha_token_present(page):
            return True
        if recaptcha_challenge_present(page):
            if not audio_announced:
                print("[captcha] reCAPTCHA challenge opened; switching to audio solve.")
                audio_announced = True
            if solve_recaptcha_audio_challenge(page):
                return True
        time.sleep(1)
    print("[captcha] reCAPTCHA not solved within timeout")
    return False


def click_search(page):
    ensure_maiden_search_unchecked(page)
    if not ensure_recaptcha_solved(page):
        raise RuntimeError("reCAPTCHA was not solved; aborting submit")
    try:
        button = page.locator("#btnSearch")
        button.wait_for(state="attached", timeout=WAIT_SECONDS * 1000)
        button.scroll_into_view_if_needed()
        try:
            button.click(timeout=10000)
        except Exception:
            page.evaluate("(el) => el.click()", button.element_handle())
        time.sleep(RESULT_SETTLE_SECONDS)
    except Exception as exc:
        raise RuntimeError("Failed to click Search button") from exc


def wait_for_results_or_empty(page, timeout=WAIT_SECONDS):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if page.locator("a.btn.btn-primary.mb-2[href*='/Home/Report?o=']").count() > 0:
                break
            body_text = normalize(page.locator("body").inner_text()).lower()
            if any(phrase in body_text for phrase in
                   ["no results", "no records", "no matching", "no license"]):
                break
        except Exception:
            pass
        time.sleep(0.3)
    time.sleep(0.8)


# ─── EXTRACTION ────────────────────────────────────────────────────────────────

_EXTRACT_RESULT_CARDS_JS = r"""
() => {
  const cards = [];
  const anchors = Array.from(document.querySelectorAll(
    "a.btn.btn-primary.mb-2[href*='/Home/Report?o=']"
  ));
  const seen = new Set();
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    if (!href) continue;
    const detailUrl = new URL(href, window.location.href).toString();
    if (seen.has(detailUrl)) continue;
    seen.add(detailUrl);

    const card = a.closest('.card-body');
    if (!card) continue;
    const rows = card.querySelectorAll('table tbody tr');
    if (!rows.length) continue;

    const cells = Array.from(rows[0].querySelectorAll('td'))
      .map(td => (td.innerText || '').replace(/\s+/g, ' ').trim());
    if (cells.length < 5) continue;

    let fullName = '';
    const nameEl = card.querySelector('h3 span.fw-bold.fs-3.wrap-all-text');
    if (nameEl) fullName = (nameEl.innerText || '').replace(/\s+/g, ' ').trim();
    const idx = fullName.indexOf(' [NCSBN ID:');
    if (idx !== -1) fullName = fullName.slice(0, idx).trim();

    cards.push({
      "First Name": cells[0],
      "Middle Initial": cells[1],
      "Last Name": cells[2],
      "License/Certificate Type": cells[3],
      "License/Certificate Number": cells[4],
      "Detail URL": detailUrl,
      "Name on License": fullName,
    });
  }
  return cards;
}
"""


def extract_result_cards(page):
    try:
        return page.evaluate(_EXTRACT_RESULT_CARDS_JS) or []
    except Exception:
        return []


_EXTRACT_LABEL_VALUE_CARDS_JS = r"""
() => {
  const records = [];
  const cards = Array.from(document.querySelectorAll('div.card.mb-3'));
  for (const card of cards) {
    const fields = {};
    const items = card.querySelectorAll("div[role='listitem']");
    for (const item of items) {
      const strong = item.querySelector('strong');
      if (!strong) continue;
      const label = (strong.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase().replace(/:$/, '');
      const valueParts = [];
      for (const child of Array.from(item.children)) {
        if (child.tagName.toLowerCase() === 'strong') continue;
        const t = (child.innerText || '').replace(/\s+/g, ' ').trim();
        if (t) valueParts.push(t);
      }
      const value = valueParts.join(' ').replace(/\s+/g, ' ').trim();
      if (label) fields[label] = value;
    }
    if (Object.keys(fields).length === 0) continue;
    const record = {
      "Name on License": fields["name on license"] || "",
      "License/Certificate Type": fields["license/certificate type"] || "",
      "License/Certificate Number": fields["license/certificate number"] || "",
      "License Status": fields["license status"] || "",
      "Original Issue Date": fields["original issue date"] || "",
      "Current Issue Date": fields["current issue date"] || "",
      "Current Expiration Date": fields["current expiration date"] || "",
    };
    if (Object.values(record).some(Boolean)) records.push(record);
  }
  return records;
}
"""


def extract_label_value_cards(page):
    try:
        return page.evaluate(_EXTRACT_LABEL_VALUE_CARDS_JS) or []
    except Exception:
        return []


def collect_search_results(page, search_last, search_first, license_type_value, license_type_label):
    """Phase 1: run ONE search in place (no page reload) and return the summary
    cards. The search page is never navigated away from, so the CAPTCHA
    challenge is not re-triggered between searches."""
    set_text_input(page, "LicenseSearch_NameSearchInput_LastName", search_last)
    set_text_input(page, "LicenseSearch_NameSearchInput_FirstName", search_first)
    select_license_type(page, license_type_value)
    click_search(page)
    wait_for_results_or_empty(page)

    results = []
    for card in extract_result_cards(page):
        card["Search Last Name"] = search_last
        card["Search First Name"] = search_first
        card["License Type Filter"] = license_type_label
        results.append(card)
    return results


def enrich_with_detail(page, result):
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
        page.goto(detail_url, wait_until="domcontentloaded")
        human_pause(PAGE_LOAD_SLEEP, PAGE_LOAD_SLEEP + 0.5)
        try:
            page.wait_for_selector("div.card.mb-3", timeout=10000)
        except PWTimeoutError:
            pass
        time.sleep(0.6)
        detail_records = extract_label_value_cards(page) or [{}]
    except Exception:
        detail_records = [{}]

    records = []
    for detail in detail_records:
        record = dict(base)
        record.update({
            "Name on License": detail.get("Name on License") or result.get("Name on License", ""),
            "License/Certificate Type": detail.get("License/Certificate Type") or result.get("License/Certificate Type", ""),
            "License/Certificate Number": detail.get("License/Certificate Number") or result.get("License/Certificate Number", ""),
            "License Status": detail.get("License Status", ""),
            "Original Issue Date": detail.get("Original Issue Date", ""),
            "Current Issue Date": detail.get("Current Issue Date", ""),
            "Current Expiration Date": detail.get("Current Expiration Date", ""),
        })
        records.append(record)
    return records


# ─── PERSISTENCE ───────────────────────────────────────────────────────────────

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
        json.dump({"phase": phase, "search_index": search_index, "detail_index": detail_index}, fh, indent=2)


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


# ─── BROWSER SESSION ────────────────────────────────────────────────────────────

def create_browser_session(playwright, proxy=None):
    browser, context, page = new_stealth_browser(playwright, proxy=proxy, headless=HEADLESS)
    open_search_page(page)
    return browser, context, page


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run():
    checkpoint = load_checkpoint()
    if checkpoint["phase"] == "done":
        print(
            f"Checkpoint {CHECKPOINT_FILE} shows the Oregon scrape already completed. "
            f"Delete {CHECKPOINT_FILE} and {COLLECTED_FILE} to restart from scratch."
        )
        return

    collected = load_collected_results()
    proxies = proxy_pool.load_proxies() if USE_PROXY else []

    with sync_playwright() as playwright:
        proxy = proxy_pool.next_proxy(proxies) if proxies else None
        if proxy:
            print(f"[PROXY] Using {proxy['server']}")
        browser, context, page = create_browser_session(playwright, proxy=proxy)
        state = {"consecutive_failures": 0}

        def rotate_session():
            nonlocal browser, context, page
            try:
                context.close()
                browser.close()
            except Exception:
                pass
            new_proxy = proxy_pool.next_proxy(proxies) if proxies else None
            if new_proxy:
                print(f"[PROXY] Rotating to {new_proxy['server']}")
            browser, context, page = create_browser_session(playwright, proxy=new_proxy)
            state["consecutive_failures"] = 0

        def note_result(ok: bool):
            if ok:
                state["consecutive_failures"] = 0
                return
            state["consecutive_failures"] += 1
            if proxies and state["consecutive_failures"] >= CONSECUTIVE_FAILURE_ROTATE_THRESHOLD:
                print("[BLOCKED] Repeated failures; rotating proxy and rebuilding session.")
                rotate_session()

        try:
            license_types = get_license_type_options(page)
            prefixes = list(generate_prefixes())
            search_tasks = [
                (prefix, license_type_value, license_type_label)
                for prefix in prefixes
                for license_type_value, license_type_label in license_types
            ]

            # ----- Phase 1: all searches on the single already-loaded page -----
            if checkpoint["phase"] == "search":
                start_index = min(checkpoint["search_index"], len(search_tasks))
                if start_index:
                    print(f"Resuming search phase at task {start_index + 1}/{len(search_tasks)}.")
                for idx in range(start_index, len(search_tasks)):
                    prefix, license_type_value, license_type_label = search_tasks[idx]
                    try:
                        results = collect_search_results(
                            page, prefix, prefix, license_type_value, license_type_label
                        )
                        collected.extend(results)
                        save_collected_results(collected)
                        print(f"[search {prefix} | {license_type_label}] found {len(results)} results")
                        note_result(True)
                    except Exception as exc:
                        print(f"[search {prefix} | {license_type_label}] failed: {exc}")
                        note_result(False)
                    finally:
                        save_checkpoint(phase="search", search_index=idx + 1, detail_index=0)

                checkpoint = {"phase": "detail", "search_index": len(search_tasks), "detail_index": 0}
                save_checkpoint(phase="detail", search_index=len(search_tasks), detail_index=0)

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
                    save_checkpoint(phase="detail", search_index=len(search_tasks), detail_index=idx + 1)
                    continue
                seen_urls.add(detail_url)

                try:
                    records = enrich_with_detail(page, result)
                    append_csv(OUTPUT_FILE, records)
                    print(f"[detail {idx + 1}/{total}] saved {len(records)} rows")
                    note_result(True)
                except Exception as exc:
                    print(f"[detail {idx + 1}/{total}] failed: {exc}")
                    note_result(False)
                finally:
                    save_checkpoint(phase="detail", search_index=len(search_tasks), detail_index=idx + 1)

            save_checkpoint(phase="done", search_index=len(search_tasks), detail_index=total)
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    run()
