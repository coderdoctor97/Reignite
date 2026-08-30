#!/usr/bin/env python3
"""
pull_latest_key.py
──────────────────
Fetch the most recent API key from the provider's dashboard
and update active_key.txt if it has changed.

This allows multiple machines to stay in sync without a shared folder.
Run periodically (e.g., via Task Scheduler) or from the Control Panel.
"""

import requests
import json
import os
import sys
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── Configuration ──────────────────────────────────────────────
BASE_URL = "https://opus.abhibots.com"
KEYS_URL = f"{BASE_URL}/dashboard/api/keys"
SESSION_COOKIE = "opus_session=mr616O4oW3bZDW1WYsprLtpujNKCw-oOzDHO-nJmP_Oe9VfK"  # Update if needed

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/dashboard?view=overview",
}

ACTIVE_KEY_FILE = "active_key.txt"


def get_cookies():
    return {"opus_session": SESSION_COOKIE.split("=", 1)[1]}


def fetch_latest_key():
    """Return the latest key string, or None if error."""
    try:
        resp = requests.get(KEYS_URL, headers=HEADERS, cookies=get_cookies(),
                            timeout=15, verify=False)
        if resp.status_code != 200:
            print(f"[!] Failed to fetch keys: {resp.status_code}")
            return None

        data = resp.json()
        if isinstance(data, dict):
            keys = data.get("keys", data.get("data", []))
        elif isinstance(data, list):
            keys = data
        else:
            keys = []

        if not keys:
            print("[!] No keys found in response.")
            return None

        # Keys are usually returned in creation order (newest last).
        # But we sort by 'createdAt' or 'id' to be safe.
        def sort_key(k):
            # Prefer 'createdAt' (ISO timestamp) or fallback to 'id'.
            created = k.get("createdAt")
            if created:
                try:
                    return datetime.fromisoformat(created.replace("Z", "+00:00"))
                except Exception:
                    pass
            return k.get("id", "")

        latest = max(keys, key=sort_key)
        key_value = latest.get("key") or latest.get("apiKey") or latest.get("token")
        if not key_value:
            print(f"[!] Key field not found in latest key: {latest}")
            return None

        return key_value.strip()

    except Exception as e:
        print(f"[!] Error fetching keys: {e}")
        return None


def update_active_key(new_key):
    """Write new_key to active_key.txt if different."""
    if not new_key:
        return

    # Read current key
    current = ""
    if os.path.exists(ACTIVE_KEY_FILE):
        with open(ACTIVE_KEY_FILE, "r") as f:
            current = f.read().strip()

    if current == new_key:
        print("[*] Key already up to date.")
        return

    # Write new key
    with open(ACTIVE_KEY_FILE, "w") as f:
        f.write(new_key)
    print(f"[+] Updated active_key.txt with new key: {new_key[:12]}…")


def main():
    print("=" * 50)
    print("  PULL LATEST KEY FROM PROVIDER")
    print("=" * 50)

    key = fetch_latest_key()
    if key:
        update_active_key(key)
    else:
        print("[FATAL] Could not retrieve latest key.")
        sys.exit(1)


if __name__ == "__main__":
    main()