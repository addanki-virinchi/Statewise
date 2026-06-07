import csv
import re
import time

from selenium import webdriver
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


SEARCH_URL = "https://nmrldlpi.my.site.com/bcd/s/rld-public-search"
OUTPUT_FILE = "newmexico.csv"
WAIT_SECONDS = 60
PAGE_LOAD_SLEEP = 1.5
HEADLESS = False

PROFESSIONS = [
	"Board of Nursing Home Administrators",
	"Board of Pharmacy",
	"Board of Counseling and Therapy Practice",
	"Nutrition and Dietetic Practice Board",
]

LICENSE_STATUS = "Active"
LICENSE_TYPE_EXCLUDES = {"All", "Select a License Type"}


def normalize(text):
	return " ".join(text.split()).strip().lower()


def slugify(text):
	slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
	return slug or "keyword"


def build_driver():
	options = ChromeOptions()
	options.add_argument("--start-maximized")
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument("--disable-notifications")
	if HEADLESS:
		options.add_argument("--headless=new")

	try:
		return webdriver.Chrome(options=options)
	except Exception:
		service = Service(ChromeDriverManager().install())
		return webdriver.Chrome(service=service, options=options)


def wait_for_page_ready(driver, wait):
	wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
	time.sleep(PAGE_LOAD_SLEEP)


def get_combobox_button(driver, label):
	return driver.find_element(By.XPATH, f"//button[@role='combobox' and @aria-label={repr(label)}]")


def open_combobox(driver, wait, label):
	button = wait.until(
		EC.presence_of_element_located((By.XPATH, f"//button[@role='combobox' and @aria-label={repr(label)}]"))
	)
	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
	try:
		button.click()
	except ElementClickInterceptedException:
		driver.execute_script("arguments[0].click();", button)
	wait.until(lambda d: get_combobox_button(d, label).get_attribute("aria-expanded") == "true")


def close_dropdown(driver):
	try:
		driver.switch_to.active_element.send_keys("\ue00c")
	except Exception:
		pass


def select_combobox_option(driver, wait, label, option_text, required=True):
	open_combobox(driver, wait, label)
	option_xpath = f"//div[@role='listbox']//*[normalize-space()={repr(option_text)}]"

	try:
		option = wait.until(EC.element_to_be_clickable((By.XPATH, option_xpath)))
	except TimeoutException:
		close_dropdown(driver)
		if required:
			raise RuntimeError(f"Could not find '{option_text}' in the '{label}' dropdown.")
		return False

	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", option)
	try:
		option.click()
	except ElementClickInterceptedException:
		driver.execute_script("arguments[0].click();", option)

	wait.until(lambda d: normalize(get_combobox_button(d, label).text) == normalize(option_text))
	return True


def collect_license_type_options(driver, wait):
	button = get_combobox_button(driver, "License Type")
	if button.get_attribute("disabled"):
		return []

	open_combobox(driver, wait, "License Type")
	try:
		wait.until(EC.presence_of_element_located((By.XPATH, "//div[@role='listbox']")))
		option_elements = driver.find_elements(By.XPATH, "//div[@role='listbox']//*[@role='option']")
		options = []
		for option in option_elements:
			text = option.text.strip()
			if text and text not in LICENSE_TYPE_EXCLUDES and text not in options:
				options.append(text)
		return options
	finally:
		close_dropdown(driver)


def select_license_type_if_present(driver, wait, license_type):
	if not license_type:
		return False
	button = get_combobox_button(driver, "License Type")
	if button.get_attribute("disabled"):
		return False
	return select_combobox_option(driver, wait, "License Type", license_type, required=False)


def click_search(driver, wait):
	button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Search']")))
	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
	try:
		button.click()
	except ElementClickInterceptedException:
		driver.execute_script("arguments[0].click();", button)


def get_result_table(driver):
	tables = driver.find_elements(By.XPATH, "//table")
	return tables[0] if tables else None


def get_result_rows(driver):
	table = get_result_table(driver)
	if table is None:
		return []
	rows = table.find_elements(By.XPATH, ".//tr[td]")
	return rows


def parse_row(row):
	cells = row.find_elements(By.XPATH, "./td")
	if len(cells) < 9:
		return None

	values = [cell.text.strip() for cell in cells[:9]]
	return {
		"License Holder Name": values[0],
		"License Type": values[1],
		"License Number": values[2],
		"License Status": values[3],
		"Temporary": values[4],
		"Issue Date": values[5],
		"Expiration Date": values[6],
		"Address": values[7],
		"County": values[8],
	}


def wait_for_results(driver, wait) -> bool:
	def results_ready(current_driver):
		if get_result_rows(current_driver):
			return True
		if current_driver.find_elements(By.XPATH, "//button[normalize-space()='Next']"):
			return True
		if current_driver.find_elements(By.XPATH, "//table"):
			return True
		if current_driver.find_elements(By.XPATH, "//*[contains(normalize-space(),'No results')]"):
			return True
		if current_driver.find_elements(By.XPATH, "//*[contains(normalize-space(),'No records')]"):
			return True
		if current_driver.find_elements(By.XPATH, "//*[contains(normalize-space(),'No data')]"):
			return True
		return False

	try:
		wait.until(results_ready)
		return True
	except TimeoutException:
		return False


def click_next_page(driver, wait):
	next_buttons = driver.find_elements(By.XPATH, "//button[normalize-space()='Next']")
	if not next_buttons:
		return False

	next_button = next_buttons[0]
	aria_disabled = (next_button.get_attribute("aria-disabled") or "").lower()
	if not next_button.is_enabled() or aria_disabled == "true":
		return False

	current_rows = get_result_rows(driver)
	first_row = current_rows[0] if current_rows else None
	driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_button)
	try:
		next_button.click()
	except ElementClickInterceptedException:
		driver.execute_script("arguments[0].click();", next_button)

	if first_row is not None:
		try:
			wait.until(EC.staleness_of(first_row))
		except TimeoutException:
			time.sleep(PAGE_LOAD_SLEEP)
	else:
		time.sleep(PAGE_LOAD_SLEEP)

	wait_for_results(driver, wait)
	return True


def scrape_current_search(driver, wait, profession, license_type):
	if not wait_for_results(driver, wait):
		return []
	records = []
	seen_rows = set()
	page_number = 1

	while True:
		page_rows = get_result_rows(driver)
		if not page_rows:
			break

		for row_index, row in enumerate(page_rows, start=1):
			try:
				record = parse_row(row)
				if record is None:
					continue
				record["Profession"] = profession
				if license_type and not record["License Type"]:
					record["License Type"] = license_type
				unique_key = (
					record["License Holder Name"],
					record["License Type"],
					record["License Number"],
					record["Issue Date"],
					record["Expiration Date"],
				)
				if unique_key not in seen_rows:
					seen_rows.add(unique_key)
					records.append(record)
			except Exception:
				continue

		if not click_next_page(driver, wait):
			break
		page_number += 1

	return records


def save_csv(records):
	fieldnames = [
		"License Type",
		"License Holder Name",
		"License Number",
		"License Status",
		"Temporary",
		"Issue Date",
		"Expiration Date",
		"Address",
		"County",
	]

	with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as csv_file:
		writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
		writer.writeheader()
		writer.writerows(records)

	print(f"Saved {len(records)} rows to {OUTPUT_FILE}")


def prepare_search(driver, wait, profession):
	select_combobox_option(driver, wait, "Profession", profession)
	time.sleep(0.5)
	select_combobox_option(driver, wait, "License Status", LICENSE_STATUS)
	time.sleep(0.5)


def run_search(driver, wait, profession, license_type=None):
	driver.get(SEARCH_URL)
	wait_for_page_ready(driver, wait)
	prepare_search(driver, wait, profession)
	if license_type:
		select_license_type_if_present(driver, wait, license_type)
	click_search(driver, wait)
	return scrape_current_search(driver, wait, profession, license_type)


def main():
	driver = build_driver()
	wait = WebDriverWait(driver, WAIT_SECONDS)
	all_records = []

	try:
		for profession in PROFESSIONS:
			driver.get(SEARCH_URL)
			wait_for_page_ready(driver, wait)
			prepare_search(driver, wait, profession)

			license_types = collect_license_type_options(driver, wait)
			if license_types:
				print(f"{profession}: iterating {len(license_types)} license type option(s)")
				for license_type in license_types:
					print(f"Searching {profession} -> {license_type}")
					records = run_search(driver, wait, profession, license_type)
					all_records.extend(records)
			else:
				print(f"{profession}: no license type options found; searching without touching License Type")
				records = run_search(driver, wait, profession, None)
				all_records.extend(records)

		save_csv(all_records)
	finally:
		driver.quit()


if __name__ == "__main__":
	main()