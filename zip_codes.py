import requests
from bs4 import BeautifulSoup
import pandas as pd
from urllib.parse import urljoin

start_url = "https://www.zipcode.com.ng/2022/06/list-of-washington-zip-codes.html"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0"
})

all_rows = []

for page in range(1, 6):

    if page == 1:
        url = start_url
    else:
        url = urljoin(start_url, f"?page{page}#topcontent")

    soup = BeautifulSoup(session.get(url).text, "html.parser")

    for row in soup.select("table tbody tr"):
        tds = row.select("td")

        if len(tds) == 4:
            all_rows.append({
                "City": tds[0].get_text(strip=True),
                "ZIP_Code": tds[1].get_text(strip=True),
                "County": tds[2].get_text(strip=True),
                "State": tds[3].get_text(strip=True),
            })

pd.DataFrame(all_rows).to_csv(
    "zip_codes_wh.csv",
    index=False
)

print(f"Saved {len(all_rows)} rows to zip_codes_sc.csv")

