from __future__ import annotations

import argparse
import csv
import mimetypes
from pathlib import Path
from typing import Dict, Iterable, Tuple

import requests


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit candidate profiles from a CSV file to the VMS API."
    )
    parser.add_argument(
        "--csv",
        default="candidate_submission_template.csv",
        help="Path to the CSV file. Default: candidate_submission_template.csv",
    )
    parser.add_argument(
        "--base-url",
        default="https://radixsolvms.com/",
        help="Base URL of the backend API. Default: https://radixsolvms.com/",
    )
    parser.add_argument(
        "--endpoint",
        default="/api/candidates/submit",
        help="Submission endpoint path. Default: /api/candidates/submit",
    )
    parser.add_argument(
        "--login-endpoint",
        default="/api/auth/login",
        help="Login endpoint path. Default: /api/auth/login",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Vendor email for API login.",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="Vendor password for API login.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds. Default: 60",
    )
    return parser.parse_args()


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
        raise ValueError(
            "CSV is missing required columns: " + ", ".join(missing)
        )


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
        files = {
            "resume": (resume_path.name, resume_file, content_type),
        }
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


def main() -> None:
    args = parse_args()
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


if __name__ == "__main__":
    main()

#run this command - python submit_candidate_client.py --email your_vendor_email --password your_password --csv candidate_submission_template.csv --base-url https://radixsolvms.com/
# python submit_candidate_client.py --email virinchi@radixsol.com --password virinchi@321 --csv candidate_submission_template.csv --base-url https://radixsolvms.com/