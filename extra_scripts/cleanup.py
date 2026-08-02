"""
cleanup.py — Maintenance tasks for VehicleUpdates storage files.

Tasks performed:
  1. Remove `not_found` placeholder entries from rdw_raw.json.
  2. Move rdw_raw.json entries for kentekens not in kenteken_status.json to storage/archive/rdw_raw.json.
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


def load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cleanup_rdw_raw() -> None:
    raw = load_json(RDW_RAW_FILE)
    if not isinstance(raw, dict):
        print("rdw_raw.json niet gevonden of ongeldig formaat — overgeslagen.")
        return

    status = load_json(KENTEKEN_STATUS_FILE)
    # Normalize to strip dashes/spaces so HNX-16-X matches HNX16X
    def normalize(k: str) -> str:
        return k.replace("-", "").replace(" ", "").upper()

    tracked_normalized: set[str] = (
        {normalize(k) for k in status.keys()} if isinstance(status, dict) else set()
    )

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

        if tracked_normalized and normalize(kenteken) not in tracked_normalized:
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


def main() -> None:
    print("=== Cleanup ===")
    cleanup_rdw_raw()
    print("=== Klaar ===")


if __name__ == "__main__":
    main()
