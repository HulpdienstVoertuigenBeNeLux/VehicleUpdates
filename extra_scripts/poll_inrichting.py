import json
import os
import sys
import time
from datetime import datetime
from typing import Any

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
LEGACY_DATA_DIR = os.path.join(BASE_DIR, "Data")
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from Report_rdw_mismatch import generate_all_reports

RDW_VOERTUIGEN_URL = "https://opendata.rdw.nl/resource/m9d7-ebf2.json"
CHANGES_WEBHOOK_URL = os.getenv(
    "RDW_API_CHANGES_WEBHOOK",
    "",
)
PAGE_SIZE = int(os.getenv("RDW_PAGE_SIZE", "10000"))
TIMEOUT_SECONDS = int(os.getenv("RDW_TIMEOUT_SECONDS", "30"))
TARGETS = [
    {
        "naam": "brandweer",
        "where": "inrichting is not null and upper(inrichting) like '%BRANDWEER%'",
        "output": os.getenv("RDW_BRANDWEER_OUTPUT", os.path.join(STORAGE_DIR, "Brandweer_rdw.json")),
    },
    {
        "naam": "ambulance",
        "where": "inrichting is not null and upper(inrichting) like '%AMBULANCE%'",
        "output": os.getenv("RDW_AMBULANCE_OUTPUT", os.path.join(STORAGE_DIR, "Ambulance_rdw.json")),
    },
]


def ensure_storage_output_paths() -> None:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    for filename in ("Brandweer_rdw.json", "Ambulance_rdw.json"):
        legacy_path = os.path.join(LEGACY_DATA_DIR, filename)
        storage_path = os.path.join(STORAGE_DIR, filename)
        if os.path.exists(legacy_path) and not os.path.exists(storage_path):
            os.replace(legacy_path, storage_path)


def fetch_page(session: requests.Session, where_query: str, offset: int) -> list[dict[str, Any]]:
    params = {
        "$select": "*",
        "$where": where_query,
        "$order": "kenteken",
        "$limit": PAGE_SIZE,
        "$offset": offset,
    }

    response = session.get(RDW_VOERTUIGEN_URL, params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        raise ValueError("RDW response was geen lijst.")

    return data


def dedupe_records(voertuigen: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Deduplicatie op kenteken om dubbele records vanuit paginering te voorkomen.
    voertuigen_by_kenteken: dict[str, dict[str, Any]] = {}
    zonder_kenteken: list[dict[str, Any]] = []

    for voertuig in voertuigen:
        kenteken = voertuig.get("kenteken")
        if kenteken:
            voertuigen_by_kenteken[kenteken] = voertuig
        else:
            zonder_kenteken.append(voertuig)

    resultaat = list(voertuigen_by_kenteken.values())
    resultaat.sort(key=lambda x: x.get("kenteken", ""))
    resultaat.extend(zonder_kenteken)
    return resultaat


def has_non_expired_apk(record: dict[str, Any]) -> bool:
    vervaldatum_apk = (record.get("vervaldatum_apk") or "").strip()
    if not vervaldatum_apk:
        return False

    try:
        expiry_date = datetime.strptime(vervaldatum_apk, "%Y%m%d").date()
    except ValueError:
        return False

    return expiry_date >= datetime.today().date()


def filter_non_expired_apk(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if has_non_expired_apk(record)]


def load_previous_kentekens(output_file: str) -> set[str]:
    if not os.path.exists(output_file):
        return set()

    try:
        with open(output_file, encoding="utf-8") as infile:
            data = json.load(infile)
    except Exception:
        return set()

    result: set[str] = set()
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            kenteken = (item.get("kenteken") or "").strip().upper()
            if kenteken:
                result.add(kenteken)
    return result


def extract_kentekens(records: list[dict[str, Any]]) -> set[str]:
    return {
        (record.get("kenteken") or "").strip().upper()
        for record in records
        if (record.get("kenteken") or "").strip()
    }


def send_change_message(message: str) -> None:
    if not CHANGES_WEBHOOK_URL:
        return

    message_lower = message.lower()
    color = 3447003
    if "added" in message_lower:
        color = 5763719
    elif "removed" in message_lower:
        color = 15548997

    payload = {
        "username": "RDW Inrichting Monitor",
        "embeds": [
            {
                "title": "RDW Inrichting Update",
                "description": message,
                "color": color,
            }
        ],
    }
    try:
        response = requests.post(CHANGES_WEBHOOK_URL, json=payload, timeout=15)
        if not (200 <= response.status_code < 300):
            print(f"Discord webhook failed ({response.status_code}): {response.text}")
    except requests.RequestException as exc:
        print(f"Discord webhook failed: {exc}")
    finally:
        time.sleep(10)


def notify_kenteken_changes(naam: str, added: list[str], removed: list[str]) -> None:
    for kenteken in added:
        send_change_message(f"[{naam}] added kenteken: {kenteken}")

    for kenteken in removed:
        send_change_message(f"[{naam}] removed kenteken: {kenteken}")


def poll_inrichting_voertuigen(session: requests.Session, naam: str, where_query: str) -> list[dict[str, Any]]:
    voertuigen: list[dict[str, Any]] = []
    offset = 0

    while True:
        page = fetch_page(session, where_query, offset)
        if not page:
            break

        voertuigen.extend(page)
        print(f"[{naam}] Offset {offset}: {len(page)} records opgehaald")

        if len(page) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    deduped = dedupe_records(voertuigen)
    filtered = filter_non_expired_apk(deduped)
    print(f"[{naam}] Na APK-filter (niet verlopen): {len(filtered)} records")
    return filtered


def save_result(output_file: str, data: list[dict[str, Any]]) -> None:
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as outfile:
        json.dump(data, outfile, indent=2, ensure_ascii=False)


def main() -> None:
    ensure_storage_output_paths()

    headers = {}
    app_token = os.getenv("RDW_APP_TOKEN")
    if app_token:
        headers["X-App-Token"] = app_token

    with requests.Session() as session:
        if headers:
            session.headers.update(headers)

        for target in TARGETS:
            previous_kentekens = load_previous_kentekens(target["output"])
            records = poll_inrichting_voertuigen(session, target["naam"], target["where"])
            save_result(target["output"], records)
            print(f"Klaar. {len(records)} {target['naam']}-voertuigen opgeslagen in {target['output']}")

            if previous_kentekens:
                new_kentekens = extract_kentekens(records)
                added = sorted(new_kentekens - previous_kentekens)
                removed = sorted(previous_kentekens - new_kentekens)
                if added or removed:
                    notify_kenteken_changes(target["naam"], added, removed)
                    print(f"[{target['naam']}] Mutaties gemeld: +{len(added)} / -{len(removed)}")
            else:
                print(f"[{target['naam']}] Geen vorige dataset gevonden, notificaties overgeslagen.")

    print("Start rapportgeneratie voor brandweer en ambulance")
    generate_all_reports()


if __name__ == "__main__":
    main()