import csv
import re
import time

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


SEARCH_URL = "https://newjersey.mylicense.com/verification/Search.aspx?facility=N"
OUTPUT_FILE = "newjersey.csv"
WAIT_SECONDS = 120
PAGE_LOAD_SLEEP = 2.0
HEADLESS = False
COMMAND_TIMEOUT = 180

KEYWORDS = [
	#"Dentistry",
	"Nursing",
	"Occupational Therapy",
	"Ophthalmic Dispensers",
	"Optometry",
	"Pharmacy",
	"Physical Therapy",
	"Physician Assistants",
	"Psychology",
	"Respiratory Care",
]


def normalize(text):
	return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def build_driver():
	options = ChromeOptions()
	options.add_argument("--start-maximized")
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument("--disable-notifications")
	if HEADLESS:
		options.add_argument("--headless=new")

	service = Service(ChromeDriverManager().install())
	driver = webdriver.Chrome(service=service, options=options)
	driver.set_page_load_timeout(COMMAND_TIMEOUT)
	driver.set_script_timeout(COMMAND_TIMEOUT)
	try:
		driver.command_executor.set_timeout(COMMAND_TIMEOUT)
	except AttributeError:
		pass
	return driver


def wait_for_page_ready(driver, wait):
	wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
	time.sleep(PAGE_LOAD_SLEEP)


def get_profession_select(wait):
	return Select(wait.until(EC.presence_of_element_located((By.ID, "t_web_lookup__profession_name"))))


def get_license_type_select(wait):
	return Select(wait.until(EC.presence_of_element_located((By.ID, "t_web_lookup__license_type_name"))))


def collect_profession_options(wait):
	select = get_profession_select(wait)
	return [option.text.strip() for option in select.options if option.text.strip() and option.text.strip() != "All"]


def find_matching_option(keyword, available_options):
	normalized_keyword = normalize(keyword)

	for option_text in available_options:
		if normalize(option_text) == normalized_keyword:
			return option_text

	contains_matches = []
	keyword_tokens = set(normalized_keyword.split())
	scored_matches = []

	for option_text in available_options:
		normalized_option = normalize(option_text)
		if normalized_keyword in normalized_option or normalized_option in normalized_keyword:
			contains_matches.append(option_text)

		option_tokens = set(normalized_option.split())
		overlap = len(keyword_tokens & option_tokens)
		if overlap:
			scored_matches.append((overlap, len(option_tokens), option_text))

	if contains_matches:
		contains_matches.sort(key=len)
		return contains_matches[0]

	if scored_matches:
		scored_matches.sort(key=lambda item: (-item[0], item[1], item[2]))
		return scored_matches[0][2]

	return None


def select_profession(wait, option_text):
	select = get_profession_select(wait)
	select.select_by_visible_text(option_text)
	time.sleep(0.5)


def select_license_type_all(wait):
	select = get_license_type_select(wait)
	try:
		select.select_by_visible_text("All")
	except NoSuchElementException:
		select.select_by_value("")
	time.sleep(0.5)


def safe_click(driver, wait, by, value, retries=3):
	last_error = None
	for _ in range(retries):
		try:
			element = wait.until(EC.element_to_be_clickable((by, value)))
			driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
			element.click()
			return
		except (TimeoutException, StaleElementReferenceException, WebDriverException) as exc:
			last_error = exc
			time.sleep(1.0)
	raise last_error


def click_search(driver, wait):
	safe_click(driver, wait, By.ID, "sch_button")


def wait_for_results(driver, wait):
	wait.until(EC.presence_of_element_located((By.ID, "datagrid_results")))
	wait_for_page_ready(driver, wait)
	wait.until(lambda d: len(get_result_rows(d)) > 0)


def get_result_table(driver):
	return driver.find_element(By.ID, "datagrid_results")


def get_row_cells(row):
	return row.find_elements(By.XPATH, "./td")


def get_result_rows(driver):
	table = get_result_table(driver)
	rows = table.find_elements(By.CSS_SELECTOR, "tr")
	valid_rows = []
	for row in rows[1:]:
		cells = get_row_cells(row)
		if len(cells) == 7:
			valid_rows.append(row)
	return valid_rows


def parse_current_page(driver):
	records = []
	for row in get_result_rows(driver):
		try:
			cells = get_row_cells(row)

			records.append(
				{
					"Full Name": cells[0].text.strip(),
					"License Number": cells[1].text.strip(),
					"Profession": cells[2].text.strip(),
					"License Type": cells[3].text.strip(),
					"License Status": cells[4].text.strip(),
					"City": cells[5].text.strip(),
					"State": cells[6].text.strip(),
				}
			)
		except StaleElementReferenceException:
			continue

	return records


def find_page_link(driver, page_number):
	xpaths = [
		f"//a[normalize-space()='{page_number}' and contains(@href,'datagrid_results')]",
		f"//a[normalize-space()='{page_number}']",
	]
	for xpath in xpaths:
		try:
			return driver.find_element(By.XPATH, xpath)
		except NoSuchElementException:
			continue
	return None


def is_disabled_pagination_link(link):
	if link is None:
		return True

	class_name = (link.get_attribute("class") or "").lower()
	aria_disabled = (link.get_attribute("aria-disabled") or "").lower()
	return not link.is_enabled() or "disabled" in class_name or aria_disabled == "true"


def get_first_result_signature(driver):
	rows = get_result_rows(driver)
	if not rows:
		return None

	first_row = rows[0]
	cells = get_row_cells(first_row)
	return tuple(cell.text.strip() for cell in cells)


def wait_for_result_page_change(driver, wait, previous_signature):
	def page_changed(current_driver):
		try:
			return get_first_result_signature(current_driver) != previous_signature
		except WebDriverException:
			return False

	wait.until(page_changed)


def scrape_all_pages(driver, wait):
	records = []
	seen_rows = set()
	current_page = 1

	while True:
		current_page_records = parse_current_page(driver)
		for record in current_page_records:
			unique_key = (
				record["License Number"],
				record["Full Name"],
				record["Profession"],
				record["License Type"],
				record["City"],
				record["State"],
			)
			if unique_key not in seen_rows:
				seen_rows.add(unique_key)
				records.append(record)

		next_page_number = current_page + 1
		next_page_link = find_page_link(driver, next_page_number)
		if is_disabled_pagination_link(next_page_link):
			break

		first_row_signature = get_first_result_signature(driver)
		if first_row_signature is None:
			break

		try:
			safe_click(driver, wait, By.XPATH, f"//a[normalize-space()='{next_page_number}' and contains(@href,'datagrid_results')]", retries=2)
		except WebDriverException:
			safe_click(driver, wait, By.XPATH, f"//a[normalize-space()='{next_page_number}']", retries=2)
		try:
			wait_for_result_page_change(driver, wait, first_row_signature)
		except TimeoutException:
			time.sleep(PAGE_LOAD_SLEEP)
		wait_for_results(driver, wait)
		current_page = next_page_number

	return records


def reset_to_search_page(driver, wait):
	driver.get(SEARCH_URL)
	wait_for_page_ready(driver, wait)


def save_csv(records):
	fieldnames = [
		"Full Name",
		"License Number",
		"Profession",
		"License Type",
		"License Status",
		"City",
		"State",
	]

	with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as csv_file:
		writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
		writer.writeheader()
		writer.writerows(records)

	print(f"Saved {len(records)} rows to {OUTPUT_FILE}")


def main():
	driver = build_driver()
	wait = WebDriverWait(driver, WAIT_SECONDS)
	all_records = []

	try:
		driver.get(SEARCH_URL)
		wait_for_page_ready(driver, wait)

		available_options = collect_profession_options(wait)
		print(f"Loaded {len(available_options)} profession options.")

		for keyword in KEYWORDS:
			matched_option = find_matching_option(keyword, available_options)
			if not matched_option:
				print(f"Skipping '{keyword}' because no matching profession option was found.")
				continue

			print(f"Preparing search for '{keyword}' using '{matched_option}'.")
			reset_to_search_page(driver, wait)
			select_profession(wait, matched_option)
			select_license_type_all(wait)

			click_search(driver, wait)

			try:
				wait_for_results(driver, wait)
			except TimeoutException:
				print(f"No results table found for '{keyword}'.")
				continue

			keyword_records = scrape_all_pages(driver, wait)
			print(f"Collected {len(keyword_records)} rows for '{keyword}'.")
			all_records.extend(keyword_records)

		save_csv(all_records)
	finally:
		driver.quit()


if __name__ == "__main__":
	main()
