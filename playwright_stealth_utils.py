"""Shared Playwright launch + human-behavior helpers for the state scrapers.

Centralizes the stealth setup (real Chrome channel, randomized fingerprint,
navigator/WebGL patches) and the human-pacing primitives (mouse movement,
typing cadence, scroll jitter) so each scraper only has to describe its own
page flow.
"""

import random
import time

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    (1920, 1080), (1536, 864), (1440, 900), (1600, 900), (1366, 768),
]

TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
]

# Classic navigator/WebGL fingerprint patches. Runs before any page script via
# context.add_init_script, so it applies on every navigation automatically.
STEALTH_JS = r"""
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch (e) {}
  try {
    window.chrome = window.chrome || { runtime: {} };
  } catch (e) {}
  try {
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
    );
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
  } catch (e) {}
  try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (parameter) {
      if (parameter === 37445) return 'Intel Inc.';
      if (parameter === 37446) return 'Intel Iris OpenGL Engine';
      return getParameter.call(this, parameter);
    };
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
  } catch (e) {}
})();
"""

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-notifications",
    "--disable-popup-blocking",
]


def new_stealth_browser(playwright, proxy=None, headless=False, channel="chrome"):
    """Launch a browser + context with a randomized, stealth-patched fingerprint.

    ``channel="chrome"`` drives the real, installed Chrome (closer to
    undetected-chromedriver's behavior than bundled Chromium); falls back to
    bundled Chromium automatically if Chrome isn't installed.
    """
    ua = random.choice(USER_AGENTS)
    width, height = random.choice(VIEWPORTS)
    launch_kwargs = {"headless": headless, "args": list(LAUNCH_ARGS)}
    if proxy:
        launch_kwargs["proxy"] = proxy
    if channel:
        launch_kwargs["channel"] = channel
    try:
        browser = playwright.chromium.launch(**launch_kwargs)
    except Exception:
        launch_kwargs.pop("channel", None)
        browser = playwright.chromium.launch(**launch_kwargs)

    context = browser.new_context(
        user_agent=ua,
        viewport={"width": width, "height": height},
        locale="en-US",
        timezone_id=random.choice(TIMEZONES),
        color_scheme="light",
    )
    context.add_init_script(STEALTH_JS)
    page = context.new_page()
    page.set_default_timeout(30000)
    return browser, context, page


# ─── HUMAN PACING PRIMITIVES ──────────────────────────────────────────────────

def human_pause(min_delay=0.25, max_delay=0.9):
    time.sleep(random.uniform(min_delay, max_delay))


def human_mouse_move(page, x, y, steps=None):
    steps = steps or random.randint(15, 35)
    try:
        page.mouse.move(x + random.uniform(-2, 2), y + random.uniform(-2, 2), steps=steps)
    except Exception:
        pass


def human_scroll(page, min_px=-120, max_px=240):
    try:
        page.mouse.wheel(0, random.randint(min_px, max_px))
    except Exception:
        pass
    human_pause(0.1, 0.4)


def human_type(page, text: str, min_delay=30, max_delay=140):
    """Types into whatever element currently has focus, one key at a time."""
    for ch in text:
        page.keyboard.type(ch, delay=random.randint(min_delay, max_delay))
        if random.random() < 0.04:
            time.sleep(random.uniform(0.12, 0.35))


def human_click(page, element_handle, offset_x=None, offset_y=None) -> bool:
    """Move the mouse to the element like a person, then click it."""
    if element_handle is None:
        return False
    try:
        element_handle.scroll_into_view_if_needed()
    except Exception:
        pass
    human_pause(0.15, 0.5)
    try:
        box = element_handle.bounding_box()
        if box:
            x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
            y = box["y"] + box["height"] / 2 + random.uniform(-2, 2)
            human_mouse_move(page, x, y)
            human_pause(0.05, 0.2)
    except Exception:
        pass
    try:
        element_handle.click(timeout=5000)
        human_pause()
        return True
    except Exception:
        pass
    try:
        page.evaluate("(el) => el.click()", element_handle)
        human_pause()
        return True
    except Exception:
        pass
    try:
        page.evaluate(_SYNTHETIC_CLICK_JS, element_handle)
        human_pause()
        return True
    except Exception:
        return False


_SYNTHETIC_CLICK_JS = r"""
(el) => {
  const r = el.getBoundingClientRect();
  const base = { bubbles: true, cancelable: true, view: window,
                 clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0 };
  function fire(type) {
    const E = type.indexOf('pointer') === 0 ? window.PointerEvent : window.MouseEvent;
    try { el.dispatchEvent(new E(type, base)); } catch (e) { el.dispatchEvent(new MouseEvent(type, base)); }
  }
  try { el.focus && el.focus(); } catch (e) {}
  ['pointerover', 'pointerenter', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(fire);
}
"""
