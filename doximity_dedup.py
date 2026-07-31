"""Deduplicate Doximity output CSVs on the canonical profile_url.

The scraper's `profile_url` column is the intended unique key, but the same
doctor appears under several different URL strings (tracking query params, a
trailing slash, mixed host casing, or a /cv/ link instead of /pub/). This tool
collapses those to one canonical identity and keeps a single row per doctor --
preferring a row whose scrape_status is "ok" when duplicates disagree.

It does NOT re-scrape and does NOT overwrite the input: for each file it writes
a sibling <name>_deduped.csv (override with --suffix or --out).

Usage:
    python doximity_dedup.py doximity_output_server_3.csv
    python doximity_dedup.py *.csv
    python doximity_dedup.py in.csv --out cleaned.csv
    python doximity_dedup.py in.csv --key profile_url --suffix _clean
"""

import argparse
import csv
import glob
import os
import sys
from urllib.parse import urlparse

# CSVs can carry long fields; lift the default limit so nothing is silently cut.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def canonical_profile_url(url: str) -> str:
    """Stable identity for a profile URL -- must match doximity_ser.py."""
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


def dedupe_file(path: str, key: str, out_path: str) -> dict:
    """Rewrite `path` into `out_path` with one row per canonical key.

    Returns a small stats dict. Rows lacking the key column are passed through
    untouched (they can't be deduped), but that is reported.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        if key not in fieldnames:
            return {"skipped": True, "reason": f"no '{key}' column", "fieldnames": fieldnames}

        rows_by_key = {}
        order = []
        total = 0
        no_key = 0
        for row in reader:
            total += 1
            canon = canonical_profile_url(row.get(key) or "")
            if not canon:
                # No usable key -- keep it, but under a unique sentinel so it is
                # never merged with another keyless row.
                no_key += 1
                order.append(("__nokey__", no_key))
                rows_by_key[("__nokey__", no_key)] = row
                continue

            existing = rows_by_key.get(canon)
            if existing is None:
                order.append(canon)
                rows_by_key[canon] = row
            elif _prefer(row, existing):
                rows_by_key[canon] = row

    with open(out_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for canon in order:
            writer.writerow(rows_by_key[canon])

    kept = len(order)
    return {
        "skipped": False,
        "total": total,
        "kept": kept,
        "removed": total - kept,
        "no_key": no_key,
        "out_path": out_path,
    }


def _prefer(new_row: dict, existing_row: dict) -> bool:
    """True if new_row should replace existing_row for the same key.

    Prefer a successful scrape; between two same-status rows keep the first seen.
    """
    if "scrape_status" not in new_row and "scrape_status" not in existing_row:
        return False
    new_ok = (new_row.get("scrape_status") or "").strip() == "ok"
    old_ok = (existing_row.get("scrape_status") or "").strip() == "ok"
    return new_ok and not old_ok


def resolve_inputs(patterns):
    """Expand globs (and pass through literal paths) into a de-duplicated list."""
    seen = set()
    files = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if not matches and os.path.exists(pattern):
            matches = [pattern]
        for match in matches:
            full = os.path.abspath(match)
            if full in seen or not os.path.isfile(match):
                continue
            seen.add(full)
            files.append(match)
    return files


def main():
    parser = argparse.ArgumentParser(description="Deduplicate Doximity CSVs on canonical profile_url.")
    parser.add_argument("files", nargs="+", help="CSV file(s) or glob(s) to deduplicate")
    parser.add_argument("--key", default="profile_url", help="Column holding the unique URL (default: profile_url)")
    parser.add_argument("--suffix", default="_deduped", help="Suffix for output files (default: _deduped)")
    parser.add_argument("--out", default=None, help="Explicit output path (only valid with a single input file)")
    args = parser.parse_args()

    inputs = resolve_inputs(args.files)
    if not inputs:
        print("No matching input files.")
        return
    if args.out and len(inputs) > 1:
        print("--out can only be used with a single input file.")
        return

    for path in inputs:
        if args.out:
            out_path = args.out
        else:
            base, ext = os.path.splitext(path)
            out_path = f"{base}{args.suffix}{ext or '.csv'}"

        if os.path.abspath(out_path) == os.path.abspath(path):
            print(f"! {path}: output path equals input; skipping to avoid overwrite")
            continue

        stats = dedupe_file(path, args.key, out_path)
        if stats.get("skipped"):
            print(f"! {path}: {stats['reason']} (columns: {stats.get('fieldnames')}) -- skipped")
            continue

        note = f" ({stats['no_key']} rows had no key)" if stats["no_key"] else ""
        print(
            f"* {path}: {stats['total']} rows -> {stats['kept']} unique "
            f"({stats['removed']} duplicates removed){note}"
        )
        print(f"    wrote {stats['out_path']}")


if __name__ == "__main__":
    main()
