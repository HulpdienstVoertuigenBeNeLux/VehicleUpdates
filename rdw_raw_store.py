import atexit
import json
import os
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
RDW_RAW_FILE = os.path.join(STORAGE_DIR, "rdw_raw.json")
SAVE_EVERY_UPDATES = int(os.getenv("RDW_RAW_SAVE_EVERY", "50"))

_cache: dict[str, dict[str, Any]] = {}
_loaded = False
_dirty_updates = 0


def _normalize_kenteken(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.replace("-", "").replace(" ", "")


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
        _cache = {
            _normalize_kenteken(k): v
            for k, v in data.items()
            if _normalize_kenteken(k) and isinstance(v, dict)
        }
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
        kenteken = _normalize_kenteken(record.get("kenteken"))
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
