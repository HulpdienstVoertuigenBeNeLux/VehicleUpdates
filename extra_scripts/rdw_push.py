import json
import os
import time
from typing import Any

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

# Bestanden
FULL_COMBINED_FILE = os.path.join(PROJECT_ROOT, "storage", "rdw_full_combined.json")
AIRCRAFT_DATA_FILE = os.path.join(PROJECT_ROOT, "storage", "aircraft_data.json")

# API Endpoint (beide maken gebruik van hetzelfde endpoint)
API_URL_VEHICLES = "https://hulpdienstvoertuigenbenelux.nl/api/rdw/vehicles"

REQUEST_TIMEOUT_SECONDS = 30
RED_COLOR = 15158332
GREEN_COLOR = 3066993
DISCORD_EMBED_DESCRIPTION_LIMIT = 4096

# Vliegtuig-specifieke mapping: alleen 'registration' wordt 'kenteken'
AIRCRAFT_FIELD_MAP = {
    "registration": "kenteken"
}


def _strip_url(message: str, url: str) -> str:
    return message.replace(url, "").strip()


def _normalize_value(value: Any) -> Any:
    if value is None or (isinstance(value, str) and value.strip().lower() in ("null", "")):
        return None
    if not isinstance(value, bool) and isinstance(value, (int, float, str)) and str(value).strip() == "0":
        return None
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_kenteken(value: Any) -> str:
    return str(value or "").strip().upper()


def _kentekens_by_record(records: list) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        kenteken = _normalize_kenteken(record.get("kenteken"))
        if kenteken:
            result[kenteken] = record
    return result


# ==========================================
# DISCORD NOTIFICATIES
# ==========================================

def notify_fetch_failure(error: str, prefix: str, target_url: str) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL_RDW_API_PUSH", "")
    if not webhook_url:
        print(f"Discord notificatie overgeslagen: geen webhook URL ({error})")
        return

    payload = {
        "username": f"HulpdienstVoertuigenBeNeLux {prefix} Push",
        "embeds": [
            {
                "title": f"{prefix} push: ophalen mislukt",
                "description": _strip_url(error, target_url)[:DISCORD_EMBED_DESCRIPTION_LIMIT],
                "color": RED_COLOR,
            }
        ],
    }
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except requests.RequestException as exc:
        print(f"Discord notificatie mislukt: {exc}")
    finally:
        time.sleep(10)


def notify_push_summary(pushed: int, failures: list[tuple[str, str]], prefix: str, target_url: str) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL_RDW_API_PUSH", "")
    if not webhook_url:
        print(f"Discord {prefix} sync samenvatting overgeslagen: geen webhook URL")
        return

    summary_line = f"Gepusht: {pushed}, mislukt: {len(failures)}"
    failure_lines = [
        f"- {identifier}: {_strip_url(error, target_url)}"[:DISCORD_EMBED_DESCRIPTION_LIMIT]
        for identifier, error in failures
    ]

    descriptions = [summary_line]
    for line in failure_lines:
        candidate = f"{descriptions[-1]}\n{line}"
        if len(candidate) <= DISCORD_EMBED_DESCRIPTION_LIMIT:
            descriptions[-1] = candidate
        else:
            descriptions.append(line)

    for index, description in enumerate(descriptions, start=1):
        title = f"{prefix} push sync voltooid" if index == 1 else f"{prefix} push sync voltooid (vervolg {index})"
        payload = {
            "username": f"HulpdienstVoertuigenBeNeLux {prefix} Push",
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": GREEN_COLOR if not failures else RED_COLOR,
                }
            ],
        }
        try:
            requests.post(webhook_url, json=payload, timeout=10)
        except requests.RequestException as exc:
            print(f"Discord sync samenvatting mislukt: {exc}")
        finally:
            time.sleep(10)


# ==========================================
# GENERIEKE API FETCH & DATA LOADERS
# ==========================================

# De site stopt na 5 minuten zonder verzoeken en start bij het volgende verzoek opnieuw op.
# Tijdens dat opstarten komt er soms een lege/HTML-pagina terug; daarom met wachttijd opnieuw proberen.
FETCH_WAITS_SECONDS = (0, 20, 45, 90)


def fetch_vehicles() -> list:
    last_error: Exception | None = None
    attempts = len(FETCH_WAITS_SECONDS)
    for attempt, wait in enumerate(FETCH_WAITS_SECONDS, start=1):
        if wait:
            time.sleep(wait)
        try:
            response = requests.get(API_URL_VEHICLES, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                start = response.text[:120].replace("\n", " ")
                raise ValueError(
                    f"geen JSON (HTTP {response.status_code}, {response.headers.get('Content-Type', '?')}, "
                    f"{len(response.content)} bytes, begin: {start!r})"
                ) from exc
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            print(f"Ophalen mislukt (poging {attempt}/{attempts}): {exc}")

    raise RuntimeError(f"Ophalen van {API_URL_VEHICLES} definitief mislukt: {last_error}") from last_error


def load_full_combined() -> list:
    with open(FULL_COMBINED_FILE, encoding="utf-8") as infile:
        return json.load(infile)


def load_aircraft_data() -> list:
    with open(AIRCRAFT_DATA_FILE, encoding="utf-8") as infile:
        return json.load(infile)


# ==========================================
# RDW VOERTUIGEN LOGICA
# ==========================================

def push_vehicle(record: dict[str, Any]) -> None:
    api_key = os.getenv("HVNBL_RDW_API_KEY", "")
    headers = {"X-RDW-API-Key": api_key}
    response = requests.post(API_URL_VEHICLES, headers=headers, json=record, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()


def compare_vehicles_with_api() -> None:
    try:
        vehicles = fetch_vehicles()
    except RuntimeError as exc:
        print(str(exc))
        notify_fetch_failure(str(exc), prefix="RDW Voertuigen", target_url=API_URL_VEHICLES)
        return

    print(f"Opgehaald: {len(vehicles)} voertuigen van {API_URL_VEHICLES}")
    if not vehicles:
        message = f"Lege lijst ontvangen van {API_URL_VEHICLES}, sync overgeslagen (niets gepusht)."
        print(message)
        notify_fetch_failure(message, prefix="RDW Voertuigen", target_url=API_URL_VEHICLES)
        return

    api_by_kenteken = _kentekens_by_record(vehicles)
    combined_by_kenteken = _kentekens_by_record(load_full_combined())

    shared = set(api_by_kenteken) & set(combined_by_kenteken)
    only_in_combined = set(combined_by_kenteken) - set(api_by_kenteken)
    only_in_api = set(api_by_kenteken) - set(combined_by_kenteken)

    same = 0
    different_kentekens: set[str] = set()
    diffs_by_kenteken: dict[str, set[str]] = {}
    for kenteken in shared:
        api_record = api_by_kenteken[kenteken]
        combined_record = combined_by_kenteken[kenteken]
        changed_keys = {
            key for key in set(api_record) | set(combined_record)
            if _normalize_value(api_record.get(key)) != _normalize_value(combined_record.get(key))
        }
        if changed_keys:
            different_kentekens.add(kenteken)
            diffs_by_kenteken[kenteken] = changed_keys
        else:
            same += 1

    print("\n--- RDW VOERTUIGEN SUMMARY ---")
    print(f"Zelfde in beide: {same}")
    print(f"Verschillend (zelfde kenteken, andere waarden): {len(different_kentekens)}")
    print(f"Alleen in full combined (niet in API): {len(only_in_combined)}")
    print(f"Alleen in API (niet in full combined): {len(only_in_api)}")

    for kenteken in sorted(different_kentekens):
        api_record = api_by_kenteken[kenteken]
        combined_record = combined_by_kenteken[kenteken]
        print(f"Verschil voor {combined_record.get('kenteken')}:")
        for key in sorted(diffs_by_kenteken[kenteken]):
            api_value = api_record.get(key)
            combined_value = combined_record.get(key)
            api_display = json.dumps("null" if api_value is None else api_value, ensure_ascii=False)
            combined_display = json.dumps("null" if combined_value is None else combined_value, ensure_ascii=False)
            print(f"  {key}: API={api_display} FullCombined={combined_display}")

    pushed = 0
    failures: list[tuple[str, str]] = []
    for kenteken in only_in_combined | different_kentekens:
        record = combined_by_kenteken[kenteken]
        try:
            push_vehicle(record)
            print(f"Gepusht naar API: {record.get('kenteken')}")
            pushed += 1
        except requests.RequestException as exc:
            print(f"Push mislukt voor {record.get('kenteken')}: {exc}")
            failures.append((str(record.get("kenteken")), str(exc)))

    if pushed + len(failures) > 0:
        notify_push_summary(pushed, failures, prefix="RDW Voertuigen", target_url=API_URL_VEHICLES)


# ==========================================
# LUCHTVAARTUIGEN (AIRCRAFT) LOGICA
# ==========================================

def push_aircraft(record: dict[str, Any]) -> None:
    api_key = os.getenv("HVNBL_RDW_API_KEY", "")
    headers = {"X-RDW-API-Key": api_key}

    payload = {}
    for key, val in record.items():
        clean_val = val.strip() if isinstance(val, str) else val
        mapped_key = AIRCRAFT_FIELD_MAP.get(key, key)
        payload[mapped_key] = clean_val

    if "kenteken" not in payload or not payload["kenteken"]:
        reg_val = record.get("registration") or record.get("kenteken")
        payload["kenteken"] = reg_val.strip() if isinstance(reg_val, str) else reg_val

    response = requests.post(API_URL_VEHICLES, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    
    if not response.ok:
        print(f"Server respons ({response.status_code}) voor {record.get('registration')}: {response.text}")
        
    response.raise_for_status()


def _aircraft_record_to_api_format(record: dict[str, Any]) -> dict[str, Any]:
    mapped = {}
    for key, val in record.items():
        clean_val = val.strip() if isinstance(val, str) else val
        mapped_key = AIRCRAFT_FIELD_MAP.get(key, key)
        mapped[mapped_key] = clean_val

    if "kenteken" not in mapped or not mapped["kenteken"]:
        reg_val = record.get("registration") or record.get("kenteken")
        mapped["kenteken"] = reg_val.strip() if isinstance(reg_val, str) else reg_val

    return mapped


def compare_aircraft_with_api() -> None:
    try:
        vehicles = fetch_vehicles()
    except RuntimeError as exc:
        print(str(exc))
        notify_fetch_failure(str(exc), prefix="Aircraft", target_url=API_URL_VEHICLES)
        return

    print(f"\nOpgehaald: {len(vehicles)} records van API voor Aircraft-vergelijking")

    api_by_kenteken = _kentekens_by_record(vehicles)
    
    raw_aircraft_data = load_aircraft_data()
    file_by_kenteken = {}
    for record in raw_aircraft_data:
        if isinstance(record, dict):
            formatted_record = _aircraft_record_to_api_format(record)
            kenteken = formatted_record.get("kenteken")
            if kenteken:
                file_by_kenteken[kenteken] = formatted_record

    shared = set(api_by_kenteken) & set(file_by_kenteken)
    only_in_file = set(file_by_kenteken) - set(api_by_kenteken)

    same = 0
    different_kentekens: set[str] = set()
    diffs_by_kenteken: dict[str, set[str]] = {}

    for kenteken in shared:
        api_record = api_by_kenteken[kenteken]
        file_record = file_by_kenteken[kenteken]
        
        changed_keys = {
            key for key in set(file_record)
            if _normalize_value(api_record.get(key)) != _normalize_value(file_record.get(key))
        }
        if changed_keys:
            different_kentekens.add(kenteken)
            diffs_by_kenteken[kenteken] = changed_keys
        else:
            same += 1

    print("\n--- AIRCRAFT SUMMARY ---")
    print(f"Zelfde in beide: {same}")
    print(f"Verschillend: {len(different_kentekens)}")
    print(f"Alleen in aircraft_data.json: {len(only_in_file)}")

    for kenteken in sorted(different_kentekens):
        api_record = api_by_kenteken[kenteken]
        file_record = file_by_kenteken[kenteken]
        print(f"Verschil voor {kenteken}:")
        for key in sorted(diffs_by_kenteken[kenteken]):
            api_value = api_record.get(key)
            file_value = file_record.get(key)
            print(f"  {key}: API={json.dumps(api_value)} File={json.dumps(file_value)}")

    pushed = 0
    failures: list[tuple[str, str]] = []
    for kenteken in only_in_file | different_kentekens:
        record = file_by_kenteken[kenteken]
        try:
            push_aircraft(record)
            print(f"Gepusht naar API: {kenteken}")
            pushed += 1
        except requests.RequestException as exc:
            print(f"Push mislukt voor {kenteken}: {exc}")
            failures.append((kenteken, str(exc)))

    if pushed + len(failures) > 0:
        notify_push_summary(pushed, failures, prefix="Aircraft", target_url=API_URL_VEHICLES)


# ==========================================
# HOOFDPROCES
# ==========================================

def run() -> None:
    print(">>> Starten RDW Voertuigen sync...")
    compare_vehicles_with_api()

    print("\n>>> Starten Aircraft sync...")
    compare_aircraft_with_api()


if __name__ == "__main__":
    run()
