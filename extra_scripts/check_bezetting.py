import json
import os
import re
import sys
from collections import defaultdict
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

import check_tenaamstelling_changes

RAW_FILE = os.path.join(BASE_DIR, "raw", "hulpdienstvoertuigenbenelux_raw.json")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
REPORT_FILE = os.path.join(REPORTS_DIR, "check_bezetting_report.txt")
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
OUTPUT_JSON_FILE = os.path.join(STORAGE_DIR, "bezetting.json")
STATUS_JSON_FILE = os.path.join(STORAGE_DIR, "kenteken_status.json")

BEZETTING_RE = re.compile(r"bezetting\s*:\s*(\d+)\s*personen?", re.IGNORECASE)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_kenteken(value: Any) -> str:
    return _safe_text(value).replace("-", "").replace(" ", "").upper()


def _parse_int(value: Any) -> int | None:
    text = _safe_text(value)
    if not text:
        return None
    if text.isdigit():
        return int(text)
    return None


def _load_records() -> list[dict[str, Any]]:
    if not os.path.exists(RAW_FILE):
        raise FileNotFoundError(f"Raw input file niet gevonden: {RAW_FILE}")

    with open(RAW_FILE, encoding="utf-8") as infile:
        data = json.load(infile)

    if not isinstance(data, list):
        raise ValueError("Raw input heeft onverwacht formaat (verwacht lijst van objecten).")

    return [row for row in data if isinstance(row, dict)]


def _load_status_by_kenteken() -> dict[str, dict[str, Any]]:
    return _status_lookup(_load_status())


def _load_status() -> dict[str, dict[str, Any]]:
    if not os.path.exists(STATUS_JSON_FILE):
        return {}

    try:
        with open(STATUS_JSON_FILE, encoding="utf-8") as infile:
            status_data = json.load(infile)
    except Exception:
        return {}

    if not isinstance(status_data, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for kenteken, row in status_data.items():
        if isinstance(row, dict):
            result[kenteken] = row
    return result


def _save_status(status: dict[str, dict[str, Any]]) -> None:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with open(STATUS_JSON_FILE, "w", encoding="utf-8") as outfile:
        json.dump(status, outfile, indent=2, ensure_ascii=False)


def _status_lookup(status: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized_map: dict[str, dict[str, Any]] = {}
    for kenteken, row in status.items():
        normalized = _normalize_kenteken(kenteken)
        if normalized:
            normalized_map[normalized] = row
    return normalized_map


def _status_key_lookup(status: dict[str, dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for kenteken in status.keys():
        normalized = _normalize_kenteken(kenteken)
        if normalized:
            mapping[normalized] = kenteken
    return mapping


def _parse_bezetting(bijzonderheden: str) -> int | None:
    match = BEZETTING_RE.search(bijzonderheden)
    if not match:
        return None
    return int(match.group(1))


def _analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    with_bezetting = 0
    malformed = []
    out_of_range = []
    distribution: dict[int, int] = defaultdict(int)

    for row in records:
        bijzonderheden = _safe_text(row.get("Bijzonderheden"))
        if "bezetting" not in bijzonderheden.lower():
            continue

        parsed = _parse_bezetting(bijzonderheden)
        if parsed is None:
            malformed.append(row)
            continue

        with_bezetting += 1
        distribution[parsed] += 1

        if parsed < 1 or parsed > 12:
            out_of_range.append(row)

    return {
        "total": total,
        "with_bezetting": with_bezetting,
        "malformed": malformed,
        "out_of_range": out_of_range,
        "distribution": dict(sorted(distribution.items())),
    }


def _line_for_record(row: dict[str, Any]) -> str:
    hulpdienst = _safe_text(row.get("Hulpdienst"))
    roepnummer = _safe_text(row.get("Roepnummer"))
    kenteken = _safe_text(row.get("Kenteken"))
    adres = _safe_text(row.get("Adres"))
    bijzonderheden = _safe_text(row.get("Bijzonderheden"))
    return (
        f"- Hulpdienst={hulpdienst} | Roepnummer={roepnummer} | Kenteken={kenteken} | "
        f"Adres={adres} | Bijzonderheden={bijzonderheden}"
    )


def _extract_bezetting_rows(
    records: list[dict[str, Any]],
    status_by_kenteken: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    extracted: list[dict[str, Any]] = []
    for row in records:
        parsed = _parse_bezetting(_safe_text(row.get("Bijzonderheden")))
        if parsed is None:
            continue

        kenteken = _safe_text(row.get("Kenteken"))
        status_row = status_by_kenteken.get(_normalize_kenteken(kenteken), {})
        zitplaatsen = _parse_int(status_row.get("aantal_zitplaatsen"))

        extracted.append(
            {
                "roepnummer": _safe_text(row.get("Roepnummer")),
                "kenteken": kenteken,
                "bezetting": parsed,
                "zitplaatsen": zitplaatsen,
            }
        )

    return extracted


def _write_bezetting_json(rows: list[dict[str, Any]]) -> None:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    with open(OUTPUT_JSON_FILE, "w", encoding="utf-8") as outfile:
        json.dump(rows, outfile, indent=2, ensure_ascii=False)


def _collect_mismatches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        bezetting = row.get("bezetting")
        zitplaatsen = row.get("zitplaatsen")
        if bezetting is None or zitplaatsen is None:
            continue
        if bezetting != zitplaatsen:
            mismatches.append(row)
    return mismatches


def _collect_missing_zitplaatsen_kentekens(
    records: list[dict[str, Any]],
    status: dict[str, dict[str, Any]],
) -> list[str]:
    status_keys_by_normalized = _status_key_lookup(status)
    missing: list[str] = []
    seen: set[str] = set()

    for row in records:
        if _parse_bezetting(_safe_text(row.get("Bijzonderheden"))) is None:
            continue

        raw_kenteken = _safe_text(row.get("Kenteken"))
        normalized = _normalize_kenteken(raw_kenteken)
        if not normalized or normalized in seen:
            continue

        status_key = status_keys_by_normalized.get(normalized)
        if not status_key:
            continue

        status_row = status.get(status_key, {})
        if _parse_int(status_row.get("aantal_zitplaatsen")) is not None:
            continue

        missing.append(status_key)
        seen.add(normalized)

    return missing


def _enrich_missing_zitplaatsen(
    records: list[dict[str, Any]],
    status: dict[str, dict[str, Any]],
    max_checks: int | None,
) -> int:
    pending_kentekens = _collect_missing_zitplaatsen_kentekens(records, status)
    if max_checks is not None:
        max_checks = max(0, max_checks)
        pending_kentekens = pending_kentekens[:max_checks]

    if pending_kentekens:
        print(f"Start zitplaatsen-opvraging voor {len(pending_kentekens)} kentekens...")

    used_checks = 0
    for index, kenteken in enumerate(pending_kentekens, 1):
        print(f"[{index}/{len(pending_kentekens)}] Zitplaatsen check {kenteken}...")
        record = check_tenaamstelling_changes.fetch_rdw_record(kenteken)
        used_checks += 1
        if not record:
            continue

        zitplaatsen = check_tenaamstelling_changes.normalize_aantal_zitplaatsen(
            record.get("aantal_zitplaatsen")
        )
        status[kenteken]["aantal_zitplaatsen"] = zitplaatsen

    return used_checks


def _write_report(result: dict[str, Any], mismatches: list[dict[str, Any]]) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)

    width = 90
    sep = "=" * width
    thin = "-" * width

    with open(REPORT_FILE, "w", encoding="utf-8") as outfile:
        def w(line: str = "") -> None:
            outfile.write(line + "\n")

        w(sep)
        w("  Bezetting Check Report")
        w(sep)
        w(f"  Totaal records                  : {result['total']}")
        w(f"  Records met parsebare bezetting : {result['with_bezetting']}")
        w(f"  Onjuist geformatteerde bezetting: {len(result['malformed'])}")
        w(f"  Buiten range (1-12)             : {len(result['out_of_range'])}")
        w(f"  Bezetting != RDW zitplaatsen    : {len(mismatches)}")

        w()
        w(thin)
        w("  Verdeling bezetting")
        w(thin)
        if result["distribution"]:
            for size, count in result["distribution"].items():
                w(f"  {size:>2} personen : {count}")
        else:
            w("  (geen bezetting gevonden)")

        w()
        w(thin)
        w("  Onjuist geformatteerde bezetting")
        w(thin)
        if result["malformed"]:
            for row in result["malformed"]:
                w(_line_for_record(row))
        else:
            w("  (geen)")

        w()
        w(thin)
        w("  Buiten range (1-12)")
        w(thin)
        if result["out_of_range"]:
            for row in result["out_of_range"]:
                w(_line_for_record(row))
        else:
            w("  (geen)")

        w()
        w(thin)
        w("  Verschil: bezetting vs RDW zitplaatsen")
        w(thin)
        if mismatches:
            for row in mismatches:
                w(
                    "- Roepnummer="
                    f"{row.get('roepnummer', '')} | "
                    f"Kenteken={row.get('kenteken', '')} | "
                    f"Bezetting={row.get('bezetting')} | "
                    f"RDW zitplaatsen={row.get('zitplaatsen')}"
                )
        else:
            w("  (geen)")

        w()
        w(sep)


def run(max_checks: int | None = None) -> int:
    records = _load_records()
    status = _load_status()
    used_checks = _enrich_missing_zitplaatsen(records, status, max_checks=max_checks)
    _save_status(status)

    status_by_kenteken = _status_lookup(status)
    result = _analyze(records)
    bezetting_rows = _extract_bezetting_rows(records, status_by_kenteken)
    mismatches = _collect_mismatches(bezetting_rows)
    _write_report(result, mismatches)
    _write_bezetting_json(bezetting_rows)
    print(f"Bezetting rapport geschreven naar: {REPORT_FILE}")
    print(f"Bezetting JSON geschreven naar: {OUTPUT_JSON_FILE}")
    return used_checks


def main() -> None:
    run()


if __name__ == "__main__":
    main()
