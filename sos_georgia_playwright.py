"""
Georgia SOS licensee scraper — Playwright edition.

Same fixed-profession scrape as sos_georgia.py, ported from
undetected-chromedriver/Selenium to Playwright:

  * Real Chrome (channel="chrome") launched through p.chromium.launch(...).
    No proxy — the site stopped responding when routed through the Webshare
    proxies, so this connects directly.
  * Shadow-DOM-aware combobox helpers (HELPERS_JS) are injected once via
    context.add_init_script, so window.__scraper exists on every navigation
    without a re-inject/poll loop.
  * Clicks/typing go through playwright_stealth_utils' human_click/human_type
    (randomized mouse path, per-key delay, occasional hesitation).
  * On a PaginationBlocked (captcha/refresh stall), the run stops with a
    checkpoint saved so a rerun resumes where it left off.
"""

import csv
import json
import os
import random
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

from playwright_stealth_utils import (
    human_click,
    human_pause,
    human_scroll,
    human_type,
    new_stealth_browser,
)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

SEARCH_URL  = "https://goals.sos.ga.gov/GASOSOneStop/s/licensee-search"
OUTPUT_FILE = "sos_georgia.csv"
CHECKPOINT_FILE = "sos_georgia_checkpoint.json"
HEADLESS    = False

PROFESSIONS = [
    "Nursing",
    "Occupational Therapy",
    "Optometry",
    "Physical Therapy",
    "Professional Counselors/Social Workers/Marriage & Family Therapists",
    "Psychology",
    "Speech Language Pathology & Audiology",
]

PROFESSION_COMBO = {"name": "GASOS_Profession_Type__c", "label": "profession"}
LICENSE_COMBO    = {"name": "GASOS_License_Type__c",    "label": "license"}

PAGE_TIMEOUT = 120_000
SHORT_WAIT   = 15
RESULTS_WAIT = 60

MIN_PAGE_DELAY = 1.0
MAX_PAGE_DELAY = 2.4

CSV_FIELDS = [
    "full_name", "license_number", "profession",
    "license_type", "license_sub_type", "status", "city",
]

PLACEHOLDER_VALUES = {
    "", "all", "none", "--none--", "select an option",
    "select a profession", "select profession type", "select a license type",
    "select license type", "select",
}


class PaginationBlocked(RuntimeError):
    pass


# ─── INJECTED SHADOW-DOM-AWARE HELPERS (unchanged from the Selenium version) ──

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
  function currentPageNumber() {
    const buttons = paginationButtons();
    for (const b of buttons) {
      const text = norm(b.innerText);
      if (!/^\d+$/.test(text)) continue;
      const host = b.closest && b.closest('lightning-button');
      const cls = ((host && host.className) || b.className || '').toLowerCase();
      const pressed = norm(b.getAttribute('aria-current')).toLowerCase();
      if (cls.indexOf('active') !== -1 || pressed === 'page' || pressed === 'true') {
        return parseInt(text, 10);
      }
    }
    return null;
  }
  function numberedPageButtons() {
    const out = [];
    for (const b of paginationButtons()) {
      const text = norm(b.innerText);
      if (!/^\d+$/.test(text)) continue;
      out.push({ page: parseInt(text, 10), button: b });
    }
    return out;
  }
  function pageButtonFor(page) {
    page = parseInt(page, 10);
    if (!page) return null;
    for (const item of numberedPageButtons()) {
      if (item.page === page) return item.button;
    }
    return null;
  }
  function nearestResumePage(targetPage) {
    targetPage = parseInt(targetPage, 10);
    if (!targetPage || targetPage < 1) return null;
    const current = currentPageNumber();
    if (current === null) return null;
    if (current === targetPage) return { page: current, delta: 0 };
    const movingForward = current < targetPage;
    let best = null;
    for (const item of numberedPageButtons()) {
      if (item.page === current) continue;
      if (movingForward) {
        if (item.page <= current || item.page > targetPage) continue;
        if (!best || item.page > best.page) best = { page: item.page, delta: targetPage - item.page };
      } else {
        if (item.page >= current || item.page < targetPage) continue;
        if (!best || item.page < best.page) best = { page: item.page, delta: item.page - targetPage };
      }
    }
    return best;
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
            paginationInfo, currentPageNumber, numberedPageButtons, pageButtonFor, nearestResumePage,
            nextButton, isDisabled, rows, resultsState, findControl,
            captchaDetected, describeCombos };
})();
"""

# ─── LOW-LEVEL HELPERS ────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def fuzzy_match(a: str, b: str) -> bool:
    a, b = normalize(a), normalize(b)
    return bool(a) and bool(b) and (a in b or b in a)


def call(page, expr, *args):
    """Call window.__scraper.<expr already written as full call>."""
    return page.evaluate(f"() => window.__scraper.{expr}")


def call_handle(page, expr):
    handle = page.evaluate_handle(f"() => window.__scraper.{expr}")
    el = handle.as_element()
    if el is None:
        handle.dispose()
        return None
    return el


def send_escape(page):
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def dump_comboboxes(page):
    try:
        combos = call(page, "describeCombos()") or []
    except Exception as exc:
        print(f"    [DIAG] could not read comboboxes: {exc}")
        return
    print(f"    [DIAG] {len(combos)} combobox(es) on the page:")
    for c in combos:
        print(f"      - tag={c.get('tag')} name={c.get('name')!r} "
              f"label={c.get('label')!r} value={c.get('value')!r} "
              f"disabled={c.get('disabled')!r}")

# ─── COMBOBOX INTERACTION ─────────────────────────────────────────────────────

def _combo_args(combo):
    return f"{json.dumps(combo['name'])}, {json.dumps(combo['label'])}"


def combo_el(page, combo):
    return call_handle(page, f"resolveCombo({_combo_args(combo)})")


def combo_value(page, combo) -> str:
    return call(page, f"comboValueOf({_combo_args(combo)})") or ""


def combo_input(page, combo):
    return call_handle(page, f"comboInput({_combo_args(combo)})")


def combo_disabled(el) -> bool:
    if el is None:
        return True
    disabled = (el.get_attribute("disabled") or "").lower()
    aria_dis = (el.get_attribute("aria-disabled") or "").lower()
    return disabled in ("true", "disabled") or aria_dis == "true"


def open_combo(page, combo) -> bool:
    el = combo_el(page, combo)
    if el is None or combo_disabled(el):
        return False
    for _attempt in range(2):
        human_click(page, el)
        end = time.time() + (SHORT_WAIT / 2)
        while time.time() < end:
            if call(page, "visibleOptions()"):
                return True
            e2 = combo_el(page, combo)
            if e2 is not None and e2.get_attribute("aria-expanded") == "true":
                return True
            time.sleep(0.2)
        el = combo_el(page, combo) or el
    return True  # proceed anyway; typeahead fields show options only after typing


def _wait_option(page, text, contains, timeout):
    end = time.time() + timeout
    while time.time() < end:
        el = call_handle(page, f"visibleOptionEl({json.dumps(text)}, {json.dumps(bool(contains))})")
        if el is not None:
            return el
        time.sleep(0.2)
    return None


def get_options(page, combo) -> list:
    if combo_el(page, combo) is None:
        return []
    if not open_combo(page, combo):
        return []
    opts, end = [], time.time() + SHORT_WAIT
    while time.time() < end:
        opts = call(page, "visibleOptions()") or []
        if opts:
            break
        time.sleep(0.2)
    send_escape(page)
    out = []
    for o in opts:
        o = (o or "").strip()
        if not o or normalize(o) in PLACEHOLDER_VALUES or o in out:
            continue
        out.append(o)
    return out


def select_value(page, combo, value) -> bool:
    if not open_combo(page, combo):
        return False

    el = _wait_option(page, value, True, max(3, SHORT_WAIT // 2))

    if el is None:  # typeahead / lookup: type the keyword, then pick
        inp = combo_input(page, combo)
        if inp is not None:
            try:
                inp.fill("")
                inp.click()
                human_type(page, value)
            except Exception:
                page.evaluate(
                    "(el, val) => { el.value = val; el.dispatchEvent(new Event('input', {bubbles: true})); }",
                    inp, value,
                )
            el = _wait_option(page, value, True, SHORT_WAIT)

    if el is None:
        send_escape(page)
        return False

    human_click(page, el)

    end = time.time() + SHORT_WAIT
    while time.time() < end:
        if fuzzy_match(value, combo_value(page, combo)):
            return True
        time.sleep(0.2)

    # One more attempt: re-open and re-click the option (Lightning sometimes
    # swallows the first synthetic click on re-render).
    if open_combo(page, combo):
        el2 = _wait_option(page, value, True, max(3, SHORT_WAIT // 2))
        if el2 is not None:
            human_click(page, el2)
            end = time.time() + SHORT_WAIT
            while time.time() < end:
                if fuzzy_match(value, combo_value(page, combo)):
                    return True
                time.sleep(0.2)
    return fuzzy_match(value, combo_value(page, combo))


def select_retry(page, combo, value, attempts=3) -> bool:
    for _ in range(attempts):
        if select_value(page, combo, value):
            return True
        send_escape(page)
        time.sleep(0.6)
    return False

# ─── SEARCH / RESULTS / DETAIL ────────────────────────────────────────────────

def click_search(page) -> bool:
    btn = call_handle(page, "searchButton()")
    if btn is not None and human_click(page, btn):
        human_pause(MIN_PAGE_DELAY, MAX_PAGE_DELAY)
        return True
    # Fallback: press Enter inside the License Type field.
    try:
        inp = combo_input(page, LICENSE_COMBO)
        if inp is not None:
            human_pause(0.15, 0.45)
            inp.press("Enter")
            human_pause(MIN_PAGE_DELAY, MAX_PAGE_DELAY)
            return True
    except Exception:
        pass
    return False


def wait_results_state(page, timeout=RESULTS_WAIT) -> str:
    end = time.time() + timeout
    while time.time() < end:
        state = call(page, "resultsState()")
        if state in ("results", "empty"):
            return state
        time.sleep(0.3)
    return ""


def wait_table_refresh(page, previous_signature: str, timeout=RESULTS_WAIT) -> bool:
    end = time.time() + timeout
    previous_signature = previous_signature or ""
    while time.time() < end:
        rows = call(page, "rows()") or []
        if rows:
            signature = "|".join(r.get("license_number", "") for r in rows)
            if not previous_signature or signature != previous_signature:
                return True
        if call(page, "resultsState()") == "empty":
            return True
        time.sleep(0.3)
    return False


def jump_to_resume_page(page, target_page: int, profession: str = "", license_type: str = "") -> int:
    target_page = max(1, int(target_page or 1))
    if target_page <= 1:
        return 1

    while True:
        current_page = int(call(page, "currentPageNumber()") or 1)
        if current_page == target_page:
            return current_page

        nearest = call(page, f"nearestResumePage({target_page})") or {}
        jump_page = int(nearest.get("page") or 0)
        if not jump_page or jump_page == current_page:
            save_checkpoint(profession, license_type, current_page, "resume_jump_stalled")
            raise PaginationBlocked(
                f"Resume jump stalled at page {current_page} while targeting page {target_page}"
            )

        jump_button = call_handle(page, f"pageButtonFor({jump_page})")
        if jump_button is None:
            save_checkpoint(profession, license_type, current_page, "resume_jump_button_missing")
            raise PaginationBlocked(
                f"Resume jump could not find button for page {jump_page} while targeting page {target_page}"
            )

        previous_signature = "|".join(
            r.get("license_number", "") for r in (call(page, "rows()") or [])
        )
        print(f"    resume jump target {target_page}; page {current_page} -> {jump_page}")
        human_scroll(page)
        if not human_click(page, jump_button):
            save_checkpoint(profession, license_type, current_page, "resume_jump_click_failed")
            raise PaginationBlocked(f"Could not jump to page {jump_page} while resuming toward page {target_page}")
        human_pause(MIN_PAGE_DELAY, MAX_PAGE_DELAY)
        if not wait_table_refresh(page, previous_signature):
            reason = "captcha_or_blocked_refresh" if captcha_detected(page) else "page_jump_refresh_timeout"
            save_checkpoint(profession, license_type, jump_page, reason)
            raise PaginationBlocked(f"Jump to page {jump_page} did not refresh in time: {reason}")


# ─── SCRAPE ───────────────────────────────────────────────────────────────────

def captcha_detected(page) -> bool:
    try:
        return bool(call(page, "captchaDetected()"))
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
    return outfile, writer


def scrape_results(page, writer, seen_licenses: set, profession: str,
                    license_type: str = "", start_page: int = 1) -> int:
    state = wait_results_state(page, RESULTS_WAIT)
    if state == "empty":
        print("    no results")
        return 0
    if state != "results":
        print("    results never appeared")
        return 0

    written, visited_signatures, current = 0, set(), 1
    start_page = max(1, int(start_page or 1))
    if start_page > 1:
        current = jump_to_resume_page(page, start_page, profession, license_type)
        print(f"    resuming at page {start_page}; landed on page {current}")

    while True:
        rows = call(page, "rows()") or []
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
                written += 1
                print(f"      + {lic} {row.get('full_name', '')}")
                human_pause(0.05, 0.18)

        info = call(page, "paginationInfo()") or {}
        next_btn = call_handle(page, "nextButton()")
        is_disabled = call(page, "isDisabled(window.__scraper.nextButton())")
        print("    pagination controls: "
              f"{info.get('totalButtons', 0)} total / {info.get('numberedButtons', 0)} numbered")
        if next_btn is None or is_disabled:
            break

        previous_signature = signature
        human_scroll(page)
        if not human_click(page, next_btn):
            print("    could not click next page")
            save_checkpoint(profession, license_type, current + 1, "next_click_failed")
            raise PaginationBlocked(f"Could not click Next at page {current}")
        current += 1
        human_pause(MIN_PAGE_DELAY, MAX_PAGE_DELAY)
        if not wait_table_refresh(page, previous_signature):
            reason = "captcha_or_blocked_refresh" if captcha_detected(page) else "page_refresh_timeout"
            print(f"    page {current} did not refresh in time; checkpoint saved")
            save_checkpoint(profession, license_type, current, reason)
            raise PaginationBlocked(
                f"Pagination stopped at {profession} / {license_type or '(profession only)'} page {current}: {reason}"
            )

    return written

# ─── BROWSER / PAGE LOAD ──────────────────────────────────────────────────────

def build_session(playwright):
    browser, context, page = new_stealth_browser(playwright, headless=HEADLESS)
    context.add_init_script(HELPERS_JS)
    page.set_default_navigation_timeout(PAGE_TIMEOUT)
    return browser, context, page


def load_search_page(page) -> bool:
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    human_pause(1.8, 3.2)
    end = time.time() + 25
    while time.time() < end:
        if call(page, "resolveCombo('GASOS_Profession_Type__c','profession')") is not None:
            return True
        if page.evaluate("typeof window.__scraper === 'undefined'"):
            page.add_script_tag(content=HELPERS_JS)
        time.sleep(0.5)
    return False


def dismiss_overlays(page):
    """Best-effort: click a cookie/consent accept button if one is present."""
    try:
        page.evaluate(
            "() => { var b = window.__scraper.findControl("
            "['accept all cookies','accept cookies','accept all','accept',"
            "'i accept','i agree','agree']); if (b) b.click(); }"
        )
    except Exception:
        pass


def prepare_form(page) -> bool:
    """Reload the search page and wait until the Profession combobox is usable."""
    if not load_search_page(page):
        return False
    dismiss_overlays(page)
    end = time.time() + SHORT_WAIT
    while time.time() < end:
        el = combo_el(page, PROFESSION_COMBO)
        if el is not None and not combo_disabled(el):
            return True
        time.sleep(0.3)
    return combo_el(page, PROFESSION_COMBO) is not None


def wait_dependent_ready(page, timeout=SHORT_WAIT) -> bool:
    """Wait for the License Type combobox to become enabled after a profession pick."""
    end = time.time() + timeout
    while time.time() < end:
        el = combo_el(page, LICENSE_COMBO)
        if el is not None and not combo_disabled(el):
            return True
        time.sleep(0.3)
    return False


def discover_license_types(page, timeout=SHORT_WAIT) -> list:
    end = time.time() + timeout
    while time.time() < end:
        opts = [lt for lt in get_options(page, LICENSE_COMBO)
                if normalize(lt) not in PLACEHOLDER_VALUES]
        if opts:
            return opts
        time.sleep(0.5)
    return []

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def scrape_session(page, writer, seen_licenses, checkpoint) -> int:
    """Runs the full profession/license loop on one already-open page.

    Raises PaginationBlocked if the site stalls mid-scrape so the caller can
    rotate proxies and retry from the checkpoint this function just saved.
    """
    total = 0
    if not load_search_page(page):
        print("[ERROR] Could not find the Profession combobox on the page.")
        dump_comboboxes(page)
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
        if not prepare_form(page):
            print("  page reload failed; skipping")
            continue
        if not select_retry(page, PROFESSION_COMBO, prof):
            print(f"  could not select profession '{prof}'")
            dump_comboboxes(page)
            continue

        wait_dependent_ready(page)
        lic_types = discover_license_types(page)

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
                if not prepare_form(page):
                    print("    reload failed"); continue
                if not select_retry(page, PROFESSION_COMBO, prof):
                    print("    profession reselect failed"); dump_comboboxes(page); continue
                wait_dependent_ready(page)
                if not select_retry(page, LICENSE_COMBO, lt):
                    print("    license select failed"); continue
                if not click_search(page):
                    print("    (Search button not confirmed — waiting for results anyway)")
                start_page = resume_page if checkpoint and prof == resume_profession and lt == resume_license else 1
                total += scrape_results(page, writer, seen_licenses, prof, lt, start_page)
                clear_checkpoint()
                checkpoint = {}
        else:
            if checkpoint and prof == resume_profession and resume_license:
                raise RuntimeError(
                    f"Checkpoint expected license type {resume_license!r}, but no license types were found."
                )
            print("  no license types found; searching profession only.")
            if not click_search(page):
                print("  (Search button not confirmed — waiting for results anyway)")
            start_page = resume_page if checkpoint and prof == resume_profession else 1
            total += scrape_results(page, writer, seen_licenses, prof, "", start_page)
            clear_checkpoint()
            checkpoint = {}

    return total


def run():
    total = 0
    seen_licenses = load_seen_licenses()
    outfile, writer = open_output_writer()

    try:
        with sync_playwright() as playwright:
            browser, context, page = build_session(playwright)
            checkpoint = load_checkpoint()
            try:
                total += scrape_session(page, writer, seen_licenses, checkpoint)
            except PaginationBlocked as exc:
                print(f"\n[BLOCKED] {exc}")
                print(f"[BLOCKED] Resume point saved in {CHECKPOINT_FILE}. Resolve the page/session issue and run again.")
            except KeyboardInterrupt:
                print("\n[INFO] interrupted by user.")
            finally:
                context.close()
                browser.close()
    finally:
        outfile.close()

    print(f"\n[DONE] {total} active records written to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    run()
