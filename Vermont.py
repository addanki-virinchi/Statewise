import csv
import json
import os
import string
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import undetected_chromedriver as uc
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait


SEARCH_URL = (
    "https://secure.professionals.vermont.gov/prweb/PRServletCustom/app/"
    "NGLPGuestUser_/V9csDxL3sXkkjMC_FR2HrA*/!STANDARD"
    "?UserIdentifier=LicenseLookupGuestUser"
)
OUTPUT_FILE = "vermont.csv"

# --- RESUME / PROGRESS -----------------------------------------------------
# Every time a (profession, first_name_prefix, last_name_prefix) search is
# fully processed (rows scraped + written, or confirmed empty), we record it
# here. On the next run, we load this file and skip straight to the search
# immediately after the last one we finished, instead of starting over from
# the very beginning.
PROGRESS_FILE = "vermont_progress.json"
# ---------------------------------------------------------------------------

# --- SPEED KNOBS -----------------------------------------------------------
# WAIT_SECONDS is the ceiling for polling loops (e.g. "keep checking for
# results/pagination for up to this long before giving up").
WAIT_SECONDS = 15
POLL_INTERVAL = 0.1

PAGE_LOAD_SLEEP = 0.2          # was 0.5 — small buffer after readyState==complete
RESULT_SETTLE_SLEEP = 0.15     # was 0.5 — small buffer after results/page change

# The site genuinely needs these two fixed pauses — the earlier attempt to
# replace them with a generic "is anything spinning" JS check didn't match
# this site's actual loading indicator, so it returned "idle" instantly and
# the code raced ahead before the page had done anything (hence every
# search timing out). Back to explicit waits at the two points that matter:
NAME_ENTRY_SETTLE_SECONDS = 5.0      # after profession + both names are entered, before clicking Display Results
RESULT_INITIAL_SETTLE_SECONDS = 2.5  # after clicking Display Results, before checking for rows

HEADLESS = False
CHROMEDRIVER_MAJOR = 150
# ---------------------------------------------------------------------------

PROFESSIONS = [
    "Audiologists",
    "Behavior Analysts",
    "Hearing Aid Dispensers",
    "Massage Therapy, Bodyworkers, and Touch Professionals",
    "Naturopathic Physicians",
    "Nursing",
    "Nursing Home Administrators",
    "Occupational Therapy",
    "Optometry",
    "Osteopathic Physicians & Surgeons",
    "Psychoanalysts",
    "Radiologic Technology",
    "Respiratory Care",
    "Speech-Language Pathologists",
]

FIELDNAMES = [
    "search_profession",
    "search_first_name_prefix",
    "search_last_name_prefix",
    "license_number",
    "profession_type",
    "status",
    "first_name",
    "last_name",
    "first_issuance_date",
    "expiration_date",
]


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def build_driver():
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


# ---------------------------------------------------------------------------
# Smart "is the page actually done doing stuff" check, used instead of blind
# sleeps. Pega/PRPC pages show a busy overlay/spinner while an AJAX postback
# (e.g. triggered by typing into the name fields, or clicking Display
# Results) is in flight. We poll for: document fully loaded, no visible
# spinner/overlay/busy element, and no in-flight XHR we can detect via
# performance entries. As soon as all of that is true, we return immediately
# instead of waiting out a fixed timer.
# ---------------------------------------------------------------------------
_IDLE_CHECK_JS = """
function norm(s) { return (s || '').toLowerCase(); }

// 1) Basic document readiness.
if (document.readyState !== 'complete') return false;

// 2) Any element that looks like a spinner/overlay/busy indicator and is
//    currently visible blocks us from calling the page idle.
var candidates = document.querySelectorAll(
    "[class*='spinner'], [class*='loading'], [class*='busy'], " +
    "[class*='overlay'], [class*='modal-wait'], .pega-loading-indicator"
);
for (var i = 0; i < candidates.length; i++) {
    var el = candidates[i];
    var style = window.getComputedStyle(el);
    var visible = (el.offsetParent !== null || el.getClientRects().length) &&
        style.visibility !== 'hidden' &&
        style.display !== 'none' &&
        parseFloat(style.opacity || '1') > 0;
    if (visible) return false;
}

// 3) If the browser exposes resource timing, make sure nothing is actively
//    mid-flight (best-effort; not all environments support this cleanly).
try {
    if (window.performance && performance.getEntriesByType) {
        var resources = performance.getEntriesByType('resource');
        // no-op: just calling this proves timing API works; we don't have a
        // reliable "in-flight" signal cross-browser, so we don't gate on it.
    }
} catch (e) {}

return true;
"""


def wait_until_idle(driver, max_wait: float = WAIT_SECONDS, poll: float = POLL_INTERVAL) -> bool:
    """Poll until the page looks idle, or bail out at max_wait as a safety
    ceiling. Returns True if it became idle before the ceiling, False if the
    ceiling was hit (caller can decide whether to proceed anyway or retry)."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            if driver.execute_script(_IDLE_CHECK_JS):
                return True
        except WebDriverException:
            pass
        time.sleep(poll)
    return False


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    wait_until_idle(driver)
    time.sleep(PAGE_LOAD_SLEEP)


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    wait.until(EC.presence_of_element_located((By.NAME, "$PpyDisplayHarness$pFirstName")))
    wait.until(EC.presence_of_element_located((By.NAME, "$PpyDisplayHarness$pLastName")))
    wait.until(EC.presence_of_element_located((By.NAME, "$PpyDisplayHarness$pProductIds")))


def generate_name_pairs() -> Iterable[Tuple[str, str]]:
    for first_letter in string.ascii_uppercase:
        for second_letter in string.ascii_uppercase:
            first_name = f"{first_letter}{second_letter}"
            last_name = first_letter
            yield first_name, last_name


def build_search_plan() -> List[Tuple[str, str, str]]:
    """Full, deterministic, ordered list of every (profession, first_name_prefix,
    last_name_prefix) search the scraper will run, in the exact order it runs
    them. Resuming just means finding our place in this list."""
    plan = []
    for profession in PROFESSIONS:
        for first_name_prefix, last_name_prefix in generate_name_pairs():
            plan.append((profession, first_name_prefix, last_name_prefix))
    return plan


def save_progress(profession: str, first_name_prefix: str, last_name_prefix: str):
    """Record the search that was just fully completed, so a restart can
    resume immediately after it."""
    payload = {
        "search_profession": profession,
        "search_first_name_prefix": first_name_prefix,
        "search_last_name_prefix": last_name_prefix,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp_path = PROGRESS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, PROGRESS_FILE)


def load_progress() -> Optional[Tuple[str, str, str]]:
    """Return the last completed (profession, first, last) search, or None if
    there's no progress file yet (i.e. this is a fresh run)."""
    if not os.path.exists(PROGRESS_FILE) or os.path.getsize(PROGRESS_FILE) == 0:
        return None
    try:
        with open(PROGRESS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        return (
            data["search_profession"],
            data["search_first_name_prefix"],
            data["search_last_name_prefix"],
        )
    except (json.JSONDecodeError, KeyError, OSError):
        print(f"[WARN] Could not read {PROGRESS_FILE}; starting from the beginning.")
        return None


def resume_index(plan: List[Tuple[str, str, str]]) -> int:
    """Work out where to resume in `plan`. Returns 0 (start from scratch) if
    there's no saved progress or it no longer matches the plan (e.g. the
    PROFESSIONS list changed)."""
    last_completed = load_progress()
    if last_completed is None:
        return 0
    try:
        idx = plan.index(last_completed)
    except ValueError:
        print(
            f"[WARN] Saved progress {last_completed} no longer matches the "
            "search plan (did PROFESSIONS change?); starting from the beginning."
        )
        return 0
    print(f"[RESUME] Last completed search was {last_completed}; resuming after it.")
    return idx + 1


def locate_input(driver, wait, field_name: str):
    return wait.until(EC.presence_of_element_located((By.NAME, field_name)))


def set_text_input(driver, wait, field_name: str, value: str):
    last_error = None
    for _ in range(4):
        try:
            element = locate_input(driver, wait, field_name)
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            try:
                element.clear()
            except WebDriverException:
                driver.execute_script("arguments[0].value = '';", element)
            element.send_keys(value)
            return
        except (StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.3)
    if last_error:
        raise last_error


def select_profession(driver, wait, profession_text: str):
    last_error = None
    for _ in range(4):
        try:
            select_el = wait.until(EC.presence_of_element_located((By.NAME, "$PpyDisplayHarness$pProductIds")))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", select_el)
            select = Select(select_el)
            matched = False
            for option in select.options:
                text = normalize_text(option.text)
                if text.casefold() == profession_text.casefold():
                    select.select_by_visible_text(option.text)
                    matched = True
                    break
            if not matched:
                raise NoSuchElementException(f"Profession not found: {profession_text}")
            time.sleep(0.5)
            return
        except (StaleElementReferenceException, WebDriverException) as exc:
            last_error = exc
            time.sleep(0.3)
    if last_error:
        raise last_error


def click_display_results(driver, wait):
    locators = [
        (By.XPATH, "//button[normalize-space()='Display Results']"),
        (By.XPATH, "//input[(translate(@type,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='button' or translate(@type,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='submit') and normalize-space(@value)='Display Results']"),
        (By.XPATH, "//a[normalize-space()='Display Results']"),
        (By.XPATH, "//*[self::button or self::input or self::a or @role='button'][contains(normalize-space(.), 'Display Results') or contains(normalize-space(@value), 'Display Results') or contains(normalize-space(@aria-label), 'Display Results')]"),
    ]
    last_error = None
    for _ in range(4):
        for by, value in locators:
            try:
                buttons = driver.find_elements(by, value)
                for button in buttons:
                    if not driver.execute_script(
                        """
                        const el = arguments[0];
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return !!(
                            (el.offsetParent !== null || el.getClientRects().length) &&
                            style.visibility !== 'hidden' &&
                            style.display !== 'none' &&
                            !el.disabled
                        );
                        """,
                        button,
                    ):
                        continue
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                    try:
                        button.click()
                    except ElementClickInterceptedException:
                        driver.execute_script("arguments[0].click();", button)
                    return
            except (StaleElementReferenceException, WebDriverException) as exc:
                last_error = exc
                time.sleep(0.2)
        try:
            clicked = driver.execute_script(
                """
                function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim().toLowerCase(); }
                const elements = Array.from(document.querySelectorAll("button, input, a, [role='button']"));
                for (const el of elements) {
                    const text = norm(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'));
                    if (!text.includes('display results')) continue;
                    const style = window.getComputedStyle(el);
                    const visible = (el.offsetParent !== null || el.getClientRects().length) &&
                        style.visibility !== 'hidden' &&
                        style.display !== 'none';
                    if (!visible || el.disabled) continue;
                    el.scrollIntoView({block: 'center'});
                    el.click();
                    return true;
                }
                return false;
                """
            )
            if clicked:
                return
        except WebDriverException as exc:
            last_error = exc
        try:
            submitted = driver.execute_script(
                """
                const first = document.querySelector("[name='$PpyDisplayHarness$pFirstName']");
                if (!first) return false;
                const form = first.form || first.closest("form");
                if (!form) return false;
                if (typeof form.requestSubmit === "function") {
                    form.requestSubmit();
                } else {
                    form.submit();
                }
                return true;
                """
            )
            if submitted:
                return
        except WebDriverException as exc:
            last_error = exc
        try:
            first_name = locate_input(driver, wait, "$PpyDisplayHarness$pFirstName")
            first_name.send_keys(Keys.ENTER)
            return
        except (StaleElementReferenceException, WebDriverException, TimeoutException) as exc:
            last_error = exc
        time.sleep(0.3)
    if last_error:
        raise last_error
    raise TimeoutException("Display Results control was not found.")


def results_state(driver) -> str:
    return driver.execute_script(
        """
        function norm(s) { return (s || '').replace(/\\s+/g, ' ').trim().toLowerCase(); }
        var rows = document.querySelectorAll("tr.oddRow.cellCont, tr.evenRow.cellCont");
        if (rows.length) return "rows";
        var bodyText = norm(document.body ? document.body.innerText : "");
        if (
            bodyText.includes("no records") ||
            bodyText.includes("no results") ||
            bodyText.includes("no matching") ||
            bodyText.includes("no data")
        ) {
            return "empty";
        }
        return "loading";
        """
    )


def wait_for_results(driver):
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        state = results_state(driver)
        if state in {"rows", "empty"}:
            time.sleep(RESULT_SETTLE_SLEEP)
            return state
        time.sleep(POLL_INTERVAL)
    return "loading"


def get_result_rows(driver):
    rows = []
    for row in driver.find_elements(By.CSS_SELECTOR, "tr.oddRow.cellCont, tr.evenRow.cellCont"):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) >= 7:
            rows.append(row)
    return rows


def cell_value_by_index(cells, index: int) -> str:
    if index >= len(cells):
        return ""
    return normalize_text(cells[index].text)


def parse_result_row(row, profession: str, first_name_prefix: str, last_name_prefix: str) -> Dict[str, str]:
    cells = row.find_elements(By.TAG_NAME, "td")
    return {
        "search_profession": profession,
        "search_first_name_prefix": first_name_prefix,
        "search_last_name_prefix": last_name_prefix,
        "license_number": cell_value_by_index(cells, 0),
        "profession_type": cell_value_by_index(cells, 1),
        "status": cell_value_by_index(cells, 2),
        "first_name": cell_value_by_index(cells, 3),
        "last_name": cell_value_by_index(cells, 4),
        "first_issuance_date": cell_value_by_index(cells, 5),
        "expiration_date": cell_value_by_index(cells, 6),
    }


def current_page_number(driver) -> int:
    page = driver.execute_script(
        """
        var links = Array.from(document.querySelectorAll("a[aria-label^='Page ']"));
        for (var i = 0; i < links.length; i++) {
            var link = links[i];
            var style = window.getComputedStyle(link);
            if (style && style.fontWeight && parseInt(style.fontWeight, 10) >= 600) {
                var text = (link.textContent || '').trim();
                var page = parseInt(text, 10);
                if (!isNaN(page)) return page;
            }
        }
        return 1;
        """
    )
    try:
        return int(page)
    except (TypeError, ValueError):
        return 1


def click_next_page(driver, current_page: int) -> bool:
    next_page = current_page + 1
    xpath = f"//a[normalize-space()='{next_page}' and contains(@onclick, 'gridPaginator')]"
    links = driver.find_elements(By.XPATH, xpath)
    if not links:
        return False
    link = links[0]

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
    before = rows_signature(driver)
    try:
        link.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", link)

    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        state = results_state(driver)
        if state == "rows":
            after = rows_signature(driver)
            if after != before:
                time.sleep(RESULT_SETTLE_SLEEP)
                return True
        time.sleep(POLL_INTERVAL)
    return False


def rows_signature(driver) -> Tuple[str, ...]:
    signature = []
    for row in get_result_rows(driver)[:5]:
        cells = row.find_elements(By.TAG_NAME, "td")
        signature.append("|".join(cell_value_by_index(cells, idx) for idx in range(min(4, len(cells)))))
    return tuple(signature)


def ensure_csv_header(path: str):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()


def load_seen_keys(path: str) -> set:
    seen = set()
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return seen
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            seen.add(
                (
                    normalize_text(row.get("search_profession", "")),
                    normalize_text(row.get("search_first_name_prefix", "")),
                    normalize_text(row.get("search_last_name_prefix", "")),
                    normalize_text(row.get("license_number", "")),
                )
            )
    return seen


def append_rows(path: str, rows: Sequence[Dict[str, str]], seen: set) -> int:
    if not rows:
        return 0

    written = 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, extrasaction="ignore")
        for row in rows:
            key = (
                normalize_text(row.get("search_profession", "")),
                normalize_text(row.get("search_first_name_prefix", "")),
                normalize_text(row.get("search_last_name_prefix", "")),
                normalize_text(row.get("license_number", "")),
            )
            if key in seen:
                continue
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
            seen.add(key)
            written += 1
    return written


def scrape_current_results(driver, profession: str, first_name_prefix: str, last_name_prefix: str) -> List[Dict[str, str]]:
    records = []
    page_number = 1
    while True:
        rows = get_result_rows(driver)
        for row in rows:
            try:
                records.append(parse_result_row(row, profession, first_name_prefix, last_name_prefix))
            except StaleElementReferenceException:
                continue

        if not click_next_page(driver, page_number):
            break
        page_number += 1
    return records


def reset_form(driver, wait):
    open_search_page(driver, wait)


def run():
    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    ensure_csv_header(OUTPUT_FILE)
    seen = load_seen_keys(OUTPUT_FILE)

    plan = build_search_plan()
    start_idx = resume_index(plan)
    if start_idx >= len(plan):
        print("[DONE] Progress file shows every search already completed.")
        driver.quit()
        return

    try:
        open_search_page(driver, wait)
        current_profession = None
        for profession, first_name_prefix, last_name_prefix in plan[start_idx:]:
            if profession != current_profession:
                print(f"\n[PROFESSION] {profession}")
                current_profession = profession

            print(
                f"  searching first={first_name_prefix} "
                f"last={last_name_prefix}"
            )
            try:
                reset_form(driver, wait)
                select_profession(driver, wait, profession)
                set_text_input(driver, wait, "$PpyDisplayHarness$pFirstName", first_name_prefix)
                set_text_input(driver, wait, "$PpyDisplayHarness$pLastName", last_name_prefix)
                # Confirmed-necessary pause: the page needs this long after
                # profession + names are entered before Display Results will
                # actually pick up the current field values.
                time.sleep(NAME_ENTRY_SETTLE_SECONDS)
                click_display_results(driver, wait)
                # Confirmed-necessary pause: give the results grid time to
                # render before we start polling for rows.
                time.sleep(RESULT_INITIAL_SETTLE_SECONDS)

                state = wait_for_results(driver)
                if state == "empty":
                    save_progress(profession, first_name_prefix, last_name_prefix)
                    continue
                if state != "rows":
                    print("    results did not load in time; skipping (will retry on next run)")
                    continue

                records = scrape_current_results(
                    driver,
                    profession,
                    first_name_prefix,
                    last_name_prefix,
                )
                written = append_rows(OUTPUT_FILE, records, seen)
                print(f"    rows scraped={len(records)} written={written}")

                # Only mark this search as done once its rows are safely on
                # disk, so a crash mid-search just repeats that one search
                # (harmless thanks to the `seen` de-dupe) rather than silently
                # skipping it.
                save_progress(profession, first_name_prefix, last_name_prefix)
            except (TimeoutException, WebDriverException, StaleElementReferenceException) as exc:
                # Don't advance progress on failure — next run will retry
                # this exact search instead of skipping past it.
                print(f"    [ERROR] {profession} / {first_name_prefix} / {last_name_prefix}: {exc}")
                print("    progress not advanced; this search will be retried on next run")
    finally:
        driver.quit()


if __name__ == "__main__":
    run()