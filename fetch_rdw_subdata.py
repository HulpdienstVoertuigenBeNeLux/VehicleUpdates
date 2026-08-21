import os
import json
import time
import requests

STORAGE_DIR = "storage"
RDW_RAW_FILE = os.path.join(STORAGE_DIR, "rdw_raw.json")
MAX_CHECKS = 100
RDW_REQUEST_TIMEOUT_SECONDS = int(os.getenv("RDW_REQUEST_TIMEOUT_SECONDS", "6"))
RDW_APP_TOKEN = os.getenv("RDW_APP_TOKEN")  # Haalt het token uit GitHub Secrets

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


def run():
    raw_data = load_json(RDW_RAW_FILE)
    if not raw_data:
        print(f"Kan {RDW_RAW_FILE} niet vinden of het bestand is leeg.", flush=True)
        return

    all_kentekens = extract_kentekens(raw_data)
    print(f"Totaal {len(all_kentekens)} unieke kentekens gevonden in {RDW_RAW_FILE}.", flush=True)

    data_stores = {}
    verwerkte_kentekens_per_api = {}

    for key, info in API_ENDPOINTS.items():
        existing_content = load_json(info["file"]) or []
        data_stores[key] = existing_content

        verwerkt = set()
        for item in existing_content:
            if isinstance(item, dict) and "kenteken" in item:
                verwerkt.add(item["kenteken"])
        verwerkte_kentekens_per_api[key] = verwerkt

    te_verwerken = []
    for k in all_kentekens:
        if any(k not in verwerkte_kentekens_per_api[api_key] for api_key in API_ENDPOINTS):
            te_verwerken.append(k)

    te_verwerken = te_verwerken[:MAX_CHECKS]

    if not te_verwerken:
        print("Alle kentekens zijn voor alle 5 de RDW sub-bestanden al volledig opgehaald!", flush=True)
        return

    print(f"Start batch van {len(te_verwerken)} kentekens voor alle 5 sub-API's...", flush=True)

    for idx, kenteken in enumerate(te_verwerken, 1):
        print(f"[{idx}/{len(te_verwerken)}] Ophalen data voor kenteken: {kenteken}...", flush=True)

        for api_key, info in API_ENDPOINTS.items():
            if kenteken not in verwerkte_kentekens_per_api[api_key]:
                results = fetch_api_data(info["url"], kenteken)
                if results:
                    for res in results:
                        if "kenteken" not in res:
                            res["kenteken"] = kenteken
                        data_stores[api_key].append(res)
                else:
                    data_stores[api_key].append({"kenteken": kenteken, "no_data": True})

                verwerkte_kentekens_per_api[api_key].add(kenteken)

        time.sleep(0.05)

    for api_key, info in API_ENDPOINTS.items():
        save_json(info["file"], data_stores[api_key])
        print(f"Opgeslagen: {info['file']} ({len(data_stores[api_key])} records)", flush=True)

    print(f"\nKlaar! Batch van {len(te_verwerken)} kentekens verwerkt.", flush=True)


if __name__ == "__main__":
    run()