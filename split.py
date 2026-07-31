import pandas as pd

# Read master CSV
df = pd.read_csv("PA_Medicine copy.csv")

# Split by board column
for board_name, group in df.groupby("Searched Board/Commission"):
    # Clean filename (optional)
    filename = f"PA {board_name}.csv"
    
    # Save each board's data
    group.to_csv(filename, index=False)

    print(f"Saved: {filename} ({len(group)} rows)")