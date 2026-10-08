import csv
import json
import os
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_FILE = os.path.join(BASE_DIR, "raw", "hulpdienstvoertuigenbenelux_raw.json")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # Loaded from GitHub Secrets

# Roepnummers zoals '01-81' worden door Excel ten onrechte als datum gezien (Jan-81).
TEXT_COLUMNS = ["Roepnummer"]


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
        # Sla op als CSV
        csv_filename = "onbekend_entries.csv"
        save_to_csv(onbekend_entries, headers_row, csv_filename)

        # Verstuur naar Discord als bestand
        send_discord_alert_with_file(onbekend_entries, csv_filename)
    else:
        print("No 'ONBEKEND' values found!")


def save_to_csv(entries, headers, filename):
    print(f"Saving results to {filename}...")
    with open(filename, mode="w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(headers)

        for entry in entries:
            row_data = []
            for column in headers:
                val = str(entry.get(column, "") or "")
                # De apostrof (') werkt in Excel als tekst-indicator.
                if column in TEXT_COLUMNS and val and not val.startswith("'"):
                    val = f"'{val}"
                row_data.append(val)

            writer.writerow(row_data)


def send_discord_alert_with_file(entries, filename):
    if not DISCORD_WEBHOOK_URL:
        print(f"Discord Webhook URL not set. CSV is saved locally as {filename}.")
        return

    print("Sending CSV file to Discord...")

    payload = {
        "content": f"⚠️ **'ONBEKEND' values detected!** Totaal {len(entries)} rijen gevonden. Zie bijgevoegde CSV."
    }

    try:
        with open(filename, "rb") as f:
            files = {
                "file": (filename, f, "text/csv")
            }
            response = requests.post(DISCORD_WEBHOOK_URL, data=payload, files=files)
            response.raise_for_status()
            print("Successfully sent alert and CSV to Discord.")
    except Exception as e:
        print(f"Failed to send Discord alert with file: {e}")


if __name__ == "__main__":
    fetch_and_check()
