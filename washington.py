"""Washington DOH provider credential scraper (wahelms.my.site.com).

The search page is a Salesforce Lightning (LWC) Experience Cloud site, and every
form control lives inside *native* Shadow DOM:

    c-helms-parent-cred-search-comp
      -> c-helms-professional-cred-search
           -> lightning-combobox -> lightning-base-combobox -> button[name=credStatus]
           -> lightning-input    -> lightning-primitive-input-simple -> input[name=city]

Selenium's CSS/XPath locators do not pierce shadow roots, so every element here is
resolved with the ``deepAll`` JavaScript helper below instead of ``By.CSS_SELECTOR``.
"""

import csv
import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:  # pragma: no cover - fallback when webdriver_manager is unavailable
    ChromeDriverManager = None

try:
    import nopecha_1
except Exception:  # pragma: no cover - scraper can still run with manual captcha solving
    nopecha_1 = None


BASE_DIR = Path(__file__).resolve().parent
SEARCH_URL = "https://wahelms.my.site.com/s/license-search"
DETAIL_URL = "https://wahelms.my.site.com/s/license-search?recordId={record_id}"
KEYWORDS_FILE = BASE_DIR / "downloads" / "monota" / "license_keywords.csv"
OUTPUT_FILE = BASE_DIR / "washington_results.csv"
PROGRESS_FILE = BASE_DIR / "washington_progress.txt"
DEFAULT_CITY_FILE = BASE_DIR / "zip_codes_wh.csv"

# Public sitekey of the reCAPTCHA v2 checkbox guarding the Search button.
RECAPTCHA_SITEKEY = "6Le8T6orAAAAAN5PdEKkIq1lPkpbPe-yWLgnSjni"

HEADLESS = False
WAIT_SECONDS = 45
PAGE_SLEEP = 1.0
POST_PAGE_SLEEP = 0.4
# Observed NopeCHA solve times for this sitekey run 200s+, so keep the per-attempt
# budget well above that and retry: a timed-out solve is the most common failure.
RECAPTCHA_SOLVE_TIMEOUT = 360
RECAPTCHA_ATTEMPTS = 4
SEARCH_ATTEMPTS = 2
MAX_PAGES = 5000

STATUS_VALUES = [
    "Active",
    "Active Not Renewable",
    "Active On Probation",
    "Active Provisional",
]

FIELDNAMES = [
    "Credential Status Search",
    "Credential Type Search",
    "City Search",
    "Credential Number",
    "Credential Type",
    "First Name",
    "M.I",
    "Last Name",
    "Suffix",
    "Year of Birth",
    "CE Due Date",
    "Status",
    "Enforcement Action",
    "Action URL",
]

# Columns the site renders as td[data-label="..."].
DATA_LABELS = [
    "Credential Number",
    "Credential Type",
    "First Name",
    "M.I",
    "Last Name",
    "Suffix",
    "Year of Birth",
    "CE Due Date",
    "Status",
    "Enforcement Action",
]


# --------------------------------------------------------------------------
# Shadow-DOM aware querying
# --------------------------------------------------------------------------

# Walks the light DOM plus every open shadow root and collects matches.
DEEP_PRELUDE = """
function deepAll(selector, root) {
  root = root || document;
  const out = [];
  const stack = [root];
  const seen = new Set();
  while (stack.length) {
    const node = stack.pop();
    if (seen.has(node)) continue;
    seen.add(node);
    try { out.push(...node.querySelectorAll(selector)); } catch (e) {}
    let all = [];
    try { all = node.querySelectorAll('*'); } catch (e) {}
    for (const el of all) if (el.shadowRoot) stack.push(el.shadowRoot);
  }
  return out;
}
function clean(text) { return (text || '').replace(/\\s+/g, ' ').trim(); }
// Options for a combobox live in the same shadow root as its button. Scoping the
// lookup that way stops a second (or stale) dropdown's items from being matched.
function comboItems(name) {
  const button = deepAll("button[name='" + name + "']")[0];
  if (!button) return [];
  const root = button.getRootNode();
  if (!root || !root.querySelectorAll) return [];
  return Array.from(root.querySelectorAll('lightning-base-combobox-item'));
}
function comboValue(el) {
  return ((el.dataset && el.dataset.value) || clean(el.innerText) || '');
}
"""


def deep_script(driver, body, *args):
    """Run JS with the deepAll/clean helpers already defined."""
    return driver.execute_script(DEEP_PRELUDE + body, *args)


def deep_find(driver, selector):
    return deep_script(driver, "return deepAll(arguments[0])[0] || null;", selector)


def deep_count(driver, selector):
    return deep_script(driver, "return deepAll(arguments[0]).length;", selector)


def wait_for_deep(driver, selector, timeout=WAIT_SECONDS, what=None):
    """Poll until a shadow-DOM selector resolves, then return the element."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            element = deep_find(driver, selector)
        except WebDriverException:
            element = None
        if element is not None:
            return element
        time.sleep(0.25)
    raise TimeoutException(f"Timed out waiting for {what or selector}")


def normalize(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def build_driver():
    options = ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")

    profile_dir = BASE_DIR / "chrome_profile_washington"
    if profile_dir.exists():
        options.add_argument(f"--user-data-dir={profile_dir}")

    if ChromeDriverManager is not None:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(120)
    driver.set_script_timeout(120)
    return driver


def wait_for_page_ready(driver, wait):
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(PAGE_SLEEP)


def open_search_page(driver, wait):
    driver.get(SEARCH_URL)
    wait_for_page_ready(driver, wait)
    # The LWC bundle renders after page load, so these must be polled, not
    # looked up once with a plain CSS locator.
    wait_for_deep(driver, "button[name='credStatus']", what="Credential Status combobox")
    wait_for_deep(driver, "button[name='credType']", what="Credential Type combobox")
    wait_for_deep(driver, "input[name='city']", what="City input")


# --------------------------------------------------------------------------
# Input files
# --------------------------------------------------------------------------


def _load_column(path, column, label):
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = {name.lower(): name for name in (reader.fieldnames or [])}
        key = fieldnames.get(column)
        if not key:
            raise ValueError(f"{path} must contain a '{label}' column")
        seen = set()
        values = []
        for row in reader:
            value = normalize(row.get(key, ""))
            if not value:
                continue
            folded = value.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            values.append(value)
        return values


def load_keywords(path=KEYWORDS_FILE):
    return _load_column(path, "keyword", "Keyword")


def load_cities(path=DEFAULT_CITY_FILE):
    if not path.exists():
        raise FileNotFoundError(
            f"City input file not found: {path}. "
            "Create a CSV such as zip_codes_wh.csv with a 'city' column."
        )
    return _load_column(path, "city", "city")


# --------------------------------------------------------------------------
# Form controls
# --------------------------------------------------------------------------


def open_combobox(driver, button_name):
    button = wait_for_deep(driver, f"button[name='{button_name}']", what=button_name)
    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", button
    )
    # Options render into lightning-base-combobox's shadow root once opened.
    deadline = time.time() + 10
    while time.time() < deadline:
        if deep_script(driver, "return comboItems(arguments[0]).length;", button_name):
            return button
        time.sleep(0.2)
    raise TimeoutException(f"Dropdown '{button_name}' did not open")


def close_combobox(driver):
    try:
        driver.execute_script("document.activeElement && document.activeElement.blur();")
    except WebDriverException:
        pass
    time.sleep(0.2)


def collect_combobox_options(driver, button_name):
    """Return the data-value of every option in a combobox."""
    open_combobox(driver, button_name)
    values = deep_script(
        driver,
        """
        const out = [];
        for (const el of comboItems(arguments[0])) {
          const value = comboValue(el);
          if (value && !out.includes(value)) out.push(value);
        }
        return out;
        """,
        button_name,
    )
    close_combobox(driver)
    return values or []


def selected_combobox_value(driver, button_name):
    return deep_script(
        driver,
        """
        const b = deepAll("button[name='" + arguments[0] + "']")[0];
        if (!b) return null;
        return (b.dataset && b.dataset.value) || clean(b.innerText);
        """,
        button_name,
    )


def select_combobox_option(driver, button_name, value):
    """Pick an option by its data-value (matching is exact, then case-insensitive)."""
    if selected_combobox_value(driver, button_name) == value:
        return

    for _ in range(3):
        open_combobox(driver, button_name)
        clicked = deep_script(
            driver,
            """
            const name = arguments[0];
            const want = arguments[1];
            const items = comboItems(name);
            for (const el of items) {
              if (comboValue(el) === want) {
                el.scrollIntoView({block:'center'});
                el.click();
                return true;
              }
            }
            const lowered = want.toLowerCase();
            for (const el of items) {
              if (comboValue(el).toLowerCase() === lowered) {
                el.scrollIntoView({block:'center'});
                el.click();
                return true;
              }
            }
            return false;
            """,
            button_name,
            value,
        )
        if not clicked:
            close_combobox(driver)
            raise ValueError(f"Option '{value}' not found in '{button_name}' dropdown")

        # Confirm the LWC actually committed the selection before moving on.
        deadline = time.time() + 5
        while time.time() < deadline:
            current = selected_combobox_value(driver, button_name)
            if current and current.casefold() == value.casefold():
                close_combobox(driver)
                return
            time.sleep(0.2)
        close_combobox(driver)

    raise TimeoutException(f"Could not select '{value}' in '{button_name}'")


def set_text_input(driver, input_name, value):
    """Set an lightning-input's value so the LWC parent sees the change.

    Writing straight to the inner <input> is not enough: the events must be
    ``composed`` so they cross the shadow boundary and reach the component that
    owns the search state.
    """
    element = wait_for_deep(driver, f"input[name='{input_name}']", what=input_name)
    driver.execute_script(
        """
        const el = arguments[0];
        const value = arguments[1];
        el.focus();
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, '');
        el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        setter.call(el, value);
        el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        el.blur();
        """,
        element,
        value,
    )
    time.sleep(0.2)
    current = driver.execute_script("return arguments[0].value;", element)
    if normalize(current) != normalize(value):
        raise RuntimeError(f"Failed to set '{input_name}' to {value!r} (got {current!r})")


# --------------------------------------------------------------------------
# reCAPTCHA
# --------------------------------------------------------------------------

TOKEN_SELECTOR = 'textarea#g-recaptcha-response, textarea[name="g-recaptcha-response"]'


def recaptcha_token_present(driver):
    try:
        return bool(
            deep_script(
                driver,
                "return deepAll(arguments[0]).some(t => t.value && t.value.length > 0);",
                TOKEN_SELECTOR,
            )
        )
    except InvalidSessionIdException:
        # A dead browser must not be mistaken for "no token yet" - otherwise the
        # solver keeps buying tokens it can never inject anywhere.
        raise
    except WebDriverException:
        return False


def recaptcha_sitekey(driver):
    """Read the sitekey off the widget, falling back to the known constant."""
    try:
        key = deep_script(
            driver,
            """
            const keyed = deepAll('[data-sitekey]')[0];
            if (keyed) return keyed.getAttribute('data-sitekey');
            for (const frame of deepAll('iframe[src*="recaptcha"]')) {
              try {
                const k = new URL(frame.src, location.href).searchParams.get('k');
                if (k) return k;
              } catch (e) {}
            }
            return '';
            """,
        )
        return normalize(key) or RECAPTCHA_SITEKEY
    except WebDriverException:
        return RECAPTCHA_SITEKEY


def inject_recaptcha_token(driver, token):
    """Write the token into every response textarea and fire grecaptcha callbacks."""
    try:
        return bool(
            deep_script(
                driver,
                """
                const token = arguments[0];
                const areas = deepAll(arguments[1]);
                areas.forEach(el => {
                  el.value = token;
                  el.innerHTML = token;
                  el.textContent = token;
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                });
                const cfg = window.___grecaptcha_cfg;
                if (cfg && cfg.clients) {
                  Object.values(cfg.clients).forEach(client => {
                    try {
                      Object.values(client).forEach(entry => {
                        if (entry && typeof entry.callback === 'function') entry.callback(token);
                        if (entry && typeof entry === 'object') {
                          Object.values(entry).forEach(inner => {
                            if (inner && typeof inner.callback === 'function') inner.callback(token);
                          });
                        }
                      });
                    } catch (e) {}
                  });
                }
                return areas.length > 0;
                """,
                token,
                TOKEN_SELECTOR,
            )
        )
    except InvalidSessionIdException:
        raise
    except WebDriverException:
        return False


def solve_recaptcha(driver):
    """Mint a fresh token through NopeCHA and inject it.

    The site's checkbox always escalates to an image challenge for automated
    browsers, so the checkbox path is not usable; NopeCHA is required.
    """
    if nopecha_1 is None:
        print("[captcha] nopecha_1 not available - solve the captcha manually")
        deadline = time.time() + RECAPTCHA_SOLVE_TIMEOUT
        while time.time() < deadline:
            if recaptcha_token_present(driver):
                return True
            time.sleep(0.5)
        return False

    sitekey = recaptcha_sitekey(driver)
    for attempt in range(1, RECAPTCHA_ATTEMPTS + 1):
        # Solving blocks for minutes; make sure the browser is still alive first
        # so a crashed session fails fast instead of burning paid solves.
        page_url = driver.current_url
        try:
            token = nopecha_1.solve_recaptcha_v2(
                sitekey, page_url, timeout=RECAPTCHA_SOLVE_TIMEOUT
            )
        except InvalidSessionIdException:
            raise
        except Exception as exc:
            print(f"[captcha] attempt {attempt}/{RECAPTCHA_ATTEMPTS} failed: {exc}")
            continue
        if not token:
            continue
        if inject_recaptcha_token(driver, token):
            time.sleep(0.5)
            if recaptcha_token_present(driver):
                return True
    return False


def ensure_recaptcha_solved(driver):
    """reCAPTCHA v2 tokens expire (~2 min) and are cleared after each search."""
    if recaptcha_token_present(driver):
        return True
    return solve_recaptcha(driver)


# --------------------------------------------------------------------------
# Searching
# --------------------------------------------------------------------------


def loading_count(driver):
    """Number of active LWC spinners.

    At rest this is 0. The page's five ``siteforceLoadingBalls`` elements are
    always in the DOM, which is why a generic ``[class*=spinner]`` check cannot
    be used here.
    """
    try:
        return deep_count(driver, "lightning-spinner")
    except InvalidSessionIdException:
        # Never report "not loading" for a dead session - that would silently
        # turn a crashed browser into an empty result set.
        raise
    except WebDriverException:
        return 0


def click_search(driver):
    clicked = deep_script(
        driver,
        """
        for (const b of deepAll('button')) {
          if (clean(b.innerText) === 'Search' && !b.disabled) {
            b.scrollIntoView({block:'center'});
            b.click();
            return true;
          }
        }
        return false;
        """,
    )
    if not clicked:
        raise NoSuchElementException("Search button not found")


def wait_for_loading(driver, appear_timeout=20, finish_timeout=WAIT_SECONDS):
    """Wait for the spinner to appear and then clear.

    A populated table is not a usable ready-signal: results can take ~9s to
    arrive, and until then the previous search's rows (or an empty table) are
    still on screen. Waiting on the spinner is what separates "still loading"
    from "genuinely zero results".
    """
    deadline = time.time() + appear_timeout
    appeared = False
    while time.time() < deadline:
        if loading_count(driver) > 0:
            appeared = True
            break
        time.sleep(0.15)

    deadline = time.time() + finish_timeout
    idle_streak = 0
    while time.time() < deadline:
        if loading_count(driver) == 0:
            idle_streak += 1
            if idle_streak >= 3:
                time.sleep(0.3)
                return appeared
        else:
            idle_streak = 0
        time.sleep(0.2)
    raise TimeoutException("Results did not finish loading")


PAGE_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)


def pagination_state(driver):
    """Return (current_page, total_pages, next_enabled)."""
    info = deep_script(
        driver,
        """
        const p = deepAll('c-helms-pagination')[0];
        if (!p || !p.shadowRoot) return null;
        const next = p.shadowRoot.querySelector('button.next');
        return {
          text: clean(p.shadowRoot.textContent),
          nextEnabled: !!(next && !next.disabled),
        };
        """,
    )
    if not info:
        return 1, 1, False
    match = PAGE_RE.search(info.get("text") or "")
    if not match:
        return 1, 1, bool(info.get("nextEnabled"))
    return int(match.group(1)), int(match.group(2)), bool(info.get("nextEnabled"))


EXTRACT_ROWS_JS = """
const detailTemplate = arguments[0];
return deepAll('tbody tr').map(tr => {
  const row = {};
  const cells = tr.querySelectorAll('td');
  // Cells are addressed by data-label, so a column re-order upstream cannot
  // silently shift every value one place to the left.
  for (const td of cells) {
    const label = td.getAttribute('data-label');
    if (label) row[label] = clean(td.innerText || td.textContent);
  }
  let recordId = '';
  const button = tr.querySelector('lightning-button[data-id]');
  if (button) recordId = button.dataset.id || '';
  if (!recordId) {
    const link = tr.querySelector('a[href]');
    if (link) row['Action URL'] = link.href;
  }
  if (recordId) row['Action URL'] = detailTemplate.replace('{record_id}', recordId);
  row['Record Id'] = recordId;
  return row;
});
"""


def extract_current_page(driver):
    rows = deep_script(driver, EXTRACT_ROWS_JS, DETAIL_URL) or []
    extracted = []
    for row in rows:
        record = {label: normalize(row.get(label, "")) for label in DATA_LABELS}
        record["Action URL"] = normalize(row.get("Action URL", ""))
        extracted.append(record)
    return extracted


def paginate_results(driver):
    all_rows = []
    current, total, _ = pagination_state(driver)

    for _ in range(MAX_PAGES):
        all_rows.extend(extract_current_page(driver))

        current, total, next_enabled = pagination_state(driver)
        if not next_enabled or current >= total:
            break

        previous_page = current
        advanced = deep_script(
            driver,
            """
            const p = deepAll('c-helms-pagination')[0];
            const b = p && p.shadowRoot && p.shadowRoot.querySelector('button.next');
            if (!b || b.disabled) return false;
            b.scrollIntoView({block:'center'});
            b.click();
            return true;
            """,
        )
        if not advanced:
            break

        try:
            wait_for_loading(driver, appear_timeout=8)
        except TimeoutException:
            print("  WARNING: page load timed out, stopping pagination")
            break

        # Confirm the page counter actually moved before scraping again.
        deadline = time.time() + 10
        while time.time() < deadline:
            current, total, _ = pagination_state(driver)
            if current != previous_page:
                break
            time.sleep(0.25)
        else:
            print(f"  WARNING: stuck on page {previous_page}, stopping pagination")
            break

        time.sleep(POST_PAGE_SLEEP)

    return all_rows


def run_search(driver, status_value, credential_type, city):
    last_error = None
    for attempt in range(1, SEARCH_ATTEMPTS + 1):
        try:
            select_combobox_option(driver, "credStatus", status_value)
            select_combobox_option(driver, "credType", credential_type)
            set_text_input(driver, "city", city)

            if not ensure_recaptcha_solved(driver):
                raise RuntimeError("reCAPTCHA was not solved before search")

            click_search(driver)
            wait_for_loading(driver)
            break
        except InvalidSessionIdException:
            raise  # the browser is gone; retrying cannot help
        except (TimeoutException, WebDriverException, RuntimeError) as exc:
            last_error = exc
            print(f"  retry {attempt}/{SEARCH_ATTEMPTS} after: {exc}")
            if attempt == SEARCH_ATTEMPTS:
                raise
            open_search_page(driver, WebDriverWait(driver, WAIT_SECONDS))
    else:  # pragma: no cover - loop always breaks or raises
        raise last_error

    # The <table> is removed from the DOM entirely when a search matches nothing.
    if deep_count(driver, "table") == 0:
        return []

    records = paginate_results(driver)
    for record in records:
        record["Credential Status Search"] = status_value
        record["Credential Type Search"] = credential_type
        record["City Search"] = city
    return records


def resolve_keywords_to_run(driver, requested_keywords):
    available = collect_combobox_options(driver, "credType")
    available_map = {option.casefold(): option for option in available}
    matched = []
    missing = []

    for keyword in requested_keywords:
        match = available_map.get(keyword.casefold())
        if match:
            matched.append(match)
        else:
            missing.append(keyword)

    print(f"Credential types on page: {len(available)}; matched {len(matched)} of "
          f"{len(requested_keywords)} keywords")
    if missing:
        for keyword in missing[:20]:
            print(f"  missing: {keyword}")
        if len(missing) > 20:
            print(f"  ...and {len(missing) - 20} more")

    deduped = []
    seen = set()
    for item in matched:
        folded = item.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        deduped.append(item)
    return deduped


# --------------------------------------------------------------------------
# Output / resume
# --------------------------------------------------------------------------


def record_key(record):
    return (
        record.get("Credential Number", ""),
        record.get("Credential Type", ""),
        record.get("First Name", ""),
        record.get("Last Name", ""),
        record.get("Status", ""),
        record.get("Action URL", ""),
    )


def append_csv(path, records, write_header=False):
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(records)


def load_existing_keys(path=OUTPUT_FILE):
    """Seed the dedupe set from a previous run so restarts do not duplicate rows."""
    if not path.exists() or path.stat().st_size == 0:
        return set()
    keys = set()
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        for row in csv.DictReader(csv_file):
            keys.add(record_key(row))
    return keys


def load_progress(path=PROGRESS_FILE):
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.rstrip("\n") for line in handle if line.strip()}


def mark_progress(combo, path=PROGRESS_FILE):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(combo + "\n")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def run():
    keywords = load_keywords()
    cities = load_cities()

    driver = build_driver()
    wait = WebDriverWait(driver, WAIT_SECONDS)
    header_written = OUTPUT_FILE.exists() and OUTPUT_FILE.stat().st_size > 0
    seen = load_existing_keys()
    done = load_progress()
    total_rows = 0

    if seen:
        print(f"Resuming: {len(seen)} rows already in {OUTPUT_FILE.name}, "
              f"{len(done)} combinations already completed")

    try:
        open_search_page(driver, wait)
        credential_types = resolve_keywords_to_run(driver, keywords)

        for status_value in STATUS_VALUES:
            for credential_type in credential_types:
                for city in cities:
                    combo = f"{status_value}|{credential_type}|{city}"
                    if combo in done:
                        continue
                    try:
                        records = run_search(driver, status_value, credential_type, city)
                    except Exception as exc:
                        print(f"ERROR on {combo}: {exc}")
                        continue

                    fresh = []
                    for record in records:
                        key = record_key(record)
                        if key in seen:
                            continue
                        seen.add(key)
                        fresh.append(record)

                    if fresh:
                        append_csv(OUTPUT_FILE, fresh, write_header=not header_written)
                        header_written = True
                        total_rows += len(fresh)

                    mark_progress(combo)
                    print(
                        f"{combo}: {len(fresh)} new of {len(records)} scraped "
                        f"(total {total_rows})"
                    )
    finally:
        driver.quit()

    print(f"Done. Saved {total_rows} rows to {OUTPUT_FILE}")


if __name__ == "__main__":
    run()
