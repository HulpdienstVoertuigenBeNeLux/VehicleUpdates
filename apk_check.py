import requests
import json
from datetime import datetime
import time
import os
import shutil

REPORTS_DIR = "reports"
REPORT_FILENAME = "apk_expiry_report.txt"
REPORT_FILE = os.path.join(REPORTS_DIR, REPORT_FILENAME)
RAW_DIR = "raw"
RAW_NL_FILE = os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_raw.json")
STORAGE_DIR = "storage"
KENTEKEN_STATUS_FILENAME = "apk_kenteken_status.json"
KENTEKEN_STATUS_FILE = os.path.join(STORAGE_DIR, KENTEKEN_STATUS_FILENAME)


def ensure_report_path():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    root_path = REPORT_FILENAME
    if os.path.exists(root_path) and not os.path.exists(REPORT_FILE):
        shutil.move(root_path, REPORT_FILE)


def ensure_raw_input_path():
    os.makedirs(RAW_DIR, exist_ok=True)
    root_path = "hulpdienstvoertuigenbenelux_raw.json"
    if os.path.exists(root_path) and not os.path.exists(RAW_NL_FILE):
        shutil.move(root_path, RAW_NL_FILE)


def ensure_status_path():
    os.makedirs(STORAGE_DIR, exist_ok=True)
    root_path = KENTEKEN_STATUS_FILENAME
    if os.path.exists(root_path) and not os.path.exists(KENTEKEN_STATUS_FILE):
        shutil.move(root_path, KENTEKEN_STATUS_FILE)

# 1. Collect all kentekens from the relevant JSON files
def collect_kentekens_with_roepnummer():
    kenteken_map = {}
    ensure_raw_input_path()
    # Use NL raw file for all kentekens
    with open(RAW_NL_FILE, encoding='utf-8') as f:
        data = json.load(f)
        for entry in data:
            kenteken = entry.get('Kenteken', '').strip()
            roep = entry.get('Roepnummer', '').strip()
            # Only add valid kentekens (not empty, not GEEN/ONBEKEND/-/header)
            if kenteken and kenteken.upper() not in ['GEEN', 'ONBEKEND', '-', 'KENTEKEN']:
                kenteken_map.setdefault(kenteken, set()).add(roep)
    # Convert sets to sorted lists and remove None/empty
    kenteken_map = {k: sorted([r for r in v if r]) for k, v in kenteken_map.items()}
    return kenteken_map

# 2. Load or initialize kenteken status file
def load_kenteken_status(kenteken_map):
    if os.path.exists(KENTEKEN_STATUS_FILE):
        with open(KENTEKEN_STATUS_FILE, encoding='utf-8') as f:
            status = json.load(f)
    else:
        status = {}
    # Remove kentekens that are no longer in the lists
    to_remove = [k for k in status if k not in kenteken_map]
    for k in to_remove:
        del status[k]
    # Ensure all kentekens are present and update roepnummers
    for k, roepnummers in kenteken_map.items():
        if k not in status:
            status[k] = {"expiry": None, "checked": False, "unknown": True, "roepnummers": roepnummers, "last_check_date": None}
        else:
            # Always update roepnummers for completeness
            status[k]["roepnummers"] = roepnummers
            if "last_check_date" not in status[k]:
                status[k]["last_check_date"] = None
    return status

def save_kenteken_status(status):
    with open(KENTEKEN_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)

# 3. Collect APK info from RDW Open Data API
def get_apk_info(kenteken):
    url = f'https://opendata.rdw.nl/resource/m9d7-ebf2.json?kenteken={kenteken.replace("-", "").upper()}'
    retryable_status_codes = {408, 425, 429, 500, 502, 503, 504}
    max_attempts = 4
    retry_delay_seconds = 30

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, timeout=10)
            if response.status_code in retryable_status_codes and attempt < max_attempts:
                print(
                    f"RDW API gaf status {response.status_code} voor {kenteken}. "
                    f"Retry in {retry_delay_seconds}s (attempt {attempt}/{max_attempts})..."
                )
                time.sleep(retry_delay_seconds)
                continue

            if response.status_code == 200:
                data = response.json()
                if data:
                    return data[0]  # Return first result
            return None
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt >= max_attempts:
                print(f"Fout bij ophalen APK info voor {kenteken}: {e}")
                return None
            print(
                f"Netwerk/time-out fout voor {kenteken}: {e}. "
                f"Retry in {retry_delay_seconds}s (attempt {attempt}/{max_attempts})..."
            )
            time.sleep(retry_delay_seconds)
        except requests.RequestException as e:
            print(f"Fout bij ophalen APK info voor {kenteken}: {e}")
            return None
    return None

# 4. Check if APK is valid
def is_apk_valid(apk_info):
    vervaldatum = apk_info.get('vervaldatum_apk')
    if vervaldatum:
        expiry = datetime.strptime(vervaldatum, '%Y%m%d').date()
        return expiry >= datetime.today().date(), expiry
    return False, None

# 4. Webhook notification
def webhook_APK(message):
    color = "16711680"
    if "verlopen" in message:
        color = "16711680"
    elif "verlengt" in message:
        color = "8388352"

    webhook_url = os.getenv("DISCORD_APK")
    embed = {
        "title": "RDW APK Check",
        "description": message,
        "color": color,
    }
    webhookdata = {
        "username": "HulpdienstVoertuigenBeNeLux APK",
        "embeds": [
            embed
        ],
    }

    headers = {
        "Content-Type": "application/json"
    }

    result = requests.post(webhook_url, json=webhookdata, headers=headers)
    if 200 <= result.status_code < 300:
        print(f"Webhook sent {result.status_code}")
    else:
        print(f"Not sent with {result.status_code}, response:\n{result.json()}")

# 6. Main script
def main():
    ensure_report_path()
    ensure_status_path()
    kenteken_map = collect_kentekens_with_roepnummer()
    status = load_kenteken_status(kenteken_map)
    # Prioritize unknown, then expired, then others
    today = datetime.today().date()
    week_ago = today.fromordinal(today.toordinal() - 7)
    # Check all unknown kentekens (never checked or not checked in 7+ days)
    unknowns = [k for k, v in status.items() if v["unknown"] and (not v.get("last_check_date") or datetime.strptime(v["last_check_date"], "%Y-%m-%d").date() <= week_ago)]
    # Check all expired kentekens
    expired = [
        k for k, v in status.items()
        if v["expiry"] and v["expiry"] not in [None, "None", "null", ""]
        and datetime.strptime(v["expiry"], "%Y-%m-%d").date() < today
        and (
            not v.get("last_check_date")
            or datetime.strptime(v["last_check_date"], "%Y-%m-%d").date() < today
        )
    ]
    # Combine and deduplicate
    to_check = list(dict.fromkeys(unknowns + expired))
    # Limit to max 1000 per run
    if len(to_check) > 1000:
        print(f"Let op: er zijn {len(to_check)} kentekens om te checken, maar maximaal 1000 worden nu verwerkt.")
        to_check = to_check[:1000]
    output_lines = []
    print(f"Start batch check van {len(to_check)} kentekens...")
    for idx, kenteken in enumerate(to_check, 1):
        print(f"[{idx}/{len(to_check)}] Check {kenteken}...")
        apk_info = get_apk_info(kenteken)
        status[kenteken]["last_check_date"] = today.strftime("%Y-%m-%d")
        if not apk_info:
            print(f"  Geen APK info gevonden voor {kenteken}")
            status[kenteken]["expiry"] = None
            status[kenteken]["checked"] = True
            status[kenteken]["unknown"] = True
            continue
        valid, expiry = is_apk_valid(apk_info)
        previous_expiry = status[kenteken].get("expiry")
        status[kenteken]["expiry"] = str(expiry)
        status[kenteken]["checked"] = True
        # If expiry is None, treat as unknown, not expired
        if expiry is None:
            status[kenteken]["unknown"] = True
            print(f"  {kenteken}: APK vervaldatum onbekend")
        else:
            status[kenteken]["unknown"] = False
            roepnummers = status[kenteken].get("roepnummers", [])
            roep_str = f" ({', '.join(roepnummers)})" if roepnummers else ""
            # Check if previously expired and now valid (verlengt)
            verlengt = False
            if previous_expiry not in [None, "None", "null", ""]:
                try:
                    prev_expiry_date = datetime.strptime(previous_expiry, "%Y-%m-%d").date()
                    if prev_expiry_date < today and valid:
                        verlengt = True
                except Exception:
                    pass
            if verlengt:
                print(f"  {kenteken}: APK verlengt tot {expiry}")
                # Only send Discord message if previous expiry was a week or more ago
                if previous_expiry:
                    try:
                        prev_expiry_date = datetime.strptime(previous_expiry, "%Y-%m-%d").date()
                        if (today - prev_expiry_date).days >= 7:
                            msg = f"{kenteken}{roep_str}: APK verlengt tot {expiry}"
                            webhook_APK(msg)
                            time.sleep(10)
                    except Exception:
                        pass
            elif not valid:
                print(f"  {kenteken}: verlopen op {expiry}")
                # Only send Discord message if expiry was a week or more ago
                if expiry and (today - expiry).days >= 7:
                    msg = f"{kenteken}{roep_str}: APK verlopen op {expiry}"
                    webhook_APK(msg)
                    time.sleep(10)
            else:
                print(f"  {kenteken}: APK geldig tot {expiry}")
    save_kenteken_status(status)

    # Always write a fresh, deduplicated, up-to-date report, excluding unchecked kentekens
    expired_lines = []
    unknown_lines = []
    for kenteken, v in status.items():
        if not v.get("checked", False):
            continue  # Skip unchecked kentekens
        roepnummers = v.get("roepnummers", [])
        roep_str = f" ({', '.join(roepnummers)})" if roepnummers else ""
        if v["unknown"] or v["expiry"] in [None, "None", "null", ""]:
            unknown_lines.append((kenteken, roep_str))
        else:
            try:
                expiry_date = datetime.strptime(v["expiry"], "%Y-%m-%d").date()
                if expiry_date < today:
                    expired_lines.append((kenteken, roep_str, v["expiry"]))
            except Exception:
                expired_lines.append((kenteken, roep_str, v["expiry"]))
    expired_lines = sorted(set(expired_lines))
    unknown_lines = sorted(set(unknown_lines))

    WIDTH = 80
    SEP  = "=" * WIDTH
    THIN = "-" * WIDTH
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        def w(line=""):
            f.write(line + "\n")

        w(SEP)
        w("  APK Expiry Report")
        w(f"  Expired    : {len(expired_lines):,}")
        w(f"  Unknown    : {len(unknown_lines):,}")
        w(SEP)

        w()
        w(THIN)
        w(f"  Expired ({len(expired_lines)})")
        w(THIN)
        if expired_lines:
            for kenteken, roep_str, expiry in expired_lines:
                w(f"  {kenteken}{roep_str}")
                w(f"      Verlopen op : {expiry}")
        else:
            w("  (geen)")

        w()
        w(THIN)
        w(f"  Unknown ({len(unknown_lines)})")
        w(THIN)
        if unknown_lines:
            for kenteken, roep_str in unknown_lines:
                w(f"  {kenteken}{roep_str}")
        else:
            w("  (geen)")

        w()
        w(SEP)

    print(f"Rapport bijgewerkt in {REPORT_FILE}")

if __name__ == "__main__":
    main()
