import pandas as pd

# Read CSV
df = pd.read_csv("newjersey.csv")

# Keep only rows where Status is "Active"
cleaned_df = df[df["License Status"] == "Active"]

# Save cleaned CSV
cleaned_df.to_csv("newjersey_active.csv", index=False)

print(f"Saved {len(cleaned_df)} active rows to priority_ohio_nursing_registered_nurse.csv")