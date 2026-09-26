import json
import os
import re
import urllib.request

RAW_URL = "https://raw.githubusercontent.com/HulpdienstVoertuigenBeNeLux/VehicleUpdates/refs/heads/master/raw/hulpdienstvoertuigenbenelux_raw.json"
RDW_URL = "https://raw.githubusercontent.com/HulpdienstVoertuigenBeNeLux/VehicleUpdates/refs/heads/master/rdw_combined.json"

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL_ZITPLAATSEN")


def fetch_json(url):
  with urllib.request.urlopen(url) as response:
    return json.loads(response.read().decode())


def clean_plate(plate):
  return plate.replace("-", "").upper()


def extract_seats(bijzonderheden):
  match = re.search(r"Bezetting:\s*(\d+)", bijzonderheden)
  if match:
    return int(match.group(1))
  return None


def send_discord_alert(message):
  if not WEBHOOK_URL:
    print("Discord webhook URL is niet ingesteld.")
    return
  payload = {"content": message}
  data = json.dumps(payload).encode("utf-8")
  req = urllib.request.Request(
      WEBHOOK_URL,
      data=data,
      headers={"Content-Type": "application/json", "User-Agent": "Mozilla"},
  )
  try:
    urllib.request.urlopen(req)
    print("Discord melding succesvol verzonden.")
  except Exception as e:
    print(f"Fout bij versturen Discord melding: {e}")


def main():
  print("Beide JSON-bestanden worden opgehaald...")
  raw_data = fetch_json(RAW_URL)
  rdw_data = fetch_json(RDW_URL)

  # RDW-data omzetten naar een dictionary gebaseerd op het kenteken zonder streepjes
  rdw_dict = {}
  for item in rdw_data:
    plate = clean_plate(item.get("kenteken", ""))
    rdw_dict[plate] = item

  mismatches = 0

  for item in raw_data:
    raw_plate = item.get("Kenteken", "")
    clean_raw_plate = clean_plate(raw_plate)
    bijzonderheden = item.get("Bijzonderheden", "")

    raw_seats = extract_seats(bijzonderheden)
    if raw_seats is None:
      continue  # Sla over als er geen bezetting vermeld staat

    if clean_raw_plate in rdw_dict:
      rdw_item = rdw_dict[clean_raw_plate]
      rdw_seats = int(rdw_item.get("aantal_zitplaatsen", 0))

      if raw_seats != rdw_seats:
        mismatches += 1
        msg = (
            f"⚠️ **Zitplaatsen-mismatch gedetecteerd!**\n"
            f"- **Roepnummer:** {item.get('Roepnummer')}\n"
            f"- **Kenteken:** {raw_plate}\n"
            f"- **Hulpdienst:** {item.get('Hulpdienst')} ({item.get('Regio')})\n"
            f"- **Raw data (Bijzonderheden):** {raw_seats} personen\n"
            f"- **RDW data:** {rdw_seats} zitplaatsen"
        )
        print(msg)
        send_discord_alert(msg)

  print(f"Controle afgerond. Totaal aantal mismatches gevonden: {mismatches}")


if __name__ == "__main__":
  main()
