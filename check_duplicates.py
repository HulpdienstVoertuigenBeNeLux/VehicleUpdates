import csv
import io
import json
import os
import shutil
import sys
import time
from collections import defaultdict

import requests

REPORTS_DIR = "reports"
OUTPUT_FILENAME = "check_duplicates_report.txt"
OUTPUT_FILE = os.path.join(REPORTS_DIR, OUTPUT_FILENAME)
RAW_DIR = "raw"
STORAGE_DIR = "storage"
EXCEPTIONS_FILE = os.path.join(STORAGE_DIR, "uitzonderingen.json")

FILES = {
    "NL": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_raw.json"),
    "BE": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_be_raw.json"),
    "LUX": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_lux_raw.json"),
}

# Discord: alleen NIEUWE dubbelen (secret DISCORD_WEBHOOK_URL_ZITPLAATSEN, in de workflow als DUPLICATES_WEBHOOK
# doorgegeven zodat check_bezetting.py in de uurlijkse run stil blijft). Wat al gemeld is: landen/<LAND>/dubbelen_gemeld.json
WEBHOOK = os.getenv("DUPLICATES_WEBHOOK")
STATE_FILE = os.path.join("landen", "{land}", "dubbelen_gemeld.json")
MAX_IN_EMBED = 10
MELD_LANDEN = {"NL"}   # BE/LUX alleen in het rapport (BE heeft veel korte roepnummers per zone)

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


def load_exceptions() -> dict[str, set[str]]:
    default = {"Roepnummer": set(), "Kenteken": set()}
    if not os.path.exists(EXCEPTIONS_FILE):
        return default

    try:
        with open(EXCEPTIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default

    if not isinstance(data, dict):
        return default

    roep = data.get("roepnummer", [])
    kenteken = data.get("kenteken", [])

    if isinstance(roep, list):
        default["Roepnummer"] = {str(v).strip().upper() for v in roep if str(v).strip()}
    if isinstance(kenteken, list):
        default["Kenteken"] = {str(v).strip().upper() for v in kenteken if str(v).strip()}

    return default


def find_duplicates(records: list, field: str, ignored_values: set[str]) -> dict:
    seen = defaultdict(list)
    for i, record in enumerate(records):
        value = record.get(field, "").strip()
        if value.lower() in INVALID_VALUES:
            continue
        if value.upper() in ignored_values:
            continue
        seen[value].append(i)
    return {value: indices for value, indices in seen.items() if len(indices) > 1}


FOUND: dict = {}   # land -> {(veld, waarde): [records]} voor de Discord-melding


def check_region(region: str, filepath: str, emit, ignored_by_field: dict[str, set[str]]) -> bool:
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
        duplicates = find_duplicates(filtered, field, ignored_by_field.get(field, set()))
        if not duplicates:
            emit(f"  {field:<14} : no duplicates", console=True)
            continue

        found_any = True
        for value, indices in duplicates.items():
            FOUND.setdefault(region, {})[(field, value)] = [filtered[i] for i in indices]
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
    ignored_by_field = load_exceptions()

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
        emit(
            f"  Exceptions: Roepnummer={len(ignored_by_field.get('Roepnummer', set()))}, "
            f"Kenteken={len(ignored_by_field.get('Kenteken', set()))}",
            console=True,
        )
        emit(SEP, console=True)

        any_duplicates = False
        for region, filepath in FILES.items():
            if check_region(region, filepath, emit, ignored_by_field):
                any_duplicates = True

        emit(f"\n{SEP}", console=True)
        if any_duplicates:
            emit("  RESULT: Duplicates detected -- review the entries above.", console=True)
        else:
            emit("  RESULT: All regions clean -- no duplicates found.", console=True)
        emit(f"{SEP}\n", console=True)

    print(f"Report written to {OUTPUT_FILE}")
    for region in FILES:
        if region not in MELD_LANDEN:
            continue
        try:
            notify_new(region, FOUND.get(region, {}))
        except Exception as e:                       # melding mislukt: rapport is er wel, volgende run opnieuw
            print(f"Discord-melding dubbelen {region} mislukt: {e}")


def _rij(r: dict) -> str:
    return " · ".join(x for x in (r.get("Adres", "").strip(), r.get("Roepnummer", "").strip(),
                                  r.get("Kenteken", "").strip(), r.get("TypeVoertuig", "").strip()) if x)


def notify_new(region: str, dups: dict) -> None:
    path = STATE_FILE.format(land=region)
    first_run = not os.path.exists(path)
    try:
        with open(path, encoding="utf-8") as f:
            gemeld = set(json.load(f))
    except (OSError, ValueError):
        gemeld = set()
    keys = {f"{field}|{value}" for field, value in dups}
    new = sorted((k for k in dups if f"{k[0]}|{k[1]}" not in gemeld), key=lambda k: (k[0], k[1]))
    print(f"Dubbelen {region}: {len(dups)} totaal, {len(new)} nieuw")
    ok = True
    if new and WEBHOOK:
        lines = []
        for field, value in new[:MAX_IN_EMBED]:
            recs = dups[(field, value)]
            lines.append(f"**{field} `{value}`** ({len(recs)}x)\n" + "\n".join(f"• {_rij(r)[:110]}" for r in recs[:4]))
        if len(new) > MAX_IN_EMBED:
            lines.append(f"… en nog {len(new) - MAX_IN_EMBED} (zie bijlage)")
        embed = {
            "title": f"[{region}] Dubbele roepnummers/kentekens: {len(new)} nieuw",
            "description": ("Eerste controle: alle dubbelen die er nu zijn.\n\n" if first_run else "")
                           + "\n\n".join(lines)[:3900]
                           + "\n\nKlopt een dubbele wel? Zet de waarde in `storage/uitzonderingen.json`.",
            "color": 15105570,
            "footer": {"text": f"Totaal nu in {region}: {len(dups)} dubbele waarde(n)"},
        }
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Land", "Veld", "Waarde", "Adres", "Roepnummer", "Kenteken", "TypeVoertuig", "Hulpdienst", "Regio"])
        for field, value in new:
            for r in dups[(field, value)]:
                w.writerow([region, field, value] + [r.get(k, "").strip() for k in
                                                     ("Adres", "Roepnummer", "Kenteken", "TypeVoertuig", "Hulpdienst", "Regio")])
        files = {"file": (f"dubbelen_{region}.csv", buf.getvalue().encode("utf-8-sig"), "text/csv")}
        r = requests.post(WEBHOOK, data={"payload_json": json.dumps({"embeds": [embed]})}, files=files, timeout=30)
        print(f"Discord dubbelen {region}: HTTP {r.status_code}")
        ok = r.status_code in (200, 204)
        time.sleep(2)
    elif new:
        print("Geen DUPLICATES_WEBHOOK ingesteld; niets verstuurd.")
        ok = False
    # Alleen bewaren wat nu nog dubbel is en gemeld is; mislukte melding: volgende run opnieuw
    bewaar = keys if ok else (keys & gemeld)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(bewaar), f, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
