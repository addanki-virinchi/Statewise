"""Minimal NopeCHA REST client.

Only the two solvers the Colorado scraper actually uses are kept:
  * solve_textcaptcha        -> FormShield image/text captcha
  * solve_awscaptcha_audio   -> AWS WAF audio-accessibility challenge
Plus get_balance() so you can verify the key the script is actually sending.

API shape (from https://nopecha.com/api-reference/):
  POST {BASE_URL}/recognition/<type>   body {"image_data"/"audio_data": [...]}
       -> {"data": "<job_id>"}
  GET  {BASE_URL}/recognition/<type>?id=<job_id>
       -> {"data": ["answer"]}                       (solved)
       -> {"error": 14, "message": "Incomplete job"} (still working)
  GET  {BASE_URL}/status                              (remaining credit / plan)
"""

import os
import time

import requests

# Prefer an env var so the key isn't hard-coded, but fall back to a literal for
# quick local runs. IMPORTANT: if this ends up as the placeholder, NopeCHA treats
# the request as the anonymous free tier and returns error 16 "Out of credit"
# even though your paid account has credit. Set NOPECHA_API_KEY, or paste your
# real key into the fallback below.
API_KEY = "sub_1TosCxCRwBwvt6ptg5RjmbIv"

BASE_URL = "https://api.nopecha.com/v1"
HEADERS = {
    "Authorization": f"Basic {API_KEY}",
    "Content-Type": "application/json",
}

DEFAULT_TIMEOUT = 180  # seconds to wait for a solve before giving up
POLL_INTERVAL = 3      # seconds between polls

# NopeCHA error codes.
ERROR_INCOMPLETE = 14   # job still being solved -> keep polling
ERROR_OUT_OF_CREDIT = 16

# Transient HTTP statuses worth retrying rather than failing the whole solve.
# 520-524 are Cloudflare "origin returned an unknown error / timed out".
TRANSIENT_STATUS = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
SUBMIT_MAX_RETRIES = 4
SUBMIT_RETRY_DELAY = 3  # seconds, grows linearly per attempt


class NopechaError(RuntimeError):
    """Raised when a captcha cannot be solved via NopeCHA."""


def _is_placeholder_key():
    return not API_KEY or API_KEY == "YOUR_NOPECHA_API_KEY"


def _submit(path, payload):
    """POST a job and return its job id, retrying transient upstream errors."""
    if _is_placeholder_key():
        raise NopechaError(
            "NopeCHA API key is not set (still the placeholder). Set the "
            "NOPECHA_API_KEY environment variable or edit API_KEY in nopecha.py."
        )

    last_exc = None
    for attempt in range(1, SUBMIT_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{BASE_URL}/{path}", headers=HEADERS, json=payload, timeout=30
            )
        except requests.RequestException as exc:
            last_exc = NopechaError(f"submit {path} -> network error: {exc}")
            time.sleep(SUBMIT_RETRY_DELAY * attempt)
            continue

        if resp.status_code in TRANSIENT_STATUS:
            last_exc = NopechaError(
                f"submit {path} -> HTTP {resp.status_code} (transient)"
            )
            time.sleep(SUBMIT_RETRY_DELAY * attempt)
            continue

        if resp.status_code >= 400:
            # Surface out-of-credit clearly; it's a config/account problem, not
            # something a retry will fix.
            try:
                err = resp.json().get("error")
            except ValueError:
                err = None
            if err == ERROR_OUT_OF_CREDIT:
                raise NopechaError(
                    "submit -> error 16 Out of credit. The request is using the "
                    "wrong/placeholder key (paid credit is on a different key), "
                    "or this key is expired. Run `python nopecha.py` to check the "
                    "balance of the key the script is actually sending."
                )
            raise NopechaError(f"submit {path} -> HTTP {resp.status_code}: {resp.text[:200]}")

        job_id = resp.json().get("data")
        if not job_id:
            raise NopechaError(f"submit {path} returned no job id: {resp.text[:200]}")
        return job_id

    raise last_exc or NopechaError(f"submit {path} failed after {SUBMIT_MAX_RETRIES} retries")


def _poll(path, job_id, timeout, interval):
    """Poll a job until the result is ready, the job fails, or time runs out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{BASE_URL}/{path}", headers=HEADERS, params={"id": job_id}, timeout=30
            )
        except requests.RequestException:
            time.sleep(interval)
            continue

        # 202/409 and transient upstream errors mean "not ready / try again".
        if resp.status_code in (202, 409) or resp.status_code in TRANSIENT_STATUS:
            time.sleep(interval)
            continue
        if resp.status_code >= 400:
            raise NopechaError(f"poll {path} -> HTTP {resp.status_code}: {resp.text[:200]}")

        body = resp.json()

        # An "error" in a 200 body: 14 means "still solving" (keep polling),
        # anything else is terminal -> raise now rather than blocking for the
        # whole timeout on an image NopeCHA can't read.
        error = body.get("error")
        if error is not None:
            if error == ERROR_INCOMPLETE:
                time.sleep(interval)
                continue
            raise NopechaError(
                f"poll {path} -> error {error}: {body.get('message', '')[:200]}"
            )

        data = body.get("data")
        if data in (None, "", []):
            time.sleep(interval)
            continue
        return data

    raise NopechaError(f"poll {path} timed out after {timeout}s (job {job_id})")


def _first_result(result):
    """Recognition results come back as a list; return the first item as text."""
    if isinstance(result, list):
        result = result[0] if result else ""
    return str(result).strip()


def solve_textcaptcha(image_data_url, timeout=DEFAULT_TIMEOUT, interval=POLL_INTERVAL):
    """Solve an image/text captcha.

    ``image_data_url`` must be a full data URL, e.g.
    ``"data:image/png;base64,iVBORw0..."``.
    Returns the recognised string (case may need normalising by the caller).
    """
    job_id = _submit("recognition/textcaptcha", {"image_data": [image_data_url]})
    result = _poll("recognition/textcaptcha", job_id, timeout, interval)
    return _first_result(result)


def solve_awscaptcha_audio(audio_data_url, timeout=DEFAULT_TIMEOUT, interval=POLL_INTERVAL):
    """Solve the audio-accessibility challenge of an AWS WAF CAPTCHA.

    ``audio_data_url`` must be a full data URL, e.g.
    ``"data:audio/mp3;base64,SUQzBAA..."``.
    Returns the recognised word/phrase spoken in the clip.
    """
    job_id = _submit("recognition/awscaptcha", {"audio_data": [audio_data_url]})
    result = _poll("recognition/awscaptcha", job_id, timeout, interval)
    return _first_result(result)


def solve_recaptcha_v2(
    sitekey,
    page_url,
    data=None,
    enterprise=False,
    proxy=None,
    timeout=DEFAULT_TIMEOUT,
    interval=POLL_INTERVAL,
):
    """Solve a Google reCAPTCHA v2 (checkbox / invisible) challenge.

    ``sitekey``    the public ``data-sitekey`` of the reCAPTCHA on the page.
    ``page_url``   URL of the page hosting the challenge.
    ``data``       optional dict of extra render options (e.g. ``{"s": "..."}``).
    ``enterprise`` True for reCAPTCHA Enterprise.
    ``proxy``      optional dict ``{"scheme","host","port","username","password"}``;
                   useful when the target rate-limits by IP.

    Returns the ``g-recaptcha-response`` token string, which the caller injects
    into the page's ``#g-recaptcha-response`` textarea before submitting.
    """
    payload = {"sitekey": sitekey, "url": page_url}
    if data:
        payload["data"] = data
    if enterprise:
        payload["enterprise"] = True
    if proxy:
        payload["proxy"] = proxy
    job_id = _submit("token/recaptcha2", payload)
    result = _poll("token/recaptcha2", job_id, timeout, interval)
    return _first_result(result)


def get_balance():
    """Return the status/balance dict for the key this module is sending.

    Useful to confirm the key is recognised. A healthy paid key returns
    ``credit`` in the thousands and ``status`` == "Active"; if you instead see a
    tiny credit count, the request is falling back to the free IP tier because
    the key isn't set correctly.
    """
    if _is_placeholder_key():
        raise NopechaError(
            "NopeCHA API key is not set (still the placeholder). Set the "
            "NOPECHA_API_KEY environment variable or edit API_KEY in nopecha.py."
        )
    resp = requests.get(f"{BASE_URL}/status", headers=HEADERS, timeout=30)
    if resp.status_code >= 400:
        raise NopechaError(f"status -> HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("data", resp.json())


if __name__ == "__main__":
    # Quick key/credit check: `python nopecha.py`
    masked = API_KEY[:4] + "..." + API_KEY[-4:] if len(API_KEY) > 8 else API_KEY
    print(f"Using API key: {masked}")
    try:
        print("Balance/status:", get_balance())
    except NopechaError as exc:
        print("Check failed:", exc)