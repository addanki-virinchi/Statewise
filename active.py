import pandas as pd

# Read the CSV
df = pd.read_csv("combined_licenses.csv")

# Keep only rows where status is Active
active_df = df[df["status"] == "Active"]

# Save the filtered data
active_df.to_csv("active_records_wi.csv", index=False)

print(f"Total Active records: {len(active_df)}")