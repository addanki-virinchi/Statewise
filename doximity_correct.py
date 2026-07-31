"""Correct a Doximity output CSV that was written by a mismatched schema.

Two problems get fixed here:

1. Column shift. An older/other-server scraper wrote some rows with an extra
   empty field near the start (a stray comma -> ",,"), which pushes every
   later value one column to the right. Read by column name, such a row shows
   `profile_url = "Abdelmottaleb"` and `scrape_status = "...png"` instead of the
   real URL and "ok". This tool anchors on the profile URL (which has an
   unmistakable /pub/ or /cv/ shape), figures out how far the row is shifted,
   and removes the offending empty field(s) to snap it back onto the schema.

2. Duplicates + orphan screenshots. After realignment the same doctor can
   appear on several rows. We keep one row per canonical profile_url (an "ok",
   aligned row wins) and, for each dropped row, delete its screenshot file --
   but only when no surviving row still points at that same file.

The corrected CSV is written to a NEW file (<name>_corrected.csv) so the
original is never touched. Screenshot deletion is destructive and off by
default: run once to preview, then re-run with --delete-screenshots.

Usage:
    python doximity_correct.py doximity_output.csv                 # preview
    python doximity_correct.py doximity_output.csv --delete-screenshots
    python doximity_correct.py in.csv --out fixed.csv
"""

import argparse
import csv
import os
import re
import sys
from urllib.parse import urlparse

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# --- what to correct ------------------------------------------------------
# Set this to the CSV you want to fix, then just run:  python doximity_correct.py
# (There are several output files; change this line before each run.) A CLI
# argument, if given, overrides this.
INPUT_FILE = ""                 # e.g. "doximity_output.csv"
DELETE_SCREENSHOTS = True     # True -> actually delete orphaned duplicate screenshots

# The canonical, current schema every corrected row is snapped onto.
FIELDNAMES = [
    "input_name",
    "input_url",
    "profile_name",
    "first_name",
    "last_name",
    "profile_url",
    "secondary_occupation",
    "sub_speciality_occupations",
    "main_primary_occupation",
    "job_title",
    "alternative_job_title",
    "address",
    "city",
    "state",
    "active_certifications_licenses",
    "other_licenses",
    "board_certification",
    "screenshot_file",
    "scrape_status",
    "scrape_notes",
]
N = len(FIELDNAMES)
IDX_PROFILE_URL = FIELDNAMES.index("profile_url")
IDX_SCREENSHOT = FIELDNAMES.index("screenshot_file")
IDX_STATUS = FIELDNAMES.index("scrape_status")

# A Doximity profile link -- the reliable anchor for realigning a row.
PROFILE_URL_RE = re.compile(r"doximity\.com/(?:pub|cv)/", re.IGNORECASE)


def canonical_profile_url(url: str) -> str:
    """Stable identity for a profile URL -- matches doximity_ser.py."""
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if path.startswith("/cv/"):
        path = "/pub/" + path[len("/cv/"):]
    if netloc:
        return f"{parsed.scheme.lower()}://{netloc}{path}"
    return path


def find_profile_url_index(fields):
    for i, value in enumerate(fields):
        if PROFILE_URL_RE.search(value or ""):
            return i
    return -1


def repair_row(fields):
    """Return (fixed_fields, status).

    status is one of:
      aligned   -- already correct, unchanged
      fixed     -- shift removed / length normalized
      no_url    -- no profile URL found; kept as-is (needs manual review)
      unfixable -- shifted but not enough empty fields to remove safely
    """
    idx = find_profile_url_index(fields)
    if idx == -1:
        # Can't anchor it; leave the data intact, just pad/trim to width.
        return _normalize_width(list(fields)), "no_url"

    delta = idx - IDX_PROFILE_URL
    work = list(fields)

    if delta > 0:
        # Too far right -> extra empty fields were inserted before the URL.
        # Remove `delta` empties from the region between input_url and the URL.
        removed = 0
        i = 2
        end = idx
        while removed < delta and 2 <= i < end and i < len(work):
            if work[i] == "":
                del work[i]
                removed += 1
                end -= 1
            else:
                i += 1
        if removed < delta:
            return _normalize_width(list(fields)), "unfixable"
        return _normalize_width(work), "fixed"

    if delta < 0:
        # Too far left -> fields missing before the URL; pad the name region.
        for _ in range(-delta):
            work.insert(2, "")
        return _normalize_width(work), "fixed"

    normalized = _normalize_width(work)
    return normalized, ("aligned" if normalized == list(fields) else "fixed")


def _normalize_width(fields):
    """Force a row to exactly N fields (pad with '' / trim trailing empties)."""
    if len(fields) < N:
        return fields + [""] * (N - len(fields))
    if len(fields) > N:
        # Only trailing blanks are safe to drop; anything else is truncated but
        # such rows are rare and get flagged by the caller via width change.
        return fields[:N]
    return fields


def looks_like_header(fields):
    lowered = [f.strip().lower() for f in fields]
    return "profile_url" in lowered and "scrape_status" in lowered


def correct_file(path, out_path, delete_screenshots):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        print(f"! {path}: empty file")
        return

    start = 1 if looks_like_header(rows[0]) else 0

    stats = {"aligned": 0, "fixed": 0, "no_url": 0, "unfixable": 0}
    processed = []  # (canon, fixed_fields, status)
    for raw in rows[start:]:
        if not any((cell or "").strip() for cell in raw):
            continue  # skip blank lines
        fixed, status = repair_row(raw)
        stats[status] += 1
        canon = canonical_profile_url(fixed[IDX_PROFILE_URL]) if len(fixed) > IDX_PROFILE_URL else ""
        processed.append((canon, fixed, status))

    # --- dedupe on canonical profile_url ---------------------------------
    keep_by_canon = {}
    order = []
    review_rows = []  # rows with no usable key -- always kept, never merged
    for canon, fixed, status in processed:
        if not canon:
            review_rows.append(fixed)
            continue
        if canon not in keep_by_canon:
            # Keep the FIRST occurrence of each profile_url; every later row
            # with the same URL is a duplicate to drop.
            order.append(canon)
            keep_by_canon[canon] = (fixed, status)

    kept_rows = [keep_by_canon[c][0] for c in order]
    total_rows = len(processed)
    duplicates_removed = total_rows - len(order) - len(review_rows)

    # --- screenshot cleanup for dropped duplicates -----------------------
    kept_shots = {
        r[IDX_SCREENSHOT].strip()
        for r in kept_rows + review_rows
        if len(r) > IDX_SCREENSHOT and r[IDX_SCREENSHOT].strip()
    }
    dropped_shots = []
    kept_canons = set(order)
    seen_kept = set()
    for canon, fixed, status in processed:
        if not canon:
            continue
        keeper = keep_by_canon[canon][0]
        if fixed is keeper and canon not in seen_kept:
            seen_kept.add(canon)
            continue  # this is the survivor
        shot = fixed[IDX_SCREENSHOT].strip() if len(fixed) > IDX_SCREENSHOT else ""
        if shot and shot not in kept_shots:
            dropped_shots.append(shot)

    deleted, missing, protected = _handle_screenshots(dropped_shots, delete_screenshots)

    # --- write corrected CSV ---------------------------------------------
    with open(out_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDNAMES)
        for row in kept_rows:
            writer.writerow(row)
        for row in review_rows:
            writer.writerow(row)

    # --- report ----------------------------------------------------------
    print(f"* {path}")
    print(f"    rows read           : {total_rows}")
    print(f"    aligned / fixed     : {stats['aligned']} / {stats['fixed']}")
    if stats["unfixable"]:
        print(f"    UNFIXABLE (kept)    : {stats['unfixable']}  <- inspect manually")
    if stats["no_url"]:
        print(f"    no profile_url (kept): {stats['no_url']}")
    print(f"    duplicates removed  : {duplicates_removed}")
    print(f"    unique rows written : {len(kept_rows) + len(review_rows)} -> {out_path}")
    print(f"    duplicate screenshots: {len(dropped_shots)} "
          f"(deleted {deleted}, not-on-disk {missing}, protected {protected})")
    if not delete_screenshots and dropped_shots:
        print("    (screenshots NOT deleted -- re-run with --delete-screenshots to remove them)")


def _handle_screenshots(paths, delete):
    deleted = missing = protected = 0
    for path in paths:
        if not os.path.exists(path):
            missing += 1
            continue
        if delete:
            try:
                os.remove(path)
                deleted += 1
                print(f"      deleted screenshot: {path}")
            except OSError as exc:
                protected += 1
                print(f"      could NOT delete {path}: {exc}")
        else:
            print(f"      would delete: {path}")
    return deleted, missing, protected


def main():
    parser = argparse.ArgumentParser(description="Correct + dedupe a Doximity output CSV.")
    parser.add_argument("files", nargs="*", help="CSV file(s) to correct (overrides INPUT_FILE at top of script)")
    parser.add_argument("--out", default=None, help="Output path (single input only; default <name>_corrected.csv)")
    parser.add_argument("--suffix", default="_corrected", help="Suffix for output files (default: _corrected)")
    parser.add_argument("--delete-screenshots", action="store_true",
                        help="Actually delete orphaned duplicate screenshot files (default: preview only)")
    args = parser.parse_args()

    # Fall back to the INPUT_FILE constant when no path is given on the CLI.
    files = args.files or ([INPUT_FILE] if INPUT_FILE else [])
    if not files:
        print("No input file. Set INPUT_FILE at the top of the script, or pass a path.")
        return
    delete_screenshots = args.delete_screenshots or DELETE_SCREENSHOTS

    if args.out and len(files) > 1:
        print("--out can only be used with a single input file.")
        return

    for path in files:
        if not os.path.isfile(path):
            print(f"! {path}: not found")
            continue
        if args.out:
            out_path = args.out
        else:
            base, ext = os.path.splitext(path)
            out_path = f"{base}{args.suffix}{ext or '.csv'}"
        if os.path.abspath(out_path) == os.path.abspath(path):
            print(f"! {path}: output equals input; skipping")
            continue
        correct_file(path, out_path, delete_screenshots)


if __name__ == "__main__":
    main()



