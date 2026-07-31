"""
Bulk candidate creation script for LaborEdge Nexus (api-nexus.laboredge.com)

STATUS: This is a working scaffold, not a finished script.
The resume-upload and candidate-lookup calls are filled in from confirmed
requests. The CREATE-candidate endpoint and its JSON payload are placeholders
marked with TODO — I don't have that request yet, so guessing the field
names would likely just produce 400/422 errors against the real API.

HOW TO FINISH THIS:
1. In the Nexus UI, manually create one test candidate.
2. In Chrome DevTools > Network (XHR filter), find the POST request that
   fires on submit. It's likely:
       POST /api/candidate-service/v1/agencies/{agencyId}/atscandidates
   but confirm the exact path from the Network tab.
3. Copy its Payload/Request JSON body and send it to me (redact nothing
   sensitive about the candidate is needed, just the field *names* matter,
   but real candidate data should also be handled per your data policies).
4. I'll fill in `build_candidate_payload()` below to match exactly.

SECURITY NOTE:
- Never commit real bearer tokens to source control or shared chats.
- Load the token from an environment variable, not hardcoded in this file.
- Tokens with embedded JWT claims (like the one shown earlier) reveal the
  acting user's identity, org ID, and role — treat them like passwords.
"""

import os
import sys
import csv
import time
import logging
from dataclasses import dataclass
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://api-nexus.laboredge.com"
AGENCY_ID = 751          # from the token's agencyId claim
ORGANIZATION_ID = 491    # from the token's organizationId claim

# Load token from environment, e.g.:
#   export LABOREDGE_TOKEN="Bearer eyJ..."
BEARER_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyaWNreS5zaW5naEByYWRpeHNvbC5jb20iLCJsYXN0TmFtZSI6IlNpbmdoIiwiaXNzIjoiaHR0cHM6Ly9hcGktbmV4dXMubGFib3JlZGdlLmNvbS9hdXRoIiwic3lzdGVtVXNlciI6ZmFsc2UsImFnZW5jeUlkIjo3NTEsIm11bHRpU3VwcGxpZXJFbmFibGVkIjpmYWxzZSwib3JnYW5pemF0aW9uSWQiOjQ5MSwicG93ZXJVc2VyIjpmYWxzZSwiYXBwbGljYXRpb25UeXBlSWQiOiJBVFNfQUdFTkNZIiwib3JnYW5pemF0aW9uSWRlbnRpZmllciI6IlJhZGl4Iiwic2NvcGUiOlsib3BlbmlkIiwicHJvZmlsZSJdLCJleHAiOjE3ODQxMzM3NjgsImlhdCI6MTc4NDEzMDE2OCwianRpIjoiMWQ2MmYyYWQtMTFmYi00OWYyLWJhMDUtMzBiMjFjNjQ2ZjQyIiwiZW1haWwiOiJyaWNreS5zaW5naEByYWRpeHNvbC5jb20iLCJlbmFibGVNYXNrQ2FuZGlkYXRlRW1haWxQaG9uZSI6ZmFsc2UsInJvbGVJZCI6MTk2MCwiaGllcmFyY2h5RW5hYmxlZCI6ZmFsc2UsIm1hc3RlckFnZW5jeSI6ZmFsc2UsInVzZXJJZCI6MTY5MTA4OCwib3JnYW5pemF0aW9uVHlwZSI6IkFUUyIsImF1ZCI6Im5leHVzIiwiZmlyc3ROYW1lIjoiUmlja3kiLCJuYmYiOjE3ODQxMzAxNjgsIm9yZ2FuaXphdGlvbkNvZGUiOiJSYWRpeCIsIm9yZ2FuaXphdGlvbkNvdW50cnlJZCI6MzcwLCJ0ZW5hbnRJZCI6IlRFTkFOVF9GU00ifQ.UmA10O-lg2e8EhQzeNS7nqZFdP39Yuax4SqcnVoYauo"
if not BEARER_TOKEN:
    sys.exit(
        "ERROR: set the LABOREDGE_TOKEN environment variable first, e.g.\n"
        '  export LABOREDGE_TOKEN="BearereyJhbGciOi..."'
    )

HEADERS_JSON = {
    "accept": "application/json, text/plain, */*",
    "authorization": BEARER_TOKEN,
    "content-type": "application/json",
    "origin": "https://nexus.laboredge.com",
    "referer": "https://nexus.laboredge.com/",
}

HEADERS_MULTIPART = {
    "accept": "application/json, text/plain, */*",
    "authorization": BEARER_TOKEN,
    "origin": "https://nexus.laboredge.com",
    "referer": "https://nexus.laboredge.com/",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bulk_create_candidates.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model — adjust fields to match your CSV columns
# ---------------------------------------------------------------------------

@dataclass
class CandidateRow:
    first_name: str
    last_name: str
    email: str
    phone: str
    resume_path: Optional[str] = None
    document_type_id: int = 10739  # confirmed from your resume upload call
    notes: str = ""

    @classmethod
    def from_csv_row(cls, row: dict) -> "CandidateRow":
        return cls(
            first_name=row.get("first_name", "").strip(),
            last_name=row.get("last_name", "").strip(),
            email=row.get("email", "").strip(),
            phone=row.get("phone", "").strip(),
            resume_path=row.get("resume_path", "").strip() or None,
            notes=row.get("notes", "").strip(),
        )


# ---------------------------------------------------------------------------
# Step 1: Upload resume (confirmed endpoint)
# ---------------------------------------------------------------------------

def upload_resume(candidate: CandidateRow) -> Optional[dict]:
    """Uploads a resume file and returns the API response (likely contains
    a document ID to reference when creating the candidate)."""
    if not candidate.resume_path or not os.path.isfile(candidate.resume_path):
        log.info(f"No resume file for {candidate.email}, skipping upload.")
        return None

    url = f"{BASE_URL}/api/api-integration/v1/nexus/resume/upload/organizations/{ORGANIZATION_ID}/resume"

    with open(candidate.resume_path, "rb") as f:
        files = {
            "resume": (os.path.basename(candidate.resume_path), f, "application/pdf"),
        }
        data = {
            "documentTypeId": str(candidate.document_type_id),
            "notes": candidate.notes,
        }
        resp = requests.post(url, headers=HEADERS_MULTIPART, files=files, data=data)

    if resp.status_code >= 400:
        log.error(f"Resume upload failed for {candidate.email}: {resp.status_code} {resp.text}")
        return None

    log.info(f"Resume uploaded for {candidate.email}: {resp.status_code}")
    return resp.json()


# ---------------------------------------------------------------------------
# Step 2: Create candidate — PLACEHOLDER, needs real endpoint + payload
# ---------------------------------------------------------------------------

def build_candidate_payload(candidate: CandidateRow, resume_response: Optional[dict]) -> dict:
    """
    TODO: Replace this with the exact field names/structure captured from
    DevTools when you manually create a candidate in the UI.

    This is only a best-guess placeholder based on common ATS field naming
    and should NOT be trusted to work as-is.
    """
    payload = {
        "firstName": candidate.first_name,
        "lastName": candidate.last_name,
        "email": candidate.email,
        "phone": candidate.phone,
        "notes": candidate.notes,
        # "resumeDocumentId": resume_response.get("id") if resume_response else None,
    }
    return payload


def create_candidate(candidate: CandidateRow, resume_response: Optional[dict]) -> Optional[dict]:
    # TODO: confirm exact path — this is a guess based on the URL pattern
    # seen in your other agency-scoped calls.
    url = f"{BASE_URL}/api/candidate-service/v1/agencies/{AGENCY_ID}/atscandidates"

    payload = build_candidate_payload(candidate, resume_response)

    resp = requests.post(url, headers=HEADERS_JSON, json=payload)

    if resp.status_code >= 400:
        log.error(f"Create failed for {candidate.email}: {resp.status_code} {resp.text}")
        return None

    log.info(f"Created candidate {candidate.email}: {resp.status_code}")
    return resp.json()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main(csv_path: str):
    if not os.path.isfile(csv_path):
        sys.exit(f"CSV file not found: {csv_path}")

    results = {"success": 0, "failed": 0}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            candidate = CandidateRow.from_csv_row(row)
            log.info(f"[{i}] Processing {candidate.email}")

            resume_response = upload_resume(candidate)
            created = create_candidate(candidate, resume_response)

            if created:
                results["success"] += 1
            else:
                results["failed"] += 1

            # Be polite to the API — avoid hammering it in a tight loop
            time.sleep(0.5)

    log.info(f"Done. Success: {results['success']}, Failed: {results['failed']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python bulk_create_candidates.py candidates.csv")
    main(sys.argv[1])