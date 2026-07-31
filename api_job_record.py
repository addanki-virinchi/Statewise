import requests
import pandas as pd

URL = "https://api-nexus.laboredge.com/api/leap-service/v1/unsecured/jobboard/organization/491?offeringId=ADVANCE_PRACTICE"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://nexus-leap.laboredge.com",
    "Referer": "https://nexus-leap.laboredge.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
}

PAGE_SIZE = 50

payload = {
    "professionIds": [],
    "countryId": 370,
    "specialtyIds": None,
    "stateCodes": None,
    "jobTypeIds": ["LOCAL", "LOCUM", "PERM", "TRAVEL"],
    "startDate": None,
    "assignmentDuration": None,
    "weeklyPayRange": None,
    "filterByType": None,
    "compactAll": None,
    "featured": None,
    "hotJob": None,
    "openJobFilter": None,
    "pagingSortingDetails": {
        "start": 0,
        "maxRowsToFetch": PAGE_SIZE,
        "sortField": "clientName",
        "sortOrder": -1,
    },
    "exclusive": False,
}

all_jobs = []
start = 0

while True:
    payload["pagingSortingDetails"]["start"] = start

    response = requests.post(
        URL,
        headers=HEADERS,
        json=payload,
        timeout=30
    )
    response.raise_for_status()

    data = response.json()
    records = data.get("records", [])

    if not records:
        break

    all_jobs.extend(records)

    print(f"Fetched {len(records)} records (Total: {len(all_jobs)})")

    if len(all_jobs) >= data["count"]:
        break

    start += PAGE_SIZE

print(f"\nTotal records fetched: {len(all_jobs)}")

# Keep only useful fields
rows = []

for job in all_jobs:
    rows.append({
        "Job ID": job.get("jobId"),
        "Reference Code": job.get("refCode"),
        "Status": job.get("status"),
        "Job Type": job.get("jobType"),
        "Profession": job.get("profession"),
        "Specialty": job.get("specialty"),
        "Client Name": job.get("clientName"),
        "City": job.get("city"),
        "State": job.get("state"),
        "Start Date": job.get("startDate"),
        "End Date": job.get("endDate"),
        "Length": job.get("length"),
        "Duration": job.get("duration"),
        "Shift": job.get("shiftName"),
        "Weekly Pay": job.get("weeklyPay"),
        "Hourly Pay": job.get("hourlyPay"),
        "Regular Pay Rate": job.get("regularPayRate"),
        "Bill Rate": job.get("billRate"),
        "Available Openings": job.get("availableOpenings"),
        "Posted Date": job.get("postDate"),
        "Sales Rep": job.get("salesRep"),
        "VMS": job.get("vms"),
        "Latitude": job.get("latitude"),
        "Longitude": job.get("longitude"),
        "Offering ID": job.get("offeringId"),
    })

df = pd.DataFrame(rows)

df.to_csv("jobs.csv", index=False, encoding="utf-8-sig")

print(f"Saved {len(df)} records to jobs.csv")