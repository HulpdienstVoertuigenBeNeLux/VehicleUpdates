import requests
import json
from datetime import datetime
import time
import os
import shutil

import check_tenaamstelling_changes
import fetch_rdw_subdata
from extra_scripts import rdw_export_notifier
from extra_scripts import rdw_raw_store

REPORTS_DIR = "reports"
REPORT_FILENAME = "apk_expiry_report.txt"
REPORT_FILE = os.path.join(REPORTS_DIR, REPORT_FILENAME)
RAW_DIR = "raw"
RAW_NL_FILE = os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_raw.json")
STORAGE_DIR = "storage"
KENTEKEN_STATUS_FILENAME = "kenteken_status.json"
KENTEKEN_STATUS_FILE = os.path.join(STORAGE_DIR, KENTEKEN_STATUS_FILENAME)
RDW_REQUEST_TIMEOUT_SECONDS = int(os.getenv("RDW_REQUEST_TIMEOUT_SECONDS", "6"))


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


def normalize_aantal_zitplaatsen(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text

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
            status[k] = {
                "expiry": None,
                "checked": False,
                "unknown": True,
                "roepnummers": roepnummers,
                "last_check_date": None,
                "last_expired_notification_date": None,
                "datum_tenaamstelling_dt": None,
                "last_tenaamstelling_check_date": None,
            }
        else:
            # Always update roepnummers for completeness
            status[k]["roepnummers"] = roepnummers
            if "last_check_date" not in status[k]:
                status[k]["last_check_date"] = None
            if "last_expired_notification_date" not in status[k]:
                status[k]["last_expired_notification_date"] = None
            if "datum_tenaamstelling_dt" not in status[k]:
                status[k]["datum_tenaamstelling_dt"] = None
            if "last_tenaamstelling_check_date" not in status[k]:
                status[k]["last_tenaamstelling_check_date"] = None
    return status

def save_kenteken_status(status):
    with open(KENTEKEN_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)

# 3. Collect APK info from RDW Open Data API
def get_apk_info(kenteken):
    url = f'https://opendata.rdw.nl/resource/m9d7-ebf2.json?kenteken={kenteken.replace("-", "").upper()}'
    try:
        response = requests.get(url, timeout=RDW_REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 200:
            data = response.json()
            if data:
                return data[0]  # Return first result
        return None
    except requests.Timeout as e:
        print(f"Time-out bij ophalen APK info voor {kenteken}: {e}")
        return None
    except requests.ConnectionError as e:
        print(f"Netwerkfout bij ophalen APK info voor {kenteken}: {e}")
        return None
    except requests.RequestException as e:
        print(f"Fout bij ophalen APK info voor {kenteken}: {e}")
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
    elif "onbekend" in message:
        color = "16753920"

    webhook_url = os.getenv("DISCORD_APK")
    if not webhook_url:
        print("Geen Discord webhook gevonden. Zet DISCORD_APK.")
        return

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
def run(max_checks=None):
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
    run_budget = len(to_check) if max_checks is None else max(0, int(max_checks))
    # Limit to max RDW checks per run
    if len(to_check) > run_budget:
        print(
            f"Let op: er zijn {len(to_check)} kentekens om te checken, "
            f"maar maximaal {run_budget} worden nu verwerkt."
        )
        to_check = to_check[:run_budget]
    output_lines = []
    print(f"Start batch check van {len(to_check)} kentekens...")
    for idx, kenteken in enumerate(to_check, 1):
        print(f"[{idx}/{len(to_check)}] Check {kenteken}...")
        apk_info = get_apk_info(kenteken)
        status[kenteken]["last_check_date"] = today.strftime("%Y-%m-%d")
        previous_expiry = status[kenteken].get("expiry")
        had_known_expiry = previous_expiry not in [None, "None", "null", ""]
        roepnummers = status[kenteken].get("roepnummers", [])
        roep_str = f" ({', '.join(roepnummers)})" if roepnummers else ""
        if not apk_info:
            print(f"  Geen APK info gevonden voor {kenteken}")
            status[kenteken]["expiry"] = None
            status[kenteken]["checked"] = True
            status[kenteken]["unknown"] = True
            if had_known_expiry:
                msg = f"{kenteken}{roep_str}: APK vervaldatum onbekend (was {previous_expiry}) mogelijk export of gesloopt voertuig"
                webhook_APK(msg)
                time.sleep(10)
            continue

        rdw_raw_store.upsert_record(apk_info, fallback_kenteken=kenteken)
        rdw_export_notifier.notify_if_exported(
            apk_info,
            source="APK check",
            roepnummer=", ".join(roepnummers) if roepnummers else None,
        )
        fetch_rdw_subdata.update_kenteken(kenteken)

        stored_tenaamstelling = check_tenaamstelling_changes.normalize_tenaamstelling(
            status[kenteken].get("datum_tenaamstelling_dt")
        )
        rdw_tenaamstelling = check_tenaamstelling_changes.normalize_tenaamstelling(
            apk_info.get("datum_tenaamstelling_dt")
        )
        status[kenteken]["aantal_zitplaatsen"] = normalize_aantal_zitplaatsen(
            apk_info.get("aantal_zitplaatsen")
        )
        if stored_tenaamstelling is None and rdw_tenaamstelling is not None:
            status[kenteken]["datum_tenaamstelling_dt"] = rdw_tenaamstelling
            status[kenteken]["last_tenaamstelling_check_date"] = today.strftime("%Y-%m-%d")
        elif stored_tenaamstelling == rdw_tenaamstelling:
            status[kenteken]["last_tenaamstelling_check_date"] = today.strftime("%Y-%m-%d")
        else:
            # Force immediate follow-up in tenaamstelling flow when values differ.
            status[kenteken]["last_tenaamstelling_check_date"] = None

        valid, expiry = is_apk_valid(apk_info)
        status[kenteken]["expiry"] = str(expiry)
        status[kenteken]["checked"] = True
        # If expiry is None, treat as unknown, not expired
        if expiry is None:
            status[kenteken]["unknown"] = True
            print(f"  {kenteken}: APK vervaldatum onbekend")
            if had_known_expiry:
                msg = f"{kenteken}{roep_str}: APK vervaldatum onbekend (was {previous_expiry}) mogelijk export of gesloopt voertuig"
                webhook_APK(msg)
                time.sleep(10)
        else:
            status[kenteken]["unknown"] = False
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
                status[kenteken]["last_expired_notification_date"] = None
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
                # Send expired message at most once every 7 days per kenteken.
                if expiry and (today - expiry).days >= 7:
                    should_send_expired = False
                    last_sent = status[kenteken].get("last_expired_notification_date")
                    if not last_sent:
                        should_send_expired = True
                    else:
                        try:
                            last_sent_date = datetime.strptime(last_sent, "%Y-%m-%d").date()
                            if (today - last_sent_date).days >= 7:
                                should_send_expired = True
                        except Exception:
                            should_send_expired = True

                    if should_send_expired:
                        msg = f"{kenteken}{roep_str}: APK verlopen op {expiry}"
                        webhook_APK(msg)
                        status[kenteken]["last_expired_notification_date"] = today.strftime("%Y-%m-%d")
                        time.sleep(10)
            else:
                print(f"  {kenteken}: APK geldig tot {expiry}")
                status[kenteken]["last_expired_notification_date"] = None
    save_kenteken_status(status)
    rdw_raw_store.flush()
    fetch_rdw_subdata.flush()

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
    return len(to_check)


def main():
    run()

if __name__ == "__main__":
    main()
