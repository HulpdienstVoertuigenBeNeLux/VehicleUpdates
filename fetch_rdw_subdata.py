import atexit
import os
import json
import time
import requests

STORAGE_DIR = "storage"
RDW_RAW_FILE = os.path.join(STORAGE_DIR, "rdw_raw.json")
MAX_CHECKS = 100
RDW_REQUEST_TIMEOUT_SECONDS = int(os.getenv("RDW_REQUEST_TIMEOUT_SECONDS", "6"))
RDW_APP_TOKEN = os.getenv("RDW_APP_TOKEN")  # Haalt het token uit GitHub Secrets
SAVE_EVERY_UPDATES = int(os.getenv("RDW_SUBDATA_SAVE_EVERY", "20"))

API_ENDPOINTS = {
    "brandstof": {
        "url": "https://opendata.rdw.nl/resource/8ys7-d773.json?kenteken={kenteken}",
        "file": os.path.join(STORAGE_DIR, "rdw_brandstof.json"),
    },
    "assen": {
        "url": "https://opendata.rdw.nl/resource/3huj-srit.json?kenteken={kenteken}",
        "file": os.path.join(STORAGE_DIR, "rdw_assen.json"),
    },
    "carrosserie": {
        "url": "https://opendata.rdw.nl/resource/vezc-m2t6.json?kenteken={kenteken}",
        "file": os.path.join(STORAGE_DIR, "rdw_carrosserie.json"),
    },
    "carrosserie_specifiek": {
        "url": "https://opendata.rdw.nl/resource/jhie-znh9.json?kenteken={kenteken}",
        "file": os.path.join(STORAGE_DIR, "rdw_carrosserie_specifiek.json"),
    },
    "voertuigklasse": {
        "url": "https://opendata.rdw.nl/resource/kmfi-hrps.json?kenteken={kenteken}",
        "file": os.path.join(STORAGE_DIR, "rdw_voertuigklasse.json"),
    },
}


def load_json(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return None


def save_json(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_kentekens(raw_data):
    kentekens = []
    if isinstance(raw_data, dict):
        for key, val in raw_data.items():
            if isinstance(val, dict):
                k = val.get("kenteken") or key
            else:
                k = key
            if k:
                kentekens.append(str(k).strip())
    elif isinstance(raw_data, list):
        for item in raw_data:
            if isinstance(item, dict):
                k = item.get("kenteken")
                if k:
                    kentekens.append(str(k).strip())
            elif isinstance(item, str):
                kentekens.append(item.strip())
    return sorted(list(set(kentekens)))


def fetch_api_data(endpoint_pattern, kenteken):
    clean_kenteken = kenteken.replace("-", "").upper()
    url = endpoint_pattern.format(kenteken=clean_kenteken)
    
    headers = {}
    if RDW_APP_TOKEN:
        headers["X-App-Token"] = RDW_APP_TOKEN

    try:
        response = requests.get(url, headers=headers, timeout=RDW_REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 200:
            return response.json()
        return []
    except requests.RequestException as e:
        print(f"  Fout bij API-call op {url}: {e}", flush=True)
        return []


# In-memory cache: api_key -> {kenteken: [records]}
_cache = {}
_loaded_apis = set()
_dirty_apis = set()
_dirty_updates = 0


def _dedupe_records(records):
    """Drop exact duplicates and records that are a strict subset of a richer one."""
    unique = []
    for item in records:
        item_items = set(item.items())
        if any(item_items <= set(other.items()) for other in unique):
            continue
        unique = [other for other in unique if not set(other.items()) <= item_items]
        unique.append(item)
    return unique


def _load_store(api_key):
    if api_key in _loaded_apis:
        return
    info = API_ENDPOINTS[api_key]
    existing = load_json(info["file"]) or []
    grouped = {}
    for item in existing:
        if isinstance(item, dict) and "kenteken" in item:
            grouped.setdefault(item["kenteken"], []).append(item)
    for kenteken, records in grouped.items():
        grouped[kenteken] = _dedupe_records(records)
    _cache[api_key] = grouped
    _loaded_apis.add(api_key)


def _save_store(api_key):
    info = API_ENDPOINTS[api_key]
    records = []
    for kenteken in sorted(_cache[api_key].keys()):
        records.extend(_cache[api_key][kenteken])
    save_json(info["file"], records)


def update_kenteken(kenteken):
    """Fetch fresh sub-data for a single kenteken and refresh it across all sub-API stores."""
    global _dirty_updates

    kenteken = str(kenteken or "").strip()
    if not kenteken:
        return

    for api_key, info in API_ENDPOINTS.items():
        _load_store(api_key)
        results = fetch_api_data(info["url"], kenteken)
        if results:
            for res in results:
                if "kenteken" not in res:
                    res["kenteken"] = kenteken
            _cache[api_key][kenteken] = results
        else:
            _cache[api_key][kenteken] = [{"kenteken": kenteken, "no_data": True}]
        _dirty_apis.add(api_key)

    _dirty_updates += 1
    if _dirty_updates >= max(1, SAVE_EVERY_UPDATES):
        flush()


def flush():
    """Persist any pending sub-data updates to disk."""
    global _dirty_updates
    for api_key in list(_dirty_apis):
        _save_store(api_key)
    _dirty_apis.clear()
    _dirty_updates = 0


atexit.register(flush)


def run():
    """Bulk-fill sub-data for any kenteken in rdw_raw.json missing from one or more sub-files."""
    raw_data = load_json(RDW_RAW_FILE)
    if not raw_data:
        print(f"Kan {RDW_RAW_FILE} niet vinden of het bestand is leeg.", flush=True)
        return

    all_kentekens = extract_kentekens(raw_data)
    print(f"Totaal {len(all_kentekens)} unieke kentekens gevonden in {RDW_RAW_FILE}.", flush=True)

    for api_key in API_ENDPOINTS:
        _load_store(api_key)

    te_verwerken = [
        k for k in all_kentekens
        if any(k not in _cache[api_key] for api_key in API_ENDPOINTS)
    ]
    te_verwerken = te_verwerken[:MAX_CHECKS]

    if not te_verwerken:
        print("Alle kentekens zijn voor alle 5 de RDW sub-bestanden al volledig opgehaald!", flush=True)
        return

    print(f"Start batch van {len(te_verwerken)} kentekens voor alle 5 sub-API's...", flush=True)

    for idx, kenteken in enumerate(te_verwerken, 1):
        print(f"[{idx}/{len(te_verwerken)}] Ophalen data voor kenteken: {kenteken}...", flush=True)

        for api_key, info in API_ENDPOINTS.items():
            if kenteken not in _cache[api_key]:
                results = fetch_api_data(info["url"], kenteken)
                if results:
                    for res in results:
                        if "kenteken" not in res:
                            res["kenteken"] = kenteken
                    _cache[api_key][kenteken] = results
                else:
                    _cache[api_key][kenteken] = [{"kenteken": kenteken, "no_data": True}]
                _dirty_apis.add(api_key)

        time.sleep(0.05)

    flush()
    for api_key, info in API_ENDPOINTS.items():
        print(f"Opgeslagen: {info['file']} ({sum(len(v) for v in _cache[api_key].values())} records)", flush=True)

    print(f"\nKlaar! Batch van {len(te_verwerken)} kentekens verwerkt.", flush=True)


if __name__ == "__main__":
    run()