"""Combine rdw_raw.json with all RDW sub-data files (assen, brandstof, carrosserie,
carrosserie_specifiek, voertuigklasse) into a single JSON list, one object per kenteken,
with the kenteken formatted the same way as in the hulpdienstvoertuigenbenelux sheets.

Example:
    python extra_scripts/combine_rdw_subdata.py
    -> writes storage/rdw_full_combined.json (a list).
"""
import json
import os
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
STORAGE_DIR = os.path.join(PROJECT_ROOT, "storage")
RAW_DIR = os.path.join(PROJECT_ROOT, "raw")
RDW_RAW_FILE = os.path.join(STORAGE_DIR, "rdw_raw.json")
OUTPUT_FILE = os.path.join(STORAGE_DIR, "rdw_full_combined.json")

# Source file where kentekens are stored in their original, correctly dashed format.
# Only the NL source has RDW data; BE/DE/LUX vehicles aren't Dutch-registered.
HULPDIENST_RAW_FILES = [
    os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_raw.json"),
]

SUBDATA_FILES = {
    "brandstof": os.path.join(STORAGE_DIR, "rdw_brandstof.json"),
    "assen": os.path.join(STORAGE_DIR, "rdw_assen.json"),
    "carrosserie": os.path.join(STORAGE_DIR, "rdw_carrosserie.json"),
    "carrosserie_specifiek": os.path.join(STORAGE_DIR, "rdw_carrosserie_specifiek.json"),
    "voertuigklasse": os.path.join(STORAGE_DIR, "rdw_voertuigklasse.json"),
}

# The destination API truncates field/column names longer than this, so keys are
# shortened here to match and stay unique.
MAX_KEY_LENGTH = 60


def load_json(filepath: str) -> Any:
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, encoding="utf-8") as infile:
            return json.load(infile)
    except json.JSONDecodeError:
        return None


def save_json(filepath: str, data: Any) -> None:
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as outfile:
        json.dump(data, outfile, indent=2, ensure_ascii=False)


def _format_kenteken(kenteken: str) -> str:
    """Insert dashes at letter/digit transitions, e.g. 00BBK3 -> 00-BBK-3."""
    clean = str(kenteken or "").replace("-", "").replace(" ", "").upper()
    if not clean:
        return clean

    groups = [clean[0]]
    for char in clean[1:]:
        if char.isdigit() == groups[-1][-1].isdigit():
            groups[-1] += char
        else:
            groups.append(char)
    return "-".join(groups)


def _normalize_kenteken(kenteken: str) -> str:
    return str(kenteken or "").replace("-", "").replace(" ", "").upper()


def _load_original_kentekens() -> dict[str, str]:
    """Map normalized (dashless) kenteken -> original kenteken as scraped, dashes included."""
    original_by_normalized: dict[str, str] = {}
    for filepath in HULPDIENST_RAW_FILES:
        data = load_json(filepath)
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            original = str(item.get("Kenteken", "")).strip()
            if not original:
                continue
            original_by_normalized[_normalize_kenteken(original)] = original
    return original_by_normalized


def _normalize_raw(raw_data: Any) -> dict[str, dict[str, Any]]:
    """rdw_raw.json is a dict keyed by kenteken, but tolerate a list as well."""
    if isinstance(raw_data, dict):
        return {k: v for k, v in raw_data.items() if isinstance(v, dict)}
    if isinstance(raw_data, list):
        return {
            str(item.get("kenteken", "")).strip(): item
            for item in raw_data
            if isinstance(item, dict) and item.get("kenteken")
        }
    return {}


def _unique_truncate(key: str, existing: dict[str, Any], max_length: int) -> str:
    """Shorten key to max_length, appending a numeric suffix if that collides with an existing key."""
    if len(key) <= max_length:
        return key

    truncated = key[:max_length]
    if truncated not in existing:
        return truncated

    suffix = 1
    while True:
        suffix_str = f"~{suffix}"
        candidate = key[: max_length - len(suffix_str)] + suffix_str
        if candidate not in existing:
            return candidate
        suffix += 1


def _flatten_records(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Turn a list of sub-data records into numbered scalar fields, e.g. assen_1_hefas."""
    flat: dict[str, Any] = {}
    for index, record in enumerate(records, start=1):
        for key, value in record.items():
            full_key = f"{name}_{index}_{key}"
            flat[_unique_truncate(full_key, flat, MAX_KEY_LENGTH)] = value
    return flat


def _group_by_kenteken(records: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in records or []:
        if not isinstance(item, dict) or "kenteken" not in item:
            continue
        if item.get("no_data"):
            continue
        kenteken = str(item["kenteken"]).strip()
        record = {k: v for k, v in item.items() if k != "kenteken"}
        grouped.setdefault(kenteken, []).append(record)
    return grouped


def run() -> None:
    raw_map = _normalize_raw(load_json(RDW_RAW_FILE))
    if not raw_map:
        print(f"Kan {RDW_RAW_FILE} niet vinden of het bestand is leeg.", flush=True)
        return

    original_kentekens = _load_original_kentekens()

    subdata_grouped = {
        name: _group_by_kenteken(load_json(path))
        for name, path in SUBDATA_FILES.items()
    }

    combined: list[dict[str, Any]] = []
    for kenteken, base in raw_map.items():
        # Drop the API links since the actual sub-data is embedded below.
        entry = {k: v for k, v in base.items() if not k.startswith("api_gekentekende_voertuigen")}
        normalized = _normalize_kenteken(kenteken)
        # Prefer the original scraped kenteken (correct dashes) over re-deriving them.
        entry["kenteken"] = original_kentekens.get(normalized) or _format_kenteken(kenteken)
        for name, grouped in subdata_grouped.items():
            entry.update(_flatten_records(name, grouped.get(kenteken, [])))
        combined.append(entry)

    # Ensure every entry has the same keys, so vehicles without certain sub-data
    # get an explicit null instead of the field being absent entirely.
    all_keys: set[str] = set()
    for entry in combined:
        all_keys.update(entry)
    for entry in combined:
        for key in all_keys:
            entry.setdefault(key, None)

    save_json(OUTPUT_FILE, combined)
    print(f"Succesvol {len(combined)} voertuigen gecombineerd naar {OUTPUT_FILE}!", flush=True)


if __name__ == "__main__":
    run()
