"""Shared Webshare proxy pool for Playwright scrapers.

Reads proxies from a plain "ip:port:username:password" list (Webshare's
default export format) and hands them out to scrapers as Playwright
``proxy={"server": ..., "username": ..., "password": ...}`` dicts.

Rotation state persists to a small JSON file next to the proxy list so
consecutive script runs -- and different scrapers -- spread their load
across all configured proxies instead of hammering the same exit IP.
"""

import json
import os
import random

PROXY_FILE = "Webshare 10 proxies.txt"
ROTATION_STATE_FILE = "proxy_rotation_state.json"


def _base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def load_proxies(path: str = PROXY_FILE) -> list:
    full_path = path if os.path.isabs(path) else os.path.join(_base_dir(), path)
    proxies = []
    if not os.path.exists(full_path):
        return proxies
    with open(full_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) != 4:
                continue
            host, port, username, password = parts
            proxies.append({
                "server": f"http://{host}:{port}",
                "username": username,
                "password": password,
            })
    return proxies


def _rotation_path() -> str:
    return os.path.join(_base_dir(), ROTATION_STATE_FILE)


def _read_index() -> int:
    path = _rotation_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(json.load(fh).get("index", 0))
    except Exception:
        return 0


def _write_index(index: int):
    with open(_rotation_path(), "w", encoding="utf-8") as fh:
        json.dump({"index": index}, fh)


def next_proxy(proxies: list = None) -> dict:
    """Round-robin the next proxy, persisting the pointer across runs."""
    proxies = proxies if proxies is not None else load_proxies()
    if not proxies:
        return None
    index = _read_index() % len(proxies)
    _write_index((index + 1) % len(proxies))
    return proxies[index]


def random_proxy(proxies: list = None) -> dict:
    proxies = proxies if proxies is not None else load_proxies()
    if not proxies:
        return None
    return random.choice(proxies)
