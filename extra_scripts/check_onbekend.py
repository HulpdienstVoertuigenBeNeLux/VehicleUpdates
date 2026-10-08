import json
import os
from collections import Counter
from datetime import datetime, timezone

import requests
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_FILE = os.path.join(BASE_DIR, "raw", "hulpdienstvoertuigenbenelux_raw.json")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # Loaded from GitHub Secrets

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def fetch_and_check():
    print(f"Loading raw data from {RAW_FILE}...")

    try:
        with open(RAW_FILE, encoding="utf-8") as f:
            records = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Failed to load raw data: {e}")
        return

    headers_row = []
    for record in records:
        for key in record:
            if key not in headers_row:
                headers_row.append(key)

    onbekend_entries = [
        record for record in records
        if any("ONBEKEND" in str(value).upper() for value in record.values())
    ]

    print(f"Found {len(onbekend_entries)} rows containing 'ONBEKEND'.")

    if onbekend_entries:
        # Sla op als Excel-bestand: blad 1 overzicht, blad 2 de rijen zelf
        filename = f"onbekend_{datetime.now(timezone.utc):%Y-%m-%d}.xlsx"
        save_to_xlsx(onbekend_entries, headers_row, filename)

        # Verstuur naar Discord als bestand
        send_discord_alert_with_file(onbekend_entries, filename)
    else:
        print("No 'ONBEKEND' values found!")


def _autosize(sheet):
    for column_cells in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[get_column_letter(column_cells[0].column)].width = min(width + 2, 60)


def _write_counts(sheet, title, counter):
    sheet.append([title, "Aantal"])
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
    for name, count in counter.most_common():
        sheet.append([name or "(leeg)", count])
    sheet.append([])


def save_to_xlsx(entries, headers, filename):
    print(f"Saving results to {filename}...")
    workbook = Workbook()

    # Blad 1: overzicht
    overzicht = workbook.active
    overzicht.title = "Overzicht"
    overzicht.append(["Controle ONBEKEND-waarden"])
    overzicht["A1"].font = Font(bold=True, size=14)
    overzicht.append(["Datum", datetime.now(timezone.utc).strftime("%Y-%m-%d")])
    overzicht.append(["Totaal rijen met ONBEKEND", len(entries)])
    overzicht.append([])

    per_veld = Counter(
        key for entry in entries for key, value in entry.items()
        if "ONBEKEND" in str(value).upper()
    )
    _write_counts(overzicht, "Per veld", per_veld)
    _write_counts(overzicht, "Per hulpdienst", Counter(entry.get("Hulpdienst", "") for entry in entries))
    _write_counts(overzicht, "Per regio", Counter(entry.get("Regio", "") for entry in entries))
    _autosize(overzicht)

    # Blad 2: alle rijen met ONBEKEND. Alles als tekst, zodat Excel
    # roepnummers zoals '01-81' niet als datum (Jan-81) leest.
    data = workbook.create_sheet("Onbekend")
    data.append(headers)
    for cell in data[1]:
        cell.font = Font(bold=True)
    for entry in entries:
        data.append([str(entry.get(column, "") or "") for column in headers])
    for row in data.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = "@"
    data.freeze_panes = "A2"
    data.auto_filter.ref = data.dimensions
    _autosize(data)

    workbook.save(filename)


def send_discord_alert_with_file(entries, filename):
    if not DISCORD_WEBHOOK_URL:
        print(f"Discord Webhook URL not set. File is saved locally as {filename}.")
        return

    print("Sending file to Discord...")

    payload = {
        "content": (
            "🔎 **Wekelijkse controle: ontbrekende gegevens**\n"
            f"Bij **{len(entries)} rijen** staat nog `ONBEKEND` ingevuld. "
            "In de bijlage vind je een overzicht en de volledige lijst."
        )
    }

    try:
        with open(filename, "rb") as f:
            files = {
                "file": (filename, f, XLSX_MIME)
            }
            response = requests.post(DISCORD_WEBHOOK_URL, data=payload, files=files)
            response.raise_for_status()
            print("Successfully sent alert and file to Discord.")
    except Exception as e:
        print(f"Failed to send Discord alert with file: {e}")


if __name__ == "__main__":
    fetch_and_check()
