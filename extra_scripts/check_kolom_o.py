"""Controle kolom O (DataType) van de megasheets NL, BE en LUX.

Elke rij met een ID (kolom L: POST-... of VEH-...) moet in kolom O een waarde hebben: "Locatie", "Voertuig"
of "-". Een rij met "-" mag geen foto (kolom J, Fotoboek) hebben. Rijen zonder ID zijn scheidingsrijen (tussen gebouwen, alleen hulpdienst/regio ingevuld) en worden
overgeslagen.

Meldt alleen rijen die sinds de vorige run nieuw zonder waarde zijn (storage/kolom_o_gemeld.json), zodat de
uurlijkse run niet elk uur dezelfde lijst stuurt. Bij een fout (sheet niet bereikbaar e.d.) stopt het script
stil met exit 0: de rest van de uurlijkse run gaat gewoon door.

Rood in de sheet: met GOOGLE_SERVICE_ACCOUNT_JSON (serviceaccount met bewerkrechten) zorgt het script dat elke
megasheet twee regels voor voorwaardelijke opmaak op kolom O heeft (leeg bij een rij met ID, en "-" met foto).
Die kleuren een fout vakje meteen rood terwijl iemand typt; ontbreekt een regel (bijv. per ongeluk verwijderd),
dan zet de volgende run hem terug.

Omgevingsvariabelen: GOOGLE_SHEETS_API_KEY, SPREADSHEET_ID_NL/_BE/_LUX, DISCORD_WEBHOOK_URL_REGIONS,
GOOGLE_SERVICE_ACCOUNT_JSON (optioneel, voor de rode opmaak).
"""
import csv
import io
import json
import os
import sys
import time
import base64
import urllib.parse

import requests

SHEETS = {"NL": "MegaSheetNL", "BE": "MegaSheetBE", "LUX": "MegaSheetLUX"}
STATE_FILE = os.path.join("storage", "kolom_o_gemeld.json")
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL_REGIONS")
API_KEY = os.getenv("GOOGLE_SHEETS_API_KEY", "")
COL_FOTO, COL_ID, COL_O = 9, 11, 14   # J, L en O (0-based)
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


SOORTEN = {
    "leeg": ("⚠️ Kolom O (DataType) leeg", "Vul in kolom O **Locatie**, **Voertuig** of **-** in.", 15105570),
    "foto": ("🚫 Foto bij rij met \"-\" in kolom O", "Rijen met **-** in kolom O mogen geen foto hebben. Haal de foto (kolom J) weg of zet de juiste waarde in kolom O.", 15158332),
}


def check_rows(region, values):
    out = []
    for nr, row in enumerate(values, start=1):          # nr = echte rij in de sheet
        rid = cell(row, COL_ID)
        if not (rid.startswith("POST-") or rid.startswith("VEH-")):
            continue
        o = cell(row, COL_O)
        if not o:
            soort = "leeg"
        elif o == "-" and cell(row, COL_FOTO):
            soort = "foto"
        else:
            continue
        out.append({
            "soort": soort, "land": region, "rij": nr, "id": rid,
            "adres": cell(row, 0), "roepnummer": cell(row, 1), "type": cell(row, 3),
            "hulpdienst": cell(row, 6), "regio": cell(row, 7), "foto": cell(row, COL_FOTO),
        })
    return out


def key(m):
    return f"{m['soort']}|{m['land']}|{m['id']}"


def describe(m):
    naam = " · ".join(x for x in (m["adres"], m["roepnummer"], m["type"]) if x)[:90] or "(leeg)"
    return f"**{m['land']} rij {m['rij']}** `{m['id']}` — {naam}"


def send(soort, new, totals):
    if not WEBHOOK:
        print("Geen DISCORD_WEBHOOK_URL_REGIONS ingesteld; niets verstuurd.")
        return False
    titel, uitleg, kleur = SOORTEN[soort]
    per_land = ", ".join(f"{land}: {n}" for land, n in totals.items())
    lines = [describe(m) for m in new[:MAX_IN_EMBED]]
    if len(new) > MAX_IN_EMBED:
        lines.append(f"… en nog {len(new) - MAX_IN_EMBED} (zie bijlage)")
    embed = {
        "title": f"{titel}: {len(new)} nieuwe rij(en)",
        "description": uitleg + "\n\n" + "\n".join(lines),
        "color": kleur,
        "footer": {"text": f"Totaal nu: {per_land}"},
    }
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Land", "Rij", "ID", "Adres", "Roepnummer", "TypeVoertuig", "Hulpdienst", "Regio", "Foto"])
    for m in new:
        w.writerow([m["land"], m["rij"], m["id"], m["adres"], m["roepnummer"], m["type"], m["hulpdienst"], m["regio"], m["foto"]])
    files = {"file": (f"kolom_o_{soort}.csv", buf.getvalue().encode("utf-8-sig"), "text/csv")}
    r = requests.post(WEBHOOK, data={"payload_json": json.dumps({"embeds": [embed]})}, files=files, timeout=30)
    print(f"Discord ({soort}): HTTP {r.status_code}")
    return r.status_code in (200, 204)



# ---- Rode opmaak in de sheet (voorwaardelijke opmaak, via serviceaccount) ----
RED = {"red": 0.96, "green": 0.6, "blue": 0.6}
RULES = [
    '=AND($L4<>"",TRIM($O4)="")',              # rij met ID, kolom O leeg
    '=AND(TRIM($O4)="-",TRIM($J4)<>"")',       # "-" maar wel een foto
]


def sa_token():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        return None
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    d = json.loads(raw)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
    now = int(time.time())
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = b64(json.dumps({"iss": d["client_email"], "scope": "https://www.googleapis.com/auth/spreadsheets",
                            "aud": d["token_uri"], "iat": now, "exp": now + 3600}).encode())
    key = serialization.load_pem_private_key(d["private_key"].encode(), None)
    sig = b64(key.sign(head + b"." + claim, padding.PKCS1v15(), hashes.SHA256()))
    r = requests.post(d["token_uri"], data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                             "assertion": (head + b"." + claim + b"." + sig).decode()}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def ensure_red_rules(token):
    """Zet de twee regels op kolom O (vanaf rij 4) als ze er nog niet staan. Raakt andere regels niet aan."""
    hdr = {"Authorization": f"Bearer {token}"}
    for region, tab in SHEETS.items():
        sid = os.getenv(f"SPREADSHEET_ID_{region}", "")
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
        meta = requests.get(url, headers=hdr, timeout=60,
                            params={"fields": "sheets(properties(title,sheetId),conditionalFormats)"}).json()
        sheet = next((x for x in meta.get("sheets", []) if x["properties"]["title"] == tab), None)
        if not sheet:
            print(f"{region}: tabblad {tab} niet gevonden")
            continue
        have = set()
        for cf in sheet.get("conditionalFormats", []):
            for v in (((cf.get("booleanRule") or {}).get("condition") or {}).get("values") or []):
                have.add(str(v.get("userEnteredValue", "")).replace(" ", "").replace(";", ","))
        missing = [f for f in RULES if f.replace(" ", "") not in have]
        if not missing:
            print(f"{region}: opmaakregels kolom O staan er al")
            continue
        # Formules volgen de taal van de sheet: Nederlands = puntkomma, Engels = komma
        for sep in (";", ","):
            reqs = [{"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{"sheetId": sheet["properties"]["sheetId"], "startRowIndex": 3,
                            "startColumnIndex": COL_O, "endColumnIndex": COL_O + 1}],
                "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                              "values": [{"userEnteredValue": f.replace(",", sep)}]},
                                "format": {"backgroundColor": RED}}}}} for f in missing]
            r = requests.post(url + ":batchUpdate", headers=hdr, json={"requests": reqs}, timeout=60)
            if r.ok:
                print(f"{region}: {len(reqs)} opmaakregel(s) gezet")
                break
        else:
            print(f"{region}: opmaakregels zetten mislukt: HTTP {r.status_code} {r.text[:200]}")


def main():
    try:
        token = sa_token()
        if token:
            ensure_red_rules(token)
        else:
            print("Geen GOOGLE_SERVICE_ACCOUNT_JSON: rode opmaak overgeslagen.")
    except Exception as e:                                # opmaak mislukt: melding gaat gewoon door
        print(f"Rode opmaak overgeslagen: {e}")
    try:
        found = []
        for region in SHEETS:
            found += check_rows(region, fetch_sheet(region))
    except Exception as e:                                # sheet tijdelijk niet bereikbaar: volgende run opnieuw
        print(f"Kolom O-controle overgeslagen: {e}")
        return
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            gemeld = set(json.load(f))
    except (OSError, ValueError):
        gemeld = set()
    bewaar = set()
    for soort in SOORTEN:
        nu = [m for m in found if m["soort"] == soort]
        totals = {land: sum(1 for m in nu if m["land"] == land) for land in SHEETS}
        new = [m for m in nu if key(m) not in gemeld]
        print(f"Kolom O {soort}: {totals}; nieuw t.o.v. vorige melding: {len(new)}")
        ok = True
        if new:
            try:
                ok = send(soort, new, totals)
            except Exception as e:
                print(f"Discord-melding mislukt: {e}")
                ok = False
        # Alleen bewaren wat nu nog speelt en gemeld is; mislukte melding: volgende run opnieuw
        bewaar |= {key(m) for m in nu if ok or key(m) in gemeld}
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(bewaar), f, ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
    sys.exit(0)
