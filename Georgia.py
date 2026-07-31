# import csv
# import os
# import time

# import undetected_chromedriver as uc
# from selenium.common.exceptions import TimeoutException
# from selenium.webdriver.common.by import By
# from selenium.webdriver.support import expected_conditions as EC
# from selenium.webdriver.support.ui import WebDriverWait

# # ─── CONFIGURATION ────────────────────────────────────────────────────────────

# SEARCH_URL         = "https://goals.sos.ga.gov/GASOSOneStop/s/licensee-search"
# OUTPUT_FILE        = "georgia_sos_active.csv"
# WAIT               = 10
# WAIT_DETAIL        = 10
# HEADLESS           = False
# CHROMEDRIVER_MAJOR = 150

# CSV_FIELDS = [
#     "first_name", "middle_name", "last_name", "address",
#     "license_number", "profession", "license_type", "sub_type",
#     "obtained_by", "status", "issued", "expires", "last_renewal_date",
# ]

# PLACEHOLDER_VALUES = {
#     "",
#     "all",
#     "none",
#     "--none--",
#     "select an option",
#     "select a profession",
#     "select profession type",
#     "select a license type",
# }

# # ─── JAVASCRIPT SNIPPETS ──────────────────────────────────────────────────────

# # Detect which profession is currently selected in the combobox.
# _JS_GET_COMBOBOX_VALUE = """
# var name = arguments[0];
# var btn = document.querySelector('button[role="combobox"][name="' + name + '"]');
# if (!btn) return '';
# var span = btn.querySelector('span.slds-media__body span');
# if (span && span.innerText.trim()) return span.innerText.trim();
# var text = (btn.innerText || '').trim();
# if (text) return text;
# var label = (btn.getAttribute('aria-label') || '').trim();
# return label;
# """

# # Detect the currently selected License Type value from the button label.
# _JS_GET_VISIBLE_OPTIONS = """
# var listbox = document.querySelector('div[role="listbox"]');
# if (!listbox) return [];
# var options = [];
# var nodes = listbox.querySelectorAll('[role="option"]');
# for (var node of nodes) {
#     var text = (node.innerText || '').trim();
#     if (text) options.push(text);
# }
# return options;
# """

# # Detect ALL currently visible combobox selections (returns {fieldName: selectedText}).
# _JS_GET_ALL_COMBOBOX_SELECTIONS = """
# var result = {};
# var btns = document.querySelectorAll('button[role="combobox"]');
# for (var b of btns) {
#     var name = b.getAttribute('name') || b.getAttribute('data-name') || '';
#     var span = b.querySelector('span.slds-media__body span');
#     var text = span ? span.innerText.trim() : b.innerText.trim();
#     if (name && text) result[name] = text;
# }
# return result;
# """

# # Watch for results table appearance.
# _JS_HAS_RESULTS = """
# return document.querySelectorAll('table.table-default tbody tr, button[aria-label^="SELECT License Number"]').length > 0;
# """

# _JS_RESULTS_STATE = """
# function hasText(pattern) {
#     var nodes = document.querySelectorAll('body *');
#     for (var i = 0; i < nodes.length; i++) {
#         var text = (nodes[i].innerText || '').trim();
#         if (text && text.toLowerCase().indexOf(pattern) !== -1) return true;
#     }
#     return false;
# }

# if (document.querySelectorAll('button[aria-label^="SELECT License Number"]').length > 0) return 'results';
# if (document.querySelectorAll('table.table-default tbody tr').length > 0) return 'results';
# if (document.querySelectorAll('button[aria-label^="Navigate to page"]').length > 0) return 'results';
# if (hasText('no results')) return 'empty';
# if (hasText('no records')) return 'empty';
# if (hasText('no data')) return 'empty';
# return '';
# """

# # Get rows from the result table.
# _JS_GET_ROWS = """
# var rows = [];
# var trs = document.querySelectorAll('table.table-default tbody tr');
# for (var tr of trs) {
#     var tds = tr.querySelectorAll('td');
#     if (tds.length < 6) continue;
#     rows.push({
#         fullName:      tds[0].innerText.trim(),
#         licenseNumber: tds[1].innerText.trim(),
#         status:        tds[5].innerText.trim()
#     });
# }
# return rows;
# """

# # Click the SELECT button for a given license number.
# _JS_CLICK_SELECT = """
# var lic = arguments[0];
# var btns = document.querySelectorAll('button[aria-label]');
# for (var b of btns) {
#     if (b.getAttribute('aria-label') === 'SELECT License Number ' + lic) {
#         b.click();
#         return true;
#     }
# }
# return false;
# """

# # Pagination: get available page numbers.
# _JS_GET_PAGES = """
# var pages = [];
# var btns = document.querySelectorAll('button[aria-label^="Navigate to page"]');
# for (var b of btns) {
#     var m = b.getAttribute('aria-label').match(/\d+/);
#     if (m) pages.push(parseInt(m[0]));
# }
# return pages;
# """

# # Pagination: click a specific page number.
# _JS_CLICK_PAGE = """
# var num = arguments[0];
# var btns = document.querySelectorAll('button[aria-label="Navigate to page ' + num + '"]');
# if (btns.length) { btns[0].click(); return true; }
# return false;
# """

# # Extract a field from the detail panel by its label text.
# _JS_LABEL_VALUE = """
# var label = arguments[0];
# var divs = document.querySelectorAll('div.title-label');
# for (var d of divs) {
#     if (d.innerText.trim() === label) {
#         var sib = d.nextElementSibling;
#         while (sib) {
#             if (sib.tagName === 'P') return sib.innerText.trim();
#             sib = sib.nextElementSibling;
#         }
#     }
# }
# return '';
# """

# # ─── DRIVER ───────────────────────────────────────────────────────────────────

# def build_driver() -> uc.Chrome:
#     options = uc.ChromeOptions()
#     options.add_argument("--start-maximized")
#     options.add_argument("--disable-notifications")
#     options.add_argument("--disable-popup-blocking")

#     profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
#     os.makedirs(profile_dir, exist_ok=True)
#     options.add_argument(f"--user-data-dir={profile_dir}")

#     driver = uc.Chrome(
#         options=options,
#         headless=HEADLESS,
#         version_main=CHROMEDRIVER_MAJOR,
#     )
#     driver.set_page_load_timeout(120)
#     driver.set_script_timeout(120)
#     return driver


# def js(driver, script, *args):
#     return driver.execute_script(script, *args)


# def normalize_text(text: str) -> str:
#     return " ".join((text or "").split()).strip().lower()

# # ─── WAIT HELPERS ─────────────────────────────────────────────────────────────

# def wait_for_results(driver, timeout=WAIT) -> bool:
#     try:
#         WebDriverWait(driver, timeout).until(
#             lambda d: d.execute_script(_JS_RESULTS_STATE) in ("results", "empty")
#         )
#         return True
#     except TimeoutException:
#         return False


# def wait_for_detail(driver, timeout=WAIT_DETAIL) -> bool:
#     try:
#         WebDriverWait(driver, timeout).until(
#             lambda d: bool(js(d, _JS_LABEL_VALUE, "FIRST NAME"))
#         )
#         return True
#     except TimeoutException:
#         return False


# def wait_for_search_controls(driver, timeout=WAIT) -> bool:
#     try:
#         WebDriverWait(driver, timeout).until(
#             lambda d: bool(d.find_elements(By.CSS_SELECTOR, 'button[role="combobox"]'))
#         )
#         return True
#     except TimeoutException:
#         return False

# # ─── READ CURRENT FILTER STATE FROM BROWSER ──────────────────────────────────

# def get_combobox_button(driver, name: str):
#     return driver.find_element(By.CSS_SELECTOR, f'button[role="combobox"][name="{name}"]')


# def open_combobox(driver, wait, name: str):
#     button = wait.until(
#         EC.presence_of_element_located((By.CSS_SELECTOR, f'button[role="combobox"][name="{name}"]'))
#     )
#     driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
#     try:
#         button.click()
#     except Exception:
#         driver.execute_script("arguments[0].click();", button)

#     wait.until(lambda d: get_combobox_button(d, name).get_attribute("aria-expanded") == "true")


# def close_dropdown(driver):
#     try:
#         from selenium.webdriver.common.keys import Keys

#         driver.switch_to.active_element.send_keys(Keys.ESCAPE)
#     except Exception:
#         pass


# def get_combobox_value(driver, name: str) -> str:
#     return js(driver, _JS_GET_COMBOBOX_VALUE, name) or ""


# def collect_combobox_options(driver, wait, name: str) -> list[str]:
#     button = get_combobox_button(driver, name)
#     if (button.get_attribute("disabled") or "").lower() == "true":
#         return []

#     open_combobox(driver, wait, name)
#     try:
#         wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="listbox"]')))
#         options = js(driver, _JS_GET_VISIBLE_OPTIONS) or []
#         cleaned = []
#         for option in options:
#             option = option.strip()
#             if not option:
#                 continue
#             if normalize_text(option) in PLACEHOLDER_VALUES:
#                 continue
#             if option not in cleaned:
#                 cleaned.append(option)
#         return cleaned
#     finally:
#         close_dropdown(driver)


# def select_combobox_option(driver, wait, name: str, option_text: str):
#     open_combobox(driver, wait, name)
#     option_xpath = f'//div[@role="listbox"]//*[@role="option" and normalize-space()={repr(option_text)}]'
#     try:
#         option = wait.until(EC.element_to_be_clickable((By.XPATH, option_xpath)))
#     except TimeoutException as exc:
#         close_dropdown(driver)
#         raise RuntimeError(f"Could not find '{option_text}' in combobox '{name}'.") from exc

#     driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", option)
#     try:
#         option.click()
#     except Exception:
#         driver.execute_script("arguments[0].click();", option)

#     wait.until(lambda d: normalize_text(get_combobox_value(d, name)) == normalize_text(option_text))


# def select_profession(driver, wait, profession: str):
#     select_combobox_option(driver, wait, "GASOS_Profession_Type__c", profession)
#     time.sleep(0.5)


# def select_license_type(driver, wait, license_type: str):
#     select_combobox_option(driver, wait, "GASOS_License_Type__c", license_type)
#     time.sleep(0.5)


# def read_current_selections(driver) -> dict:
#     raw = {
#         "GASOS_Profession_Type__c": get_combobox_value(driver, "GASOS_Profession_Type__c"),
#         "GASOS_License_Type__c": get_combobox_value(driver, "GASOS_License_Type__c"),
#     }
#     cleaned = {}
#     for k, v in raw.items():
#         if v and normalize_text(v) not in PLACEHOLDER_VALUES:
#             cleaned[k] = v
#     return cleaned


# # ─── DETAIL EXTRACTION ────────────────────────────────────────────────────────

# DETAIL_FIELDS = {
#     "first_name":        "FIRST NAME",
#     "middle_name":       "MIDDLE",
#     "last_name":         "LAST NAME",
#     "address":           "ADDRESS",
#     "license_number":    "LICENSE NUMBER",
#     "profession":        "PROFESSION",
#     "license_type":      "LICENSE TYPE",
#     "sub_type":          "SUB TYPE",
#     "obtained_by":       "OBTAINED BY",
#     "status":            "STATUS",
#     "issued":            "ISSUED",
#     "expires":           "EXPIRES",
#     "last_renewal_date": "LAST RENEWAL DATE",
# }


# def extract_detail(driver) -> dict:
#     return {field: js(driver, _JS_LABEL_VALUE, label) or ""
#             for field, label in DETAIL_FIELDS.items()}

# # ─── CLICK SEARCH (robust) ────────────────────────────────────────────────────

# _JS_CLICK_SEARCH = """
# var btns = document.querySelectorAll('button.slds-button_brand, button[type="button"]');
# for (var b of btns) {
#     var t = b.innerText.trim();
#     if (t === 'Search' && b.getAttribute('role') !== 'combobox') {
#         b.click();
#         return true;
#     }
# }
# return false;
# """

# # ─── SCRAPE RESULTS (called after user has set filters & script clicks Search) ─

# def scrape_current_results(driver, writer, profession_label: str, lic_type_label: str, click_search: bool = True) -> int:
#     """
#     Assumes the search form already has the right filters set.
#     Clicks Search, then paginates through all results, writing active records.
#     """
#     if click_search:
#         print(f"\n[SCRAPE] Clicking Search…")
#         if not js(driver, _JS_CLICK_SEARCH):
#             print("  [WARN] Search button not found — trying Enter key fallback")
#             from selenium.webdriver.common.keys import Keys
#             try:
#                 driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RETURN)
#             except Exception:
#                 pass
#     else:
#         print(f"\n[SCRAPE] Waiting for Search results…")

#     if not wait_for_results(driver, timeout=120):
#         print("  [INFO] Search did not reach a results state in time.")
#         return 0

#     state = js(driver, _JS_RESULTS_STATE)
#     if state == "empty":
#         print("  [INFO] No results found for this combination.")
#         return 0

#     count = 0
#     visited_pages: set = set()
#     current_page = 1

#     while True:
#         if current_page in visited_pages:
#             break
#         visited_pages.add(current_page)

#         rows   = js(driver, _JS_GET_ROWS) or []
#         active = [r for r in rows if r.get("status", "").lower() == "active"]
#         print(f"  Page {current_page}: {len(active)}/{len(rows)} active rows")

#         for row in active:
#             lic_num = row["licenseNumber"]

#             if not js(driver, _JS_CLICK_SELECT, lic_num):
#                 print(f"    [WARN] Could not click SELECT for {lic_num}")
#                 continue

#             if not wait_for_detail(driver):
#                 print(f"    [WARN] Detail timed out for {lic_num} — skipping")
#                 driver.back()
#                 wait_for_results(driver)
#                 continue

#             record = extract_detail(driver)
#             writer.writerow(record)
#             count += 1
#             print(f"    ✓ {record['license_number']} – "
#                   f"{record['first_name']} {record['last_name']}")

#             driver.back()
#             wait_for_results(driver)

#         # ── Advance to next page ───────────────────────────────────────────
#         all_pages  = sorted(set(js(driver, _JS_GET_PAGES) or []))
#         next_pages = [p for p in all_pages if p > current_page and p not in visited_pages]
#         if not next_pages:
#             break

#         current_page = next_pages[0]
#         if not js(driver, _JS_CLICK_PAGE, current_page):
#             print(f"  [WARN] Could not navigate to page {current_page}")
#             break
#         wait_for_results(driver)

#     return count


# def load_search_page(driver):
#     driver.get(SEARCH_URL)
#     time.sleep(3)
#     if not wait_for_search_controls(driver):
#         raise RuntimeError("Search controls did not load.")


# def scrape_all_filters(driver, writer, outfile) -> int:
#     total = 0
#     wait = WebDriverWait(driver, WAIT)

#     load_search_page(driver)

#     profession_options = collect_combobox_options(driver, wait, "GASOS_Profession_Type__c")
#     if not profession_options:
#         raise RuntimeError("No profession options were detected.")

#     print(f"[INFO] Found {len(profession_options)} profession option(s).")

#     for profession in profession_options:
#         print(f"\n[INFO] Selecting profession: {profession}")
#         try:
#             load_search_page(driver)
#             select_profession(driver, wait, profession)
#         except Exception as exc:
#             print(f"  [WARN] Could not select profession '{profession}': {exc}")
#             continue

#         try:
#             license_options = collect_combobox_options(driver, wait, "GASOS_License_Type__c")
#         except Exception:
#             license_options = []

#         license_options = [
#             option for option in license_options
#             if normalize_text(option) not in PLACEHOLDER_VALUES
#         ]

#         if license_options:
#             print(f"  [INFO] Found {len(license_options)} license type option(s).")
#             for license_type in license_options:
#                 print(f"  [INFO] Selecting license type: {license_type}")
#                 try:
#                     load_search_page(driver)
#                     select_profession(driver, wait, profession)
#                     select_license_type(driver, wait, license_type)
#                 except Exception as exc:
#                     print(f"    [WARN] Could not select '{profession}' / '{license_type}': {exc}")
#                     continue

#                 total += scrape_current_results(driver, writer, profession, license_type)
#                 outfile.flush()
#         else:
#             print("  [INFO] No license type options detected; scraping profession only.")
#             total += scrape_current_results(driver, writer, profession, "")
#             outfile.flush()

#     return total


# # ─── MAIN LOOP ────────────────────────────────────────────────────────────────

# def main():
#     driver = build_driver()
#     total  = 0

#     print("[INFO] Opening search page…")
#     driver.get(SEARCH_URL)
#     time.sleep(3)  # let LWC boot without waiting for a specific element
#     print("[INFO] Browser is open. You are in control of the dropdowns.\n")

#     file_exists = os.path.exists(OUTPUT_FILE)
#     outfile = open(OUTPUT_FILE, "a", newline="", encoding="utf-8")
#     writer  = csv.DictWriter(outfile, fieldnames=CSV_FIELDS)
#     if not file_exists:
#         writer.writeheader()

#     try:
#         while True:
#             # ── Let user pick filters manually ────────────────────────────
#             selections = wait_for_user_to_select(driver)

#             profession_label = selections.get("GASOS_Profession_Type__c", "Unknown")
#             lic_type_label   = selections.get("GASOS_License_Type__c", "")

#             print(f"\n[START] Scraping: {profession_label}"
#                   + (f" / {lic_type_label}" if lic_type_label else ""))

#             n = scrape_current_results(driver, writer, profession_label, lic_type_label)
#             total += n
#             outfile.flush()

#             print(f"\n[DONE] {n} records written for this batch (running total: {total})")

#             # ── Ask whether to continue with another set of filters ────────
#             print("\n" + "-" * 60)
#             again = input("Scrape another profession/license type? [Y/n]: ").strip().lower()
#             if again in ("n", "no"):
#                 break

#             # Reload the search page so filters are fresh
#             print("[INFO] Reloading search page for next selection…")
#             driver.get(SEARCH_URL)
#             time.sleep(3)

#     except KeyboardInterrupt:
#         print("\n[INFO] Stopped by user.")
#     finally:
#         outfile.close()
#         driver.quit()

#     print(f"\n[DONE] Total: {total} active records written to '{OUTPUT_FILE}'")


# def main_automated():
#     driver = build_driver()
#     total = 0

#     print("[INFO] Opening search page...")
#     driver.get(SEARCH_URL)
#     time.sleep(3)
#     print("[INFO] Browser is open. Select profession, license type, and click Search yourself.\n")
#     print("[INFO] After Search is clicked, come back here and press ENTER.\n")

#     file_exists = os.path.exists(OUTPUT_FILE)
#     outfile = open(OUTPUT_FILE, "a", newline="", encoding="utf-8")
#     writer = csv.DictWriter(outfile, fieldnames=CSV_FIELDS)
#     if not file_exists:
#         writer.writeheader()

#     try:
#         input("Press ENTER after you have selected filters and clicked Search in Chrome: ")

#         if not wait_for_results(driver, timeout=120):
#             print("[INFO] Search did not reach a results state in time.")
#         elif js(driver, _JS_RESULTS_STATE) == "empty":
#             print("[INFO] Search completed but returned no results.")
#         else:
#             total = scrape_current_results(driver, writer, "", "", click_search=False)
#             outfile.flush()
#     except KeyboardInterrupt:
#         print("\n[INFO] Stopped by user.")
#     finally:
#         outfile.close()
#         driver.quit()

#     print(f"\n[DONE] Total: {total} active records written to '{OUTPUT_FILE}'")


# if __name__ == "__main__":
#     main_automated()

"""
Georgia SOS licensee scraper — fully automated.

Why the original "wasn't clickable"
------------------------------------
The search UI is a Salesforce Experience Cloud (Lightning) site served under
`/s/`. Much of its markup — including the combobox buttons, the results table,
and the detail panel — lives inside **shadow DOM**. Selenium's `find_element`
and plain `document.querySelector` cannot cross shadow boundaries, so the
elements were never actually located, which is why clicks silently did nothing.

What changed
------------
  * Every DOM lookup pierces shadow DOM (see the injected `window.__scraper`
    helpers) and returns real elements to Python so we can click them natively.
  * Clicking tries a trusted native click first, then a JS click, then a full
    synthetic pointer/mouse event sequence. Lightning ignores some untrusted
    clicks, so the escalation matters.
  * The entire profession x license-type matrix is iterated automatically.
    All the manual steps (ENTER prompts, "select the dropdown yourself",
    the two separate main() entry points) are gone.
  * Records are de-duplicated by license number, so any SPA "back" that
    resets to page 1 can't produce duplicate rows.

Note: this targets a public government transparency database. Keep the pacing
gentle (the built-in waits already do) and check the site's terms of use for
automated-access rules before running at scale.
"""

import csv
import os
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
OUTPUT_FILE = "georgia_sos_active.csv"
HEADLESS    = False

# Leave as None to let undetected-chromedriver match your installed Chrome.
# Only pin a number here if auto-detection fails.
CHROMEDRIVER_MAJOR = 150

# Timeouts (seconds)
PAGE_TIMEOUT = 120
SHORT_WAIT   = 15
RESULTS_WAIT = 60
DETAIL_WAIT  = 20

CSV_FIELDS = [
    "first_name", "middle_name", "last_name", "address",
    "license_number", "profession", "license_type", "sub_type",
    "obtained_by", "status", "issued", "expires", "last_renewal_date",
]

PLACEHOLDER_VALUES = {
    "", "all", "none", "--none--", "select an option",
    "select a profession", "select profession type", "select a license type",
}

DETAIL_FIELDS = {
    "first_name":        "FIRST NAME",
    "middle_name":       "MIDDLE",
    "last_name":         "LAST NAME",
    "address":           "ADDRESS",
    "license_number":    "LICENSE NUMBER",
    "profession":        "PROFESSION",
    "license_type":      "LICENSE TYPE",
    "sub_type":          "SUB TYPE",
    "obtained_by":       "OBTAINED BY",
    "status":            "STATUS",
    "issued":            "ISSUED",
    "expires":           "EXPIRES",
    "last_renewal_date": "LAST RENEWAL DATE",
}

# ─── INJECTED SHADOW-DOM-AWARE HELPERS ────────────────────────────────────────
# Installed once per page load as window.__scraper. Every query walks into open
# shadow roots so we can see and return Lightning's real elements.

HELPERS_JS = r"""
window.__scraper = (function () {
  function deepAll(selector, root) {
    const out = [];
    const roots = [root || document];
    while (roots.length) {
      const r = roots.pop();
      r.querySelectorAll(selector).forEach(e => out.push(e));
      r.querySelectorAll('*').forEach(e => { if (e.shadowRoot) roots.push(e.shadowRoot); });
    }
    return out;
  }
  function deepOne(selector, root) {
    const a = deepAll(selector, root);
    return a.length ? a[0] : null;
  }
  function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }

  function comboButton(name) {
    return deepOne('button[role="combobox"][name="' + name + '"]')
        || deepOne('button[role="combobox"][data-name="' + name + '"]');
  }
  function comboValue(name) {
    const b = comboButton(name);
    if (!b) return '';
    const span = b.querySelector('span.slds-media__body span') || b.querySelector('span');
    let t = span ? norm(span.innerText) : '';
    if (!t) t = norm(b.innerText);
    if (!t) t = norm(b.getAttribute('aria-label'));
    return t;
  }
  function options() {
    const box = deepOne('div[role="listbox"]');
    if (!box) return [];
    return Array.from(box.querySelectorAll('[role="option"]'))
                .map(o => norm(o.innerText)).filter(Boolean);
  }
  function optionEl(text) {
    const box = deepOne('div[role="listbox"]');
    if (!box) return null;
    const nodes = box.querySelectorAll('[role="option"]');
    for (const n of nodes) if (norm(n.innerText) === norm(text)) return n;
    return null;
  }
  function searchButton() {
    const btns = deepAll('button');
    for (const b of btns) {
      if (b.getAttribute('role') === 'combobox') continue;
      if (norm(b.innerText) === 'Search') return b;
    }
    return null;
  }
  function selectButton(lic) {
    return deepOne('button[aria-label="SELECT License Number ' + lic + '"]');
  }
  function pageButton(num) {
    return deepOne('button[aria-label="Navigate to page ' + num + '"]');
  }
  function pages() {
    return deepAll('button[aria-label^="Navigate to page"]')
      .map(b => { const m = (b.getAttribute('aria-label') || '').match(/\d+/); return m ? parseInt(m[0]) : null; })
      .filter(n => n !== null);
  }
  function rows() {
    const out = [];
    const trs = deepAll('table.table-default tbody tr');
    for (const tr of trs) {
      const tds = tr.querySelectorAll('td');
      if (tds.length < 6) continue;
      out.push({
        fullName:      norm(tds[0].innerText),
        licenseNumber: norm(tds[1].innerText),
        status:        norm(tds[5].innerText)
      });
    }
    return out;
  }
  function resultsState() {
    if (deepAll('button[aria-label^="SELECT License Number"]').length) return 'results';
    if (deepAll('table.table-default tbody tr').length) return 'results';
    if (deepAll('button[aria-label^="Navigate to page"]').length) return 'results';
    const body = norm((document.body && document.body.innerText) || '').toLowerCase();
    if (body.indexOf('no results') !== -1) return 'empty';
    if (body.indexOf('no records') !== -1) return 'empty';
    if (body.indexOf('no data')    !== -1) return 'empty';
    return '';
  }
  function labelValue(label) {
    const divs = deepAll('div.title-label');
    for (const d of divs) {
      if (norm(d.innerText) === norm(label)) {
        let sib = d.nextElementSibling;
        while (sib) { if (sib.tagName === 'P') return norm(sib.innerText); sib = sib.nextElementSibling; }
      }
    }
    return '';
  }
  function findControl(labels) {
    const btns = deepAll('button, a');
    for (const b of btns) {
      const t  = norm(b.innerText).toLowerCase();
      const al = norm(b.getAttribute('aria-label') || '').toLowerCase();
      if (labels.includes(t) || labels.includes(al)) return b;
    }
    return null;
  }
  return { deepOne, deepAll, comboButton, comboValue, options, optionEl,
           searchButton, selectButton, pageButton, pages, rows,
           resultsState, labelValue, findControl, norm };
})();
"""

SYNTHETIC_CLICK_JS = r"""
const el = arguments[0];
const r = el.getBoundingClientRect();
const base = {bubbles:true, cancelable:true, view:window,
              clientX:r.left + r.width/2, clientY:r.top + r.height/2, button:0};
function fire(type) {
  const E = type.indexOf('pointer') === 0 ? window.PointerEvent : window.MouseEvent;
  try { el.dispatchEvent(new E(type, base)); }
  catch (e) { el.dispatchEvent(new MouseEvent(type, base)); }
}
try { el.focus && el.focus(); } catch (e) {}
['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click'].forEach(fire);
"""

# ─── LOW-LEVEL HELPERS ────────────────────────────────────────────────────────

def js(driver, script, *args):
    return driver.execute_script(script, *args)


def inject_helpers(driver):
    driver.execute_script(HELPERS_JS)


def normalize(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def robust_click(driver, element) -> bool:
    """Native trusted click, then JS click, then a full synthetic event sequence."""
    if element is None:
        return False
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});", element)
    except Exception:
        pass
    try:
        element.click()
        return True
    except (ElementClickInterceptedException, ElementNotInteractableException,
            StaleElementReferenceException, Exception):
        pass
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        pass
    try:
        driver.execute_script(SYNTHETIC_CLICK_JS, element)
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

# ─── COMBOBOX INTERACTION ─────────────────────────────────────────────────────

def combo_button(driver, name):
    return js(driver, "return window.__scraper.comboButton(arguments[0]);", name)


def combo_value(driver, name) -> str:
    return js(driver, "return window.__scraper.comboValue(arguments[0]);", name) or ""


def combo_disabled(driver, name) -> bool:
    btn = combo_button(driver, name)
    if not btn:
        return True
    disabled = (btn.get_attribute("disabled") or "").lower()
    aria_dis = (btn.get_attribute("aria-disabled") or "").lower()
    return disabled in ("true", "disabled") or aria_dis == "true"


def open_combobox(driver, name) -> bool:
    btn = combo_button(driver, name)
    if not btn:
        return False
    robust_click(driver, btn)
    end = time.time() + SHORT_WAIT
    while time.time() < end:
        b = combo_button(driver, name)
        if b and b.get_attribute("aria-expanded") == "true":
            return True
        if js(driver, 'return !!window.__scraper.deepOne(\'div[role="listbox"]\');'):
            return True
        time.sleep(0.2)
    return False


def collect_options(driver, name) -> list:
    if combo_disabled(driver, name):
        return []
    if not open_combobox(driver, name):
        return []
    opts, end = [], time.time() + SHORT_WAIT
    while time.time() < end:
        opts = js(driver, "return window.__scraper.options();") or []
        if opts:
            break
        time.sleep(0.2)
    send_escape(driver)
    cleaned = []
    for o in opts:
        o = (o or "").strip()
        if not o or normalize(o) in PLACEHOLDER_VALUES or o in cleaned:
            continue
        cleaned.append(o)
    return cleaned


def select_option(driver, name, text) -> bool:
    if not open_combobox(driver, name):
        return False
    el, end = None, time.time() + SHORT_WAIT
    while time.time() < end:
        el = js(driver, "return window.__scraper.optionEl(arguments[0]);", text)
        if el:
            break
        time.sleep(0.2)
    if not el:
        send_escape(driver)
        return False
    robust_click(driver, el)
    target, end = normalize(text), time.time() + SHORT_WAIT
    while time.time() < end:
        if normalize(combo_value(driver, name)) == target:
            return True
        time.sleep(0.2)
    return normalize(combo_value(driver, name)) == target


def select_profession(driver, profession) -> bool:
    return select_option(driver, "GASOS_Profession_Type__c", profession)


def select_license_type(driver, license_type) -> bool:
    return select_option(driver, "GASOS_License_Type__c", license_type)

# ─── SEARCH / RESULTS / DETAIL ────────────────────────────────────────────────

def click_search(driver) -> bool:
    btn = js(driver, "return window.__scraper.searchButton();")
    return robust_click(driver, btn) if btn else False


def wait_results_state(driver, timeout=RESULTS_WAIT) -> str:
    end = time.time() + timeout
    while time.time() < end:
        state = js(driver, "return window.__scraper.resultsState();")
        if state in ("results", "empty"):
            return state
        time.sleep(0.3)
    return ""


def wait_for_detail(driver, timeout=DETAIL_WAIT) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if js(driver, "return window.__scraper.labelValue('FIRST NAME');"):
            return True
        time.sleep(0.3)
    return False


def extract_detail(driver) -> dict:
    return {field: js(driver, "return window.__scraper.labelValue(arguments[0]);", label) or ""
            for field, label in DETAIL_FIELDS.items()}


def return_to_results(driver) -> bool:
    """Detail may be a modal/panel (Escape or a Back/Close button restores the
    list) or a full navigation (browser Back). Try the cheap options first."""
    if js(driver, "return window.__scraper.resultsState();") == "results":
        return True
    back = js(driver,
              "return window.__scraper.findControl(['back','close','back to results','return']);")
    if back:
        robust_click(driver, back)
        if wait_results_state(driver, SHORT_WAIT) == "results":
            return True
    send_escape(driver)
    if wait_results_state(driver, 3) == "results":
        return True
    try:
        driver.back()
    except Exception:
        pass
    return wait_results_state(driver, SHORT_WAIT) == "results"

# ─── SCRAPE ───────────────────────────────────────────────────────────────────

def scrape_results(driver, writer, seen_licenses: set) -> int:
    state = wait_results_state(driver, RESULTS_WAIT)
    if state == "empty":
        print("    no results")
        return 0
    if state != "results":
        print("    results never appeared")
        return 0

    written, visited, current = 0, set(), 1
    while True:
        if current in visited:
            break
        visited.add(current)

        rows = js(driver, "return window.__scraper.rows();") or []
        active = [r for r in rows
                  if r.get("status", "").lower() == "active"
                  and r.get("licenseNumber") not in seen_licenses]
        print(f"    page {current}: {len(active)} new active / {len(rows)} rows")

        for row in active:
            lic = row["licenseNumber"]
            seen_licenses.add(lic)

            sel = js(driver, "return window.__scraper.selectButton(arguments[0]);", lic)
            if not robust_click(driver, sel):
                print(f"      could not open {lic}")
                continue
            if not wait_for_detail(driver):
                print(f"      detail timeout {lic}")
                return_to_results(driver)
                continue

            writer.writerow(extract_detail(driver))
            written += 1
            print(f"      + {lic}")

            if not return_to_results(driver):
                print("      lost results view after detail; ending this batch")
                return written

        pages = sorted(set(js(driver, "return window.__scraper.pages();") or []))
        nxt = [p for p in pages if p > current and p not in visited]
        if not nxt:
            break
        current = nxt[0]
        pbtn = js(driver, "return window.__scraper.pageButton(arguments[0]);", current)
        if not robust_click(driver, pbtn):
            print(f"    could not navigate to page {current}")
            break
        wait_results_state(driver, RESULTS_WAIT)

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
    time.sleep(2)  # let the Lightning app boot
    inject_helpers(driver)
    end = time.time() + 25
    while time.time() < end:
        if not js(driver, "return typeof window.__scraper !== 'undefined';"):
            inject_helpers(driver)
        if js(driver, "return !!(window.__scraper && "
                      "window.__scraper.comboButton('GASOS_Profession_Type__c'));"):
            return True
        time.sleep(0.5)
    return False

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run():
    driver = build_driver()
    total = 0
    seen_licenses: set = set()

    file_exists = os.path.exists(OUTPUT_FILE)
    outfile = open(OUTPUT_FILE, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(outfile, fieldnames=CSV_FIELDS)
    if not file_exists:
        writer.writeheader()

    try:
        if not load_search_page(driver):
            raise RuntimeError("Search page / comboboxes never loaded.")

        professions = collect_options(driver, "GASOS_Profession_Type__c")
        if not professions:
            raise RuntimeError("No profession options detected — site markup may have changed.")
        print(f"[INFO] {len(professions)} profession(s) found.")

        for prof in professions:
            print(f"\n[PROF] {prof}")
            if not load_search_page(driver):
                print("  page reload failed; skipping")
                continue
            if not select_profession(driver, prof):
                print("  could not select profession; skipping")
                continue

            time.sleep(0.5)  # dependent License Type field repopulates
            lic_types = [lt for lt in collect_options(driver, "GASOS_License_Type__c")
                         if normalize(lt) not in PLACEHOLDER_VALUES]

            if lic_types:
                print(f"  {len(lic_types)} license type(s).")
                for lt in lic_types:
                    print(f"  [LIC] {lt}")
                    if not load_search_page(driver):
                        print("    reload failed"); continue
                    if not select_profession(driver, prof):
                        print("    profession reselect failed"); continue
                    time.sleep(0.5)
                    if not select_license_type(driver, lt):
                        print("    license select failed"); continue
                    if not click_search(driver):
                        print("    search click failed"); continue
                    total += scrape_results(driver, writer, seen_licenses)
                    outfile.flush()
            else:
                print("  no license types; searching profession only.")
                if not click_search(driver):
                    print("  search click failed"); continue
                total += scrape_results(driver, writer, seen_licenses)
                outfile.flush()

    except KeyboardInterrupt:
        print("\n[INFO] interrupted by user.")
    finally:
        outfile.close()
        driver.quit()

    print(f"\n[DONE] {total} active records written to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    run()




