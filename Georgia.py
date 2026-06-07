import csv
import os
import time

import undetected_chromedriver as uc
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ─── CONFIGURATION ────────────────────────────────────────────────────────────

SEARCH_URL         = "https://goals.sos.ga.gov/GASOSOneStop/s/licensee-search"
OUTPUT_FILE        = "georgia_sos_active.csv"
WAIT               = 30
WAIT_DETAIL        = 20
HEADLESS           = False
CHROMEDRIVER_MAJOR = 148

CSV_FIELDS = [
    "first_name", "middle_name", "last_name", "address",
    "license_number", "profession", "license_type", "sub_type",
    "obtained_by", "status", "issued", "expires", "last_renewal_date",
]

PLACEHOLDER_VALUES = {
    "",
    "all",
    "none",
    "--none--",
    "select an option",
    "select a profession",
    "select profession type",
    "select a license type",
}

# ─── JAVASCRIPT SNIPPETS ──────────────────────────────────────────────────────

# Detect which profession is currently selected in the combobox.
_JS_GET_COMBOBOX_VALUE = """
var name = arguments[0];
var btn = document.querySelector('button[role="combobox"][name="' + name + '"]');
if (!btn) return '';
var span = btn.querySelector('span.slds-media__body span');
if (span && span.innerText.trim()) return span.innerText.trim();
var text = (btn.innerText || '').trim();
if (text) return text;
var label = (btn.getAttribute('aria-label') || '').trim();
return label;
"""

# Detect the currently selected License Type value from the button label.
_JS_GET_VISIBLE_OPTIONS = """
var listbox = document.querySelector('div[role="listbox"]');
if (!listbox) return [];
var options = [];
var nodes = listbox.querySelectorAll('[role="option"]');
for (var node of nodes) {
    var text = (node.innerText || '').trim();
    if (text) options.push(text);
}
return options;
"""

# Detect ALL currently visible combobox selections (returns {fieldName: selectedText}).
_JS_GET_ALL_COMBOBOX_SELECTIONS = """
var result = {};
var btns = document.querySelectorAll('button[role="combobox"]');
for (var b of btns) {
    var name = b.getAttribute('name') || b.getAttribute('data-name') || '';
    var span = b.querySelector('span.slds-media__body span');
    var text = span ? span.innerText.trim() : b.innerText.trim();
    if (name && text) result[name] = text;
}
return result;
"""

# Watch for results table appearance.
_JS_HAS_RESULTS = """
return document.querySelectorAll('table.table-default tbody tr, button[aria-label^="SELECT License Number"]').length > 0;
"""

_JS_RESULTS_STATE = """
function hasText(pattern) {
    var nodes = document.querySelectorAll('body *');
    for (var i = 0; i < nodes.length; i++) {
        var text = (nodes[i].innerText || '').trim();
        if (text && text.toLowerCase().indexOf(pattern) !== -1) return true;
    }
    return false;
}

if (document.querySelectorAll('button[aria-label^="SELECT License Number"]').length > 0) return 'results';
if (document.querySelectorAll('table.table-default tbody tr').length > 0) return 'results';
if (document.querySelectorAll('button[aria-label^="Navigate to page"]').length > 0) return 'results';
if (hasText('no results')) return 'empty';
if (hasText('no records')) return 'empty';
if (hasText('no data')) return 'empty';
return '';
"""

# Get rows from the result table.
_JS_GET_ROWS = """
var rows = [];
var trs = document.querySelectorAll('table.table-default tbody tr');
for (var tr of trs) {
    var tds = tr.querySelectorAll('td');
    if (tds.length < 6) continue;
    rows.push({
        fullName:      tds[0].innerText.trim(),
        licenseNumber: tds[1].innerText.trim(),
        status:        tds[5].innerText.trim()
    });
}
return rows;
"""

# Click the SELECT button for a given license number.
_JS_CLICK_SELECT = """
var lic = arguments[0];
var btns = document.querySelectorAll('button[aria-label]');
for (var b of btns) {
    if (b.getAttribute('aria-label') === 'SELECT License Number ' + lic) {
        b.click();
        return true;
    }
}
return false;
"""

# Pagination: get available page numbers.
_JS_GET_PAGES = """
var pages = [];
var btns = document.querySelectorAll('button[aria-label^="Navigate to page"]');
for (var b of btns) {
    var m = b.getAttribute('aria-label').match(/\d+/);
    if (m) pages.push(parseInt(m[0]));
}
return pages;
"""

# Pagination: click a specific page number.
_JS_CLICK_PAGE = """
var num = arguments[0];
var btns = document.querySelectorAll('button[aria-label="Navigate to page ' + num + '"]');
if (btns.length) { btns[0].click(); return true; }
return false;
"""

# Extract a field from the detail panel by its label text.
_JS_LABEL_VALUE = """
var label = arguments[0];
var divs = document.querySelectorAll('div.title-label');
for (var d of divs) {
    if (d.innerText.trim() === label) {
        var sib = d.nextElementSibling;
        while (sib) {
            if (sib.tagName === 'P') return sib.innerText.trim();
            sib = sib.nextElementSibling;
        }
    }
}
return '';
"""

# ─── DRIVER ───────────────────────────────────────────────────────────────────

def build_driver() -> uc.Chrome:
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")

    profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
    os.makedirs(profile_dir, exist_ok=True)
    options.add_argument(f"--user-data-dir={profile_dir}")

    driver = uc.Chrome(
        options=options,
        headless=HEADLESS,
        version_main=CHROMEDRIVER_MAJOR,
    )
    driver.set_page_load_timeout(120)
    driver.set_script_timeout(120)
    return driver


def js(driver, script, *args):
    return driver.execute_script(script, *args)


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()

# ─── WAIT HELPERS ─────────────────────────────────────────────────────────────

def wait_for_results(driver, timeout=WAIT) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script(_JS_RESULTS_STATE) in ("results", "empty")
        )
        return True
    except TimeoutException:
        return False


def wait_for_detail(driver, timeout=WAIT_DETAIL) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: bool(js(d, _JS_LABEL_VALUE, "FIRST NAME"))
        )
        return True
    except TimeoutException:
        return False


def wait_for_search_controls(driver, timeout=WAIT) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: bool(d.find_elements(By.CSS_SELECTOR, 'button[role="combobox"]'))
        )
        return True
    except TimeoutException:
        return False

# ─── READ CURRENT FILTER STATE FROM BROWSER ──────────────────────────────────

def get_combobox_button(driver, name: str):
    return driver.find_element(By.CSS_SELECTOR, f'button[role="combobox"][name="{name}"]')


def open_combobox(driver, wait, name: str):
    button = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f'button[role="combobox"][name="{name}"]'))
    )
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    try:
        button.click()
    except Exception:
        driver.execute_script("arguments[0].click();", button)

    wait.until(lambda d: get_combobox_button(d, name).get_attribute("aria-expanded") == "true")


def close_dropdown(driver):
    try:
        from selenium.webdriver.common.keys import Keys

        driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    except Exception:
        pass


def get_combobox_value(driver, name: str) -> str:
    return js(driver, _JS_GET_COMBOBOX_VALUE, name) or ""


def collect_combobox_options(driver, wait, name: str) -> list[str]:
    button = get_combobox_button(driver, name)
    if (button.get_attribute("disabled") or "").lower() == "true":
        return []

    open_combobox(driver, wait, name)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="listbox"]')))
        options = js(driver, _JS_GET_VISIBLE_OPTIONS) or []
        cleaned = []
        for option in options:
            option = option.strip()
            if not option:
                continue
            if normalize_text(option) in PLACEHOLDER_VALUES:
                continue
            if option not in cleaned:
                cleaned.append(option)
        return cleaned
    finally:
        close_dropdown(driver)


def select_combobox_option(driver, wait, name: str, option_text: str):
    open_combobox(driver, wait, name)
    option_xpath = f'//div[@role="listbox"]//*[@role="option" and normalize-space()={repr(option_text)}]'
    try:
        option = wait.until(EC.element_to_be_clickable((By.XPATH, option_xpath)))
    except TimeoutException as exc:
        close_dropdown(driver)
        raise RuntimeError(f"Could not find '{option_text}' in combobox '{name}'.") from exc

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", option)
    try:
        option.click()
    except Exception:
        driver.execute_script("arguments[0].click();", option)

    wait.until(lambda d: normalize_text(get_combobox_value(d, name)) == normalize_text(option_text))


def select_profession(driver, wait, profession: str):
    select_combobox_option(driver, wait, "GASOS_Profession_Type__c", profession)
    time.sleep(0.5)


def select_license_type(driver, wait, license_type: str):
    select_combobox_option(driver, wait, "GASOS_License_Type__c", license_type)
    time.sleep(0.5)


def read_current_selections(driver) -> dict:
    raw = {
        "GASOS_Profession_Type__c": get_combobox_value(driver, "GASOS_Profession_Type__c"),
        "GASOS_License_Type__c": get_combobox_value(driver, "GASOS_License_Type__c"),
    }
    cleaned = {}
    for k, v in raw.items():
        if v and normalize_text(v) not in PLACEHOLDER_VALUES:
            cleaned[k] = v
    return cleaned


# ─── DETAIL EXTRACTION ────────────────────────────────────────────────────────

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


def extract_detail(driver) -> dict:
    return {field: js(driver, _JS_LABEL_VALUE, label) or ""
            for field, label in DETAIL_FIELDS.items()}

# ─── CLICK SEARCH (robust) ────────────────────────────────────────────────────

_JS_CLICK_SEARCH = """
var btns = document.querySelectorAll('button.slds-button_brand, button[type="button"]');
for (var b of btns) {
    var t = b.innerText.trim();
    if (t === 'Search' && b.getAttribute('role') !== 'combobox') {
        b.click();
        return true;
    }
}
return false;
"""

# ─── SCRAPE RESULTS (called after user has set filters & script clicks Search) ─

def scrape_current_results(driver, writer, profession_label: str, lic_type_label: str, click_search: bool = True) -> int:
    """
    Assumes the search form already has the right filters set.
    Clicks Search, then paginates through all results, writing active records.
    """
    if click_search:
        print(f"\n[SCRAPE] Clicking Search…")
        if not js(driver, _JS_CLICK_SEARCH):
            print("  [WARN] Search button not found — trying Enter key fallback")
            from selenium.webdriver.common.keys import Keys
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.RETURN)
            except Exception:
                pass
    else:
        print(f"\n[SCRAPE] Waiting for Search results…")

    if not wait_for_results(driver, timeout=120):
        print("  [INFO] Search did not reach a results state in time.")
        return 0

    state = js(driver, _JS_RESULTS_STATE)
    if state == "empty":
        print("  [INFO] No results found for this combination.")
        return 0

    count = 0
    visited_pages: set = set()
    current_page = 1

    while True:
        if current_page in visited_pages:
            break
        visited_pages.add(current_page)

        rows   = js(driver, _JS_GET_ROWS) or []
        active = [r for r in rows if r.get("status", "").lower() == "active"]
        print(f"  Page {current_page}: {len(active)}/{len(rows)} active rows")

        for row in active:
            lic_num = row["licenseNumber"]

            if not js(driver, _JS_CLICK_SELECT, lic_num):
                print(f"    [WARN] Could not click SELECT for {lic_num}")
                continue

            if not wait_for_detail(driver):
                print(f"    [WARN] Detail timed out for {lic_num} — skipping")
                driver.back()
                wait_for_results(driver)
                continue

            record = extract_detail(driver)
            writer.writerow(record)
            count += 1
            print(f"    ✓ {record['license_number']} – "
                  f"{record['first_name']} {record['last_name']}")

            driver.back()
            wait_for_results(driver)

        # ── Advance to next page ───────────────────────────────────────────
        all_pages  = sorted(set(js(driver, _JS_GET_PAGES) or []))
        next_pages = [p for p in all_pages if p > current_page and p not in visited_pages]
        if not next_pages:
            break

        current_page = next_pages[0]
        if not js(driver, _JS_CLICK_PAGE, current_page):
            print(f"  [WARN] Could not navigate to page {current_page}")
            break
        wait_for_results(driver)

    return count


def load_search_page(driver):
    driver.get(SEARCH_URL)
    time.sleep(3)
    if not wait_for_search_controls(driver):
        raise RuntimeError("Search controls did not load.")


def scrape_all_filters(driver, writer, outfile) -> int:
    total = 0
    wait = WebDriverWait(driver, WAIT)

    load_search_page(driver)

    profession_options = collect_combobox_options(driver, wait, "GASOS_Profession_Type__c")
    if not profession_options:
        raise RuntimeError("No profession options were detected.")

    print(f"[INFO] Found {len(profession_options)} profession option(s).")

    for profession in profession_options:
        print(f"\n[INFO] Selecting profession: {profession}")
        try:
            load_search_page(driver)
            select_profession(driver, wait, profession)
        except Exception as exc:
            print(f"  [WARN] Could not select profession '{profession}': {exc}")
            continue

        try:
            license_options = collect_combobox_options(driver, wait, "GASOS_License_Type__c")
        except Exception:
            license_options = []

        license_options = [
            option for option in license_options
            if normalize_text(option) not in PLACEHOLDER_VALUES
        ]

        if license_options:
            print(f"  [INFO] Found {len(license_options)} license type option(s).")
            for license_type in license_options:
                print(f"  [INFO] Selecting license type: {license_type}")
                try:
                    load_search_page(driver)
                    select_profession(driver, wait, profession)
                    select_license_type(driver, wait, license_type)
                except Exception as exc:
                    print(f"    [WARN] Could not select '{profession}' / '{license_type}': {exc}")
                    continue

                total += scrape_current_results(driver, writer, profession, license_type)
                outfile.flush()
        else:
            print("  [INFO] No license type options detected; scraping profession only.")
            total += scrape_current_results(driver, writer, profession, "")
            outfile.flush()

    return total


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    driver = build_driver()
    total  = 0

    print("[INFO] Opening search page…")
    driver.get(SEARCH_URL)
    time.sleep(3)  # let LWC boot without waiting for a specific element
    print("[INFO] Browser is open. You are in control of the dropdowns.\n")

    file_exists = os.path.exists(OUTPUT_FILE)
    outfile = open(OUTPUT_FILE, "a", newline="", encoding="utf-8")
    writer  = csv.DictWriter(outfile, fieldnames=CSV_FIELDS)
    if not file_exists:
        writer.writeheader()

    try:
        while True:
            # ── Let user pick filters manually ────────────────────────────
            selections = wait_for_user_to_select(driver)

            profession_label = selections.get("GASOS_Profession_Type__c", "Unknown")
            lic_type_label   = selections.get("GASOS_License_Type__c", "")

            print(f"\n[START] Scraping: {profession_label}"
                  + (f" / {lic_type_label}" if lic_type_label else ""))

            n = scrape_current_results(driver, writer, profession_label, lic_type_label)
            total += n
            outfile.flush()

            print(f"\n[DONE] {n} records written for this batch (running total: {total})")

            # ── Ask whether to continue with another set of filters ────────
            print("\n" + "-" * 60)
            again = input("Scrape another profession/license type? [Y/n]: ").strip().lower()
            if again in ("n", "no"):
                break

            # Reload the search page so filters are fresh
            print("[INFO] Reloading search page for next selection…")
            driver.get(SEARCH_URL)
            time.sleep(3)

    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    finally:
        outfile.close()
        driver.quit()

    print(f"\n[DONE] Total: {total} active records written to '{OUTPUT_FILE}'")


def main_automated():
    driver = build_driver()
    total = 0

    print("[INFO] Opening search page...")
    driver.get(SEARCH_URL)
    time.sleep(3)
    print("[INFO] Browser is open. Select profession, license type, and click Search yourself.\n")
    print("[INFO] After Search is clicked, come back here and press ENTER.\n")

    file_exists = os.path.exists(OUTPUT_FILE)
    outfile = open(OUTPUT_FILE, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(outfile, fieldnames=CSV_FIELDS)
    if not file_exists:
        writer.writeheader()

    try:
        input("Press ENTER after you have selected filters and clicked Search in Chrome: ")

        if not wait_for_results(driver, timeout=120):
            print("[INFO] Search did not reach a results state in time.")
        elif js(driver, _JS_RESULTS_STATE) == "empty":
            print("[INFO] Search completed but returned no results.")
        else:
            total = scrape_current_results(driver, writer, "", "", click_search=False)
            outfile.flush()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    finally:
        outfile.close()
        driver.quit()

    print(f"\n[DONE] Total: {total} active records written to '{OUTPUT_FILE}'")


if __name__ == "__main__":
    main_automated()
