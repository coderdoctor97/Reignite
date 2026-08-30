"""
rotate_now.py – Manual key rotation for Opus.
Deletes all keys, creates one new key, updates active_key.txt.
The Gateway and KeyBinder will pick it up automatically.
Opus Rotate-Now – v5.0.0
"""

import requests
import json
import os
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===================== CONFIG =====================
BASE_URL = "https://opus.abhibots.com"
KEYS_URL = f"{BASE_URL}/dashboard/api/keys"
DELETE_URL = lambda key_id: f"{BASE_URL}/dashboard/api/keys/{key_id}"
SESSION_COOKIE = "opus_session=mr616O4oW3bZDW1WYsprLtpujNKCw-oOzDHO-nJmP_Oe9VfK"
ACTIVE_KEY_FILE = "active_key.txt"   # same file the gateway watches
DAILY_LIMIT = 300000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": "https://opus.abhibots.com",
    "Referer": "https://opus.abhibots.com/dashboard?view=overview",
    "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Brave";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-gpc": "1",
}

def get_cookies():
    return {"opus_session": SESSION_COOKIE.split("=", 1)[1]}

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
        print(f"[!] List error: {e}")
    return []

def delete_key(key_id):
    url = DELETE_URL(key_id)
    try:
        r = requests.delete(url, headers=HEADERS, cookies=get_cookies(), timeout=15, verify=False)
        print(f"  Deleted key {key_id}: {r.status_code}")
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"[!] Delete error: {e}")
        return False

def delete_all_keys():
    keys = list_keys()
    if not keys:
        print("[*] No keys to delete.")
        return True
    success = True
    for k in keys:
        kid = k.get("id")
        if kid and not delete_key(kid):
            success = False
    return success

def create_key(name="ManualRotate"):
    body = {"name": name, "dailyTokenLimit": DAILY_LIMIT}
    try:
        r = requests.post(KEYS_URL, headers=HEADERS, json=body, cookies=get_cookies(), timeout=15, verify=False)
        print(f"[*] CREATE key -> {r.status_code}")
        if r.status_code in (200, 201):
            data = r.json()
            new_key = data.get("key") or data.get("apiKey") or data.get("token")
            if not new_key and isinstance(data, dict):
                new_key = data.get("data", {}).get("key") or data.get("data", {}).get("apiKey")
            if new_key:
                print(f"[+] New key: {new_key}")
                return new_key
            else:
                print(f"[-] Key field not found in response: {data}")
        elif r.status_code == 400 and "Not enough tokens" in r.text:
            print("[!] Quota full. Deleting all keys first...")
            if delete_all_keys():
                return create_key(name)  # retry
        else:
            print(f"[-] Creation failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[!] Create error: {e}")
    return None

def main():
    print("=" * 40)
    print("  MANUAL KEY ROTATION")
    print("=" * 40)

    # 1. Delete existing keys to free quota
    print("[*] Deleting old keys...")
    delete_all_keys()

    # 2. Create new key
    print("[*] Creating new key...")
    new_key = create_key()
    if not new_key:
        print("[FATAL] Could not create new key.")
        return

    # 3. Write to active_key.txt
    with open(ACTIVE_KEY_FILE, "w") as f:
        f.write(new_key)
    print("[OK] active_key.txt updated with new key.")   # <--- Changed from ✓ to [OK]

    print("\n[OK] Rotation complete. The Gateway will pick up the new key in a few seconds.")
    print("    KeyBinder will see the updated token_usage.json on its next check.")

if __name__ == "__main__":
    main()