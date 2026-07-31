"""Thin client for the NopeCHA captcha-solving API.

Docs: https://nopecha.com/api-reference/

Two capabilities are used by the Colorado scraper:

  * ``solve_textcaptcha``  -> image captcha (digits / letters), e.g. FormShield.
  * ``solve_hcaptcha``     -> hCaptcha token, e.g. the AWS WAF "Begin" challenge
                              when it is backed by hCaptcha.

Every job is a two-step flow: POST returns a job id, then GET is polled until
the result is ready (the API returns HTTP 409 "Incomplete job" while working).
"""

import time

import requests

# NopeCHA subscription key. Sent as HTTP Basic auth per the API reference.
API_KEY = "sub_1TsrPgCRwBwvt6ptOaJXxtoB"

BASE_URL = "https://api.nopecha.com/v1"
HEADERS = {
    "Authorization": f"Basic {API_KEY}",
    "Content-Type": "application/json",
}

DEFAULT_TIMEOUT = 180  # seconds to wait for a solve before giving up
POLL_INTERVAL = 1      # seconds between polls

# One pooled connection for every call: TLS handshakes to api.nopecha.com cost
# more than the polling interval itself when a job finishes quickly.
SESSION = requests.Session()


class NopechaError(RuntimeError):
    """Raised when a captcha cannot be solved via NopeCHA."""


def _submit(path, payload):
    """POST a job and return its job id."""
    resp = SESSION.post(f"{BASE_URL}/{path}", headers=HEADERS, json=payload, timeout=30)
    if resp.status_code >= 400:
        raise NopechaError(f"submit {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
    job_id = resp.json().get("data")
    if not job_id:
        raise NopechaError(f"submit {path} returned no job id: {resp.text[:200]}")
    return job_id


def _poll(path, job_id, timeout, interval):
    """Poll a job until the result is ready or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = SESSION.get(
            f"{BASE_URL}/{path}", headers=HEADERS, params={"id": job_id}, timeout=30
        )
        # 409 (and sometimes 202) means the job is still being worked on.
        if resp.status_code in (202, 409):
            time.sleep(interval)
            continue
        if resp.status_code >= 400:
            raise NopechaError(f"poll {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json().get("data")
        if data in (None, "", []):
            time.sleep(interval)
            continue
        return data
    raise NopechaError(f"poll {path} timed out after {timeout}s (job {job_id})")


def solve_textcaptcha(image_data_url, timeout=DEFAULT_TIMEOUT, interval=POLL_INTERVAL):
    """Solve an image/text captcha.

    ``image_data_url`` must be a full data URL, e.g.
    ``"data:image/png;base64,iVBORw0..."``.
    Returns the recognised string (case may need normalising by the caller).
    """
    job_id = _submit("recognition/textcaptcha", {"image_data": [image_data_url]})
    result = _poll("recognition/textcaptcha", job_id, timeout, interval)
    if isinstance(result, list):
        result = result[0] if result else ""
    return str(result).strip()


def solve_awscaptcha_audio(audio_data_url, timeout=DEFAULT_TIMEOUT, interval=POLL_INTERVAL):
    """Solve the audio-accessibility challenge of an AWS WAF CAPTCHA.

    ``audio_data_url`` must be a full data URL, e.g.
    ``"data:audio/mp3;base64,SUQzBAA..."``.
    Returns the recognised word/phrase spoken in the clip.
    """
    job_id = _submit("recognition/awscaptcha", {"audio_data": [audio_data_url]})
    result = _poll("recognition/awscaptcha", job_id, timeout, interval)
    if isinstance(result, list):
        result = result[0] if result else ""
    return str(result).strip()


def solve_audiocaptcha(audio_data_url, timeout=DEFAULT_TIMEOUT, interval=POLL_INTERVAL):
    """Solve a spoken audio captcha clip through NopeCHA.

    This is a thin alias over the same audio transcription endpoint used for
    AWS WAF audio challenges. Keeping the generic name makes the caller code
    easier to read when the source page is not AWS-specific.
    """
    return solve_awscaptcha_audio(audio_data_url, timeout=timeout, interval=interval)


def solve_recaptcha_audio(audio_data_url, timeout=DEFAULT_TIMEOUT, interval=POLL_INTERVAL):
    """Solve a Google reCAPTCHA audio challenge clip through NopeCHA."""
    return solve_audiocaptcha(audio_data_url, timeout=timeout, interval=interval)


def solve_recaptcha_v2(
    sitekey,
    page_url,
    data=None,
    proxy=None,
    enterprise=None,
    useragent=None,
    cookie=None,
    timeout=DEFAULT_TIMEOUT,
    interval=POLL_INTERVAL,
):
    """Solve a Google reCAPTCHA v2 (checkbox / invisible) challenge.

    ``sitekey``   is the public ``data-sitekey`` of the reCAPTCHA on the page.
    ``page_url``  is the URL of the page hosting the challenge.
    ``data``      optional dict of metadata, e.g. ``{"enterprise": True}`` or
                  ``{"s": "..."}`` for the sometimes-required datas token.
    ``proxy``     optional dict ``{"scheme","host","port","username","password"}``
                  to solve through; useful when the target rate-limits by IP.
    ``enterprise`` optional bool for reCAPTCHA Enterprise widgets.
    ``useragent`` optional browser user-agent string for the solver context.
    ``cookie``    optional list of browser cookies in the API's expected shape.

    Returns the ``g-recaptcha-response`` token string, which the caller injects
    into the page's ``#g-recaptcha-response`` textarea before submitting.
    """
    payload = {"sitekey": sitekey, "url": page_url}
    if data:
        payload["data"] = data
    if proxy:
        payload["proxy"] = proxy
    if enterprise is not None:
        payload["enterprise"] = bool(enterprise)
    if useragent:
        payload["useragent"] = useragent
    if cookie:
        payload["cookie"] = cookie
    job_id = _submit("token/recaptcha2", payload)
    result = _poll("token/recaptcha2", job_id, timeout, interval)
    if isinstance(result, list):
        result = result[0] if result else ""
    return str(result).strip()


def solve_recaptcha_v3(
    sitekey,
    page_url,
    action=None,
    enterprise=False,
    proxy=None,
    timeout=DEFAULT_TIMEOUT,
    interval=POLL_INTERVAL,
):
    """Solve a Google reCAPTCHA v3 (invisible, score-based) challenge.

    ``sitekey``    the public sitekey, e.g. the ``render=`` value of the page's
                   ``recaptcha/api.js`` script tag.
    ``page_url``   URL of the page hosting the challenge.
    ``action``     the ``action`` string the page passes to
                   ``grecaptcha.execute(sitekey, {action: ...})``. Sending the
                   matching action is what lets the solved token clear the
                   server's per-action score check.
    ``enterprise`` True for reCAPTCHA Enterprise.
    ``proxy``      optional dict ``{"scheme","host","port","username","password"}``.
                   v3 assigns a *score* to the token based on the reputation of
                   the IP/environment that mints it; a datacenter IP scores too
                   low to pass. Minting through a residential proxy raises the
                   score enough to clear the server's threshold.

    v3 has no checkbox: the page requests a token programmatically. The token an
    automated browser generates scores poorly and is rejected, so we ask NopeCHA
    for a higher-reputation token and inject that into the form's hidden response
    field instead. Returns the token string.
    """
    payload = {"sitekey": sitekey, "url": page_url}
    data = {}
    if action:
        data["action"] = action
    if enterprise:
        data["enterprise"] = True
    if data:
        payload["data"] = data
    if proxy:
        payload["proxy"] = proxy
    job_id = _submit("token/recaptcha3", payload)
    result = _poll("token/recaptcha3", job_id, timeout, interval)
    if isinstance(result, list):
        result = result[0] if result else ""
    return str(result).strip()
