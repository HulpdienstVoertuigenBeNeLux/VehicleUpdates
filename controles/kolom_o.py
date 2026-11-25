"""Controle kolom O (DataType) van alle sheets uit config/sheets.json (NL, BE, LUX, DE).

Regels, per tabblad, voor elke rij met een ID in kolom L (POST-... of VEH-...):
- kolom O moet een waarde hebben: "Locatie", "Voertuig" of "-" (kopregels zoals "Veiligheidsregio X" krijgen "-");
- een rij met "-" mag geen foto (kolom J) hebben.
Rijen zonder ID (scheidingsrijen tussen gebouwen) worden overgeslagen.

Melden: per land één Discord-melding (embed + CSV) met alleen de NIEUWE gevallen sinds de vorige run.
Wat al gemeld is staat per land in landen/<LAND>/kolom_o_gemeld.json.
Weekoverzicht: elke maandag vanaf 09:00 (eerste run) één bericht met het totaal per land en per land een CSV met
alle rijen die op dat moment fout zijn; zijn er geen fouten, dan staat dat er. Laatste verzending in
landen/kolom_o_weekoverzicht.json.

Rood in de sheet: met GOOGLE_SERVICE_ACCOUNT_JSON zorgt het script dat elk tabblad twee regels voor
voorwaardelijke opmaak op kolom O heeft (dezelfde twee regels als hierboven). Google kleurt een fout vakje dan
meteen rood terwijl iemand typt; ontbreekt een regel, dan zet de volgende run hem terug.

Een fout (sheet of Discord niet bereikbaar) stopt het script stil met exit 0: de rest van de uurlijkse run gaat door.

Omgevingsvariabelen: de id_env-namen uit config/sheets.json, GOOGLE_SERVICE_ACCOUNT_JSON (lezen + opmaak),
GOOGLE_SHEETS_API_KEY (alleen lezen, terugval zonder serviceaccount), DISCORD_WEBHOOK_URL_REGIONS.
"""
import base64
import csv
import datetime
import io
import json
import os
import sys
import time
import urllib.parse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "config", "sheets.json")
STATE_FILE = os.path.join(ROOT, "landen", "{land}", "kolom_o_gemeld.json")
OLD_STATE_FILE = os.path.join(ROOT, "storage", "kolom_o_gemeld.json")     # vóór de indeling per land
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL_REGIONS")
API_KEY = os.getenv("GOOGLE_SHEETS_API_KEY", "")
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets/"
COL_FOTO, COL_ID, COL_O = 9, 11, 14          # J, L en O (0-based)
MAX_IN_EMBED = 15

SOORTEN = {
    "leeg": ("⚠️ Kolom O (DataType) leeg", "Vul in kolom O **Locatie**, **Voertuig** of **-** in.", 15105570),
    "foto": ("🚫 Foto bij rij met \"-\" in kolom O",
             "Rijen met **-** in kolom O mogen geen foto hebben. Haal de foto (kolom J) weg of zet de juiste waarde in kolom O.",
             15158332),
}

# Voorwaardelijke opmaak (zelfde regels als SOORTEN); {r} = eerste datarij.
# Per sheet bepaalt "rij" in config/sheets.json wanneer een rij meetelt: "id" (kolom L heeft een ID) of
# "inhoud" (kolom A, B of D gevuld; sheets zonder ID's zoals Brandweer DE / Ambulance DE).
RED = {"red": 0.96, "green": 0.6, "blue": 0.6}
TELT_MEE = {"id": '$L{r}<>""', "inhoud": 'OR($A{r}<>"",$B{r}<>"",$D{r}<>"")'}


def rules_for(mode, r):
    return [
        f'=AND({TELT_MEE[mode].format(r=r)},TRIM($O{r})="")',
        f'=AND(TRIM($O{r})="-",TRIM($J{r})<>"")',
    ]


# ---------- Google ----------

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


def google_get(url, token, params=None):
    params = dict(params or {})
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        params["key"] = API_KEY
    for attempt in range(3):
        r = requests.get(url, headers=headers, params=params, timeout=90)
        if r.status_code == 200:
            return r.json()
        if attempt < 2 and r.status_code in (429, 500, 502, 503):
            time.sleep(20)
            continue
        raise RuntimeError(f"HTTP {r.status_code}")


def sheet_tabs(sid, sheet_cfg, token):
    """[(titel, sheetId, bestaande opmaakregels)] voor de tabbladen uit de config."""
    fields = "sheets(properties(title,sheetId),conditionalFormats)" if token else "sheets(properties(title,sheetId))"
    meta = google_get(SHEETS_API + sid, token, {"fields": fields})
    wanted = sheet_cfg["tabs"]
    exclude = set(sheet_cfg.get("exclude", []))
    out = []
    for s in meta.get("sheets", []):
        title = s["properties"]["title"]
        if (wanted == "*" and title not in exclude) or (wanted != "*" and title in wanted):
            out.append((title, s["properties"]["sheetId"], s.get("conditionalFormats", [])))
    return out


def tab_values(sid, titles, token):
    if not titles:
        return {}
    ranges = [f"'{t}'!A:O" for t in titles]
    data = google_get(SHEETS_API + sid + "/values:batchGet", token, {"ranges": ranges})
    return {t: vr.get("values", []) for t, vr in zip(titles, data.get("valueRanges", []))}


def is_ours(cf):
    """Een regel van dit script: eigen formule-vorm, rode vulkleur, alleen kolom O."""
    rule = cf.get("booleanRule") or {}
    vals = (rule.get("condition") or {}).get("values") or []
    f = str(vals[0].get("userEnteredValue", "")) if vals else ""
    bg = ((rule.get("format") or {}).get("backgroundColor") or {})
    cols = {(rg.get("startColumnIndex"), rg.get("endColumnIndex")) for rg in cf.get("ranges", [])}
    return ("TRIM($O" in f and cols == {(COL_O, COL_O + 1)}
            and abs(bg.get("red", 0) - RED["red"]) < 0.01 and abs(bg.get("green", 0) - RED["green"]) < 0.01)


def norm(f):
    return f.replace(" ", "").replace(";", ",")


def ensure_red_rules(sid, tabs, start_row, token, mode="id"):
    """Zet de ontbrekende regels op kolom O van alle tabbladen in één verzoek per sheet (Google staat maar
    60 schrijfverzoeken per minuut toe). Raakt andere opmaakregels niet aan. Geeft het aantal gezette regels terug."""
    want = rules_for(mode, start_row)
    todo, deletes = [], []
    for title, sheet_id, formats in tabs:
        have = set()
        for idx, cf in enumerate(formats):
            vals = ((cf.get("booleanRule") or {}).get("condition") or {}).get("values") or []
            f = norm(str(vals[0].get("userEnteredValue", ""))) if vals else ""
            if is_ours(cf) and f not in {norm(w) for w in want}:
                deletes.append((sheet_id, idx))           # oude versie van een eigen regel: vervangen
            else:
                have.add(f)
        for formula in want:
            if norm(formula) not in have:
                todo.append((sheet_id, formula))
    if not todo and not deletes:
        return 0
    # Eerst verwijderen (per tabblad van hoge naar lage index), dan toevoegen
    dels = [{"deleteConditionalFormatRule": {"sheetId": s, "index": i}}
            for s, i in sorted(deletes, key=lambda x: (x[0], -x[1]))]
    # Formules volgen de taal van de sheet: Nederlands = puntkomma, Engels = komma
    for sep in (";", ","):
        reqs = dels + [{"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sheet_id, "startRowIndex": start_row - 1,
                        "startColumnIndex": COL_O, "endColumnIndex": COL_O + 1}],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                          "values": [{"userEnteredValue": f.replace(",", sep)}]},
                            "format": {"backgroundColor": RED}}}}} for sheet_id, f in todo]
        r = requests.post(SHEETS_API + sid + ":batchUpdate", headers={"Authorization": f"Bearer {token}"},
                          json={"requests": reqs}, timeout=90)
        if r.ok:
            return len(todo)
        if r.status_code != 400:                       # 403 = geen bewerkrechten, 429 = te veel verzoeken
            break
    msg = (r.json().get("error", {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else r.text)
    raise RuntimeError(f"opmaakregels zetten mislukt: HTTP {r.status_code} {msg[:120]}")


# ---------- Controle ----------

def cell(row, i):
    return str(row[i]).strip() if i < len(row) and row[i] is not None else ""


def check_rows(land, sheet, tab, values, mode="id", start_row=1):
    out = []
    for nr, row in enumerate(values, start=1):          # nr = echte rij in de sheet
        if nr < start_row:
            continue
        rid = cell(row, COL_ID)
        if mode == "inhoud":
            if not (cell(row, 0) or cell(row, 1) or cell(row, 3)):
                continue
            rid = rid or f"rij {nr}"                       # geen ID in deze sheets: rijnummer als sleutel
        elif not (rid.startswith("POST-") or rid.startswith("VEH-")):
            continue
        o = cell(row, COL_O)
        if not o:
            soort = "leeg"
        elif o == "-" and cell(row, COL_FOTO):
            soort = "foto"
        else:
            continue
        out.append({
            "soort": soort, "land": land, "sheet": sheet, "tab": tab, "rij": nr, "id": rid,
            "adres": cell(row, 0), "roepnummer": cell(row, 1), "type": cell(row, 3),
            "hulpdienst": cell(row, 6), "regio": cell(row, 7), "foto": cell(row, COL_FOTO),
        })
    return out


def key(m):
    return f"{m['soort']}|{m['land']}|{m['sheet']}|{m['tab']}|{m['id']}"


def where(m):
    return m["tab"] if m["tab"] == m["sheet"] else f"{m['sheet']} / {m['tab']}"


def describe(m):
    naam = " · ".join(x for x in (m["adres"], m["roepnummer"], m["type"]) if x)[:80] or "(leeg)"
    return f"**{where(m)} rij {m['rij']}** `{m['id']}` — {naam}"


def send(land, soort, new, total):
    if not WEBHOOK:
        print("  Geen DISCORD_WEBHOOK_URL_REGIONS ingesteld; niets verstuurd.")
        return False
    titel, uitleg, kleur = SOORTEN[soort]
    lines = [describe(m) for m in new[:MAX_IN_EMBED]]
    if len(new) > MAX_IN_EMBED:
        lines.append(f"… en nog {len(new) - MAX_IN_EMBED} (zie bijlage)")
    embed = {
        "title": f"[{land}] {titel}: {len(new)} nieuwe rij(en)",
        "description": uitleg + "\n\n" + "\n".join(lines),
        "color": kleur,
        "footer": {"text": f"Totaal nu in {land}: {total}"},
    }
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Land", "Sheet", "Tabblad", "Rij", "ID", "Adres", "Roepnummer", "TypeVoertuig", "Hulpdienst", "Regio", "Foto"])
    for m in new:
        w.writerow([m["land"], m["sheet"], m["tab"], m["rij"], m["id"], m["adres"], m["roepnummer"], m["type"],
                    m["hulpdienst"], m["regio"], m["foto"]])
    files = {"file": (f"kolom_o_{land}_{soort}.csv", buf.getvalue().encode("utf-8-sig"), "text/csv")}
    r = requests.post(WEBHOOK, data={"payload_json": json.dumps({"embeds": [embed]})}, files=files, timeout=30)
    print(f"  Discord ({soort}): HTTP {r.status_code}")
    time.sleep(2)
    return r.status_code in (200, 204)


def load_state(land, sheets):
    path = STATE_FILE.format(land=land)
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        pass
    # Eenmalig overnemen uit het oude bestand (sleutels zonder sheet/tabblad, alleen NL/BE/LUX met één tabblad)
    try:
        with open(OLD_STATE_FILE, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        return set()
    if len(sheets) != 1 or sheets[0]["tabs"] == "*" or len(sheets[0]["tabs"]) != 1:
        return set()
    sheet, tab = sheets[0]["naam"], sheets[0]["tabs"][0]
    out = set()
    for k in old:
        parts = k.split("|")
        if len(parts) == 3 and parts[1] == land:
            out.add(f"{parts[0]}|{land}|{sheet}|{tab}|{parts[2]}")
    return out


def save_state(land, keys):
    path = STATE_FILE.format(land=land)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f, ensure_ascii=False, indent=0)


def check_land(land, sheets, token):
    found = []
    for sc in sheets:
        sid = os.getenv(sc["id_env"], "")
        if not sid:
            print(f"  {sc['naam']}: geen {sc['id_env']} ingesteld, overgeslagen")
            continue
        tabs = sheet_tabs(sid, sc, token)
        if token:
            try:
                n = ensure_red_rules(sid, tabs, sc["start_row"], token, sc.get("rij", "id"))
                if n:
                    print(f"  {sc['naam']}: {n} opmaakregel(s) gezet")
            except Exception as e:                        # opmaak mislukt: controle en melding gaan door
                print(f"  {sc['naam']}: {e}")
        values = tab_values(sid, [t[0] for t in tabs], token)
        for title, rows in values.items():
            found += check_rows(land, sc["naam"], title, rows, sc.get("rij", "id"), sc["start_row"])
    gemeld = load_state(land, sheets)
    bewaar = set()
    for soort in SOORTEN:
        nu = [m for m in found if m["soort"] == soort]
        new = [m for m in nu if key(m) not in gemeld]
        print(f"  {soort}: {len(nu)} totaal, {len(new)} nieuw")
        ok = True
        if new:
            try:
                ok = send(land, soort, new, len(nu))
            except Exception as e:
                print(f"  Discord-melding mislukt: {e}")
                ok = False
        # Alleen bewaren wat nu nog speelt en gemeld is; mislukte melding: volgende run opnieuw
        bewaar |= {key(m) for m in nu if ok or key(m) in gemeld}
    save_state(land, bewaar)
    return found


def main():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        config = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    try:
        token = sa_token()
    except Exception as e:
        print(f"Inloggen serviceaccount mislukt ({e}); verder met de API-sleutel, zonder rode opmaak.")
        token = None
    if not token and not API_KEY:
        print("Geen serviceaccount en geen API-sleutel: kolom O-controle overgeslagen.")
        return
    alles = {}
    for land, sheets in config.items():
        print(f"{land}:")
        try:
            alles[land] = check_land(land, sheets, token)
        except Exception as e:                            # één land mislukt: de andere gaan door
            print(f"  {land} overgeslagen: {e}")
    if len(alles) == len(config):                         # alleen met volledige data, anders volgende run
        try:
            weekoverzicht(alles, force=os.getenv("KOLOM_O_WEEKOVERZICHT") == "nu")
        except Exception as e:
            print(f"Weekoverzicht mislukt: {e}")


WEEK_FILE = os.path.join(ROOT, "landen", "kolom_o_weekoverzicht.json")
WEEK_DAG, WEEK_UUR = 0, 9          # maandag, vanaf 09:00 (TZ Europe/Amsterdam in de workflow)


def csv_bytes(rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Land", "Soort", "Sheet", "Tabblad", "Rij", "ID", "Adres", "Roepnummer", "TypeVoertuig", "Hulpdienst", "Regio", "Foto"])
    for m in rows:
        w.writerow([m["land"], "kolom O leeg" if m["soort"] == "leeg" else "\"-\" met foto", m["sheet"], m["tab"], m["rij"],
                    m["id"], m["adres"], m["roepnummer"], m["type"], m["hulpdienst"], m["regio"], m["foto"]])
    return buf.getvalue().encode("utf-8-sig")


def weekoverzicht(alles, force=False):
    """Eén keer per week: totaal per land + CSV per land met fouten, of de melding dat er geen fouten zijn."""
    nu = datetime.datetime.now()
    vandaag = nu.date().isoformat()
    try:
        with open(WEEK_FILE, encoding="utf-8") as f:
            laatste = json.load(f).get("laatste", "")
    except (OSError, ValueError):
        laatste = ""
    if not force and (nu.weekday() != WEEK_DAG or nu.hour < WEEK_UUR or laatste == vandaag):
        return
    if not WEBHOOK:
        print("Weekoverzicht: geen DISCORD_WEBHOOK_URL_REGIONS ingesteld; niets verstuurd.")
        return
    totaal = sum(len(v) for v in alles.values())
    regels = []
    for land, rows in alles.items():
        leeg = sum(1 for m in rows if m["soort"] == "leeg")
        foto = sum(1 for m in rows if m["soort"] == "foto")
        if not rows:
            regels.append(f"✅ **{land}**: geen fouten")
        else:
            delen = [f"{leeg} leeg"] if leeg else []
            if foto:
                delen.append(f"{foto} \"-\" met foto")
            regels.append(f"⚠️ **{land}**: {', '.join(delen)}")
    if totaal == 0:
        embed = {"title": "✅ Weekoverzicht kolom O: er zijn geen fouten",
                 "description": "Alle sheets zijn in orde.\n\n" + "\n".join(regels), "color": 3066993}
    else:
        embed = {"title": f"📋 Weekoverzicht kolom O: {totaal} fout(en)",
                 "description": "Alle rijen die nu fout zijn staan per land in de bijlage(n).\n\n" + "\n".join(regels),
                 "color": 15105570}
    embed["footer"] = {"text": nu.strftime("Stand %d-%m-%Y %H:%M")}
    files = {}
    for n, (land, rows) in enumerate((l, r) for l, r in alles.items() if r):
        files[f"files[{n}]"] = (f"kolom_o_{land}_{vandaag}.csv", csv_bytes(rows), "text/csv")
    r = requests.post(WEBHOOK, data={"payload_json": json.dumps({"embeds": [embed]})}, files=files or None, timeout=60)
    print(f"Weekoverzicht: HTTP {r.status_code}")
    if r.status_code in (200, 204) and not force:
        os.makedirs(os.path.dirname(WEEK_FILE), exist_ok=True)
        with open(WEEK_FILE, "w", encoding="utf-8") as f:
            json.dump({"laatste": vandaag}, f)


if __name__ == "__main__":
    main()
    sys.exit(0)
