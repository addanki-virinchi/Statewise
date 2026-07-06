import csv
import itertools
import os
import re
import string
import time

import nopecha

from selenium import webdriver
from selenium.common.exceptions import (
	NoSuchElementException,
	StaleElementReferenceException,
	TimeoutException,
	WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


SEARCH_URL = "https://secure.utah.gov/llv/search/index.html"
OUTPUT_FILE = "utah.csv"
WAIT_SECONDS = 30
SEARCH_RESULT_WAIT_SECONDS = 60
CAPTCHA_RETRIES = 3
HEADLESS = False  # keep False: reCAPTCHA v3 scores headless browsers poorly
START_PREFIX = "ab"

# A fresh search page shows no captcha. Clicking Search runs an invisible v3
# that scores low, so the server re-renders the form with a reCAPTCHA v2
# checkbox; we solve THAT via NopeCHA and resubmit.
CAPTCHA_SOLVE_TIMEOUT = 180  # seconds to wait for a NopeCHA solve

# Residential proxies (host:port:user:pass per line). Passed to NopeCHA so the
# solve runs through a clean IP; optional -- the scraper still works without them.
PROXY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Webshare 10 proxies.txt"
)

# Profession checkboxes to tick, matched against the label text next to each
# input.licenseType checkbox (case-insensitive). Matching a category header
# (e.g. "NURSE") also ticks every license type nested under it.
TARGET_PROFESSIONS = [
	"Certified Public Accountant",
	"Nurse",
]

FIELDNAMES = [
	"Search Prefix",
	"Name",
	"City",
	"Profession",
	"License #",
	"Status",
]


def normalize(text):
	return re.sub(r"\s+", " ", (text or "")).strip()


def build_driver():
	options = ChromeOptions()
	options.add_argument("--start-maximized")
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument("--disable-notifications")
	if HEADLESS:
		options.add_argument("--headless=new")

	service = Service(ChromeDriverManager().install())
	driver = webdriver.Chrome(service=service, options=options)
	driver.set_page_load_timeout(120)
	driver.set_script_timeout(120)
	return driver


def open_search_page(driver, wait):
	driver.get(SEARCH_URL)
	wait.until(EC.presence_of_element_located((By.ID, "fullName")))


SELECT_PROFESSIONS_JS = """
var keywords = arguments[0].map(function (k) {
	return k.replace(/\\s+/g, ' ').trim().toLowerCase();
});
var matched = [];

function tick(box) {
	if (!box.checked) {
		box.checked = true;
		box.dispatchEvent(new Event('change', { bubbles: true }));
	}
}

document.querySelectorAll('#searchByNameForm input.licenseType').forEach(function (box) {
	var label = document.querySelector('label[for="' + box.id + '"]');
	if (!label) { return; }
	var text = label.textContent.replace(/\\s+/g, ' ').trim().toLowerCase();
	var hit = keywords.some(function (kw) {
		return text === kw || text.indexOf(kw) !== -1 || kw.indexOf(text) !== -1;
	});
	if (!hit) { return; }
	tick(box);
	matched.push(label.textContent.replace(/\\s+/g, ' ').trim());
	// A category header owns a nested <ul> of concrete license types;
	// tick them all so the filter covers the whole category.
	var li = box.closest('li');
	if (li) {
		li.querySelectorAll('ul input.licenseType').forEach(function (child) {
			tick(child);
			var childLabel = document.querySelector('label[for="' + child.id + '"]');
			if (childLabel) {
				matched.push(childLabel.textContent.replace(/\\s+/g, ' ').trim());
			}
		});
	}
});
return matched;
"""

# The site's own setProfessions() runs from the submit button's inline onclick,
# after a pageTracker._trackEvent() call that throws when Google Analytics is
# blocked - which silently skips it. Add the hidden inputs ourselves so the
# profession filter always reaches the server.
APPEND_PROFESSIONS_JS = """
var form = document.getElementById('searchByNameForm');
form.querySelectorAll('input[name="professions"]').forEach(function (el) { el.remove(); });
form.querySelectorAll('input.licenseType:checked').forEach(function (box) {
	var input = document.createElement('input');
	input.type = 'hidden';
	input.name = 'professions';
	input.value = box.value;
	form.appendChild(input);
});
return form.querySelectorAll('input[name="professions"]').length;
"""


def prepare_form(driver, prefix, verbose=False):
	"""Fill the search form: radio, name prefix, profession checkboxes."""
	driver.execute_script(
		"var r = document.getElementById('beginning'); if (r && !r.checked) { r.click(); }"
	)

	name_input = driver.find_element(By.ID, "fullName")
	driver.execute_script(
		"""
		arguments[0].value = arguments[1];
		arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
		arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
		""",
		name_input,
		prefix,
	)

	matched = driver.execute_script(SELECT_PROFESSIONS_JS, TARGET_PROFESSIONS)
	count = driver.execute_script(APPEND_PROFESSIONS_JS)
	if verbose:
		print(f"[form] {count} profession filters selected: {matched}")
	if not matched:
		print(f"[form] WARNING: no checkbox matched {TARGET_PROFESSIONS}")
	return matched


def click_search(driver, wait):
	button = wait.until(
		EC.element_to_be_clickable(
			(
				By.XPATH,
				"//form[@id='searchByNameForm']//input[@type='submit' and normalize-space(@value)='Search']",
			)
		)
	)
	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
	try:
		button.click()
	except WebDriverException:
		driver.execute_script("arguments[0].click();", button)


def load_proxies():
    """Parse the Webshare proxy list (host:port:user:pass per line) into NopeCHA
    proxy dicts. Returns [] if the file is missing so the scraper still runs
    (tokens will just be minted from NopeCHA's default IP and likely rejected)."""
    proxies = []
    try:
        with open(PROXY_FILE, encoding="utf-8") as handle:
            for line in handle:
                parts = line.strip().split(":")
                if len(parts) != 4:
                    continue
                host, port, user, password = parts
                if not port.isdigit():
                    continue
                proxies.append(
                    {
                        "scheme": "http",
                        "host": host,
                        "port": int(port),
                        "username": user,
                        "password": password,
                    }
                )
    except OSError:
        pass
    return proxies


def get_v2_sitekey(driver):
    """Read the sitekey of the visible reCAPTCHA v2 checkbox the server injects
    after a search. The v2 widget is a `.g-recaptcha[data-sitekey]` div inside the
    form (a different key than the page's invisible v3). Returns "" if absent."""
    key = driver.execute_script(
        "var el = document.querySelector('.g-recaptcha[data-sitekey]');"
        "return el ? el.getAttribute('data-sitekey') : '';"
    )
    return (key or "").strip()


def v2_challenge_present(driver):
    """True once the post-search reCAPTCHA v2 checkbox is on the page.

    A fresh form has no `.g-recaptcha` div (v3 renders only an invisible badge);
    the checkbox appears only after the server challenges a low-score search, so
    its presence is a reliable 'solve me before you get results' signal."""
    try:
        return bool(get_v2_sitekey(driver))
    except WebDriverException:
        return False


def solve_v2_challenge(driver, prefix, proxy=None):
    """Solve the reCAPTCHA v2 checkbox the server shows after a search and submit.

    Flow: the fresh page has no captcha, so we click Search (v3 runs invisibly,
    scores low), the server re-renders the form with a v2 checkbox, and THEN we
    solve it. We fetch a v2 token from NopeCHA, write it into every
    `g-recaptcha-response` textarea, re-fill the search fields (the re-rendered
    form may have dropped them), and submit natively. On the challenge page the
    v3 hidden field is gone, so no low-score token can overwrite ours.

    Returns True if a token was obtained and the form submitted, else False."""
    sitekey = get_v2_sitekey(driver)
    if not sitekey:
        return False
    where = f" via {proxy['host']}" if proxy else ""
    print(f"[captcha] solving reCAPTCHA v2 challenge via NopeCHA{where}...")
    try:
        token = nopecha.solve_recaptcha_v2(
            sitekey,
            driver.current_url,
            proxy=proxy,
            timeout=CAPTCHA_SOLVE_TIMEOUT,
        )
    except nopecha.NopechaError as exc:
        print(f"[captcha] NopeCHA v2 failed ({exc})")
        return False
    if not token:
        return False

    # Write the token into every g-recaptcha-response field and fire the widget
    # callback so any JS listening on the checkbox sees it as solved.
    driver.execute_script(
        """
        var token = arguments[0];
        document.querySelectorAll("textarea[name='g-recaptcha-response']").forEach(
            function (t) { t.value = token; t.innerHTML = token; }
        );
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
                            }
                        }
                    }
                }
            }
        } catch (e) { /* token in the textarea is enough for the POST */ }
        """,
        token,
    )

    # Re-fill name + professions (the challenge re-render may have cleared them),
    # then submit the form natively -- the challenge page has no v3 hidden field,
    # so this simply POSTs our v2 token with the search criteria.
    prepare_form(driver, prefix)
    driver.execute_script(
        "var f = document.getElementById('searchByNameForm'); if (f) { f.submit(); }"
    )
    return True


def get_results_table(driver):
	xpaths = [
		"//th[normalize-space()='Licensee Name']/ancestor::table[1]",
		"//th[contains(normalize-space(), 'Name')]/ancestor::table[1]",
	]
	for xpath in xpaths:
		try:
			return driver.find_element(By.XPATH, xpath)
		except NoSuchElementException:
			continue
	return None


def no_records_present(driver):
	try:
		body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
	except WebDriverException:
		return False
	return any(
		marker in body_text
		for marker in ("no records", "no results", "returned no", "0 records")
	)


def wait_for_search_outcome(driver, wait):
	"""After clicking Search the page JS runs the invisible v3 and does a full-page
	POST. Wait until we can tell what happened:

	  'challenge' - server re-rendered the form with a v2 checkbox to solve
	  'captcha'   - server rejected outright, back on the form with ?message=error.captcha
	  'results'   - a results table is on screen
	  'empty'     - a no-records message is on screen

	'challenge' is checked first: on that page a stray table header could otherwise
	look like results, but the visible v2 checkbox means we must solve before the
	real results appear.
	"""

	def outcome(current_driver):
		try:
			if v2_challenge_present(current_driver):
				return "challenge"
			url = current_driver.current_url
			if "message=error.captcha" in url:
				return "captcha"
			if get_results_table(current_driver) is not None:
				return "results"
			if no_records_present(current_driver):
				return "empty"
		except WebDriverException:
			pass
		return False

	return wait.until(outcome)


def get_result_rows(driver):
	table = get_results_table(driver)
	if table is None:
		return []

	rows = table.find_elements(By.XPATH, ".//tr")
	valid_rows = []
	for row in rows:
		cells = row.find_elements(By.XPATH, "./td")
		if len(cells) >= 5:
			valid_rows.append(row)
	return valid_rows


def parse_row(row, prefix):
	cells = row.find_elements(By.XPATH, "./td")
	name = cells[0].text.strip()
	city = cells[1].text.strip()
	profession_parts = [
		part.text.strip()
		for part in cells[2].find_elements(By.CSS_SELECTOR, "p")
		if part.text.strip()
	]
	profession = " | ".join(profession_parts) if profession_parts else cells[2].text.strip()
	license_number = cells[3].text.strip()
	status = cells[4].text.strip()

	return {
		"Search Prefix": prefix,
		"Name": name,
		"City": city,
		"Profession": profession,
		"License #": license_number,
		"Status": status,
	}


def get_next_page_link(driver):
	candidates = [
		(By.ID, "pagination-next"),
		(By.CSS_SELECTOR, "a[rel='next']"),
		(By.XPATH, "//a[starts-with(normalize-space(), 'Next')]"),
		(By.XPATH, "//a[normalize-space()='>' or normalize-space()='>>' or normalize-space()='›']"),
	]
	for by, selector in candidates:
		try:
			link = driver.find_element(by, selector)
		except NoSuchElementException:
			continue
		class_name = (link.get_attribute("class") or "").lower()
		aria_disabled = (link.get_attribute("aria-disabled") or "").lower()
		if "disabled" in class_name or aria_disabled == "true":
			return None
		return link
	return None


def get_first_row_signature(driver):
	rows = get_result_rows(driver)
	if not rows:
		return None
	cells = rows[0].find_elements(By.XPATH, "./td")
	if len(cells) < 5:
		return None
	return tuple(cell.text.strip() for cell in cells[:5])


def wait_for_result_change(driver, wait, previous_signature):
	def page_changed(current_driver):
		try:
			signature = get_first_row_signature(current_driver)
			return signature is not None and signature != previous_signature
		except WebDriverException:
			return False

	wait.until(page_changed)


def scrape_all_pages(driver, wait, prefix):
	records = []
	seen = set()

	while True:
		current_rows = get_result_rows(driver)
		if not current_rows:
			break

		for row in current_rows:
			try:
				record = parse_row(row, prefix)
			except StaleElementReferenceException:
				continue
			key = (
				record["Name"],
				record["City"],
				record["Profession"],
				record["License #"],
				record["Status"],
			)
			if key not in seen:
				seen.add(key)
				records.append(record)

		next_link = get_next_page_link(driver)
		if next_link is None:
			break

		previous_signature = get_first_row_signature(driver)
		if previous_signature is None:
			break

		driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_link)
		try:
			next_link.click()
		except WebDriverException:
			driver.execute_script("arguments[0].click();", next_link)

		try:
			wait_for_result_change(driver, wait, previous_signature)
		except TimeoutException:
			break

		time.sleep(0.2)

	return records


def write_csv(path, records):
	write_header = not os.path.exists(path) or os.path.getsize(path) == 0
	with open(path, "a", newline="", encoding="utf-8-sig") as handle:
		writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
		if write_header:
			writer.writeheader()
		writer.writerows(records)


def generate_prefixes():
	letters = string.ascii_lowercase
	return [
		f"{a}{b}"
		for a, b in itertools.product(letters, repeat=2)
		if f"{a}{b}" >= START_PREFIX
	]


def run_search(driver, wait, result_wait, prefix, first_run, proxies):
	"""Load the form, fill it, search, and clear the v2 captcha it triggers.

	The fresh page has no visible captcha, so we click Search directly. The
	invisible v3 scores low and the server re-renders the form with a v2 checkbox;
	we then solve that via NopeCHA and resubmit. ``proxies`` rotates per retry so
	one flagged IP doesn't sink every attempt. Returns the outcome string."""
	for attempt in range(1, CAPTCHA_RETRIES + 1):
		proxy = proxies[(attempt - 1) % len(proxies)] if proxies else None

		open_search_page(driver, wait)
		prepare_form(driver, prefix, verbose=first_run and attempt == 1)
		click_search(driver, wait)

		try:
			outcome = wait_for_search_outcome(driver, result_wait)
		except TimeoutException:
			print(f"[{prefix}] no response after search (attempt {attempt}), retrying")
			continue

		# A v2 checkbox appeared: solve it, resubmit, then read the real outcome.
		if outcome == "challenge":
			if not solve_v2_challenge(driver, prefix, proxy):
				print(f"[{prefix}] could not solve v2 captcha (attempt {attempt}), retrying")
				time.sleep(2 * attempt)
				continue
			try:
				outcome = wait_for_search_outcome(driver, result_wait)
			except TimeoutException:
				print(f"[{prefix}] no response after captcha (attempt {attempt}), retrying")
				continue

		if outcome in ("results", "empty"):
			return outcome

		# Still challenged or rejected -- retry with a fresh page and proxy.
		print(f"[{prefix}] captcha not cleared (attempt {attempt}), retrying")
		time.sleep(2 * attempt)

	return "failed"


def run():
	driver = build_driver()
	wait = WebDriverWait(driver, WAIT_SECONDS, poll_frequency=0.25)
	result_wait = WebDriverWait(driver, SEARCH_RESULT_WAIT_SECONDS, poll_frequency=0.5)
	total = 0

	proxies = load_proxies()
	if proxies:
		print(f"Loaded {len(proxies)} proxies for NopeCHA solving")
	else:
		print("No proxies loaded -- solving via NopeCHA's default IP")

	try:
		prefixes = generate_prefixes()
		for index, prefix in enumerate(prefixes):
			print(f"Running prefix: {prefix}")
			outcome = run_search(
				driver, wait, result_wait, prefix, first_run=index == 0, proxies=proxies
			)

			if outcome == "results":
				records = scrape_all_pages(driver, wait, prefix)
				print(f"Collected {len(records)} rows for {prefix}")
				if records:
					write_csv(OUTPUT_FILE, records)
					total += len(records)
			elif outcome == "empty":
				print(f"No records for {prefix}")
			else:
				print(f"[{prefix}] giving up after {CAPTCHA_RETRIES} attempts")

		print(f"Saved {total} rows to {OUTPUT_FILE}")
	finally:
		driver.quit()


if __name__ == "__main__":
	run()
