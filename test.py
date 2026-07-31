#!/usr/bin/env python3
"""
Fetch Missouri MO PRO license application data for a list of business IDs (blId)
via the Salesforce Aura endpoint -- no browser required.

The "business id" in your CSV (e.g. 0cEcs000000Y8KKEA0) is the Salesforce record
id passed as `blId` into MODPR_LicenseSearchController.getApplicationData.

Usage:
    python mopro_fetch.py input.csv --id-column business_id --out results.jsonl

If --id-column is omitted, the first column of the CSV is used.
Output is JSON Lines: one record per business id, each with the raw returnValue.
"""

import argparse
import csv
import json
import re
import sys
import time

import requests

BASE = "https://mopro.mo.gov"
SEARCH_PAGE = f"{BASE}/license/s/license-search"
AURA_URL = f"{BASE}/license/s/sfsites/aura?r=6&aura.ApexAction.execute=1"
APP = "siteforce:communityApp"

# Captured from a working browser request. Salesforce rotates fwuid on every
# deploy, so the script tries to refresh these from the live page first and
# only falls back to these values if the refresh fails.
FALLBACK_FWUID = ("OUcwT3JDYUZld21JQ2ZOckR1VnppUWtVMjdnTGFERUU2"
                  "S3FfSVdrcU92bkExNC4xOTIuODM4ODYwOA")
FALLBACK_APP_VERSION = "1684_KM73-ooay8cQA67rJ6OvFA"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0")


def new_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def refresh_context(session):
    """Load the search page to (a) obtain guest cookies and (b) read the
    current fwuid / app version so requests aren't rejected as out-of-date."""
    fwuid, app_version = FALLBACK_FWUID, FALLBACK_APP_VERSION
    try:
        r = session.get(SEARCH_PAGE, timeout=30)
        html = r.text
        m = re.search(r'"fwuid":"([^"]+)"', html)
        if m:
            fwuid = m.group(1)
        m = re.search(
            r'"APPLICATION@markup://siteforce:communityApp":"([^"]+)"', html)
        if m:
            app_version = m.group(1)
    except requests.RequestException as e:
        print(f"[warn] could not refresh context, using fallback: {e}",
              file=sys.stderr)
    return {
        "mode": "PROD",
        "fwuid": fwuid,
        "app": APP,
        "loaded": {f"APPLICATION@markup://{APP}": app_version},
        "dn": [],
        "globals": {},
        "uad": True,
    }


def build_message(bl_id, status="Active", direct_application=False):
    return {
        "actions": [{
            "id": "78;a",
            "descriptor": "aura://ApexActionController/ACTION$execute",
            "callingDescriptor": "UNKNOWN",
            "params": {
                "namespace": "",
                "classname": "MODPR_LicenseSearchController",
                "method": "getApplicationData",
                "params": {
                    "blId": bl_id,
                    "status": status,
                    "directApplication": direct_application,
                },
                "cacheable": False,
                "isContinuation": False,
            },
        }]
    }


def fetch_one(session, ctx, bl_id, status="Active"):
    payload = {
        "message": json.dumps(build_message(bl_id, status)),
        "aura.context": json.dumps(ctx),
        "aura.pageURI": "/license/s/license-search",
        "aura.token": "null",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": BASE,
        "Referer": SEARCH_PAGE,
        "X-SFDC-LDS-Endpoints": ("ApexActionController.execute:"
                                 "MODPR_LicenseSearchController.getApplicationData"),
    }
    r = session.post(AURA_URL, data=payload, headers=headers, timeout=30)
    text = r.text
    # strip any XSSI / junk prefix just in case
    i = text.find("{")
    data = json.loads(text[i:]) if i >= 0 else {}
    return r.status_code, data


def is_out_of_sync(data):
    if not isinstance(data, dict):
        return False
    ev = data.get("event") or {}
    desc = ev.get("descriptor", "") or ""
    return ("outOfSync" in desc) or bool(data.get("exceptionEvent"))


def extract_return_value(data):
    """Return (value, error_message). error_message is None on success."""
    actions = (data or {}).get("actions") or []
    if not actions:
        # look for a top-level aura error
        if isinstance(data, dict) and data.get("event"):
            return None, f"aura event: {data['event'].get('descriptor')}"
        return None, "no actions in response"
    a = actions[0]
    if a.get("state") == "SUCCESS":
        return a.get("returnValue"), None
    errs = a.get("error") or []
    msg = errs[0].get("message") if errs else a.get("state", "unknown error")
    return None, msg


def read_ids(csv_path, id_column):
    ids = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            sys.exit("CSV appears to be empty or has no header row.")
        col = id_column or reader.fieldnames[0]
        if col not in reader.fieldnames:
            sys.exit(f"Column {col!r} not found. Available: {reader.fieldnames}")
        for row in reader:
            v = (row.get(col) or "").strip()
            if v:
                ids.append(v)
    return ids


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_path", help="Path to input CSV")
    ap.add_argument("--id-column", default=None,
                    help="Column holding the business id (blId). "
                         "Defaults to the first column.")
    ap.add_argument("--status", default="Active",
                    help="status param sent to the API (default: Active)")
    ap.add_argument("--out", default="results.jsonl",
                    help="Output JSONL path (default: results.jsonl)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="Seconds to wait between requests (default: 1.0)")
    args = ap.parse_args()

    ids = read_ids(args.csv_path, args.id_column)
    if not ids:
        sys.exit("No business ids found in CSV.")
    print(f"Loaded {len(ids)} business id(s).")

    session = new_session()
    ctx = refresh_context(session)
    print(f"Using fwuid={ctx['fwuid'][:16]}...")

    ok = err = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for i, bl_id in enumerate(ids, 1):
            try:
                status_code, data = fetch_one(session, ctx, bl_id, args.status)
                if is_out_of_sync(data):
                    # framework version changed -> refresh and retry once
                    ctx = refresh_context(session)
                    status_code, data = fetch_one(session, ctx, bl_id, args.status)
                value, problem = extract_return_value(data)
            except Exception as e:  # noqa: BLE001 - keep the batch running
                value, problem = None, f"{type(e).__name__}: {e}"

            rec = {"blId": bl_id, "ok": problem is None,
                   "error": problem, "data": value}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()

            if problem is None:
                ok += 1
                print(f"[{i}/{len(ids)}] {bl_id} -> ok")
            else:
                err += 1
                print(f"[{i}/{len(ids)}] {bl_id} -> ERROR: {problem}",
                      file=sys.stderr)

            time.sleep(args.delay)

    print(f"\nDone. {ok} ok, {err} error(s). Output written to {args.out}")


if __name__ == "__main__":
    main()