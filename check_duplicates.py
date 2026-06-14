import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime

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


def check_region(region: str, filepath: str) -> bool:
    print(header(f"Region: {region}"))

    if not os.path.exists(filepath):
        print(f"\n  [!] File not found: {filepath}\n")
        return True

    with open(filepath, encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        print("\n  [!] Unexpected file format.\n")
        return True

    filtered = [
        r for r in records
        if r.get("Hulpdienst", "").strip().lower() in RELEVANT_HULPDIENSTEN
    ]
    print(f"\n  Records checked : {len(filtered):,}  (of {len(records):,} total)")

    found_any = False

    for field in ("Roepnummer", "Kenteken"):
        duplicates = find_duplicates(filtered, field)
        if not duplicates:
            print(f"  {field:<14} : no duplicates")
            continue

        found_any = True
        print(section(f"Duplicate {field}  --  {len(duplicates)} value(s) affected"))

        for value, indices in sorted(duplicates.items()):
            print(f"\n  >> {value!r}  ({len(indices)}x)")
            for idx in indices:
                r = filtered[idx]
                adres = r.get("Adres", "").strip()
                hulpdienst = r.get("Hulpdienst", "").strip()
                roepnummer = r.get("Roepnummer", "").strip()
                kenteken = r.get("Kenteken", "").strip()
                print(f"      Adres       : {adres}")
                print(f"      Hulpdienst  : {hulpdienst}")
                print(f"      Roepnummer  : {roepnummer}")
                print(f"      Kenteken    : {kenteken}")

    if not found_any:
        print("\n  [OK] No duplicates found in Roepnummer or Kenteken.\n")

    return found_any


def main() -> None:
    ensure_report_path()
    ensure_raw_paths()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as report:
        class Tee:
            def write(self, msg: str) -> None:
                try:
                    sys.__stdout__.write(msg)
                except (UnicodeEncodeError, UnicodeDecodeError):
                    enc = sys.__stdout__.encoding or "utf-8"
                    sys.__stdout__.write(msg.encode(enc, errors="replace").decode(enc))
                report.write(msg)

            def flush(self) -> None:
                sys.__stdout__.flush()
                report.flush()

        sys.stdout = Tee()
        try:
            print(SEP)
            print("  Duplicate Check Report")
            print(f"  Generated : {generated_at}")
            print(f"  Filters   : {', '.join(sorted(RELEVANT_HULPDIENSTEN))}")
            print(SEP)

            any_duplicates = False
            for region, filepath in FILES.items():
                if check_region(region, filepath):
                    any_duplicates = True

            print(f"\n{SEP}")
            if any_duplicates:
                print("  RESULT: Duplicates detected -- review the entries above.")
            else:
                print("  RESULT: All regions clean -- no duplicates found.")
            print(f"{SEP}\n")
        finally:
            sys.stdout = sys.__stdout__

    print(f"Report written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
