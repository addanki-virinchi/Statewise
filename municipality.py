from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import pandas as pd
import time

url = "https://en.wikipedia.org/wiki/List_of_municipalities_in_Texas"

options = Options()
options.add_argument("--headless")

driver = webdriver.Chrome(options=options)

driver.get(url)
time.sleep(5)

html = driver.page_source
driver.quit()

soup = BeautifulSoup(html, "html.parser")

municipalities = []

table = soup.find("table", {"class": "wikitable"})

for row in table.find_all("tr")[1:]:
    cols = row.find_all("td")

    if len(cols) >= 2:
        municipality = cols[1].get_text(strip=True)
        municipalities.append(municipality)

df = pd.DataFrame({"Municipality": municipalities})

df.to_csv("texas_municipalities.csv", index=False)

print(f"Saved {len(df)} municipalities")