"""
cleanup.py — Maintenance tasks for VehicleUpdates storage files.

Tasks performed:
  1. Remove `not_found` placeholder entries from rdw_raw.json.
  2. Move rdw_raw.json entries for kentekens not in kenteken_status.json to storage/archive/rdw_raw.json.
  3. Move sub-API records (brandstof, assen, carrosserie, etc.) for kentekens not in
     kenteken_status.json to their matching storage/archive/ files.
"""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
ARCHIVE_DIR = os.path.join(STORAGE_DIR, "archive")

RDW_RAW_FILE = os.path.join(STORAGE_DIR, "rdw_raw.json")
ARCHIVE_RDW_RAW_FILE = os.path.join(ARCHIVE_DIR, "rdw_raw.json")
KENTEKEN_STATUS_FILE = os.path.join(STORAGE_DIR, "kenteken_status.json")

SUBDATA_FILENAMES = [
    "rdw_brandstof.json",
    "rdw_assen.json",
    "rdw_carrosserie.json",
    "rdw_carrosserie_specifiek.json",
    "rdw_voertuigklasse.json",
]


def load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# Normalize to strip dashes/spaces so HNX-16-X matches HNX16X
def normalize_kenteken(k: str) -> str:
    return k.replace("-", "").replace(" ", "").upper()


def dedupe_records(records: list) -> list:
    """Drop exact duplicates and records that are a strict subset of a richer one."""
    unique: list = []
    for item in records:
        if not isinstance(item, dict):
            unique.append(item)
            continue
        item_items = set(item.items())
        if any(isinstance(other, dict) and item_items <= set(other.items()) for other in unique):
            continue
        unique = [
            other for other in unique
            if not (isinstance(other, dict) and set(other.items()) <= item_items)
        ]
        unique.append(item)
    return unique


def load_tracked_kentekens() -> set[str]:
    status = load_json(KENTEKEN_STATUS_FILE)
    return {normalize_kenteken(k) for k in status.keys()} if isinstance(status, dict) else set()


def cleanup_rdw_raw() -> None:
    raw = load_json(RDW_RAW_FILE)
    if not isinstance(raw, dict):
        print("rdw_raw.json niet gevonden of ongeldig formaat — overgeslagen.")
        return

    tracked_normalized = load_tracked_kentekens()

    # Load existing archive to merge into it
    existing_archive = load_json(ARCHIVE_RDW_RAW_FILE)
    archive: dict = existing_archive if isinstance(existing_archive, dict) else {}

    original_count = len(raw)
    removed_placeholder = []
    archived = []

    cleaned: dict = {}
    for kenteken, record in raw.items():
        if not isinstance(record, dict):
            continue

        if record.get("rdw_lookup_result") == "not_found":
            removed_placeholder.append(kenteken)
            continue

        if tracked_normalized and normalize_kenteken(kenteken) not in tracked_normalized:
            archived.append(kenteken)
            archive[kenteken] = record
            continue

        cleaned[kenteken] = record

    if removed_placeholder:
        print(f"Verwijderd uit rdw_raw: {len(removed_placeholder)} 'not_found' placeholder(s).")
        for k in removed_placeholder:
            print(f"  - {k}")

    if archived:
        sorted_archive = {k: archive[k] for k in sorted(archive.keys())}
        save_json(ARCHIVE_RDW_RAW_FILE, sorted_archive)
        print(f"Gearchiveerd: {len(archived)} kenteken(s) verplaatst naar archive/rdw_raw.json.")
        for k in archived:
            print(f"  - {k}")

    total_removed = original_count - len(cleaned)
    if total_removed > 0:
        save_json(RDW_RAW_FILE, {k: cleaned[k] for k in sorted(cleaned.keys())})
        print(f"rdw_raw.json bijgewerkt: {original_count} -> {len(cleaned)} entries.")
    else:
        print("rdw_raw.json: niets te verwijderen.")


def cleanup_subdata_file(filename: str, tracked_normalized: set[str]) -> None:
    path = os.path.join(STORAGE_DIR, filename)
    archive_path = os.path.join(ARCHIVE_DIR, filename)

    records = load_json(path)
    if not isinstance(records, list):
        print(f"{filename} niet gevonden of ongeldig formaat — overgeslagen.")
        return

    original_count = len(records)
    # Stale placeholders fetched under the old dashed kenteken format; current kentekens are undashed.
    stale_dashed = sum(
        1 for r in records if isinstance(r, dict) and "-" in str(r.get("kenteken", ""))
    )
    records = [
        r for r in records
        if not (isinstance(r, dict) and "-" in str(r.get("kenteken", "")))
    ]
    if stale_dashed:
        print(f"{filename}: {stale_dashed} verouderd(e) record(s) met streepje-kenteken verwijderd.")

    grouped: dict[str, list] = {}
    for record in records:
        key = str(record.get("kenteken", "")).strip() if isinstance(record, dict) else ""
        grouped.setdefault(key, []).append(record)
    pre_dedupe_count = len(records)
    records = [record for key in grouped for record in dedupe_records(grouped[key])]
    if len(records) != pre_dedupe_count:
        print(f"{filename}: {pre_dedupe_count - len(records)} duplicaat/subset record(s) verwijderd.")

    existing_archive = load_json(archive_path)
    archive: list = existing_archive if isinstance(existing_archive, list) else []

    kept = []
    archived = []
    for record in records:
        if not isinstance(record, dict):
            kept.append(record)
            continue

        kenteken = str(record.get("kenteken", "")).strip()
        if tracked_normalized and kenteken and normalize_kenteken(kenteken) not in tracked_normalized:
            archived.append(record)
        else:
            kept.append(record)

    if archived:
        archive.extend(archived)
        save_json(archive_path, archive)
        archived_kentekens = sorted({str(r.get("kenteken", "")) for r in archived})
        print(
            f"Gearchiveerd uit {filename}: {len(archived)} record(s) voor "
            f"{len(archived_kentekens)} kenteken(s)."
        )

    if len(kept) != original_count:
        save_json(path, kept)
        print(f"{filename} bijgewerkt: {original_count} -> {len(kept)} entries.")
    else:
        print(f"{filename}: niets te verwijderen.")


def cleanup_subdata() -> None:
    tracked_normalized = load_tracked_kentekens()
    for filename in SUBDATA_FILENAMES:
        cleanup_subdata_file(filename, tracked_normalized)


def main() -> None:
    print("=== Cleanup ===")
    cleanup_rdw_raw()
    cleanup_subdata()
    print("=== Klaar ===")


if __name__ == "__main__":
    main()
