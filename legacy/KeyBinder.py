"""
KeyBinder – Syncs with Gateway via token_usage.json
Watches usage, auto-rotates keys when threshold is reached.
The launcher UI handles user confirmation/rotation — KeyBinder just keeps
the on-disk key fresh in the background and writes a signal when rotation
is needed (the popup code was removed because it broke the launcher's Tk root).

Communication with the launcher:
- Writes token_usage_summary.json (latest totals) for the UI to consume fast.
- Writes rotation_needed.flag when the threshold is hit; the launcher picks
  it up and presents the "Rotate Now" button highlighted.

Author: Shadow Hacker
Opus KeyBinder – v5.0.0
"""

import requests
import json
import time
import os
import sys
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===================== CONFIGURATION =====================
BASE_URL = "https://opus.abhibots.com"
KEYS_URL = f"{BASE_URL}/dashboard/api/keys"
DELETE_URL = lambda key_id: f"{BASE_URL}/dashboard/api/keys/{key_id}"

SESSION_COOKIE = "opus_session=mr616O4oW3bZDW1WYsprLtpujNKCw-oOzDHO-nJmP_Oe9VfK"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": "https://opus.abhibots.com",
    "Referer": "https://opus.abhibots.com/dashboard?view=overview",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Brave";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-gpc": "1",
    "Accept-Language": "en-US,en;q=0.7",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Priority": "u=1, i",
}

CHECK_INTERVAL = 10            # Check gateway's usage file every 10 seconds
THRESHOLD_TOKENS_USED = 2_700_00
ACTIVE_KEY_FILE = "active_key.txt"
USAGE_FILE = "token_usage.json"
ROTATION_FLAG = "rotation_needed.flag"
SUMMARY_FILE = "token_usage_summary.json"


def get_cookies():
    return {"opus_session": SESSION_COOKIE.split("=", 1)[1]}


def read_gateway_usage():
    """Read live token usage from the Gateway's token_usage.json file."""
    try:
        if os.path.exists(USAGE_FILE):
            with open(USAGE_FILE, "r") as f:
                data = json.load(f)
                total = data.get("total", 0)
                remaining = data.get("remaining", 1_500_000)
                return total, remaining
    except Exception:
        pass
    return 0, 1_500_000


def write_rotation_flag(needed, total, remaining):
    """Write a small JSON file the launcher polls to surface a 'Rotate Now' CTA."""
    try:
        with open(ROTATION_FLAG, "w") as f:
            json.dump({
                "needed": bool(needed),
                "total": total,
                "remaining": remaining,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }, f)
    except Exception as e:
        print(f"[!] write_rotation_flag error: {e}")


def list_keys():
    try:
        r = requests.get(KEYS_URL, headers=HEADERS, cookies=get_cookies(), timeout=15, verify=False)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return data.get("keys", data.get("data", []))
        else:
            print(f"[!] List keys failed: {r.status_code}")
    except Exception as e:
        print(f"[!] Error listing keys: {e}")
    return []


def delete_all_keys():
    keys = list_keys()
    if not keys:
        print("[*] No keys to delete.")
        return True
    for k in keys:
        kid = k.get("id")
        if kid:
            url = DELETE_URL(kid)
            try:
                r = requests.delete(url, headers=HEADERS, cookies=get_cookies(), timeout=15, verify=False)
                print(f"[*] Deleted key {kid} -> {r.status_code}")
            except Exception as e:
                print(f"[!] Failed to delete key {kid}: {e}")
                return False
    return True


def create_key(name="AutoRotate", daily_limit=1500000):
    body = {"name": name, "dailyTokenLimit": daily_limit}
    try:
        r = requests.post(KEYS_URL, headers=HEADERS, json=body, cookies=get_cookies(), timeout=15, verify=False)
        print(f"[*] CREATE key -> {r.status_code}")
        if r.status_code in (200, 201):
            data = r.json()
            new_key = data.get("key") or data.get("apiKey") or data.get("token")
            if not new_key and isinstance(data, dict):
                new_key = data.get("data", {}).get("key") or data.get("data", {}).get("apiKey")
            if new_key:
                print(f"[+] New key created: {new_key[:15]}...")
                return new_key
            else:
                print(f"[-] Key field not found: {data}")
        elif r.status_code == 400 and "Not enough tokens" in r.text:
            print("[!] Quota full. Deleting all existing keys to free space...")
            if delete_all_keys():
                print("[*] Retrying key creation...")
                return create_key(name, daily_limit)
            else:
                print("[FATAL] Could not free quota.")
                return None
        else:
            print(f"[-] Creation failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[!] Create error: {e}")
    return None


def delete_key_by_string(api_key):
    """Find the key's numeric ID by matching the key string, then delete it."""
    keys = list_keys()
    for k in keys:
        stored = k.get("key") or k.get("apiKey")
        if stored == api_key:
            kid = k.get("id")
            if kid:
                url = DELETE_URL(kid)
                try:
                    r = requests.delete(url, headers=HEADERS, cookies=get_cookies(), timeout=15, verify=False)
                    print(f"[*] DELETE key {kid} -> {r.status_code}")
                    return r.status_code in (200, 204)
                except Exception as e:
                    print(f"[!] Delete error: {e}")
                    return False
    print("[!] Could not find key to delete on dashboard.")
    return False


def clear_rotation_flag():
    try:
        if os.path.exists(ROTATION_FLAG):
            os.remove(ROTATION_FLAG)
    except Exception:
        pass


def main():
    print("=" * 55)
    print("  KEYBINDER – Synced with Gateway (no popup mode)")
    print(f"  Threshold: {THRESHOLD_TOKENS_USED:,} tokens used")
    print(f"  Usage file: {USAGE_FILE}")
    print(f"  Check: every {CHECK_INTERVAL}s")
    print("=" * 55)

    current_key = None
    if os.path.exists(ACTIVE_KEY_FILE):
        with open(ACTIVE_KEY_FILE, "r") as f:
            current_key = f.read().strip()

    if current_key:
        print(f"[*] Loaded existing key: {current_key[:15]}...")
    else:
        print("[*] No key found. Creating initial key...")
        current_key = create_key()
        if current_key:
            with open(ACTIVE_KEY_FILE, "w") as f:
                f.write(current_key)
            print(f"[+] Initial key saved.")
        else:
            print("[FATAL] Could not create initial key. Check session cookie.")
            sys.exit(1)

    print("[✓] KeyBinder running. Watching gateway usage...\n")

    already_warned = False  # prevent flag spam

    while True:
        try:
            total_used, remaining = read_gateway_usage()
            pct = (total_used / 2_700_00) * 100
            print(f"[*] Gateway reports: {total_used:,} used | {remaining:,} remaining ({pct:.1f}%)")

            if total_used >= THRESHOLD_TOKENS_USED and not already_warned:
                already_warned = True
                print(f"[!] Threshold reached! Writing rotation flag for launcher...")
                write_rotation_flag(True, total_used, remaining)

        except Exception as e:
            print(f"[!] Loop error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
