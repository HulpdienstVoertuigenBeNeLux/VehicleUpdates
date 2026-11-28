"""Zet de RDW- en luchtvaartrecords klaar in de repo, zodat de site ze zelf ophaalt.

Vervangt het pushen naar de site (rdw_push.py): de hosting zet runner-IP's soms achter een
botcontrole, maar de site kan GitHub altijd bereiken. Uitvoer (alleen bij wijzigingen anders):
  site_sync/rdw/index.json          {"version": 1, "records": {kenteken: [controlegetal, shard]}}
  site_sync/rdw/shards/<xx>.json    records per shard (xx = eerste 2 tekens van md5(kenteken))
De site (WebsiteData/utils/siteSync.js) vergelijkt de controlegetallen met rdw_data en haalt
alleen shards op met afwijkende records. Het controlegetal is rdw_hash() uit rdw_push.py,
gelijk aan rdwHash() op de site.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rdw_push import (  # noqa: E402
    _aircraft_record_to_api_format,
    load_aircraft_data,
    load_full_combined,
    rdw_hash,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT_ROOT, "site_sync", "rdw")
SHARD_DIR = os.path.join(OUT_DIR, "shards")


def shard_of(kenteken: str) -> str:
    return hashlib.md5(kenteken.encode("utf-8")).hexdigest()[:2]


def collect() -> dict[str, dict]:
    """kenteken -> record zoals de site het opslaat (zelfde keuze als rdw_push.py)."""
    records: dict[str, dict] = {}
    for record in load_full_combined():
        if isinstance(record, dict) and str(record.get("kenteken") or "").strip():
            records[str(record["kenteken"]).strip()] = record
    for record in load_aircraft_data():
        if isinstance(record, dict):
            formatted = _aircraft_record_to_api_format(record)
            kenteken = str(formatted.get("kenteken") or "").strip()
            if kenteken:
                formatted["kenteken"] = kenteken
                records[kenteken] = formatted
    return records


def write_if_changed(path: str, data) -> bool:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def main() -> None:
    records = collect()
    if not records:
        print("Geen records gevonden; site_sync niet bijgewerkt.")
        return

    index: dict[str, list[str]] = {}
    shards: dict[str, list[dict]] = {}
    for kenteken in sorted(records):
        record = dict(records[kenteken], kenteken=kenteken)
        shard = shard_of(kenteken)
        index[kenteken] = [rdw_hash(record), shard]
        shards.setdefault(shard, []).append(record)

    os.makedirs(SHARD_DIR, exist_ok=True)
    changed = sum(write_if_changed(os.path.join(SHARD_DIR, f"{s}.json"), shards[s]) for s in sorted(shards))
    removed = 0
    for name in os.listdir(SHARD_DIR):
        if name.endswith(".json") and name[:-5] not in shards:
            os.remove(os.path.join(SHARD_DIR, name))
            removed += 1
    index_changed = write_if_changed(os.path.join(OUT_DIR, "index.json"), {"version": 1, "records": index})

    print(f"site_sync/rdw: {len(index)} records in {len(shards)} shards; "
          f"{changed} shards bijgewerkt, {removed} verwijderd, index {'bijgewerkt' if index_changed else 'ongewijzigd'}")


if __name__ == "__main__":
    main()
