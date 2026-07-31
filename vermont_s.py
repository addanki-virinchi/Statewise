from pathlib import Path
import pandas as pd

# =====================================
# CONFIGURATION
# =====================================
ROOT_FOLDER = r"C:\Users\91630\Downloads\Scrapers\downloads\vermont_rosters"
OUTPUT_FILE = "vermont_combined.csv"

# =====================================
# FUNCTION TO EXTRACT PROFESSION TYPE
# =====================================
def extract_profession_type(file_path):
    """
    Example:
    Audiologist - Emergency_Temporary_License.xlsx
        -> Emergency Temporary License

    Audiologist - Facility_Staff_Inactive_Registration.xlsx
        -> Facility Staff Inactive Registration
    """
    name = Path(file_path).stem  # Remove .xlsx

    if " - " in name:
        profession_type = name.split(" - ", 1)[1]
    else:
        profession_type = name

    profession_type = profession_type.replace("_", " ").strip()

    return profession_type


# =====================================
# READ ALL EXCEL FILES
# =====================================
all_data = []

excel_files = list(Path(ROOT_FOLDER).rglob("*.xlsx"))

print(f"Found {len(excel_files)} Excel files.\n")

for file in excel_files:
    try:
        df = pd.read_excel(file)

        df["profession_type"] = extract_profession_type(file)

        all_data.append(df)

        print(f"✓ {file.name} ({len(df)} rows)")
    except Exception as e:
        print(f"✗ Error reading {file}: {e}")

# =====================================
# COMBINE
# =====================================
if all_data:
    combined = pd.concat(all_data, ignore_index=True)

    combined.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print("\n===================================")
    print(f"Total files : {len(all_data)}")
    print(f"Total rows  : {len(combined)}")
    print(f"Saved to    : {OUTPUT_FILE}")
    print("===================================")
else:
    print("No data found.")