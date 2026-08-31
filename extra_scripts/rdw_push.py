import json
import os
import time
from typing import Any

import requests


def _strip_url(message: str) -> str:
    return message.replace(API_URL, "").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
FULL_COMBINED_FILE = os.path.join(PROJECT_ROOT, "storage", "rdw_full_combined.json")

API_URL = "https://development.hulpdienstvoertuigenbenelux.nl/api/rdw/vehicles"
REQUEST_TIMEOUT_SECONDS = 30
RED_COLOR = 15158332
GREEN_COLOR = 3066993
DISCORD_EMBED_DESCRIPTION_LIMIT = 4096


def fetch_vehicles() -> list:
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            response = requests.get(API_URL, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, requests.exceptions.JSONDecodeError) as exc:
            last_error = exc
            print(f"Ophalen mislukt (poging {attempt}/2): {exc}")

    raise RuntimeError(f"Ophalen van {API_URL} definitief mislukt: {last_error}") from last_error


def push_vehicle(record: dict[str, Any]) -> None:
    api_key = os.getenv("HVNBL_RDW_API_KEY", "")
    headers = {"X-RDW-API-Key": api_key}
    response = requests.post(API_URL, headers=headers, json=record, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()


def notify_fetch_failure(error: str) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL_RDW_API_PUSH", "")
    if not webhook_url:
        print(f"Discord notificatie overgeslagen: geen webhook URL ({error})")
        return

    payload = {
        "username": "HulpdienstVoertuigenBeNeLux RDW Push",
        "embeds": [
            {
                "title": "RDW push: ophalen mislukt",
                "description": _strip_url(error)[:DISCORD_EMBED_DESCRIPTION_LIMIT],
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


def notify_push_summary(pushed: int, failures: list[tuple[str, str]]) -> None:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL_RDW_API_PUSH", "")
    if not webhook_url:
        print("Discord sync samenvatting overgeslagen: geen webhook URL")
        return

    summary_line = f"Gepusht: {pushed}, mislukt: {len(failures)}"
    failure_lines = [
        f"- {kenteken}: {_strip_url(error)}"[:DISCORD_EMBED_DESCRIPTION_LIMIT] for kenteken, error in failures
    ]

    # Discord embed descriptions are capped at 4096 characters, so split failures across
    # multiple embeds/messages when the summary would exceed that limit.
    descriptions = [summary_line]
    for line in failure_lines:
        candidate = f"{descriptions[-1]}\n{line}"
        if len(candidate) <= DISCORD_EMBED_DESCRIPTION_LIMIT:
            descriptions[-1] = candidate
        else:
            descriptions.append(line)

    for index, description in enumerate(descriptions, start=1):
        title = "RDW push sync voltooid" if index == 1 else f"RDW push sync voltooid (vervolg {index})"
        payload = {
            "username": "HulpdienstVoertuigenBeNeLux RDW Push",
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


def load_full_combined() -> list:
    with open(FULL_COMBINED_FILE, encoding="utf-8") as infile:
        return json.load(infile)


def _normalize_kenteken(value: Any) -> str:
    return str(value or "").strip().replace("-", "").replace(" ", "").upper()


def _kentekens_by_record(records: list) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        kenteken = _normalize_kenteken(record.get("kenteken"))
        if kenteken:
            result[kenteken] = record
    return result


def compare_with_full_combined(vehicles: list) -> None:
    api_by_kenteken = _kentekens_by_record(vehicles)
    combined_by_kenteken = _kentekens_by_record(load_full_combined())

    shared = set(api_by_kenteken) & set(combined_by_kenteken)
    only_in_combined = set(combined_by_kenteken) - set(api_by_kenteken)
    only_in_api = set(api_by_kenteken) - set(combined_by_kenteken)

    same = 0
    different_kentekens: set[str] = set()
    for kenteken in shared:
        api_record = api_by_kenteken[kenteken]
        combined_record = combined_by_kenteken[kenteken]
        if api_record == combined_record:
            same += 1
        else:
            different_kentekens.add(kenteken)

    print(f"Zelfde in beide: {same}")
    print(f"Verschillend (zelfde kenteken, andere waarden): {len(different_kentekens)}")
    print(f"Alleen in full combined (niet in API): {len(only_in_combined)}")
    print(f"Alleen in API (niet in full combined): {len(only_in_api)}")

    for kenteken in sorted(different_kentekens):
        api_record = api_by_kenteken[kenteken]
        combined_record = combined_by_kenteken[kenteken]
        changed_keys = {
            key for key in set(api_record) | set(combined_record)
            if api_record.get(key) != combined_record.get(key)
        }
        print(f"Verschil voor {combined_record.get('kenteken')}:")
        for key in sorted(changed_keys):
            print(f"  {key}: API={api_record.get(key)!r} FullCombined={combined_record.get(key)!r}")

    # Full combined is leading: (re)push records missing from the API and records with different values.
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
        notify_push_summary(pushed, failures)


def run() -> None:
    try:
        vehicles = fetch_vehicles()
    except RuntimeError as exc:
        print(str(exc))
        notify_fetch_failure(str(exc))
        return

    print(f"Opgehaald: {len(vehicles)} voertuigen van {API_URL}")
    if not vehicles:
        message = f"Lege lijst ontvangen van {API_URL}, sync overgeslagen (niets gepusht)."
        print(message)
        notify_fetch_failure(message)
        return
    compare_with_full_combined(vehicles)


if __name__ == "__main__":
    run()
