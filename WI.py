import csv
import os
import re
import time
import traceback

# Wisconsin DSPS "LicensE" public license lookup (Salesforce Lightning / LWC site).
# Flow per keyword:
#   Search By      -> "License Type"
#   Category       -> "Health"
#   License Name   -> <keyword>
#   Search -> set page size to 100 -> paginate with the "Next" arrow.
#
# Unlike Pennsylvania.py the dropdowns here are NOT native <select> elements;
# they are SLDS combobox buttons that open a listbox, so they are driven by
# clicking the button and then the matching option.

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


# Verify this is the correct landing page for the license lookup before running.
LANDING_URL = "https://license.wi.gov/s/license-lookup"
WAIT_SECONDS = 45
PAGE_LOAD_SLEEP = 2.5
RESULT_WAIT_SECONDS = 45
RESULT_SETTLE_SECONDS = 3.0
EMPTY_STABLE_SECONDS = 10.0
HEADLESS = False
OUTPUT_FILE = "WI.csv"

SEARCH_BY = "License Type"
CATEGORY = "Health"

# Visible field labels on the form. The combobox <button> elements are located
# by these labels first (most reliable on Salesforce Lightning), then by the
# legacy `name` attribute as a fallback. Adjust the label text here if the page
# wording differs.
COMBOBOX_FIELDS = {
    "searchBy": {"label": "Search By", "name": "searchBy"},
    "professionCategory": {"label": "Category", "name": "professionCategory"},
    "professionsBusiness": {"label": "License Name", "name": "professionsBusiness"},
}

KEYWORDS = [
    "Dental Hygienist", #done
    "Dental Therapist", #done
    "Dentist",#done
    "Licensed Practical Nurse",
    "Licensed Professional Counselor",
    "Limited X-Ray Machine Operator Permit",
    "Medicine and Surgery - DO",
    "Medicine and Surgery - MD",
    "Nurse - Midwife",
    "Occupational Therapist",
    "Occupational Therapy Assistant",
    "Pharmacist",
    "Pharmacy (In-State)",
    "Pharmacy (Out-of-State)",
    "Pharmacy Technician",
    "Physical Therapist",
    "Physical Therapist Assistant",
    "Physician - DO",
    "Physician - DO Compact",
    "Physician - MD",
    "Physician - MD Compact",
    "Physician Assistant", #3done
    "Psychologist",
    "Radiographer, Licensed",
    "Registered Nurse",
    "Registered Sanitarian",
    "Resident Educational License",
    "Respiratory Care Practitioner",
    "Speech-Language Pathologist",
]

# Result columns are read by their data-label so the order does not matter.
RESULT_COLUMNS = [
    "Credential/License Number",
    "Profession",
    "Credential/License Type",
    "Name",
    "DBA",
    "City",
    "State",
    "Zip Code",
    "Granted",
    "License Status",
]

FIELDNAMES = ["Searched License Name"] + RESULT_COLUMNS + ["Detail URL"]


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip()


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    if HEADLESS:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_page(driver, wait):
    driver.get(LANDING_URL)
    wait_for_page_ready(driver, wait)
    # Wait until at least one combobox button has rendered. We don't key on a
    # specific name here because the name attribute is not guaranteed to be on
    # the button element in the Lightning markup.
    wait.until(
        lambda d: len(d.find_elements(By.CSS_SELECTOR, "button[role='combobox']")) > 0
    )


# --------------------------------------------------------------------------- #
# SLDS combobox helpers
# --------------------------------------------------------------------------- #

# JS that locates a combobox <button> by, in order: (1) the visible text of its
# associated <label>, (2) its `name` attribute, (3) its `aria-label`. Returns the
# element or null. `arguments[0]` is the field label, `arguments[1]` the name.
_FIND_COMBO_JS = """
var label = (arguments[0] || '').replace(/\\s+/g, ' ').trim().toLowerCase();
var name = arguments[1] || '';
function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim().toLowerCase(); }
var btns = [].slice.call(document.querySelectorAll("button[role='combobox']"));

function labelFor(btn) {
    // Explicit label[for=id]
    if (btn.id) {
        var l = document.querySelector("label[for='" + (window.CSS && CSS.escape ? CSS.escape(btn.id) : btn.id) + "']");
        if (l) return l.innerText;
    }
    // Climb to the form element and grab its label
    var fe = btn.closest('.slds-form-element, lightning-combobox, lightning-grouped-combobox');
    if (fe) {
        var l2 = fe.querySelector('label, .slds-form-element__label');
        if (l2) return l2.innerText;
    }
    return '';
}

// 1) by associated label text
if (label) {
    for (var i = 0; i < btns.length; i++) {
        if (norm(labelFor(btns[i])) === label) return btns[i];
    }
}
// 2) by name attribute
if (name) {
    for (var j = 0; j < btns.length; j++) {
        if (btns[j].getAttribute('name') === name) return btns[j];
    }
}
// 3) by aria-label
if (label) {
    for (var k = 0; k < btns.length; k++) {
        if (norm(btns[k].getAttribute('aria-label')) === label) return btns[k];
    }
}
return null;
"""


def _dump_comboboxes(driver):
    """Return a readable list of every combobox on the page (for diagnostics)."""
    return driver.execute_script(
        """
        function norm(s){return (s||'').replace(/\\s+/g,' ').trim();}
        return [].slice.call(document.querySelectorAll("button[role='combobox']")).map(function(b){
            var lbl='';
            if(b.id){var l=document.querySelector("label[for='"+b.id+"']"); if(l) lbl=l.innerText;}
            return {
                name: b.getAttribute('name'),
                ariaLabel: b.getAttribute('aria-label'),
                dataValue: b.getAttribute('data-value'),
                label: norm(lbl),
                text: norm(b.innerText)
            };
        });
        """
    )


def _find_combobox_button(driver, field):
    return driver.execute_script(_FIND_COMBO_JS, field.get("label"), field.get("name"))


def _get_combobox_selected_value(driver, field):
    button = _find_combobox_button(driver, field)
    if button is None:
        return ""
    return driver.execute_script(
        """
        var btn = arguments[0];
        return (btn.getAttribute('data-value') || btn.innerText || '').replace(/\\s+/g, ' ').trim();
        """,
        button,
    )


def _wait_for_combobox_button(driver, wait, field):
    try:
        return wait.until(lambda d: _find_combobox_button(d, field))
    except TimeoutException:
        dump = _dump_comboboxes(driver)
        raise NoSuchElementException(
            f"Combobox not found for {field}. Comboboxes on page: {dump}"
        )


def _open_combobox(driver, button):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    if button.get_attribute("aria-expanded") == "true":
        return
    try:
        button.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", button)
    time.sleep(0.2)


def _wait_for_combobox_listbox(driver, wait):
    wait.until(
        lambda d: len(
            d.find_elements(
                By.CSS_SELECTOR, "div[role='listbox'], [role='listbox']"
            )
        )
        > 0
    )


def _wait_for_combobox_value(driver, wait, field, value_text):
    target = normalize(value_text)

    def _matches(d):
        button = _find_combobox_button(d, field)
        if button is None:
            return False
        selected = normalize(_get_combobox_selected_value(d, field))
        aria = normalize(button.get_attribute("aria-label") or "")
        return selected == target or aria == target

    wait.until(_matches)


def _wait_for_combobox_collapse(driver, wait, field):
    def _collapsed(d):
        button = _find_combobox_button(d, field)
        return button is not None and button.get_attribute("aria-expanded") == "false"

    wait.until(_collapsed)


def _click_combobox_option(driver, value_text):
    """Click the visible listbox option whose text matches value_text."""
    return driver.execute_script(
        """
        var target = arguments[0];
        function txt(el) { return (el.textContent || '').replace(/\\s+/g, ' ').trim(); }
        var items = document.querySelectorAll(
            "lightning-base-combobox-item, [role='option']"
        );
        var fallback = null;
        for (var it of items) {
            if (it.offsetParent === null) continue;   // skip hidden options
            var span = it.querySelector('span.slds-truncate, span[title]');
            var label = span && span.getAttribute('title') ? span.getAttribute('title').trim()
                        : (it.getAttribute('data-value') || txt(it));
            label = (label || '').replace(/\\s+/g, ' ').trim();
            if (label === target) {
                it.scrollIntoView({block: 'center'});
                it.click();
                return true;
            }
            if (!fallback && label.toLowerCase() === target.toLowerCase()) {
                fallback = it;
            }
        }
        if (fallback) {
            fallback.scrollIntoView({block: 'center'});
            fallback.click();
            return true;
        }
        return false;
        """,
        value_text,
    )


def select_combobox(driver, wait, field, value_text):
    last_error = None
    for _ in range(4):
        try:
            button = _wait_for_combobox_button(driver, wait, field)
            current = (button.get_attribute("data-value") or "").strip()
            selected_value = normalize(_get_combobox_selected_value(driver, field))
            if normalize(current) == normalize(value_text) or selected_value == normalize(value_text):
                return
            _open_combobox(driver, button)

            _wait_for_combobox_listbox(driver, wait)

            end = time.time() + 12
            while time.time() < end:
                if _click_combobox_option(driver, value_text):
                    _wait_for_combobox_value(driver, wait, field, value_text)
                    try:
                        _wait_for_combobox_collapse(driver, wait, field)
                    except TimeoutException:
                        pass
                    time.sleep(0.4)
                    return
                time.sleep(0.25)
            raise NoSuchElementException(
                f"Option '{value_text}' not found in combobox {field}"
            )
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def set_page_size_100(driver, wait):
    """The page-size combobox has no name/aria-label; it is the numeric one
    with aria-required='false'. Switch it from 10 to 100."""
    last_error = None
    for _ in range(4):
        try:
            button = driver.execute_script(
                """
                var btns = document.querySelectorAll("button[role='combobox']");
                for (var b of btns) {
                    if (b.getAttribute('aria-required') === 'false'
                        && /^\\d+$/.test((b.getAttribute('data-value') || '').trim())) {
                        return b;
                    }
                }
                return null;
                """
            )
            if button is None:
                return
            if (button.get_attribute("data-value") or "").strip() == "100":
                return
            _open_combobox(driver, button)

            end = time.time() + 10
            while time.time() < end:
                if _click_combobox_option(driver, "100"):
                    time.sleep(1.5)
                    return
                time.sleep(0.4)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


def click_search(driver, wait):
    last_error = None
    for _ in range(3):
        try:
            button = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[contains(@class,'slds-button_brand')][.//b[normalize-space()='Search']]")
                )
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
            try:
                button.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", button)
            time.sleep(RESULT_SETTLE_SECONDS)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
            time.sleep(0.8)
    if last_error:
        raise last_error


# --------------------------------------------------------------------------- #
# Results table
# --------------------------------------------------------------------------- #

def get_table_state(driver):
    return driver.execute_script(
        """
        var origin = window.location.origin;
        var table = document.querySelector("table[role='grid']");
        var result = { tablePresent: !!table, empty: false, rows: [] };
        if (!table) return result;

        var trs = table.querySelectorAll("tbody tr[data-row-number]");
        if (trs.length === 0) result.empty = true;
        for (var tr of trs) {
            var cells = tr.querySelectorAll(":scope > th, :scope > td");
            var obj = {};
            for (var c of cells) {
                var label = c.getAttribute('data-label');
                if (!label) continue;
                obj[label] = (c.innerText || '').replace(/\\s+/g, ' ').trim();
                if (label.indexOf('License Number') >= 0) {
                    var a = c.querySelector("a[href]");
                    if (a) {
                        var href = a.getAttribute('href') || '';
                        obj['__detail'] = href.indexOf('http') === 0 ? href : origin + href;
                    }
                }
            }
            if (Object.keys(obj).length) result.rows.push(obj);
        }
        return result;
        """
    )


def get_data_rows(driver):
    return get_table_state(driver).get("rows", [])


def wait_for_results_or_empty(driver):
    end_time = time.time() + RESULT_WAIT_SECONDS
    empty_since = None
    while time.time() < end_time:
        try:
            state = get_table_state(driver)
            rows = state.get("rows") or []
            if rows:
                return True
            if state.get("empty"):
                if empty_since is None:
                    empty_since = time.time()
                elif time.time() - empty_since >= EMPTY_STABLE_SECONDS:
                    return False
            else:
                empty_since = None
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        time.sleep(0.5)
    return False


def parse_row(row):
    parsed = {col: row.get(col, "") for col in RESULT_COLUMNS}
    parsed["Detail URL"] = row.get("__detail", "")
    return parsed


def extract_current_page_records(driver):
    for _ in range(5):
        try:
            return [parse_row(row) for row in get_data_rows(driver)]
        except StaleElementReferenceException:
            time.sleep(0.5)
    return []


def _first_rows_key(state):
    rows = (state.get("rows") or [])[:5]
    return "|".join(
        r.get("__detail", "") or r.get("Credential/License Number", "") for r in rows
    )


def click_next_page(driver, wait):
    """Click the pagination 'Next' arrow. Returns False when on the last page."""
    before_key = _first_rows_key(get_table_state(driver))

    clicked = driver.execute_script(
        """
        // Find the 'Next' control via its assistive text, then click the
        // nearest clickable ancestor.
        var spans = document.querySelectorAll("span.slds-assistive-text");
        var target = null;
        for (var s of spans) {
            if ((s.textContent || '').trim() === 'Next') { target = s; break; }
        }
        if (!target) return 'missing';
        var btn = target.closest("button, [role='button'], lightning-button-icon, lightning-button");
        if (!btn) btn = target.parentElement;
        var disabled = btn.disabled || btn.getAttribute('aria-disabled') === 'true'
                       || btn.classList.contains('slds-button_disabled');
        if (disabled) return 'disabled';
        btn.scrollIntoView({block: 'center'});
        btn.click();
        return 'clicked';
        """
    )

    if clicked != "clicked":
        return False

    try:
        wait.until(
            lambda d: _first_rows_key(get_table_state(d)) != before_key
        )
    except TimeoutException:
        pass

    time.sleep(1.5)
    # If the key did not change, assume there was no further page.
    return _first_rows_key(get_table_state(driver)) != before_key


def scrape_all_pages(driver, wait):
    records = []
    seen = set()

    while True:
        for record in extract_current_page_records(driver):
            key = (
                record.get("Credential/License Number", ""),
                record.get("Name", ""),
                record.get("Detail URL", ""),
            )
            if key not in seen:
                seen.add(key)
                records.append(record)

        if not click_next_page(driver, wait):
            break

    return records


def append_csv(path, records, write_header=False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def run_search(driver, wait, keyword):
    def step(name, fn):
        try:
            return fn()
        except Exception as exc:
            raise RuntimeError(
                f"step '{name}' failed: {type(exc).__name__}: {exc}"
            ) from exc

    step("open_search_page", lambda: open_search_page(driver, wait))
    step("select searchBy", lambda: select_combobox(driver, wait, COMBOBOX_FIELDS["searchBy"], SEARCH_BY))
    step("select professionCategory", lambda: select_combobox(driver, wait, COMBOBOX_FIELDS["professionCategory"], CATEGORY))
    step("select professionsBusiness", lambda: select_combobox(driver, wait, COMBOBOX_FIELDS["professionsBusiness"], keyword))
    step("click_search", lambda: click_search(driver, wait))

    if not wait_for_results_or_empty(driver):
        return []

    set_page_size_100(driver, wait)
    time.sleep(1.0)
    return scrape_all_pages(driver, wait)


def main():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    header_written = os.path.exists(OUTPUT_FILE) and os.path.getsize(OUTPUT_FILE) > 0
    total_rows = 0

    try:
        for keyword in KEYWORDS:
            try:
                records = run_search(driver, wait, keyword)
            except Exception as exc:
                print(f"ERROR on {keyword}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
                records = []

            if records:
                output_rows = []
                for record in records:
                    row = dict(record)
                    row["Searched License Name"] = keyword
                    output_rows.append(row)

                append_csv(OUTPUT_FILE, output_rows, write_header=not header_written)
                header_written = True
                total_rows += len(records)

            print(f"{keyword}: {len(records)} rows (total {total_rows})")
    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
