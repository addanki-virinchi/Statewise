import pandas as pd

# Read CSV
df = pd.read_csv("colorado_results.csv")

# Keep only Active licenses
cleaned_df = df[
    df["License Status"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.startswith("Active", na=False)
].copy()

# Split "LastName, FirstName"
name_split = (
    cleaned_df["Name"]
    .fillna("")
    .astype(str)
    .str.split(",", n=1, expand=True)
)

cleaned_df["Last Name"] = name_split[0].str.strip()
cleaned_df["First Name"] = name_split[1].fillna("").str.strip()

# Move First Name and Last Name next to Full Name
cols = cleaned_df.columns.tolist()

cols.remove("First Name")
cols.remove("Last Name")

full_name_index = cols.index("Name")

cols = (
    cols[:full_name_index + 1]
    + ["First Name", "Last Name"]
    + cols[full_name_index + 1:]
)

cleaned_df = cleaned_df[cols]

# Save
cleaned_df.to_csv("Colorado_DB.csv", index=False)

print(f"Saved {len(cleaned_df)} active rows.")
# import pandas as pd

# # Read CSV
# df = pd.read_csv("connecut.csv")

# # Keep only ACTIVE licenses
# cleaned_df = df[
#     df["status"]
#     .fillna("")
#     .astype(str)
#     .str.strip()
#     .str.upper()
#     .eq("ACTIVE")
# ].copy()


# def split_name(name):
#     if pd.isna(name):
#         return "", "", ""

#     parts = str(name).strip().split()

#     if len(parts) == 0:
#         return "", "", ""

#     elif len(parts) == 1:
#         return parts[0], "", ""

#     elif len(parts) == 2:
#         return parts[0], "", parts[1]

#     else:
#         first_name = parts[0]
#         middle_name = " ".join(parts[1:-1])
#         last_name = parts[-1]
#         return first_name, middle_name, last_name


# cleaned_df[["First Name", "Middle Name", "Last Name"]] = cleaned_df["name"].apply(
#     lambda x: pd.Series(split_name(x))
# )

# # Move new columns after 'name'
# cols = cleaned_df.columns.tolist()

# for col in ["First Name", "Middle Name", "Last Name"]:
#     cols.remove(col)

# name_index = cols.index("name")

# cols = (
#     cols[:name_index + 1]
#     + ["First Name", "Middle Name", "Last Name"]
#     + cols[name_index + 1:]
# )

# cleaned_df = cleaned_df[cols]

# # Save
# cleaned_df.to_csv("clean_connecticut.csv", index=False)

# print(f"Saved {len(cleaned_df)} active rows.")


import pandas as pd

# Read CSV
df = pd.read_csv("Maryland.csv")

# Keep only rows where Status starts with "Active"
cleaned_df = df[
    df["Status"]
    .fillna("")
    .astype(str)
    .str.strip()
    .str.startswith("Active", na=False)
].copy()

# Split "Full Name" into Last Name and First Name
name_split = (
    cleaned_df["Full Name"]
    .fillna("")
    .astype(str)
    .str.split(",", n=1, expand=True)
)

# Last Name
cleaned_df["Last Name"] = name_split[0].fillna("").str.strip()

# First Name (only if a second column exists)
if 1 in name_split.columns:
    cleaned_df["First Name"] = name_split[1].fillna("").str.strip()
else:
    cleaned_df["First Name"] = ""

# Move First Name and Last Name next to Full Name
cols = cleaned_df.columns.tolist()

cols.remove("First Name")
cols.remove("Last Name")

full_name_index = cols.index("Full Name")

new_cols = (
    cols[: full_name_index + 1]
    + ["First Name", "Last Name"]
    + cols[full_name_index + 1 :]
)

cleaned_df = cleaned_df[new_cols]

# Save cleaned file
cleaned_df.to_csv("clean_Maryland.csv", index=False)

print(f"Saved {len(cleaned_df)} active rows.")