import csv
import os
import requests

# URL of the JSON endpoint
JSON_URL = "https://hulpdienstvoertuigenbenelux.nl/fetch-sheet?region=NL"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # Loaded from GitHub Secrets


def fetch_and_check():
    print("Fetching JSON data...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://hulpdienstvoertuigenbenelux.nl/",
    }

    try:
        response = requests.get(JSON_URL, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch URL: {e}")
        return

    if not response.text.strip():
        print("Error: Received an empty response from server.")
        return

    try:
        data = response.json()
    except requests.exceptions.JSONDecodeError:
        print("Error: Response was not valid JSON.")
        print(f"First 200 characters of response content:\n{response.text[:200]}")
        return

    values = data.get("values", [])

    # Headers zijn meestal rond rij-index 2
    headers_row = values[2] if len(values) > 2 else []
    onbekend_entries = []

    for index, row in enumerate(values):
        row_str_repr = [str(item) for item in row]
        if any("ONBEKEND" in item.upper() for item in row_str_repr):
            onbekend_entries.append({"row_number": index + 1, "row_data": row})

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

        if headers:
            writer.writerow(["Row Number"] + headers)
        else:
            writer.writerow(["Row Number", "Row Data"])

        for entry in entries:
            row_num = entry["row_number"]
            row_data = list(entry["row_data"])

            # Kolom C is index 2.
            # Waarden zoals 'Jan-81', 'Mar-94' worden door Excel ten onrechte als datum gezien.
            # De apostrof (') werkt in Excel als tekst-indicator.
            if len(row_data) > 2:
                val = str(row_data[2])
                if val:
                    if not val.startswith("'"):
                        row_data[2] = f"'{val}"

            writer.writerow([row_num] + row_data)


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
