import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_SHEETS_DIR = os.path.join(BASE_DIR, "raw", "sheets")
REGIONS = ["NL", "BE", "LUX"]
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # Loaded from GitHub Secrets

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Cloudflare weigert verzoeken zonder browser-User-Agent
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
URL_RE = re.compile(r"https?://[^\s,]+")
# Opvulwaarden komen op veel rijen voor en zeggen niets over welk voertuig het is
PLACEHOLDER_RE = re.compile(r"^(geen|onbekend|unknown|nvt|-+|\?+)$")
ID_RE = re.compile(r"^(POST-\d+|VEH-\d+(-POST-\d+)?)$")

COLUMNS = ["Soort", "Land", "Regio", "Post", "Roepnummer", "Kenteken", "Rij-ID", "Foto", "Hoort waarschijnlijk bij", "Link"]


def slug(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def match_key(value):
    """Kenteken/roepnummer als zoeksleutel; leeg als het een opvulwaarde of te kort is."""
    key = slug(value)
    return "" if PLACEHOLDER_RE.match(key) or len(key) < 3 else key


def photo_parts(url):
    """Stukken van de bestandsnaam tussen de streepjes, bijv. 22-1782-1-ZNH-52-<tijd>.webp."""
    name = url.split("?")[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return [slug(part) for part in re.split(r"[-_\s]+", name) if slug(part)]


def in_name(key, parts):
    """True als de sleutel precies gelijk is aan een of meer opeenvolgende stukken van de naam."""
    if not key:
        return False
    for start in range(len(parts)):
        joined = ""
        for part in parts[start:]:
            joined += part
            if joined == key:
                return True
            if len(joined) >= len(key):
                break
    return False


def load_rows(region):
    """Rijen van één land als dicts, met de post (laatste adresregel met coördinaten) erbij."""
    path = os.path.join(RAW_SHEETS_DIR, f"{region}.json")
    try:
        with open(path, encoding="utf-8") as f:
            values = json.load(f).get("values", [])
    except (OSError, json.JSONDecodeError) as e:
        print(f"Failed to load {path}: {e}")
        return []
    if not values:
        return []

    header = values[0]
    rows = []
    post = ""
    for raw in values[1:]:
        row = {name: (raw[i] if i < len(raw) else "") for i, name in enumerate(header) if name}
        row_id = next((str(v).strip() for v in raw if ID_RE.match(str(v).strip())), "")
        row["_id"] = row_id
        row["_region"] = region
        if row_id.startswith("POST-"):
            post = str(row.get("Adres", "")).strip()
        row["_post"] = post
        rows.append(row)
    return rows


def is_post_row(row):
    return row["_id"].startswith("POST-") or not any(
        str(row.get(k, "")).strip() for k in ("Roepnummer", "Kenteken", "TypeVoertuig")
    )


def suggest_owner(parts, rows):
    """Voertuigen in hetzelfde land waarvan kenteken (bij voorkeur) of roepnummer in de bestandsnaam staat."""
    by_plate = [r for r in rows if not is_post_row(r) and in_name(match_key(r.get("Kenteken")), parts)]
    candidates = by_plate or [r for r in rows if not is_post_row(r) and in_name(match_key(r.get("Roepnummer")), parts)]
    return ", ".join(
        f"{r.get('Roepnummer', '').strip()} {r.get('Kenteken', '').strip()} ({r['_post'] or r.get('Adres', '').strip()}, {r['_id']})"
        for r in candidates[:3]
    )


def find_misplaced(rows):
    findings = []
    for row in rows:
        for url in URL_RE.findall(str(row.get("Fotoboek", ""))):
            if "/posts/" in url:
                continue
            parts = photo_parts(url)
            if is_post_row(row):
                findings.append(("Voertuigfoto op postregel", row, url, suggest_owner(parts, rows)))
                continue
            plate, callsign = match_key(row.get("Kenteken")), match_key(row.get("Roepnummer"))
            if not plate and not callsign:
                continue
            if in_name(plate, parts) or in_name(callsign, parts):
                continue
            findings.append(("Foto hoort bij ander voertuig", row, url, suggest_owner(parts, rows)))
    return findings


def link_status(url):
    for _ in range(3):
        try:
            return requests.head(url, headers=HEADERS, timeout=20, allow_redirects=True).status_code
        except requests.RequestException:
            continue
    return None


def find_dead_links(rows):
    links = {}
    for row in rows:
        for url in URL_RE.findall(str(row.get("Fotoboek", ""))):
            links.setdefault(url, []).append(row)
    print(f"Checking {len(links)} photo links...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = dict(zip(links, pool.map(link_status, links)))
    findings = []
    for url, status in statuses.items():
        if status == 404:
            for row in links[url]:
                findings.append(("Foto bestaat niet meer", row, url, ""))
        elif status != 200:
            print(f"Onduidelijke status {status} voor {url}")
    return findings


def to_cells(kind, row, url, owner):
    return [
        kind,
        row["_region"],
        str(row.get("Regio", "")).strip(),
        row["_post"],
        str(row.get("Roepnummer", "")).strip(),
        str(row.get("Kenteken", "")).strip(),
        row["_id"],
        url.rsplit("/", 1)[-1],
        owner,
        url,
    ]


def _autosize(sheet):
    for column_cells in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(width + 2, 60)


def save_to_xlsx(findings, filename):
    print(f"Saving results to {filename}...")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Fotos"
    sheet.append(COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for finding in findings:
        sheet.append(to_cells(*finding))
    # Alles als tekst, zodat Excel roepnummers zoals '01-81' niet als datum leest
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = "@"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    _autosize(sheet)
    workbook.save(filename)


def _md_cell(value):
    return str(value or "").replace("|", "/").replace("\n", " ").strip() or "-"


def save_to_md(findings, counts, filename):
    print(f"Saving results to {filename}...")
    lines = [f"# Fotocontrole {datetime.now(timezone.utc):%Y-%m-%d}", ""]
    lines += [f"- {kind}: **{n}**" for kind, n in counts.items()]
    for kind in counts:
        group = [f for f in findings if f[0] == kind]
        if not group:
            continue
        lines += ["", f"## {kind} ({len(group)})", "",
                  "| Land | Post | Roepnummer | Kenteken | Rij-ID | Foto | Hoort waarschijnlijk bij |",
                  "|---|---|---|---|---|---|---|"]
        for finding in group:
            cells = to_cells(*finding)
            lines.append("| " + " | ".join(_md_cell(cells[i]) for i in (1, 3, 4, 5, 6, 7, 8)) + " |")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def send_discord_alert_with_files(counts, filenames):
    if not DISCORD_WEBHOOK_URL:
        print(f"Discord Webhook URL not set. Files are saved locally as {', '.join(filenames)}.")
        return

    print("Sending files to Discord...")
    regels = "\n".join(f"- {kind}: **{n}**" for kind, n in counts.items() if n)
    payload = {
        "content": (
            "🖼️ **Wekelijkse controle: foto's in de sheets**\n"
            f"{regels}\n"
            "In de bijlagen staat per foto waar hij nu staat en bij welk voertuig hij waarschijnlijk hoort."
        )
    }
    mimes = {".xlsx": XLSX_MIME, ".md": "text/markdown"}
    handles = [open(name, "rb") for name in filenames]
    try:
        files = {
            f"file{i}": (name, handle, mimes.get(os.path.splitext(name)[1], "application/octet-stream"))
            for i, (name, handle) in enumerate(zip(filenames, handles))
        }
        response = requests.post(DISCORD_WEBHOOK_URL, data=payload, files=files)
        response.raise_for_status()
        print("Successfully sent alert and files to Discord.")
    except Exception as e:
        print(f"Failed to send Discord alert with files: {e}")
    finally:
        for handle in handles:
            handle.close()


def write_step_summary(counts):
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("## Fotocontrole\n\n| Soort | Aantal |\n|---|---|\n")
        for kind, n in counts.items():
            f.write(f"| {kind} | {n} |\n")


def main():
    findings = []
    for region in REGIONS:
        rows = load_rows(region)
        print(f"{region}: {len(rows)} rows")
        findings += find_misplaced(rows)
        findings += find_dead_links(rows)

    order = ["Foto bestaat niet meer", "Foto hoort bij ander voertuig", "Voertuigfoto op postregel"]
    findings.sort(key=lambda f: (order.index(f[0]), f[1]["_region"], f[1]["_post"], str(f[1].get("Roepnummer", ""))))
    counts = {kind: sum(1 for f in findings if f[0] == kind) for kind in order}
    print(counts)
    write_step_summary(counts)

    if not findings:
        print("No photo problems found!")
        return
    base = f"fotocontrole_{datetime.now(timezone.utc):%Y-%m-%d}"
    save_to_xlsx(findings, base + ".xlsx")
    save_to_md(findings, counts, base + ".md")
    send_discord_alert_with_files(counts, [base + ".xlsx", base + ".md"])


if __name__ == "__main__":
    main()
