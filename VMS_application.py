from __future__ import annotations

import argparse
import csv
import mimetypes
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd
import requests


JOB_API_URL = (
    "https://api-nexus.laboredge.com/api/leap-service/v1/unsecured/"
    "jobboard/organization/491?offeringId=ADVANCE_PRACTICE"
)
JOB_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://nexus-leap.laboredge.com",
    "Referer": "https://nexus-leap.laboredge.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0"
    ),
}
JOB_PAGE_SIZE = 50

CSV_COLUMNS = [
    "candidate_name",
    "email",
    "phone",
    "bill_rate",
    "current_location",
    "primary_skills",
    "job_title",
    "years_experience",
    "tentative_start_date",
    "rto",
    "candidate_summary",
    "job_id",
    "resume",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch VMS jobs and submit candidate profiles from one script."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch-jobs",
        help="Fetch job records from the LaborEdge API and save them to a CSV file.",
    )
    fetch_parser.add_argument(
        "--output",
        default="jobs.csv",
        help="Output CSV path for fetched jobs. Default: jobs.csv",
    )
    fetch_parser.add_argument(
        "--page-size",
        type=int,
        default=JOB_PAGE_SIZE,
        help=f"Number of jobs fetched per page. Default: {JOB_PAGE_SIZE}",
    )
    fetch_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds for job fetch requests. Default: 30",
    )

    submit_parser = subparsers.add_parser(
        "submit-candidates",
        help="Submit candidate profiles from a CSV file to the VMS API.",
    )
    submit_parser.add_argument(
        "--csv",
        default="candidate_submission_template.csv",
        help="Path to the CSV file. Default: candidate_submission_template.csv",
    )
    submit_parser.add_argument(
        "--base-url",
        default="https://radixsolvms.com/",
        help="Base URL of the backend API. Default: https://radixsolvms.com/",
    )
    submit_parser.add_argument(
        "--endpoint",
        default="/api/candidates/submit",
        help="Submission endpoint path. Default: /api/candidates/submit",
    )
    submit_parser.add_argument(
        "--login-endpoint",
        default="/api/auth/login",
        help="Login endpoint path. Default: /api/auth/login",
    )
    submit_parser.add_argument(
        "--email",
        required=True,
        help="Vendor email for API login.",
    )
    submit_parser.add_argument(
        "--password",
        required=True,
        help="Vendor password for API login.",
    )
    submit_parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds for login and submission. Default: 60",
    )

    return parser


def build_job_payload(page_size: int, start: int) -> Dict[str, object]:
    return {
        "professionIds": [],
        "countryId": 370,
        "specialtyIds": None,
        "stateCodes": None,
        "jobTypeIds": ["LOCAL", "LOCUM", "PERM", "TRAVEL"],
        "startDate": None,
        "assignmentDuration": None,
        "weeklyPayRange": None,
        "filterByType": None,
        "compactAll": None,
        "featured": None,
        "hotJob": None,
        "openJobFilter": None,
        "pagingSortingDetails": {
            "start": start,
            "maxRowsToFetch": page_size,
            "sortField": "clientName",
            "sortOrder": -1,
        },
        "exclusive": False,
    }


def fetch_all_jobs(page_size: int, timeout: int) -> List[Dict[str, object]]:
    all_jobs: List[Dict[str, object]] = []
    start = 0

    while True:
        response = requests.post(
            JOB_API_URL,
            headers=JOB_API_HEADERS,
            json=build_job_payload(page_size=page_size, start=start),
            timeout=timeout,
        )
        response.raise_for_status()

        data = response.json()
        records = data.get("records", [])
        if not records:
            break

        all_jobs.extend(records)
        print(f"Fetched {len(records)} records (Total: {len(all_jobs)})")

        total_count = data.get("count", len(all_jobs))
        if len(all_jobs) >= total_count:
            break

        start += page_size

    return all_jobs


def flatten_jobs(jobs: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for job in jobs:
        rows.append(
            {
                "Job ID": job.get("jobId"),
                "Reference Code": job.get("refCode"),
                "Status": job.get("status"),
                "Job Type": job.get("jobType"),
                "Profession": job.get("profession"),
                "Specialty": job.get("specialty"),
                "Client Name": job.get("clientName"),
                "City": job.get("city"),
                "State": job.get("state"),
                "Start Date": job.get("startDate"),
                "End Date": job.get("endDate"),
                "Length": job.get("length"),
                "Duration": job.get("duration"),
                "Shift": job.get("shiftName"),
                "Weekly Pay": job.get("weeklyPay"),
                "Hourly Pay": job.get("hourlyPay"),
                "Regular Pay Rate": job.get("regularPayRate"),
                "Bill Rate": job.get("billRate"),
                "Available Openings": job.get("availableOpenings"),
                "Posted Date": job.get("postDate"),
                "Sales Rep": job.get("salesRep"),
                "VMS": job.get("vms"),
                "Latitude": job.get("latitude"),
                "Longitude": job.get("longitude"),
                "Offering ID": job.get("offeringId"),
            }
        )
    return rows


def run_fetch_jobs(args: argparse.Namespace) -> None:
    jobs = fetch_all_jobs(page_size=args.page_size, timeout=args.timeout)
    print(f"\nTotal records fetched: {len(jobs)}")

    rows = flatten_jobs(jobs)
    output_path = Path(args.output).expanduser().resolve()
    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"Saved {len(df)} records to {output_path}")


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def login(
    session: requests.Session,
    base_url: str,
    login_endpoint: str,
    email: str,
    password: str,
    timeout: int,
) -> str:
    response = session.post(
        join_url(base_url, login_endpoint),
        json={"email": email, "password": password},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Login response did not include access_token")
    return token


def validate_csv_columns(fieldnames: Iterable[str] | None) -> None:
    available = set(fieldnames or [])
    missing = [column for column in CSV_COLUMNS if column not in available]
    if missing:
        raise ValueError("CSV is missing required columns: " + ", ".join(missing))


def normalize_row(row: Dict[str, str]) -> Dict[str, str]:
    return {key: (value or "").strip() for key, value in row.items()}


def resolve_resume_path(csv_path: Path, resume_value: str) -> Path:
    resume_path = Path(resume_value).expanduser()
    if not resume_path.is_absolute():
        resume_path = (csv_path.parent / resume_path).resolve()
    return resume_path


def build_form_data(row: Dict[str, str]) -> Dict[str, str]:
    return {column: row[column] for column in CSV_COLUMNS if column != "resume"}


def submit_candidate(
    session: requests.Session,
    submit_url: str,
    csv_path: Path,
    row_number: int,
    row: Dict[str, str],
    timeout: int,
) -> Tuple[bool, str, str]:
    candidate_name = row.get("candidate_name") or f"row {row_number}"
    resume_value = row.get("resume", "")

    if not resume_value:
        return False, candidate_name, "resume path is empty"

    resume_path = resolve_resume_path(csv_path, resume_value)
    if not resume_path.exists():
        return False, candidate_name, f"resume file not found: {resume_path}"
    if not resume_path.is_file():
        return False, candidate_name, f"resume path is not a file: {resume_path}"

    content_type = (
        mimetypes.guess_type(resume_path.name)[0] or "application/octet-stream"
    )
    form_data = build_form_data(row)

    with resume_path.open("rb") as resume_file:
        files = {"resume": (resume_path.name, resume_file, content_type)}
        try:
            response = session.post(
                submit_url,
                data=form_data,
                files=files,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            return False, candidate_name, str(exc)

    if response.ok:
        payload = response.json()
        candidate_id = payload.get("candidate_id", "n/a")
        return True, candidate_name, f"candidate_id={candidate_id}"

    try:
        error_payload = response.json()
        detail = error_payload.get("detail", error_payload)
    except ValueError:
        detail = response.text.strip() or "unknown error"
    return False, candidate_name, f"HTTP {response.status_code}: {detail}"


def run_submit_candidates(args: argparse.Namespace) -> None:
    csv_path = Path(args.csv).expanduser().resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    session = requests.Session()
    token = login(
        session=session,
        base_url=args.base_url,
        login_endpoint=args.login_endpoint,
        email=args.email,
        password=args.password,
        timeout=args.timeout,
    )
    session.headers.update({"Authorization": f"Bearer {token}"})

    submit_url = join_url(args.base_url, args.endpoint)

    total = 0
    success_count = 0
    failure_count = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_csv_columns(reader.fieldnames)

        for row_number, raw_row in enumerate(reader, start=2):
            row = normalize_row(raw_row)
            if not any(row.values()):
                continue

            total += 1
            success, candidate_name, detail = submit_candidate(
                session=session,
                submit_url=submit_url,
                csv_path=csv_path,
                row_number=row_number,
                row=row,
                timeout=args.timeout,
            )
            if success:
                success_count += 1
                print(f"[SUCCESS] {candidate_name}: {detail}")
            else:
                failure_count += 1
                print(f"[FAILURE] {candidate_name}: {detail}")

    print(
        f"\nProcessed {total} candidate(s). "
        f"Success: {success_count}. Failure: {failure_count}."
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "fetch-jobs":
        run_fetch_jobs(args)
        return

    if args.command == "submit-candidates":
        run_submit_candidates(args)
        return

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
