import json
import os
from typing import Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_FILE = os.path.join(BASE_DIR, "raw", "hulpdienstvoertuigenbenelux_raw.json")
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
LEGACY_DATA_DIR = os.path.join(BASE_DIR, "Data")
RDW_BRANDWEER_FILE = os.path.join(STORAGE_DIR, "Brandweer_rdw.json")
RDW_AMBULANCE_FILE = os.path.join(STORAGE_DIR, "Ambulance_rdw.json")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
LEGACY_TEXT_REPORT_FILE = os.path.join(REPORTS_DIR, "rdw_inrichting_mismatch_report.txt")
LEGACY_JSON_REPORT_FILE = os.path.join(REPORTS_DIR, "rdw_inrichting_mismatch.json")

INVALID_KENTEKENS = {"", "GEEN", "ONBEKEND", "-"}


def _normalize_kenteken(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.replace("-", "").replace(" ", "")


def _is_valid_kenteken(value: Any) -> bool:
    normalized = _normalize_kenteken(value)
    return normalized not in INVALID_KENTEKENS and normalized != ""


def _load_json_list(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []

    try:
        with open(path, encoding="utf-8") as infile:
            data = json.load(infile)
    except Exception:
        return []

    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _extract_hvb_kentekens(records: list[dict[str, Any]], hulpdienst: str) -> set[str]:
    result: set[str] = set()
    for row in records:
        if str(row.get("Hulpdienst", "")).strip().lower() != hulpdienst:
            continue
        kenteken = row.get("Kenteken")
        if _is_valid_kenteken(kenteken):
            result.add(_normalize_kenteken(kenteken))
    return result


def _extract_rdw_kentekens(records: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for row in records:
        kenteken = row.get("kenteken")
        if _is_valid_kenteken(kenteken):
            result.add(_normalize_kenteken(kenteken))
    return result


def _compare_group(
    naam: str,
    hvb_kentekens: set[str],
    rdw_kentekens: set[str],
) -> dict[str, Any]:
    missing_in_rdw = sorted(hvb_kentekens - rdw_kentekens)
    extra_in_rdw = sorted(rdw_kentekens - hvb_kentekens)

    return {
        "naam": naam,
        "hvb_count": len(hvb_kentekens),
        "rdw_count": len(rdw_kentekens),
        "missing_in_rdw": missing_in_rdw,
        "extra_in_rdw": extra_in_rdw,
    }


def _text_report_path(naam: str) -> str:
    return os.path.join(REPORTS_DIR, f"rdw_inrichting_mismatch_{naam}.txt")


def _json_report_path(naam: str) -> str:
    return os.path.join(STORAGE_DIR, f"rdw_inrichting_mismatch_{naam}.json")


def _migrate_legacy_data_files() -> None:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    for filename in (
        "Brandweer_rdw.json",
        "Ambulance_rdw.json",
        "rdw_inrichting_mismatch_brandweer.json",
        "rdw_inrichting_mismatch_ambulance.json",
    ):
        legacy_path = os.path.join(LEGACY_DATA_DIR, filename)
        storage_path = os.path.join(STORAGE_DIR, filename)
        if os.path.exists(legacy_path) and not os.path.exists(storage_path):
            os.replace(legacy_path, storage_path)


def _cleanup_legacy_reports() -> None:
    for path in (
        LEGACY_TEXT_REPORT_FILE,
        LEGACY_JSON_REPORT_FILE,
        os.path.join(REPORTS_DIR, "rdw_inrichting_mismatch_brandweer.json"),
        os.path.join(REPORTS_DIR, "rdw_inrichting_mismatch_ambulance.json"),
    ):
        if os.path.exists(path):
            os.remove(path)


def _write_text_report(result: dict[str, Any]) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    output_path = _text_report_path(result["naam"])
    width = 88
    sep = "=" * width
    thin = "-" * width

    with open(output_path, "w", encoding="utf-8") as outfile:
        def w(line: str = "") -> None:
            outfile.write(line + "\n")

        missing = result["missing_in_rdw"]
        extra = result["extra_in_rdw"]

        w(sep)
        w(f"  RDW Inrichting Mismatch Report - {result['naam'].capitalize()}")
        w(sep)

        w()
        w(thin)
        w(f"  {result['naam'].capitalize()}")
        w(thin)
        w(f"  HulpdienstvoertuigenBenelux : {result['hvb_count']}")
        w(f"  RDW inrichting dataset      : {result['rdw_count']}")
        w(f"  Missing in RDW              : {len(missing)}")
        w(f"  Extra in RDW                : {len(extra)}")

        w("\n  Kentekens in HulpdienstvoertuigenBenelux maar niet in RDW:")
        if missing:
            for kenteken in missing:
                w(f"  - {kenteken}")
        else:
            w("  - (geen)")

        w("\n  Kentekens in RDW maar niet in HulpdienstvoertuigenBenelux:")
        if extra:
            for kenteken in extra:
                w(f"  - {kenteken}")
        else:
            w("  - (geen)")

        w()
        w(sep)

    return output_path


def _write_json_report(result: dict[str, Any]) -> str:
    os.makedirs(STORAGE_DIR, exist_ok=True)
    output_path = _json_report_path(result["naam"])
    with open(output_path, "w", encoding="utf-8") as outfile:
        json.dump(result, outfile, indent=2, ensure_ascii=False)
    return output_path


def generate_all_reports() -> dict[str, Any]:
    _migrate_legacy_data_files()
    _cleanup_legacy_reports()

    hvb_records = _load_json_list(RAW_FILE)
    rdw_brandweer_records = _load_json_list(RDW_BRANDWEER_FILE)
    rdw_ambulance_records = _load_json_list(RDW_AMBULANCE_FILE)

    brandweer_result = _compare_group(
        "brandweer",
        _extract_hvb_kentekens(hvb_records, "brandweer"),
        _extract_rdw_kentekens(rdw_brandweer_records),
    )
    ambulance_result = _compare_group(
        "ambulance",
        _extract_hvb_kentekens(hvb_records, "ambulance"),
        _extract_rdw_kentekens(rdw_ambulance_records),
    )

    results = [brandweer_result, ambulance_result]
    text_reports = [_write_text_report(result) for result in results]
    json_reports = [_write_json_report(result) for result in results]

    for path in text_reports:
        print(f"RDW mismatch rapport geschreven naar: {path}")
    for path in json_reports:
        print(f"RDW mismatch JSON geschreven naar: {path}")

    return {
        "text_reports": text_reports,
        "json_reports": json_reports,
        "results": results,
    }


if __name__ == "__main__":
    generate_all_reports()
