import atexit
import json
import os
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
STORAGE_DIR = os.path.join(PROJECT_ROOT, "storage")
RDW_RAW_FILE = os.path.join(STORAGE_DIR, "rdw_raw.json")
SAVE_EVERY_UPDATES = int(os.getenv("RDW_RAW_SAVE_EVERY", "50"))

_cache: dict[str, dict[str, Any]] = {}
_loaded = False
_dirty_updates = 0


def _kenteken_key(value: Any) -> str:
    return str(value or "").strip()


def _load_cache() -> None:
    global _loaded, _cache
    if _loaded:
        return

    _loaded = True
    if not os.path.exists(RDW_RAW_FILE):
        _cache = {}
        return

    try:
        with open(RDW_RAW_FILE, encoding="utf-8") as infile:
            data = json.load(infile)
    except Exception:
        _cache = {}
        return

    if isinstance(data, dict):
        migrated: dict[str, dict[str, Any]] = {}
        for key, record in data.items():
            if not isinstance(record, dict):
                continue

            record_kenteken = _kenteken_key(record.get("kenteken"))
            source_key = record_kenteken or _kenteken_key(key)
            if not source_key:
                continue

            migrated[source_key] = record

        _cache = migrated
    else:
        _cache = {}


def _save_cache() -> None:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    sorted_cache = {kenteken: _cache[kenteken] for kenteken in sorted(_cache.keys())}
    with open(RDW_RAW_FILE, "w", encoding="utf-8") as outfile:
        json.dump(sorted_cache, outfile, indent=2, ensure_ascii=False)


def upsert_records(records: list[dict[str, Any]]) -> int:
    global _dirty_updates
    _load_cache()

    updates = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        kenteken = _kenteken_key(record.get("kenteken"))
        if not kenteken:
            continue

        previous = _cache.get(kenteken)
        if previous != record:
            _cache[kenteken] = record
            updates += 1

    if updates > 0:
        _dirty_updates += updates
        if _dirty_updates >= max(1, SAVE_EVERY_UPDATES):
            _save_cache()
            _dirty_updates = 0

    return updates


def upsert_record(record: dict[str, Any]) -> bool:
    return upsert_records([record]) > 0


def flush() -> None:
    global _dirty_updates
    _load_cache()
    if _dirty_updates > 0:
        _save_cache()
        _dirty_updates = 0


atexit.register(flush)
