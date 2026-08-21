import os
import json

STORAGE_DIR = "storage"
RDW_RAW_FILE = os.path.join(STORAGE_DIR, "rdw_raw.json")
BRANDSTOF_FILE = os.path.join(STORAGE_DIR, "rdw_brandstof.json")
COMBINED_FILE = "rdw_combined.json"


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


def run():
    raw_data = load_json(RDW_RAW_FILE)
    if not raw_data:
        print(f"Kan {RDW_RAW_FILE} niet vinden.")
        return

    brandstof_list = load_json(BRANDSTOF_FILE) or []

    brandstof_map = {}
    for item in brandstof_list:
        if isinstance(item, dict) and "kenteken" in item:
            k = item["kenteken"]
            if k not in brandstof_map and "brandstof_omschrijving" in item:
                brandstof_map[k] = item["brandstof_omschrijving"]

    # Converteer raw_data naar lijst
    voertuigen = []
    if isinstance(raw_data, dict):
        for key, val in raw_data.items():
            if isinstance(val, dict):
                item = dict(val)
                if "kenteken" not in item:
                    item["kenteken"] = key
                voertuigen.append(item)
            else:
                voertuigen.append({"kenteken": key})
    elif isinstance(raw_data, list):
        for item in raw_data:
            if isinstance(item, dict):
                voertuigen.append(item)
            elif isinstance(item, str):
                voertuigen.append({"kenteken": item})

    combined_results = []
    for v in voertuigen:
        kenteken = str(v.get("kenteken", "")).strip()
        datum_toelating = str(v.get("datum_eerste_toelating", ""))
        bouwjaar = datum_toelating[:4] if len(datum_toelating) >= 4 else None

        combined_item = {
            "kenteken": kenteken,
            "merk": v.get("merk"),
            "handelsbenaming": v.get("handelsbenaming"),
            "aantal_zitplaatsen": v.get("aantal_zitplaatsen"),
            "bouwjaar": bouwjaar,
            "brandstof_omschrijving": brandstof_map.get(kenteken),
        }
        combined_results.append(combined_item)

    save_json(COMBINED_FILE, combined_results)
    print(f"Succesvol {len(combined_results)} voertuigen gecombineerd naar {COMBINED_FILE}!")


if __name__ == "__main__":
    run()