"""
Georgia SOS licensee scraper — fixed-profession list.

Changes in this version
-----------------------
  * CSV durability: every scraped row is flushed and fsync'd to disk the moment
    it is written, so a crash or hard stop can never lose buffered rows. Append
    mode + a seen-licenses set still means restarts preserve past data and only
    add new records.
  * Minimal interaction behavior: plain clicks and plain typing. The previous
    per-character typing cadence, pointer-jitter, synthetic pointer events, and
    random scrolling have been removed. Basic pacing delays remain as polite
    rate-limiting, not mimicry.

NOTE: This will not defeat the site's CAPTCHA / bulk-access limit. For a full
roster, use the SOS active-licenses report or an open-records roster request
(Professional Licensing Boards Division, 404-424-9966).
"""

import csv
import json
import os
import random
import time

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

SEARCH_URL  = "https://goals.sos.ga.gov/GASOSOneStop/s/licensee-search"
OUTPUT_FILE = "sos_georgia.csv"
CHECKPOINT_FILE = "sos_georgia_checkpoint.json"
HEADLESS    = False

# Professions to scrape. These are matched case-insensitively and by "contains".

PROFESSIONS = [
    # "Architects & Interior Designers",
    # "Athlete Agents",
    # "Athletic Trainers",
    # "Auctioneers",
    #"Behavior Analyst",
    #"Boxing",
    # "Chiropractic Examiners",
    # "Conditioned Air Contractors",
    # "Cosmetology and Barbers",
    #"Dietitians",
    # "Dispensing Opticians",
    # "Electrical Contractors",
    # "Foresters",
    # "Funeral Services",
    # "Geologists",
    # "Immigration Assistance Providers",
    # "Landscape Architects",
    # "Librarians",
    # "Long Term Care Facility Administrators",
    # "Low Voltage Contractors",
    "Massage Therapy",
    # "Master & Journeyman Plumbers",
    # "Mixed Martial Arts",
    #"Music Therapy",
    "Nursing",
    "Occupational Therapy",
    "Optometry",
    "Physical Therapy",
    # "Podiatry",
    # "Private Detective & Security Agencies",
    "Professional Counselors/Social Workers/Marriage & Family Therapists",
    "Psychology",
    #"Residential & Commercial General Contractors",
    "Speech Language Pathology & Audiology",
    # "Trauma Scene Waste Management",
    # "Utility",
    # "Veterinary Medicine",
    # "Water & Wastewater Treatment Plant Operators & Laboratory Analysts",
]

# Leave None to auto-match your installed Chrome; pin a number only if that fails.
CHROMEDRIVER_MAJOR = 150

# Which combobox is which. `name` is a hint; `label` is the visible field label.
PROFESSION_COMBO = {"name": "GASOS_Profession_Type__c", "label": "profession"}
LICENSE_COMBO    = {"name": "GASOS_License_Type__c",    "label": "license"}

# Timeouts (seconds)
PAGE_TIMEOUT = 120
SHORT_WAIT   = 15
RESULTS_WAIT = 60

# Basic pacing delays (polite rate-limiting, not mimicry).
MIN_ACTION_DELAY = 0.25
MAX_ACTION_DELAY = 0.9
MIN_PAGE_DELAY   = 1.0
MAX_PAGE_DELAY   = 2.4

CSV_FIELDS = [
    "full_name",
    "license_number",
    "profession",
    "license_type",
    "license_sub_type",
    "status",
    "city",
]

PLACEHOLDER_VALUES = {
    "", "all", "none", "--none--", "select an option",
    "select a profession", "select profession type", "select a license type",
    "select license type", "select",
}


class PaginationBlocked(RuntimeError):
    pass

# ─── INJECTED SHADOW-DOM-AWARE HELPERS ────────────────────────────────────────

HELPERS_JS = r"""
window.__scraper = (function () {
  function deepAll(selector, root) {
    const out = [];
    const roots = [root || document];
    while (roots.length) {
      const r = roots.pop();
      try { r.querySelectorAll(selector).forEach(e => out.push(e)); } catch (e) {}
      try { r.querySelectorAll('*').forEach(e => { if (e.shadowRoot) roots.push(e.shadowRoot); }); } catch (e) {}
    }
    return out;
  }
  function deepOne(selector, root) { const a = deepAll(selector, root); return a.length ? a[0] : null; }
  function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }

  function labelFor(el) {
    let label = '';
    try {
      const id = el.getAttribute('id');
      if (id) { const lab = deepOne('label[for="' + id + '"]'); if (lab) label = norm(lab.innerText); }
      if (!label) {
        const lb = el.getAttribute('aria-labelledby');
        if (lb) { const t = lb.split(' ').map(i => { const n = deepOne('#' + i); return n ? norm(n.innerText) : ''; }).join(' '); label = norm(t); }
      }
      if (!label && el.closest) {
        const fe = el.closest('.slds-form-element') || el.closest('.slds-combobox_container') || el.closest('lightning-combobox');
        if (fe) { const l = fe.querySelector('label, .slds-form-element__label'); if (l) label = norm(l.innerText); }
      }
      if (!label) { const al = el.getAttribute('aria-label'); if (al) label = norm(al); }
    } catch (e) {}
    return label;
  }

  function allCombos() { return deepAll('[role="combobox"], lightning-combobox, .slds-combobox button, .slds-combobox input'); }

  function resolveCombo(name, labelKw) {
    if (name) {
      const byName = deepOne('[role="combobox"][name="' + name + '"]')
                  || deepOne('[role="combobox"][data-name="' + name + '"]')
                  || deepOne('[name="' + name + '"] [role="combobox"]');
      if (byName) return byName;
    }
    if (labelKw) {
      const kw = labelKw.toLowerCase();
      const els = deepAll('[role="combobox"]');
      for (const e of els) { if (labelFor(e).toLowerCase().indexOf(kw) !== -1) return e; }
    }
    return null;
  }

  function comboValueOf(name, labelKw) {
    const b = resolveCombo(name, labelKw);
    if (!b) return '';
    if (b.tagName.toLowerCase() === 'input' && b.value) return norm(b.value);
    const span = b.querySelector && (b.querySelector('span.slds-media__body span') || b.querySelector('span'));
    let t = span ? norm(span.innerText) : '';
    if (!t) t = norm(b.innerText);
    if (!t) t = norm(b.getAttribute('aria-label'));
    return t;
  }

  function comboInput(name, labelKw) {
    const c = resolveCombo(name, labelKw);
    if (!c) return null;
    if (c.tagName.toLowerCase() === 'input') return c;
    if (c.closest) {
      const fe = c.closest('.slds-combobox') || c.closest('.slds-form-element') || c.closest('lightning-combobox');
      if (fe) { const inp = fe.querySelector('input'); if (inp) return inp; }
    }
    return deepOne('input.slds-combobox__input');
  }

  function visibleListbox() {
    const boxes = deepAll('div[role="listbox"], ul[role="listbox"]');
    let fallback = null;
    for (const b of boxes) {
      const opts = b.querySelectorAll('[role="option"]');
      if (!opts.length) continue;
      const vis = b.offsetParent !== null || (b.getClientRects && b.getClientRects().length > 0);
      if (vis) return b;
      if (!fallback) fallback = b;
    }
    return fallback;
  }
  function visibleOptions() {
    const b = visibleListbox();
    if (!b) return [];
    return Array.from(b.querySelectorAll('[role="option"]')).map(o => norm(o.innerText)).filter(Boolean);
  }
  function visibleOptionEl(text, contains) {
    const b = visibleListbox();
    if (!b) return null;
    const want = norm(text).toLowerCase();
    for (const o of b.querySelectorAll('[role="option"]')) {
      const t = norm(o.innerText).toLowerCase();
      if (contains ? (t.indexOf(want) !== -1 || want.indexOf(t) !== -1) : t === want) return o;
    }
    return null;
  }

  function searchButton() {
    const cands = deepAll('button, input[type="submit"], input[type="button"], a[role="button"], [role="button"]');
    let fallback = null;
    for (const b of cands) {
      if (b.getAttribute('role') === 'combobox') continue;
      const txt = norm(b.innerText || b.value || '').toLowerCase();
      const al  = norm(b.getAttribute('aria-label') || '').toLowerCase();
      if (txt === 'search' || al === 'search') return b;
      if ((txt.indexOf('search') !== -1 || al.indexOf('search') !== -1) && !fallback) fallback = b;
    }
    return fallback;
  }
  function paginationButtons() {
    const buttons = deepAll('lightning-button button, button');
    return buttons.filter(b => {
      const al = norm(b.getAttribute('aria-label')).toLowerCase();
      const title = norm(b.getAttribute('title')).toLowerCase();
      const text = norm(b.innerText);
      return al.indexOf('navigate to page') === 0
          || al === 'navigate to next page'
          || al === 'navigate to previous page'
          || title === 'next'
          || title === 'previous'
          || /^\d+$/.test(text);
    });
  }
  function paginationInfo() {
    const buttons = paginationButtons();
    const numbered = buttons.filter(b => /^\d+$/.test(norm(b.innerText)));
    return { totalButtons: buttons.length, numberedButtons: numbered.length };
  }
  function nextButton() {
    const buttons = paginationButtons();
    for (const b of buttons) {
      const al = norm(b.getAttribute('aria-label')).toLowerCase();
      const title = norm(b.getAttribute('title')).toLowerCase();
      if (al === 'navigate to next page' || title === 'next') return b;
    }
    return null;
  }
  function isDisabled(el) {
    if (!el) return true;
    const aria = norm(el.getAttribute('aria-disabled')).toLowerCase();
    const disabled = el.disabled || norm(el.getAttribute('disabled')).toLowerCase();
    return aria === 'true' || disabled === 'true' || disabled === 'disabled';
  }
  function rows() {
    const out = [];
    const trs = deepAll('table tbody tr').concat(deepAll('tbody tr'));
    const seen = new Set();
    for (const tr of trs) {
      if (seen.has(tr)) continue;
      seen.add(tr);
      const cells = Array.from(tr.querySelectorAll('td'));
      if (cells.length < 6) continue;
      function cell(label, fallbackIndex) {
        const want = label.toLowerCase();
        const byLabel = cells.find(td => norm(td.getAttribute('data-label')).toLowerCase() === want);
        const td = byLabel || cells[fallbackIndex];
        return td ? norm(td.innerText) : '';
      }
      const row = {
        full_name: cell('FULL NAME', 0),
        license_number: cell('LICENSE NUMBER', 1),
        profession: cell('PROFESSION', 2),
        license_type: cell('LICENSE TYPE', 3),
        license_sub_type: cell('LICENSE SUB TYPE', 4),
        status: cell('STATUS', 5),
        city: cell('CITY', 6)
      };
      if (row.full_name && row.license_number) out.push(row);
    }
    return out;
  }
  function resultsState() {
    if (rows().length) return 'results';
    if (paginationButtons().length) return 'results';
    const body = norm((document.body && document.body.innerText) || '').toLowerCase();
    if (body.indexOf('no results') !== -1) return 'empty';
    if (body.indexOf('no records') !== -1) return 'empty';
    if (body.indexOf('no data')    !== -1) return 'empty';
    return '';
  }
  function captchaDetected() {
    const body = norm((document.body && document.body.innerText) || '').toLowerCase();
    if (body.indexOf('captcha') !== -1 || body.indexOf('recaptcha') !== -1) return true;
    if (deepAll('.g-recaptcha, iframe[src*="recaptcha"], iframe[title*="recaptcha"]').length) return true;
    return false;
  }
  function recaptchaTokenValue() {
    const token = deepOne('input[type="hidden"]#recaptcha-token')
               || deepOne('input[type="hidden"][name="recaptcha-token"]')
               || deepOne('input[type="hidden"][id*="recaptcha"]');
    return token ? norm(token.value || token.getAttribute('value') || '') : '';
  }
  function labelValue(label) {
    for (const d of deepAll('div.title-label')) {
      if (norm(d.innerText) === norm(label)) {
        let sib = d.nextElementSibling;
        while (sib) { if (sib.tagName === 'P') return norm(sib.innerText); sib = sib.nextElementSibling; }
      }
    }
    return '';
  }
  function findControl(labels) {
    for (const b of deepAll('button, a')) {
      const t  = norm(b.innerText).toLowerCase();
      const al = norm(b.getAttribute('aria-label') || '').toLowerCase();
      if (labels.includes(t) || labels.includes(al)) return b;
    }
    return null;
  }
  function describeCombos() {
    return deepAll('[role="combobox"]').map(e => ({
      tag:   e.tagName.toLowerCase(),
      name:  e.getAttribute('name') || e.getAttribute('data-name') || '',
      label: labelFor(e),
      value: norm(e.value || e.innerText),
      disabled: (e.getAttribute('disabled') || e.getAttribute('aria-disabled') || '')
    }));
  }
  return { deepOne, deepAll, norm, resolveCombo, comboValueOf, comboInput,
           visibleOptions, visibleOptionEl, searchButton,
           paginationInfo, nextButton, isDisabled, rows, resultsState, labelValue, findControl,
           captchaDetected, recaptchaTokenValue, describeCombos };
})();
"""

# ─── LOW-LEVEL HELPERS ────────────────────────────────────────────────────────

def js(driver, script, *args):
    return driver.execute_script(script, *args)


def inject_helpers(driver):
    driver.execute_script(HELPERS_JS)


def normalize(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def fuzzy_match(a: str, b: str) -> bool:
    a, b = normalize(a), normalize(b)
    return bool(a) and bool(b) and (a in b or b in a)


def human_pause(min_delay=MIN_ACTION_DELAY, max_delay=MAX_ACTION_DELAY):
    time.sleep(random.uniform(min_delay, max_delay))


def human_type(element, text: str):
    element.send_keys(text)          # plain typing, no per-character timing


def robust_click(driver, element) -> bool:
    if element is None:
        return False
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
    except Exception:
        pass
    try:
        element.click()
        human_pause()
        return True
    except (ElementClickInterceptedException, ElementNotInteractableException,
            StaleElementReferenceException, Exception):
        pass
    try:
        driver.execute_script("arguments[0].click();", element)  # plain JS fallback
        human_pause()
        return True
    except Exception:
        return False


def send_escape(driver):
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    except Exception:
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass


def recaptcha_token_value(driver) -> str:
    try:
        return js(driver, "return window.__scraper.recaptchaTokenValue();") or ""
    except Exception:
        return ""


def wait_recaptcha_token(driver, timeout=SHORT_WAIT, label="action") -> bool:
    """Wait for the page's own reCAPTCHA JavaScript to populate its hidden token."""
    end = time.time() + timeout
    while time.time() < end:
        token = recaptcha_token_value(driver)
        if token:
            print(f"    reCAPTCHA token ready before {label}")
            return True
        time.sleep(0.3)
    print(f"    reCAPTCHA token did not appear before {label}")
    return False


def dump_comboboxes(driver):
    try:
        combos = js(driver, "return window.__scraper.describeCombos();") or []
    except Exception as exc:
        print(f"    [DIAG] could not read comboboxes: {exc}")
        return
    print(f"    [DIAG] {len(combos)} combobox(es) on the page:")
    for c in combos:
        print(f"      - tag={c.get('tag')} name={c.get('name')!r} "
              f"label={c.get('label')!r} value={c.get('value')!r} "
              f"disabled={c.get('disabled')!r}")

# ─── COMBOBOX INTERACTION ─────────────────────────────────────────────────────

def combo_el(driver, combo):
    return js(driver, "return window.__scraper.resolveCombo(arguments[0], arguments[1]);",
              combo["name"], combo["label"])


def combo_value(driver, combo) -> str:
    return js(driver, "return window.__scraper.comboValueOf(arguments[0], arguments[1]);",
              combo["name"], combo["label"]) or ""


def combo_disabled(driver, el) -> bool:
    if el is None:
        return True
    disabled = (el.get_attribute("disabled") or "").lower()
    aria_dis = (el.get_attribute("aria-disabled") or "").lower()
    return disabled in ("true", "disabled") or aria_dis == "true"


def open_combo(driver, combo) -> bool:
    el = combo_el(driver, combo)
    if el is None or combo_disabled(driver, el):
        return False
    for _attempt in range(2):
        robust_click(driver, el)
        end = time.time() + (SHORT_WAIT / 2)
        while time.time() < end:
            if js(driver, "return window.__scraper.visibleOptions().length > 0;"):
                return True
            e2 = combo_el(driver, combo)
            if e2 and e2.get_attribute("aria-expanded") == "true":
                return True
            time.sleep(0.2)
        el = combo_el(driver, combo) or el  # re-fetch in case a re-render made it stale
    return True  # proceed anyway; typeahead fields show options only after typing


def _wait_option(driver, text, contains, timeout):
    end = time.time() + timeout
    while time.time() < end:
        el = js(driver, "return window.__scraper.visibleOptionEl(arguments[0], arguments[1]);",
                text, contains)
        if el:
            return el
        time.sleep(0.2)
    return None


def get_options(driver, combo) -> list:
    if combo_el(driver, combo) is None:
        return []
    if not open_combo(driver, combo):
        return []
    opts, end = [], time.time() + SHORT_WAIT
    while time.time() < end:
        opts = js(driver, "return window.__scraper.visibleOptions();") or []
        if opts:
            break
        time.sleep(0.2)
    send_escape(driver)
    out = []
    for o in opts:
        o = (o or "").strip()
        if not o or normalize(o) in PLACEHOLDER_VALUES or o in out:
            continue
        out.append(o)
    return out


def select_value(driver, combo, value) -> bool:
    if not open_combo(driver, combo):
        return False

    el = _wait_option(driver, value, contains=True, timeout=max(3, SHORT_WAIT // 2))

    if el is None:  # typeahead / lookup: type the keyword, then pick
        inp = js(driver, "return window.__scraper.comboInput(arguments[0], arguments[1]);",
                 combo["name"], combo["label"])
        if inp is not None:
            try:
                inp.clear()
            except Exception:
                pass
            try:
                human_type(inp, value)
            except Exception:
                driver.execute_script(
                    "arguments[0].value=arguments[1];"
                    "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));",
                    inp, value)
            el = _wait_option(driver, value, contains=True, timeout=SHORT_WAIT)

    if el is None:
        send_escape(driver)
        return False

    robust_click(driver, el)

    end = time.time() + SHORT_WAIT
    while time.time() < end:
        if fuzzy_match(value, combo_value(driver, combo)):
            return True
        time.sleep(0.2)

    # One more attempt: re-open and re-click the option (Lightning sometimes
    # swallows the first click on re-render).
    if open_combo(driver, combo):
        el2 = _wait_option(driver, value, contains=True, timeout=max(3, SHORT_WAIT // 2))
        if el2 is not None:
            robust_click(driver, el2)
            end = time.time() + SHORT_WAIT
            while time.time() < end:
                if fuzzy_match(value, combo_value(driver, combo)):
                    return True
                time.sleep(0.2)
    return fuzzy_match(value, combo_value(driver, combo))


def select_retry(driver, combo, value, attempts=3) -> bool:
    for _ in range(attempts):
        if select_value(driver, combo, value):
            return True
        send_escape(driver)
        time.sleep(0.6)
    return False

# ─── SEARCH / RESULTS / DETAIL ────────────────────────────────────────────────

def click_search(driver) -> bool:
    if not wait_recaptcha_token(driver, timeout=RESULTS_WAIT, label="search"):
        return False
    btn = js(driver, "return window.__scraper.searchButton();")
    if btn and robust_click(driver, btn):
        human_pause(MIN_PAGE_DELAY, MAX_PAGE_DELAY)
        return True
    # Fallback: press Enter inside the License Type field.
    try:
        inp = js(driver, "return window.__scraper.comboInput(arguments[0], arguments[1]);",
                 LICENSE_COMBO["name"], LICENSE_COMBO["label"])
        if inp is not None:
            inp.send_keys(Keys.RETURN)
            human_pause(MIN_PAGE_DELAY, MAX_PAGE_DELAY)
            return True
    except Exception:
        pass
    return False


def wait_results_state(driver, timeout=RESULTS_WAIT) -> str:
    end = time.time() + timeout
    while time.time() < end:
        state = js(driver, "return window.__scraper.resultsState();")
        if state in ("results", "empty"):
            return state
        time.sleep(0.3)
    return ""


def wait_table_refresh(driver, previous_signature: str, timeout=RESULTS_WAIT) -> bool:
    end = time.time() + timeout
    previous_signature = previous_signature or ""
    while time.time() < end:
        rows = js(driver, "return window.__scraper.rows();") or []
        if rows:
            signature = "|".join(r.get("license_number", "") for r in rows)
            if not previous_signature or signature != previous_signature:
                return True
        if js(driver, "return window.__scraper.resultsState();") == "empty":
            return True
        time.sleep(0.3)
    return False


# ─── SCRAPE ───────────────────────────────────────────────────────────────────

def captcha_detected(driver) -> bool:
    try:
        return bool(js(driver, "return window.__scraper.captchaDetected();"))
    except Exception:
        return False


def checkpoint_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CHECKPOINT_FILE)


def load_checkpoint() -> dict:
    path = checkpoint_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("profession") and int(data.get("page", 1)) >= 1:
            data["page"] = int(data.get("page", 1))
            data["license_type"] = data.get("license_type") or ""
            return data
    except Exception as exc:
        print(f"[WARN] Could not read checkpoint: {exc}")
    return {}


def save_checkpoint(profession: str, license_type: str, page: int, reason: str = "in_progress"):
    data = {
        "profession": profession,
        "license_type": license_type or "",
        "page": max(1, int(page or 1)),
        "reason": reason,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(checkpoint_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def clear_checkpoint():
    path = checkpoint_path()
    if os.path.exists(path):
        os.remove(path)


def load_seen_licenses() -> set:
    seen = set()
    if not os.path.exists(OUTPUT_FILE):
        return seen
    try:
        with open(OUTPUT_FILE, "r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                lic = (row.get("license_number") or "").strip()
                if lic:
                    seen.add(lic)
    except Exception as exc:
        print(f"[WARN] Could not read existing output for resume: {exc}")
    return seen


def open_output_writer():
    file_exists = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
    outfile = open(OUTPUT_FILE, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(outfile, fieldnames=CSV_FIELDS)
    if not file_exists:
        writer.writeheader()
        outfile.flush()
        os.fsync(outfile.fileno())
    return outfile, writer


def scrape_results(driver, writer, outfile, seen_licenses: set, profession: str,
                   license_type: str = "", start_page: int = 1) -> int:
    state = wait_results_state(driver, RESULTS_WAIT)
    if state == "empty":
        print("    no results")
        return 0
    if state != "results":
        print("    results never appeared")
        return 0

    written, visited_signatures, current = 0, set(), 1
    start_page = max(1, int(start_page or 1))
    if start_page > 1:
        print(f"    resuming at page {start_page}; advancing with Next")

    while True:
        rows = js(driver, "return window.__scraper.rows();") or []
        signature = "|".join(r.get("license_number", "") for r in rows)
        if signature in visited_signatures:
            print(f"    page {current}: duplicate page detected; stopping pagination")
            break
        visited_signatures.add(signature)
        save_checkpoint(profession, license_type, current)

        if current < start_page:
            print(f"    page {current}: already completed; advancing")
        else:
            active = [r for r in rows
                      if r.get("status", "").lower() == "active"
                      and r.get("license_number") not in seen_licenses]
            print(f"    page {current}: {len(active)} new active / {len(rows)} rows")

            for row in active:
                lic = row["license_number"]
                seen_licenses.add(lic)

                writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
                outfile.flush()
                os.fsync(outfile.fileno())   # row is durably on disk before we move on
                written += 1
                print(f"      + {lic} {row.get('full_name', '')}")

        info = js(driver, "return window.__scraper.paginationInfo();") or {}
        next_btn = js(driver, "return window.__scraper.nextButton();")
        is_disabled = js(driver, "return window.__scraper.isDisabled(arguments[0]);", next_btn)
        print("    pagination controls: "
              f"{info.get('totalButtons', 0)} total / {info.get('numberedButtons', 0)} numbered")
        if not next_btn or is_disabled:
            break

        previous_signature = signature
        if not wait_recaptcha_token(driver, timeout=RESULTS_WAIT, label=f"page {current + 1}"):
            save_checkpoint(profession, license_type, current + 1, "recaptcha_token_timeout")
            raise PaginationBlocked(
                f"Pagination stopped at {profession} / {license_type or '(profession only)'} "
                f"page {current + 1}: recaptcha_token_timeout"
            )
        if not robust_click(driver, next_btn):
            print("    could not click next page")
            save_checkpoint(profession, license_type, current + 1, "next_click_failed")
            raise PaginationBlocked(f"Could not click Next at page {current}")
        current += 1
        human_pause(MIN_PAGE_DELAY, MAX_PAGE_DELAY)
        if not wait_table_refresh(driver, previous_signature):
            reason = "captcha_or_blocked_refresh" if captcha_detected(driver) else "page_refresh_timeout"
            print(f"    page {current} did not refresh in time; checkpoint saved")
            save_checkpoint(profession, license_type, current, reason)
            raise PaginationBlocked(
                f"Pagination stopped at {profession} / {license_type or '(profession only)'} page {current}: {reason}"
            )

    return written

# ─── DRIVER / PAGE LOAD ───────────────────────────────────────────────────────

def build_driver() -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")

    profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
    os.makedirs(profile_dir, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_dir}")

    kwargs = {"options": options, "headless": HEADLESS}
    if CHROMEDRIVER_MAJOR:
        kwargs["version_main"] = CHROMEDRIVER_MAJOR

    driver = uc.Chrome(**kwargs)
    driver.set_page_load_timeout(PAGE_TIMEOUT)
    driver.set_script_timeout(PAGE_TIMEOUT)
    return driver


def load_search_page(driver) -> bool:
    driver.get(SEARCH_URL)
    human_pause(1.8, 3.2)
    inject_helpers(driver)
    end = time.time() + 25
    while time.time() < end:
        if not js(driver, "return typeof window.__scraper !== 'undefined';"):
            inject_helpers(driver)
        if js(driver, "return !!(window.__scraper && "
                      "window.__scraper.resolveCombo('GASOS_Profession_Type__c','profession'));"):
            return True
        time.sleep(0.5)
    return False


def dismiss_overlays(driver):
    """Best-effort: click a cookie/consent accept button if one is present."""
    try:
        driver.execute_script(
            "var b = window.__scraper.findControl("
            "['accept all cookies','accept cookies','accept all','accept',"
            "'i accept','i agree','agree']); if (b) b.click();")
    except Exception:
        pass


def prepare_form(driver) -> bool:
    """Reload the search page and wait until the Profession combobox is usable."""
    if not load_search_page(driver):
        return False
    dismiss_overlays(driver)
    end = time.time() + SHORT_WAIT
    while time.time() < end:
        el = combo_el(driver, PROFESSION_COMBO)
        if el is not None and not combo_disabled(driver, el):
            return True
        time.sleep(0.3)
    return combo_el(driver, PROFESSION_COMBO) is not None


def wait_dependent_ready(driver, timeout=SHORT_WAIT) -> bool:
    """Wait for the License Type combobox to become enabled after a profession pick."""
    end = time.time() + timeout
    while time.time() < end:
        el = combo_el(driver, LICENSE_COMBO)
        if el is not None and not combo_disabled(driver, el):
            return True
        time.sleep(0.3)
    return False


def discover_license_types(driver, timeout=SHORT_WAIT) -> list:
    end = time.time() + timeout
    while time.time() < end:
        opts = [lt for lt in get_options(driver, LICENSE_COMBO)
                if normalize(lt) not in PLACEHOLDER_VALUES]
        if opts:
            return opts
        time.sleep(0.5)
    return []

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run():
    driver = build_driver()
    total = 0
    seen_licenses: set = load_seen_licenses()
    checkpoint = load_checkpoint()

    outfile, writer = open_output_writer()

    try:
        if not load_search_page(driver):
            print("[ERROR] Could not find the Profession combobox on the page.")
            dump_comboboxes(driver)
            raise RuntimeError("Search page / Profession combobox never loaded.")

        if checkpoint:
            print("[RESUME] "
                  f"{checkpoint['profession']} / "
                  f"{checkpoint.get('license_type') or '(profession only)'} / "
                  f"page {checkpoint['page']}")
        if seen_licenses:
            print(f"[RESUME] Loaded {len(seen_licenses)} existing license number(s) from {OUTPUT_FILE}")

        print(f"[INFO] Scraping {len(PROFESSIONS)} profession(s): {', '.join(PROFESSIONS)}")

        resume_profession = checkpoint.get("profession") if checkpoint else ""
        resume_license = checkpoint.get("license_type", "") if checkpoint else ""
        resume_page = checkpoint.get("page", 1) if checkpoint else 1
        waiting_for_resume_profession = bool(checkpoint)

        for prof in PROFESSIONS:
            if waiting_for_resume_profession and prof != resume_profession:
                print(f"\n[SKIP PROF] {prof} (before checkpoint)")
                continue
            if waiting_for_resume_profession and prof == resume_profession:
                waiting_for_resume_profession = False

            print(f"\n[PROF] {prof}")
            if not prepare_form(driver):
                print("  page reload failed; skipping")
                continue
            if not select_retry(driver, PROFESSION_COMBO, prof):
                print(f"  could not select profession '{prof}'")
                dump_comboboxes(driver)
                continue

            wait_dependent_ready(driver)
            lic_types = discover_license_types(driver)

            if lic_types:
                print(f"  {len(lic_types)} license type(s): {', '.join(lic_types)}")
                waiting_for_resume_license = bool(checkpoint and prof == resume_profession)
                if waiting_for_resume_license and resume_license not in lic_types:
                    raise RuntimeError(
                        f"Checkpoint license type {resume_license!r} was not found under {prof!r}."
                    )
                for lt in lic_types:
                    if waiting_for_resume_license and lt != resume_license:
                        print(f"  [SKIP LIC] {lt} (before checkpoint)")
                        continue
                    if waiting_for_resume_license and lt == resume_license:
                        waiting_for_resume_license = False

                    print(f"  [LIC] {lt}")
                    if not prepare_form(driver):
                        print("    reload failed"); continue
                    if not select_retry(driver, PROFESSION_COMBO, prof):
                        print("    profession reselect failed"); dump_comboboxes(driver); continue
                    wait_dependent_ready(driver)
                    if not select_retry(driver, LICENSE_COMBO, lt):
                        print("    license select failed"); continue
                    if not click_search(driver):
                        print("    (Search button not confirmed \u2014 waiting for results anyway)")
                    start_page = resume_page if checkpoint and prof == resume_profession and lt == resume_license else 1
                    total += scrape_results(driver, writer, outfile, seen_licenses, prof, lt, start_page)
                    clear_checkpoint()
                    checkpoint = {}
            else:
                if checkpoint and prof == resume_profession and resume_license:
                    raise RuntimeError(
                        f"Checkpoint expected license type {resume_license!r}, but no license types were found."
                    )
                print("  no license types found; searching profession only.")
                if not click_search(driver):
                    print("  (Search button not confirmed \u2014 waiting for results anyway)")
                start_page = resume_page if checkpoint and prof == resume_profession else 1
                total += scrape_results(driver, writer, outfile, seen_licenses, prof, "", start_page)
                clear_checkpoint()
                checkpoint = {}

    except KeyboardInterrupt:
        print("\n[INFO] interrupted by user.")
    except PaginationBlocked as exc:
        print(f"\n[BLOCKED] {exc}")
        print(f"[BLOCKED] Resume point saved in {CHECKPOINT_FILE}. Resolve the page/session issue and run again.")
    finally:
        outfile.close()
        driver.quit()

    print(f"\n[DONE] {total} active records written to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    run()
