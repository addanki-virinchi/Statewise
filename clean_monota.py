import os
import glob
import pandas as pd

# ==========================
# Configuration
# ==========================
CSV_FOLDER = r"C:\Users\91630\Downloads\Scrapers\downloads\monota"   # <-- Change this
MASTER_CSV = "mastercsv-montana.csv"
CLEAN_CSV = "clean_Montana.csv"

# ==========================
# Merge all CSVs
# ==========================
csv_files = glob.glob(os.path.join(CSV_FOLDER, "*.csv"))

if not csv_files:
    print("No CSV files found.")
    exit()

print(f"Found {len(csv_files)} CSV files.")

dfs = []

for file in csv_files:
    try:
        df = pd.read_csv(file, dtype=str)
        dfs.append(df)
        print(f"Loaded: {os.path.basename(file)} ({len(df)} rows)")
    except Exception as e:
        print(f"Failed to read {file}: {e}")

master_df = pd.concat(dfs, ignore_index=True)

# Save merged CSV
master_path = os.path.join(CSV_FOLDER, MASTER_CSV)
master_df.to_csv(master_path, index=False)

print(f"\nMerged {len(master_df)} rows.")
print(f"Saved: {master_path}")

# ==========================
# Keep only Active Status
# ==========================

active_df = master_df[
    master_df["Status"]
    .fillna("")
    .str.strip()
    .str.lower()
    == "active"
]

clean_path = os.path.join(CSV_FOLDER, CLEAN_CSV)
active_df.to_csv(clean_path, index=False)

print(f"Active rows: {len(active_df)}")
print(f"Saved: {clean_path}")