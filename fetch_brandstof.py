import os
import json
import time
import requests

STORAGE_DIR = "storage"
RDW_RAW_FILE = os.path.join(STORAGE_DIR, "rdw_raw.json")
BRANDSTOF_FILE = os.path.join(STORAGE_DIR, "rdw_brandstof.json")
MAX_CHECKS = 100
RDW_REQUEST_TIMEOUT_SECONDS = int(os.getenv("RDW_REQUEST_TIMEOUT_SECONDS", "6"))


def load_json(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return None


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_kentekens(raw_data):
    """Ondersteunt zowel een lijst met dicts als een dictionary van voertuigen."""
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


def fetch_brandstof_info(kenteken):
    clean_kenteken = kenteken.replace("-", "").upper()
    url = f"https://opendata.rdw.nl/resource/8ys7-d773.json?kenteken={clean_kenteken}"
    try:
        response = requests.get(url, timeout=RDW_REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 200:
            data = response.json()
            if data:
                return data[0].get("brandstof_omschrijving")
        return None
    except requests.RequestException as e:
        print(f"Fout bij ophalen brandstof voor {kenteken}: {e}")
        return None


def run():
    raw_data = load_json(RDW_RAW_FILE)
    if not raw_data:
        print(f"Kan {RDW_RAW_FILE} niet vinden of het bestand is leeg.")
        return

    all_kentekens = extract_kentekens(raw_data)
    print(f"Totaal {len(all_kentekens)} unieke kentekens in {RDW_RAW_FILE}.")

    # Laad bestaande brandstofdata
    existing_brandstof = load_json(BRANDSTOF_FILE) or []
    brandstof_map = {item["kenteken"]: item.get("brandstof_omschrijving") for item in existing_brandstof if isinstance(item, dict) and "kenteken" in item}

    # Welke kentekens moeten nog gehaald worden?
    te_verwerken = [k for k in all_kentekens if k not in brandstof_map][:MAX_CHECKS]

    if not te_verwerken:
        print("Alle kentekens zijn al voorzien van brandstofdata!")
        return

    print(f"Start ophalen brandstof voor batch van {len(te_verwerken)} kentekens...")

    for idx, kenteken in enumerate(te_verwerken, 1):
        print(f"[{idx}/{len(te_verwerken)}] Brandstof ophalen voor: {kenteken}...")
        brandstof = fetch_brandstof_info(kenteken)
        brandstof_map[kenteken] = brandstof
        time.sleep(0.1)

    # Opslaan in rdw_brandstof.json
    output_brandstof = [
        {"kenteken": k, "brandstof_omschrijving": v}
        for k, v in brandstof_map.items()
    ]
    save_json(BRANDSTOF_FILE, output_brandstof)

    print(f"Klaar! {len(te_verwerken)} kentekens verwerkt. Totaal in {BRANDSTOF_FILE}: {len(output_brandstof)}/{len(all_kentekens)}")


if __name__ == "__main__":
    run()