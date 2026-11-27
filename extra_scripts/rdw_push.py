import hashlib
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
API_URL_BULK = API_URL_VEHICLES + "/bulk"
API_URL_LOOKUP = API_URL_VEHICLES + "/lookup"
BATCH_SIZE = 200  # records per bulk-/lookup-verzoek (site staat max 500 toe)

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


def _get_json(url: str, **kwargs) -> Any:
    last_error: Exception | None = None
    attempts = len(FETCH_WAITS_SECONDS)
    for attempt, wait in enumerate(FETCH_WAITS_SECONDS, start=1):
        if wait:
            time.sleep(wait)
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
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

    raise RuntimeError(f"Ophalen van {url} definitief mislukt: {last_error}") from last_error


def _js_string(value: Any) -> str | None:
    """Zoals de site een waarde opslaat (String() in JavaScript)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def rdw_hash(record: dict[str, Any]) -> str:
    """Controlegetal per record; moet gelijk blijven aan rdwHash() in WebsiteData/routes/rdw.js."""
    parts = []
    for key in sorted(record):
        if key in ("kenteken", "updated_at"):
            continue
        value = _js_string(record[key])
        if value is None:
            continue
        value = value.strip()
        if value in ("", "0") or value.lower() == "null":
            continue
        parts.append(f"{key}\x01{value}")
    return hashlib.md5("\x02".join(parts).encode("utf-8")).hexdigest()


class SiteData:
    """Stand van de site, één keer per run opgehaald en gedeeld door de RDW- en Aircraft-sync.

    Nieuwe site: alleen kenteken -> controlegetal, en volledige records alleen voor wat afwijkt.
    Oude site (zonder ?format=hash): de volledige lijst, zoals voorheen.
    """

    def __init__(self) -> None:
        data = _get_json(API_URL_VEHICLES, params={"format": "hash"})
        self.records: dict[str, dict[str, Any]] = {}
        self.hashes: dict[str, str] = {}
        self._raw_kenteken: dict[str, str] = {}
        if isinstance(data, dict) and isinstance(data.get("hashes"), dict):
            self.mode = "hash"
            for kenteken, value in data["hashes"].items():
                norm = _normalize_kenteken(kenteken)
                if norm:
                    self.hashes[norm] = value
                    self._raw_kenteken[norm] = kenteken
        elif isinstance(data, list):
            self.mode = "volledig"
            self.records = _kentekens_by_record(data)
        else:
            raise RuntimeError(f"Onverwacht antwoord van {API_URL_VEHICLES}: {type(data).__name__}")

    @property
    def kentekens(self) -> set[str]:
        return set(self.hashes) if self.mode == "hash" else set(self.records)

    def __len__(self) -> int:
        return len(self.kentekens)

    def records_for(self, kentekens: set[str], file_by_kenteken: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Volledige site-records voor de kentekens die (mogelijk) afwijken van het bestand."""
        if self.mode == "volledig":
            return {k: self.records[k] for k in kentekens}
        afwijkend = [k for k in kentekens if self.hashes.get(k) != rdw_hash(file_by_kenteken[k])]
        result: dict[str, dict[str, Any]] = {}
        for i in range(0, len(afwijkend), BATCH_SIZE):
            batch = [self._raw_kenteken[k] for k in afwijkend[i:i + BATCH_SIZE]]
            response = requests.post(API_URL_LOOKUP, json={"kentekens": batch}, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            result.update(_kentekens_by_record(response.json()))
        print(f"Controlegetal wijkt af bij {len(afwijkend)} van {len(kentekens)}; die records volledig opgehaald")
        return result


def load_full_combined() -> list:
    with open(FULL_COMBINED_FILE, encoding="utf-8") as infile:
        return json.load(infile)


def load_aircraft_data() -> list:
    with open(AIRCRAFT_DATA_FILE, encoding="utf-8") as infile:
        return json.load(infile)


# ==========================================
# RDW VOERTUIGEN LOGICA
# ==========================================

def _post_record(payload: dict[str, Any]) -> None:
    headers = {"X-RDW-API-Key": os.getenv("HVNBL_RDW_API_KEY", "")}
    response = requests.post(API_URL_VEHICLES, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    if not response.ok:
        print(f"Server respons ({response.status_code}) voor {payload.get('kenteken')}: {response.text[:300]}")
    response.raise_for_status()


_bulk_supported = True


def push_records(payloads: list[dict[str, Any]]) -> tuple[int, list[tuple[str, str]]]:
    """Stuurt records in batches naar de site; valt terug op één verzoek per record als /bulk ontbreekt."""
    global _bulk_supported
    headers = {"X-RDW-API-Key": os.getenv("HVNBL_RDW_API_KEY", "")}
    pushed = 0
    failures: list[tuple[str, str]] = []
    rest = list(payloads)
    while rest and _bulk_supported:
        batch, rest = rest[:BATCH_SIZE], rest[BATCH_SIZE:]
        try:
            response = requests.post(API_URL_BULK, headers=headers, json=batch, timeout=REQUEST_TIMEOUT_SECONDS * 4)
            if response.status_code in (404, 405):
                print("Site heeft nog geen /bulk; terugval op los pushen")
                _bulk_supported = False
                rest = batch + rest
                break
            response.raise_for_status()
            body = response.json()
            for item in body.get("results", []):
                print(f"Gepusht naar API: {item.get('kenteken')}")
            pushed += int(body.get("saved", 0))
            failures += [(str(e.get("kenteken")), str(e.get("error"))) for e in body.get("errors", [])]
        except (requests.RequestException, ValueError) as exc:
            print(f"Bulk-push mislukt ({len(batch)} records): {exc}")
            failures += [(str(p.get("kenteken")), str(exc)) for p in batch]
    for payload in rest:
        try:
            _post_record(payload)
            print(f"Gepusht naar API: {payload.get('kenteken')}")
            pushed += 1
        except requests.RequestException as exc:
            print(f"Push mislukt voor {payload.get('kenteken')}: {exc}")
            failures.append((str(payload.get("kenteken")), str(exc)))
    return pushed, failures


def compare_vehicles_with_api(site: SiteData) -> None:
    combined_by_kenteken = _kentekens_by_record(load_full_combined())
    site_kentekens = site.kentekens

    shared = site_kentekens & set(combined_by_kenteken)
    only_in_combined = set(combined_by_kenteken) - site_kentekens
    only_in_api = site_kentekens - set(combined_by_kenteken)

    # Alleen records waarvan het controlegetal afwijkt worden volledig vergeleken
    api_by_kenteken = site.records_for(shared, combined_by_kenteken)

    different_kentekens: set[str] = set()
    diffs_by_kenteken: dict[str, set[str]] = {}
    for kenteken, api_record in api_by_kenteken.items():
        combined_record = combined_by_kenteken[kenteken]
        changed_keys = {
            key for key in set(api_record) | set(combined_record)
            if _normalize_value(api_record.get(key)) != _normalize_value(combined_record.get(key))
        }
        if changed_keys:
            different_kentekens.add(kenteken)
            diffs_by_kenteken[kenteken] = changed_keys
    same = len(shared) - len(different_kentekens)

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

    payloads = [combined_by_kenteken[k] for k in sorted(only_in_combined | different_kentekens)]
    pushed, failures = push_records(payloads)
    if pushed + len(failures) > 0:
        notify_push_summary(pushed, failures, prefix="RDW Voertuigen", target_url=API_URL_VEHICLES)


# ==========================================
# LUCHTVAARTUIGEN (AIRCRAFT) LOGICA
# ==========================================

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


def compare_aircraft_with_api(site: SiteData) -> None:
    file_by_kenteken: dict[str, dict[str, Any]] = {}
    for record in load_aircraft_data():
        if isinstance(record, dict):
            formatted_record = _aircraft_record_to_api_format(record)
            kenteken = _normalize_kenteken(formatted_record.get("kenteken"))
            if kenteken:
                file_by_kenteken[kenteken] = formatted_record

    site_kentekens = site.kentekens
    shared = site_kentekens & set(file_by_kenteken)
    only_in_file = set(file_by_kenteken) - site_kentekens

    api_by_kenteken = site.records_for(shared, file_by_kenteken)

    different_kentekens: set[str] = set()
    diffs_by_kenteken: dict[str, set[str]] = {}
    for kenteken, api_record in api_by_kenteken.items():
        file_record = file_by_kenteken[kenteken]
        changed_keys = {
            key for key in set(file_record)
            if _normalize_value(api_record.get(key)) != _normalize_value(file_record.get(key))
        }
        if changed_keys:
            different_kentekens.add(kenteken)
            diffs_by_kenteken[kenteken] = changed_keys
    same = len(shared) - len(different_kentekens)

    print("\n--- AIRCRAFT SUMMARY ---")
    print(f"Zelfde in beide: {same}")
    print(f"Verschillend: {len(different_kentekens)}")
    print(f"Alleen in aircraft_data.json: {len(only_in_file)}")

    for kenteken in sorted(different_kentekens):
        api_record = api_by_kenteken[kenteken]
        file_record = file_by_kenteken[kenteken]
        print(f"Verschil voor {kenteken}:")
        for key in sorted(diffs_by_kenteken[kenteken]):
            print(f"  {key}: API={json.dumps(api_record.get(key))} File={json.dumps(file_record.get(key))}")

    payloads = [file_by_kenteken[k] for k in sorted(only_in_file | different_kentekens)]
    pushed, failures = push_records(payloads)
    if pushed + len(failures) > 0:
        notify_push_summary(pushed, failures, prefix="Aircraft", target_url=API_URL_VEHICLES)


# ==========================================
# HOOFDPROCES
# ==========================================

def run() -> None:
    # Eén keer ophalen voor beide syncs (scheelt een verzoek en dus kans op blokkade)
    try:
        site = SiteData()
    except RuntimeError as exc:
        print(str(exc))
        notify_fetch_failure(str(exc), prefix="RDW Voertuigen + Aircraft", target_url=API_URL_VEHICLES)
        return

    print(f"Opgehaald: {len(site)} records van {API_URL_VEHICLES} (modus: {site.mode})")
    if len(site) == 0:
        message = f"Lege lijst ontvangen van {API_URL_VEHICLES}, sync overgeslagen (niets gepusht)."
        print(message)
        notify_fetch_failure(message, prefix="RDW Voertuigen + Aircraft", target_url=API_URL_VEHICLES)
        return

    print(">>> Starten RDW Voertuigen sync...")
    try:
        compare_vehicles_with_api(site)
    except (requests.RequestException, ValueError) as exc:
        print(f"RDW Voertuigen sync mislukt: {exc}")
        notify_fetch_failure(str(exc), prefix="RDW Voertuigen", target_url=API_URL_VEHICLES)

    print("\n>>> Starten Aircraft sync...")
    try:
        compare_aircraft_with_api(site)
    except (requests.RequestException, ValueError) as exc:
        print(f"Aircraft sync mislukt: {exc}")
        notify_fetch_failure(str(exc), prefix="Aircraft", target_url=API_URL_VEHICLES)


if __name__ == "__main__":
    run()
