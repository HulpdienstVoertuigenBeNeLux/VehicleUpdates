"""Controle kolom O (DataType) van de megasheets NL, BE en LUX.

Elke rij met een ID (kolom L: POST-... of VEH-...) moet in kolom O een waarde hebben: "Locatie", "Voertuig"
of "-". Rijen zonder ID zijn scheidingsrijen (tussen gebouwen, alleen hulpdienst/regio ingevuld) en worden
overgeslagen.

Meldt alleen rijen die sinds de vorige run nieuw zonder waarde zijn (storage/kolom_o_gemeld.json), zodat de
uurlijkse run niet elk uur dezelfde lijst stuurt. Bij een fout (sheet niet bereikbaar e.d.) stopt het script
stil met exit 0: de rest van de uurlijkse run gaat gewoon door.

Omgevingsvariabelen: GOOGLE_SHEETS_API_KEY, SPREADSHEET_ID_NL/_BE/_LUX, DISCORD_WEBHOOK_URL_REGIONS.
"""
import csv
import io
import json
import os
import sys
import time
import urllib.parse

import requests

SHEETS = {"NL": "MegaSheetNL", "BE": "MegaSheetBE", "LUX": "MegaSheetLUX"}
STATE_FILE = os.path.join("storage", "kolom_o_gemeld.json")
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL_REGIONS")
API_KEY = os.getenv("GOOGLE_SHEETS_API_KEY", "")
COL_ID, COL_O = 11, 14          # L en O (0-based)
MAX_IN_EMBED = 15


def fetch_sheet(region):
    sid = os.getenv(f"SPREADSHEET_ID_{region}", "")
    if not sid or not API_KEY:
        raise RuntimeError(f"geen sheet-ID of API-sleutel voor {region}")
    rng = urllib.parse.quote(f"{SHEETS[region]}!A:O")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{rng}"
    for attempt in range(3):
        r = requests.get(url, params={"key": API_KEY}, timeout=60)
        if r.status_code == 200:
            return r.json().get("values", [])
        if attempt < 2 and r.status_code in (429, 500, 502, 503):
            time.sleep(20)
            continue
        raise RuntimeError(f"{region}: HTTP {r.status_code}")


def cell(row, i):
    return str(row[i]).strip() if i < len(row) and row[i] is not None else ""


def missing_rows(region, values):
    out = []
    for nr, row in enumerate(values, start=1):          # nr = echte rij in de sheet
        rid = cell(row, COL_ID)
        if not (rid.startswith("POST-") or rid.startswith("VEH-")):
            continue
        if cell(row, COL_O):
            continue
        out.append({
            "land": region, "rij": nr, "id": rid,
            "adres": cell(row, 0), "roepnummer": cell(row, 1), "type": cell(row, 3),
            "hulpdienst": cell(row, 6), "regio": cell(row, 7),
        })
    return out


def describe(m):
    naam = " · ".join(x for x in (m["adres"], m["roepnummer"], m["type"]) if x)[:90] or "(leeg)"
    return f"**{m['land']} rij {m['rij']}** `{m['id']}` — {naam}"


def send(new, totals):
    if not WEBHOOK:
        print("Geen DISCORD_WEBHOOK_URL_REGIONS ingesteld; niets verstuurd.")
        return False
    per_land = ", ".join(f"{land}: {n}" for land, n in totals.items())
    lines = [describe(m) for m in new[:MAX_IN_EMBED]]
    if len(new) > MAX_IN_EMBED:
        lines.append(f"… en nog {len(new) - MAX_IN_EMBED} (zie bijlage)")
    embed = {
        "title": f"⚠️ Kolom O (DataType) leeg: {len(new)} nieuwe rij(en)",
        "description": "Vul in kolom O **Locatie**, **Voertuig** of **-** in.\n\n" + "\n".join(lines),
        "color": 15105570,
        "footer": {"text": f"Totaal zonder waarde in kolom O: {per_land}"},
    }
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Land", "Rij", "ID", "Adres", "Roepnummer", "TypeVoertuig", "Hulpdienst", "Regio"])
    for m in new:
        w.writerow([m["land"], m["rij"], m["id"], m["adres"], m["roepnummer"], m["type"], m["hulpdienst"], m["regio"]])
    files = {"file": ("kolom_o_leeg.csv", buf.getvalue().encode("utf-8-sig"), "text/csv")}
    r = requests.post(WEBHOOK, data={"payload_json": json.dumps({"embeds": [embed]})}, files=files, timeout=30)
    print(f"Discord: HTTP {r.status_code}")
    return r.status_code in (200, 204)


def main():
    try:
        found = []
        for region in SHEETS:
            found += missing_rows(region, fetch_sheet(region))
    except Exception as e:                                # sheet tijdelijk niet bereikbaar: volgende run opnieuw
        print(f"Kolom O-controle overgeslagen: {e}")
        return
    totals = {land: sum(1 for m in found if m["land"] == land) for land in SHEETS}
    keys = {f"{m['land']}|{m['id']}" for m in found}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            gemeld = set(json.load(f))
    except (OSError, ValueError):
        gemeld = set()
    new = [m for m in found if f"{m['land']}|{m['id']}" not in gemeld]
    print(f"Kolom O leeg: {totals}; nieuw t.o.v. vorige melding: {len(new)}")
    if new:
        try:
            ok = send(new, totals)
        except Exception as e:
            print(f"Discord-melding mislukt: {e}")
            ok = False
        if not ok:
            return                                        # niet opslaan: volgende run opnieuw proberen
    # Alleen bewaren wat nu nog leeg is: wordt een rij later weer leeg, dan komt hij opnieuw in een melding
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
    sys.exit(0)
