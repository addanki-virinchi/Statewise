import json
import pandas as pd
from pathlib import Path

all_records = []

for json_file in Path(".").glob("*.json"):
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        for action in data.get("actions", []):
            records = (
                action.get("returnValue", {})
                .get("returnValue", [])
            )

            if isinstance(records, list):
                for record in records:
                    record["source_file"] = json_file.name
                    all_records.append(record)

        print(f"Processed {json_file.name}")

    except Exception as e:
        print(f"Error processing {json_file.name}: {e}")

if all_records:
    df = pd.DataFrame(all_records)
    df.to_csv("combined_licenses.csv", index=False)
    print(f"Saved {len(df)} records")
else:
    print("No records found")