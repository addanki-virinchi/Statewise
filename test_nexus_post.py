# import os
# import requests

# # =================================================================
# # CONFIG
# # =================================================================
# BASE_URL = "https://api-nexus.laboredge.com"
# ORG_ID = 491

# # Pull the token from an environment variable — never hardcode it.
# # In PowerShell:  $env:LABOREDGE_BEARER_TOKEN = "eyJ..."
# BEARER_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJIZXQyMzA2IiwibGFzdE5hbWUiOiJQYXJla2giLCJpc3MiOiJodHRwczovL2FwaS1uZXh1cy5sYWJvcmVkZ2UuY29tL2F1dGgiLCJzeXN0ZW1Vc2VyIjpmYWxzZSwiYWdlbmN5SWQiOjc1MSwibXVsdGlTdXBwbGllckVuYWJsZWQiOmZhbHNlLCJvcmdhbml6YXRpb25JZCI6NDkxLCJwb3dlclVzZXIiOmZhbHNlLCJhcHBsaWNhdGlvblR5cGVJZCI6IkFUU19BR0VOQ1kiLCJvcmdhbml6YXRpb25JZGVudGlmaWVyIjoiUmFkaXgiLCJzY29wZSI6WyJvcGVuaWQiLCJwcm9maWxlIl0sImV4cCI6MTc4NDgwODQ2MCwiaWF0IjoxNzg0ODA0ODYwLCJqdGkiOiIwMjE5YTc1MC0zNWEyLTQ4YzItODFlNi0yOWRlOGI3N2RiZDciLCJlbWFpbCI6ImhldEByYWRpeHNvbC5jb20iLCJlbmFibGVNYXNrQ2FuZGlkYXRlRW1haWxQaG9uZSI6ZmFsc2UsInJvbGVJZCI6MTk4NSwiaGllcmFyY2h5RW5hYmxlZCI6ZmFsc2UsIm1hc3RlckFnZW5jeSI6ZmFsc2UsInVzZXJJZCI6MTc4MDI5Nywib3JnYW5pemF0aW9uVHlwZSI6IkFUUyIsImF1ZCI6Im5leHVzIiwiZmlyc3ROYW1lIjoiSGV0IiwibmJmIjoxNzg0ODA0ODYwLCJvcmdhbml6YXRpb25Db2RlIjoiUmFkaXgiLCJvcmdhbml6YXRpb25Db3VudHJ5SWQiOjM3MCwidGVuYW50SWQiOiJURU5BTlRfRlNNIn0.bjnbXibAGG3GlPIsrJZ4R8eFezClLfhI0Xryv9XHGfs"

# HEADERS = {
#     "Authorization": f"Bearer {BEARER_TOKEN}",
#     "Accept": "application/json, text/plain, */*",
#     # Do NOT set Content-Type manually for multipart —
#     # requests will generate the correct boundary automatically.
# }


# def upload_resume(pdf_path: str, document_type_id: int = 10739, notes: str = "", org_id: int = ORG_ID) -> dict:
#     url = f"{BASE_URL}/api/api-integration/v1/nexus/resume/upload/organizations/{org_id}/resume"

#     with open(pdf_path, "rb") as f:
#         files = {
#             "resume": (os.path.basename(pdf_path), f, "application/pdf"),
#         }
#         data = {
#             "documentTypeId": str(document_type_id),
#             "notes": notes,
#         }
#         resp = requests.post(url, headers=HEADERS, files=files, data=data, timeout=60)

#     resp.raise_for_status()
#     return resp.json()


# if __name__ == "__main__":
#     pdf_path = r"C:\Users\91630\Downloads\Scrapers\Resume.pdf"   # <-- set this to your actual PDF path

#     result = upload_resume(pdf_path)
#     print("Upload response:")
#     print(result)



import os
import copy
import json
import requests

# =================================================================
# CONFIG
# =================================================================
BASE_URL = "https://api-nexus.laboredge.com"
ORG_ID = 491
AGENCY_ID = 751

# Never hardcode the token. Set it in your shell first:
#   PowerShell:  $env:LABOREDGE_BEARER_TOKEN = "eyJ..."
BEARER_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJIZXQyMzA2IiwibGFzdE5hbWUiOiJQYXJla2giLCJpc3MiOiJodHRwczovL2FwaS1uZXh1cy5sYWJvcmVkZ2UuY29tL2F1dGgiLCJzeXN0ZW1Vc2VyIjpmYWxzZSwiYWdlbmN5SWQiOjc1MSwibXVsdGlTdXBwbGllckVuYWJsZWQiOmZhbHNlLCJvcmdhbml6YXRpb25JZCI6NDkxLCJwb3dlclVzZXIiOmZhbHNlLCJhcHBsaWNhdGlvblR5cGVJZCI6IkFUU19BR0VOQ1kiLCJvcmdhbml6YXRpb25JZGVudGlmaWVyIjoiUmFkaXgiLCJzY29wZSI6WyJvcGVuaWQiLCJwcm9maWxlIl0sImV4cCI6MTc4NDgwODQ2MCwiaWF0IjoxNzg0ODA0ODYwLCJqdGkiOiIwMjE5YTc1MC0zNWEyLTQ4YzItODFlNi0yOWRlOGI3N2RiZDciLCJlbWFpbCI6ImhldEByYWRpeHNvbC5jb20iLCJlbmFibGVNYXNrQ2FuZGlkYXRlRW1haWxQaG9uZSI6ZmFsc2UsInJvbGVJZCI6MTk4NSwiaGllcmFyY2h5RW5hYmxlZCI6ZmFsc2UsIm1hc3RlckFnZW5jeSI6ZmFsc2UsInVzZXJJZCI6MTc4MDI5Nywib3JnYW5pemF0aW9uVHlwZSI6IkFUUyIsImF1ZCI6Im5leHVzIiwiZmlyc3ROYW1lIjoiSGV0IiwibmJmIjoxNzg0ODA0ODYwLCJvcmdhbml6YXRpb25Db2RlIjoiUmFkaXgiLCJvcmdhbml6YXRpb25Db3VudHJ5SWQiOjM3MCwidGVuYW50SWQiOiJURU5BTlRfRlNNIn0.bjnbXibAGG3GlPIsrJZ4R8eFezClLfhI0Xryv9XHGfs"


HEADERS_AUTH_ONLY = {
    "Authorization": f"Bearer {BEARER_TOKEN}",
    "Accept": "application/json, text/plain, */*",
}
HEADERS_JSON = {
    "Authorization": f"Bearer {BEARER_TOKEN}",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}
# =================================================================
# LOOKUP HELPERS
# =================================================================

def get_professions() -> list:
    """Returns list of {value, label, ...} profession options."""
    url = f"{BASE_URL}/api/candidate-service/v1/agencies/atscandidate/master/candidate/professions"
    resp = requests.get(url, headers=HEADERS_AUTH_ONLY, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_specialties(profession_id: int) -> list:
    """
    Specialties appear to be profession-dependent (each specialty object
    carries a professionId). CONFIRM the real endpoint + whether
    profession_id is a path param or query param.
    """
    url = f"{BASE_URL}/api/candidate-service/v1/agencies/atscandidate/master/candidate/specialties"
    params = {"professionId": profession_id}
    resp = requests.get(url, headers=HEADERS_AUTH_ONLY, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_referral_sources() -> list:
    """CONFIRM real endpoint path."""
    url = f"{BASE_URL}/api/candidate-service/v1/agencies/atscandidate/master/candidate/referralsources"
    resp = requests.get(url, headers=HEADERS_AUTH_ONLY, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_candidate_statuses() -> list:
    """CONFIRM real endpoint path."""
    url = f"{BASE_URL}/api/candidate-service/v1/agencies/atscandidate/master/candidate/statuses"
    resp = requests.get(url, headers=HEADERS_AUTH_ONLY, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_countries() -> list:
    """CONFIRM real endpoint path."""
    url = f"{BASE_URL}/api/candidate-service/v1/agencies/atscandidate/master/candidate/countries"
    resp = requests.get(url, headers=HEADERS_AUTH_ONLY, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_states(country_id: int) -> list:
    """CONFIRM real endpoint path and whether country_id is required."""
    url = f"{BASE_URL}/api/candidate-service/v1/agencies/atscandidate/master/candidate/states"
    params = {"countryId": country_id}
    resp = requests.get(url, headers=HEADERS_AUTH_ONLY, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


# --- Example: resolve a label to its ID ---
def find_by_label(options: list, label: str, label_key: str = "label", value_key: str = "value"):
    for opt in options:
        if opt.get(label_key, "").strip().lower() == label.strip().lower():
            return opt.get(value_key)
    return None


if __name__ == "__main__":
    professions = get_professions()
    print(f"Loaded {len(professions)} professions")

    nursing_id = find_by_label(professions, "Nursing")
    print("Nursing profession ID:", nursing_id)

    if nursing_id:
        specialties = get_specialties(nursing_id)
        print(f"Loaded {len(specialties)} specialties for Nursing")
        print(specialties[:3])


# # =================================================================
# # STEP 1 — Upload resume  (CONFIRMED: 200 OK, real payload shape)
# # =================================================================
# def upload_resume(pdf_path: str, document_type_id: int = 10739, notes: str = "", org_id: int = ORG_ID) -> dict:
#     url = f"{BASE_URL}/api/api-integration/v1/nexus/resume/upload/organizations/{org_id}/resume"

#     with open(pdf_path, "rb") as f:
#         files = {
#             "resume": (os.path.basename(pdf_path), f, "application/pdf"),
#         }
#         data = {
#             "documentTypeId": str(document_type_id),
#             "notes": notes,
#         }
#         # Don't set Content-Type manually — requests builds the multipart boundary itself
#         resp = requests.post(url, headers=HEADERS_AUTH_ONLY, files=files, data=data, timeout=60)

#     resp.raise_for_status()
#     return resp.json()


# # =================================================================
# # STEP 2 — Create candidate  (CONFIRMED URL: 201 Created previously)
# # Body shape is a GUESS beyond what upload_resume() already returns —
# # confirm the real body via Network tab "Copy as fetch" if this 4xx's.
# # =================================================================
# def create_candidate(candidate_data: dict, agency_id: int = AGENCY_ID) -> dict:
#     url = f"{BASE_URL}/api/candidate-service/v1/agencies/{agency_id}/atscandidates"
#     params = {"IGNORE_MESSAGE": "true"}
#     resp = requests.post(url, headers=HEADERS_JSON, params=params, json=candidate_data, timeout=30)
#     resp.raise_for_status()
#     return resp.json() if resp.content else {}


# # =================================================================
# # MAIN FLOW
# # =================================================================
# if __name__ == "__main__":
#     pdf_path = r"C:\Users\91630\Downloads\Scrapers\Resume.pdf"   # <-- set this

#     # --- Step 1: upload resume, get parsed candidate data ---
#     parsed = upload_resume(pdf_path)
#     print("Parsed resume data:")
#     print(json.dumps(parsed, indent=2))

#     # --- Step 2: edit/fill in required + desired fields ---
#     candidate_data = copy.deepcopy(parsed)

#     candidate_data["referralSourceId"] = 3        # TODO: real ID for e.g. "Mobile"/"Indeed"
#     candidate_data["candidateStatusId"] = 12       # TODO: real ID for desired status
#     candidate_data["recruiterId"] = None           # optional
#     candidate_data["staffingSpecialistId"] = None  # optional
#     candidate_data["credentialingSpecialistId"] = None  # optional

#     if candidate_data.get("candidateAddressDTOs"):
#         candidate_data["candidateAddressDTOs"][0]["countryId"] = 1     # TODO: real country ID
#         candidate_data["candidateAddressDTOs"][0]["organizationStateId"] = 5  # TODO: real state ID
#         candidate_data["candidateAddressDTOs"][0]["city"] = "Austin"

#     if candidate_data.get("candidateProfessionExperienceDTO"):
#         candidate_data["candidateProfessionExperienceDTO"]["professionIds"] = [7]     # TODO: real profession ID(s)
#         candidate_data["candidateProfessionExperienceDTO"]["specialtyIds"] = [22]     # TODO: real specialty ID(s)
#         candidate_data["candidateProfessionExperienceDTO"]["primarySpecialtyId"] = 22  # TODO: real specialty ID

#     # --- Step 3: create the candidate ---
#     created = create_candidate(candidate_data)
#     print("Created candidate:")
#     print(json.dumps(created, indent=2))

#     candidate_id = created.get("id") or created.get("candidateId")
#     if candidate_id:
#         print(f"New candidate URL: https://nexus.laboredge.com/ats/candidates/{candidate_id}/edit/details")