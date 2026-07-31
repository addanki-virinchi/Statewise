from pathlib import Path
import csv


BASE_DIR = Path(__file__).resolve().parent
DATABASE_FILE = BASE_DIR / "zip_codes.csv"      # master zip database
INPUT_FILE = BASE_DIR / "PA State Board of Medicine.csv"         # file to check against the database
OUTPUT_FILE = BASE_DIR / "zip_codes_left_Med.csv"   # remaining zip codes


def norm_zip(value):
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:5] if len(digits) >= 5 else text


def main():
    with DATABASE_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        db_rows = list(csv.DictReader(f))

    searched = set()
    with INPUT_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames and "Searched Zip" in reader.fieldnames:
            for row in reader:
                z = norm_zip(row.get("Searched Zip"))
                if z:
                    searched.add(z)

    remaining = [
        row for row in db_rows
        if norm_zip(row.get("ZIP_Code")) not in searched
    ]

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["City", "ZIP_Code", "County", "State"])
        writer.writeheader()
        writer.writerows(remaining)

    print(f"Total zip codes: {len(db_rows)}")
    print(f"Found in input file: {len(db_rows) - len(remaining)}")
    print(f"Left to process: {len(remaining)}")
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
