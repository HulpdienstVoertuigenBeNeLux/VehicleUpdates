import json
import os
import shutil
import sys
from collections import defaultdict

REPORTS_DIR = "reports"
OUTPUT_FILENAME = "check_duplicates_report.txt"
OUTPUT_FILE = os.path.join(REPORTS_DIR, OUTPUT_FILENAME)
RAW_DIR = "raw"

FILES = {
    "NL": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_raw.json"),
    "BE": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_be_raw.json"),
    "LUX": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_lux_raw.json"),
}

INVALID_VALUES = {"", "geen", "onbekend", "-"}
RELEVANT_HULPDIENSTEN = {"brandweer", "ambulance", "rode kruis", "knrm", "reddingsbrigade"}

WIDTH = 80
SEP = "=" * WIDTH
THIN = "-" * WIDTH


def ensure_report_path() -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    root_path = OUTPUT_FILENAME
    if os.path.exists(root_path) and not os.path.exists(OUTPUT_FILE):
        shutil.move(root_path, OUTPUT_FILE)


def ensure_raw_paths() -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    for path in FILES.values():
        basename = os.path.basename(path)
        if os.path.exists(basename) and not os.path.exists(path):
            shutil.move(basename, path)


def header(text: str) -> str:
    return f"\n{SEP}\n  {text}\n{SEP}"


def section(text: str) -> str:
    return f"\n{THIN}\n  {text}\n{THIN}"


def find_duplicates(records: list, field: str) -> dict:
    seen = defaultdict(list)
    for i, record in enumerate(records):
        value = record.get(field, "").strip()
        if value.lower() in INVALID_VALUES:
            continue
        seen[value].append(i)
    return {value: indices for value, indices in seen.items() if len(indices) > 1}


def check_region(region: str, filepath: str, emit) -> bool:
    emit(header(f"Region: {region}"), console=True)

    if not os.path.exists(filepath):
        emit(f"\n  [!] File not found: {filepath}\n", console=True)
        return True

    with open(filepath, encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        emit("\n  [!] Unexpected file format.\n", console=True)
        return True

    filtered = [
        r for r in records
        if r.get("Hulpdienst", "").strip().lower() in RELEVANT_HULPDIENSTEN
    ]
    emit(f"\n  Records checked : {len(filtered):,}  (of {len(records):,} total)", console=True)

    found_any = False

    for field in ("Roepnummer", "Kenteken"):
        duplicates = find_duplicates(filtered, field)
        if not duplicates:
            emit(f"  {field:<14} : no duplicates", console=True)
            continue

        found_any = True
        section_line = section(f"Duplicate {field}  --  {len(duplicates)} value(s) affected")
        emit(section_line, console=True)

        for value, indices in sorted(duplicates.items()):
            emit(f"\n  >> {value!r}  ({len(indices)}x)", console=False)
            for idx in indices:
                r = filtered[idx]
                adres = r.get("Adres", "").strip()
                hulpdienst = r.get("Hulpdienst", "").strip()
                roepnummer = r.get("Roepnummer", "").strip()
                kenteken = r.get("Kenteken", "").strip()
                emit(f"      Adres       : {adres}", console=False)
                emit(f"      Hulpdienst  : {hulpdienst}", console=False)
                emit(f"      Roepnummer  : {roepnummer}", console=False)
                emit(f"      Kenteken    : {kenteken}", console=False)

    if not found_any:
        emit("\n  [OK] No duplicates found in Roepnummer or Kenteken.\n", console=True)

    return found_any


def main() -> None:
    ensure_report_path()
    ensure_raw_paths()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as report:
        def emit(line: str = "", console: bool = True) -> None:
            report.write(line + "\n")
            if console:
                try:
                    sys.__stdout__.write(line + "\n")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    enc = sys.__stdout__.encoding or "utf-8"
                    safe = (line + "\n").encode(enc, errors="replace").decode(enc)
                    sys.__stdout__.write(safe)

        emit(SEP, console=True)
        emit("  Duplicate Check Report", console=True)
        emit(f"  Filters   : {', '.join(sorted(RELEVANT_HULPDIENSTEN))}", console=True)
        emit(SEP, console=True)

        any_duplicates = False
        for region, filepath in FILES.items():
            if check_region(region, filepath, emit):
                any_duplicates = True

        emit(f"\n{SEP}", console=True)
        if any_duplicates:
            emit("  RESULT: Duplicates detected -- review the entries above.", console=True)
        else:
            emit("  RESULT: All regions clean -- no duplicates found.", console=True)
        emit(f"{SEP}\n", console=True)

    print(f"Report written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
