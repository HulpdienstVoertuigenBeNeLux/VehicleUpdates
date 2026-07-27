import json
import os
import shutil
import time
from datetime import datetime

import requests


RAW_DIR = "raw"
RAW_NL_FILE = os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_raw.json")

STORAGE_DIR = "storage"
STATUS_FILENAME = "kenteken_status.json"
STATUS_FILE = os.path.join(STORAGE_DIR, STATUS_FILENAME)


def ensure_raw_input_path() -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    root_path = "hulpdienstvoertuigenbenelux_raw.json"
    if os.path.exists(root_path) and not os.path.exists(RAW_NL_FILE):
        shutil.move(root_path, RAW_NL_FILE)


def ensure_status_path() -> None:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    if os.path.exists(STATUS_FILENAME) and not os.path.exists(STATUS_FILE):
        shutil.move(STATUS_FILENAME, STATUS_FILE)


def collect_kentekens_with_roepnummer() -> dict:
    ensure_raw_input_path()
    with open(RAW_NL_FILE, encoding="utf-8") as f:
        data = json.load(f)

    kenteken_map = {}
    for entry in data:
        kenteken = str(entry.get("Kenteken", "")).strip().upper()
        roep = str(entry.get("Roepnummer", "")).strip()
        if kenteken and kenteken not in {"GEEN", "ONBEKEND", "-", "KENTEKEN"}:
            kenteken_map.setdefault(kenteken, set()).add(roep)

    return {k: sorted([r for r in v if r]) for k, v in kenteken_map.items()}


def load_status() -> dict:
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f)
    else:
        status = {}

    return status


def save_status(status: dict) -> None:
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)


def fetch_rdw_record(kenteken: str) -> dict | None:
    url = f"https://opendata.rdw.nl/resource/m9d7-ebf2.json?kenteken={kenteken.replace('-', '').upper()}"
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
                    return data[0]
            return None
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= max_attempts:
                print(f"Fout bij ophalen RDW info voor {kenteken}: {exc}")
                return None
            print(
                f"Netwerk/time-out fout voor {kenteken}: {exc}. "
                f"Retry in {retry_delay_seconds}s (attempt {attempt}/{max_attempts})..."
            )
            time.sleep(retry_delay_seconds)
        except requests.RequestException as exc:
            print(f"Fout bij ophalen RDW info voor {kenteken}: {exc}")
            return None
    return None


def normalize_tenaamstelling(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # RDW commonly returns YYYYMMDD; store consistently as YYYY-MM-DD.
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"

    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        # Accept values like YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS by keeping only date part.
        return text[:10]

    return text


def normalize_aantal_zitplaatsen(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def format_rdw_date(value: str | None) -> str:
    normalized = normalize_tenaamstelling(value)
    if not normalized:
        return "onbekend"
    return normalized


def format_json_date_value(value: str | None) -> str:
    normalized = normalize_tenaamstelling(value)
    return normalized if normalized is not None else "null"


def is_apk_expired(entry: dict, today_date) -> bool:
    expiry = entry.get("expiry")
    if not expiry or expiry in ["None", "null", ""]:
        return False
    try:
        expiry_date = datetime.strptime(str(expiry), "%Y-%m-%d").date()
    except ValueError:
        return False
    return expiry_date < today_date


def send_discord_change_notification(
    kenteken: str,
    roep_str: str,
    old_value: str | None,
    new_value: str | None,
    detected_at: str,
) -> None:
    webhook_url = os.getenv("DISCORD_APK")
    if not webhook_url:
        print(
            "Geen Discord webhook gevonden. Zet DISCORD_APK."
        )
        return

    message = (
        f"Voertuig met kenteken {kenteken}{roep_str} is op {new_value} van eigenaar veranderd."
    )

    payload = {
        "username": "HulpdienstVoertuigenBeNeLux Tenaamstelling",
        "embeds": [
            {
                "title": f"{kenteken}{roep_str}: tenaamstelling gewijzigd",
                "description": message,
                "color": 3447003,
            }
        ],
    }
    headers = {"Content-Type": "application/json"}

    try:
        result = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
        if 200 <= result.status_code < 300:
            print(f"Discord webhook verzonden voor {kenteken} ({result.status_code})")
        else:
            print(f"Discord webhook niet verzonden voor {kenteken}: {result.status_code}")
    except requests.RequestException as exc:
        print(f"Fout bij versturen Discord webhook voor {kenteken}: {exc}")


def run(max_checks: int | None = None) -> int:
    ensure_status_path()

    status = load_status()

    today_date = datetime.today().date()
    today = today_date.strftime("%Y-%m-%d")
    detected_at = datetime.now().strftime("%Y-%m-%d")

    kentekens = []
    for kenteken in status.keys():
        entry = status[kenteken]
        last_check = entry.get("last_tenaamstelling_check_date")
        if not last_check:
            kentekens.append(kenteken)
            continue

        try:
            last_check_date = datetime.strptime(last_check, "%Y-%m-%d").date()
        except ValueError:
            # Invalid stored date format: check now to recover and re-store correctly.
            kentekens.append(kenteken)
            continue

        interval_days = 1 if is_apk_expired(entry, today_date) else 7
        if (today_date - last_check_date).days >= interval_days:
            kentekens.append(kenteken)

    if max_checks is not None:
        max_checks = max(0, max_checks)
        kentekens = kentekens[:max_checks]

    print(f"Start controle van datum_tenaamstelling_dt voor {len(kentekens)} kentekens...")
    for index, kenteken in enumerate(kentekens, 1):
        print(f"[{index}/{len(kentekens)}] Check {kenteken}...")
        entry = status[kenteken]
        entry.pop("changes", None)
        record = fetch_rdw_record(kenteken)
        entry["last_tenaamstelling_check_date"] = today

        if not record:
            continue

        entry["aantal_zitplaatsen"] = normalize_aantal_zitplaatsen(
            record.get("aantal_zitplaatsen")
        )

        current_value = normalize_tenaamstelling(record.get("datum_tenaamstelling_dt"))
        previous_value = normalize_tenaamstelling(entry.get("datum_tenaamstelling_dt"))

        if previous_value is not None and previous_value != current_value:
            roepnummers = entry.get("roepnummers", [])
            roep_str = f" ({', '.join(roepnummers)})" if roepnummers else ""
            print(
                f"  Wijziging: {kenteken}{roep_str} "
                f"{format_rdw_date(previous_value)} -> {format_rdw_date(current_value)}"
            )
            send_discord_change_notification(
                kenteken=kenteken,
                roep_str=roep_str,
                old_value=previous_value,
                new_value=current_value,
                detected_at=detected_at,
            )
            time.sleep(2)

        entry["datum_tenaamstelling_dt"] = current_value

    save_status(status)
    print("Controle voltooid.")
    return len(kentekens)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
