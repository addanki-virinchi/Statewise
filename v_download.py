import csv
import re
import time
from pathlib import Path
from typing import Dict, List

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from playwright_stealth_utils import human_click, human_pause, new_stealth_browser


SEARCH_URL = (
    "https://secure.professionals.vermont.gov/prweb/PRServletCustom/app/"
    "NGLPGuestUser_/V9csDxL3sXkkjMC_FR2HrA*/!STANDARD"
    "?UserIdentifier=LicenseLookupGuestUser"
)

TARGET_PROFESSIONS = [
    # "Audiologists",
    # "Behavior Analysts",
    # "Hearing Aid Dispensers",
    # "Massage Therapy, Bodyworkers, and Touch Professionals",
    # "Naturopathic Physicians",
   # "Nursing",
    # "Nursing Home Administrators",
    # "Occupational Therapy",
    "Optometry",
    "Osteopathic Physicians & Surgeons",
    "Psychoanalysts",
    "Radiologic Technology",
    "Respiratory Care",
    "Speech-Language Pathologists",
]

DOWNLOAD_DIR = Path("downloads") / "vermont_rosters"
MANIFEST_CSV = DOWNLOAD_DIR / "vermont_roster_manifest.csv"


def normalize_text(value: str) -> str:
    return " ".join((value or "").split()).strip()


def slugify_filename(value: str) -> str:
    cleaned = normalize_text(value)
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned or "unknown"


def ensure_output_paths() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def ensure_manifest_header() -> None:
    if MANIFEST_CSV.exists() and MANIFEST_CSV.stat().st_size > 0:
        return
    with MANIFEST_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["profession", "profession_type", "file_path"],
        )
        writer.writeheader()


def load_existing_manifest() -> Dict[str, str]:
    existing: Dict[str, str] = {}
    if not MANIFEST_CSV.exists() or MANIFEST_CSV.stat().st_size == 0:
        return existing
    with MANIFEST_CSV.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            profession = normalize_text(row.get("profession", ""))
            profession_type = normalize_text(row.get("profession_type", ""))
            file_path = normalize_text(row.get("file_path", ""))
            if profession and profession_type and file_path:
                existing[f"{profession}||{profession_type}"] = file_path
    return existing


def append_manifest_row(profession: str, profession_type: str, file_path: Path) -> None:
    with MANIFEST_CSV.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["profession", "profession_type", "file_path"],
        )
        writer.writerow(
            {
                "profession": profession,
                "profession_type": profession_type,
                "file_path": str(file_path.resolve()),
            }
        )


def wait_for_page_idle(page, timeout_ms: int = 30000) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    try:
        # Pega keeps a background polling / session-keepalive request in
        # flight more or less continuously, so "networkidle" frequently
        # never fires even though the page is fully interactive. Treat it
        # as a best-effort, short, non-fatal wait -- real readiness is
        # confirmed downstream by polling for the specific element the
        # caller needs next (see visible_locator).
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
    except PlaywrightTimeoutError:
        pass


def visible_locator(page, selector: str, timeout_ms: int = 20000):
    """Poll for a visible element matching `selector` until timeout_ms elapses.

    This page re-renders sections via partial AJAX refreshes any time a
    dropdown changes, a checkbox is clicked, or a tab is switched. That
    means an element can be briefly absent or hidden immediately after such
    an action, even though it reliably shows up 100-2000ms later. A single
    instantaneous check is not sufficient -- this must actually retry.
    """
    deadline = time.time() + (timeout_ms / 1000)
    last_count = 0
    while True:
        locator = page.locator(selector)
        last_count = locator.count()
        for index in range(last_count):
            candidate = locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        if time.time() >= deadline:
            break
        page.wait_for_timeout(200)
    raise PlaywrightTimeoutError(
        f"No visible element found for selector: {selector} "
        f"(matched {last_count} element(s) in DOM, none visible, after {timeout_ms}ms)"
    )


def wait_for_roster_form(page, timeout_ms: int = 30000) -> None:
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        try:
            remaining_ms = max(int((deadline - time.time()) * 1000), 200)
            profession_dropdown = visible_locator(
                page,
                "select[name='$PpyDisplayHarness$pProductIds']",
                timeout_ms=min(remaining_ms, 1000),
            )
            if profession_dropdown.is_enabled():
                return
        except Exception:
            pass
        page.wait_for_timeout(200)
    raise PlaywrightTimeoutError("Roster form did not become ready in time.")


def open_roster_download(page) -> None:
    page.goto(SEARCH_URL, wait_until="domcontentloaded")
    wait_for_page_idle(page)

    # The "Profession Roster Download" tab exists as two buttons in the DOM:
    # the INACTIVE instance (class NGLP_Selection_Inactive) we must click to
    # switch into Roster Download mode, and the ACTIVE instance Pega shows once
    # we're already there. Only the inactive one carries a data-click payload
    # (LookupType=Roster+Download) -- the active twin has no data-click at all
    # -- so this selector uniquely targets the button to click, and its
    # absence means we're already in roster mode and must not re-click (that
    # would toggle us back out).
    toggle_selector = "button[data-click*='Roster+Download']"
    try:
        toggle = visible_locator(page, toggle_selector, timeout_ms=15000)
    except PlaywrightTimeoutError:
        toggle = None

    if toggle is not None:
        print("  clicking 'Profession Roster Download' toggle")
        if not human_click(page, toggle.element_handle()):
            toggle.click(timeout=5000)
        # NOTE: this is a tab-style toggle, not a button that disappears once
        # clicked -- Pega re-renders it in place with fresh dynamic IDs each
        # refresh, so waiting on state="hidden" always times out. Just let the
        # partial AJAX refresh settle instead.
        wait_for_page_idle(page)
    else:
        print("  roster download tab already active")

    wait_for_roster_form(page)


def select_dropdown_option_by_text(page, field_name: str, option_text: str) -> None:
    dropdown = visible_locator(page, f"select[name='{field_name}']")

    options = dropdown.locator("option").all()
    target_label = None
    for option in options:
        label = normalize_text(option.inner_text())
        if label.casefold() == option_text.casefold():
            target_label = label
            break

    if not target_label:
        raise ValueError(f"Dropdown option not found for {field_name}: {option_text}")

    dropdown.select_option(label=target_label)
    human_pause(0.3, 0.8)
    try:
        wait_for_page_idle(page, timeout_ms=15000)
    except PlaywrightTimeoutError:
        pass


def get_profession_type_labels(page, timeout_ms: int = 20000) -> List[str]:
    # The profession-type dropdown is populated by a partial AJAX refresh that
    # fires after a profession is chosen, so it is frequently still empty (or
    # holding only its "Select Profession Type" placeholder) for the first
    # several hundred ms. Poll until real options show up rather than reading
    # it once and giving up.
    deadline = time.time() + (timeout_ms / 1000)
    while True:
        try:
            dropdown = visible_locator(
                page,
                "select[name='$PpyDisplayHarness$pSetProfessionType']",
                timeout_ms=2000,
            )
            labels: List[str] = []
            for option in dropdown.locator("option").all():
                label = normalize_text(option.inner_text())
                if not label or label.upper() == "SELECT PROFESSION TYPE":
                    continue
                labels.append(label)
            if labels:
                return labels
        except Exception:
            pass
        if time.time() >= deadline:
            return []
        page.wait_for_timeout(300)


def ensure_active_checked(page) -> None:
    active_checkbox = visible_locator(
        page,
        "input[name='$PpyDisplayHarness$pEligibleStatusforRoster$l1$pSelectStatus']"
    )
    if not active_checkbox.is_checked():
        active_checkbox.check(timeout=5000)
        human_pause(0.2, 0.6)


def wait_for_download_popup_close(popup, timeout_ms: int = 30000) -> None:
    try:
        popup.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return

    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        if popup.is_closed():
            return
        try:
            popup.wait_for_timeout(500)
        except Exception:
            return


def trigger_download(page):
    # The actual roster download control has a name starting with
    # "NGLPRosterDownload" and is distinct from the "Profession Roster
    # Download" tab toggle -- matching by role/name "Download" hits both and
    # raises a strict-mode violation. Target it by its stable name prefix and
    # wait until Pega enables it (it stays disabled until a profession type
    # and the Active status are selected).
    selector = "button[name^='NGLPRosterDownload']"
    deadline = time.time() + 30
    while True:
        button = visible_locator(page, selector, timeout_ms=2000)
        if button.is_enabled():
            break
        if time.time() >= deadline:
            raise PlaywrightTimeoutError(
                "Roster download button did not become enabled in time."
            )
        page.wait_for_timeout(300)

    popup = None
    with page.expect_download(timeout=45000) as download_info:
        try:
            # Most rosters open a short-lived popup window to serve the file;
            # some download inline. Treat the popup as best-effort so an
            # inline download doesn't fail on a missing popup.
            with page.expect_popup(timeout=5000) as popup_info:
                if not human_click(page, button.element_handle()):
                    button.click(timeout=5000)
            popup = popup_info.value
        except PlaywrightTimeoutError:
            pass

    download = download_info.value
    if popup is not None:
        wait_for_download_popup_close(popup, timeout_ms=10000)
    return download


def download_roster_for_type(page, profession: str, profession_type: str) -> Path:
    select_dropdown_option_by_text(
        page,
        "$PpyDisplayHarness$pSetProfessionType",
        profession_type,
    )
    ensure_active_checked(page)
    download = trigger_download(page)

    suggested_name = download.suggested_filename
    suffix = Path(suggested_name).suffix or ".csv"
    target_path = DOWNLOAD_DIR / f"{slugify_filename(profession_type)}{suffix}"
    download.save_as(str(target_path))
    return target_path


def process_profession(page, profession: str, manifest_index: Dict[str, str]) -> None:
    print(f"[PROFESSION] {profession}")
    open_roster_download(page)

    print(f"  selecting profession: {profession}")
    select_dropdown_option_by_text(page, "$PpyDisplayHarness$pProductIds", profession)

    print("  looking for profession types")
    profession_types = get_profession_type_labels(page)
    if not profession_types:
        print("  no profession types found")
        return
    print(f"  found {len(profession_types)} profession type(s): {profession_types}")

    for profession_type in profession_types:
        manifest_key = f"{profession}||{profession_type}"
        existing_path = manifest_index.get(manifest_key)
        if existing_path and Path(existing_path).exists():
            print(f"  skipping existing download: {profession_type}")
            continue

        print(f"  downloading: {profession_type}")
        target_path = download_roster_for_type(page, profession, profession_type)
        append_manifest_row(profession, profession_type, target_path)
        manifest_index[manifest_key] = str(target_path.resolve())
        print(f"    saved to {target_path}")


def run() -> None:
    ensure_output_paths()
    ensure_manifest_header()
    manifest_index = load_existing_manifest()

    with sync_playwright() as playwright:
        browser, context, page = new_stealth_browser(playwright, headless=False, channel="chrome")
        try:
            for profession in TARGET_PROFESSIONS:
                process_profession(page, profession, manifest_index)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    run()