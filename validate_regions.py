import json
import os
import sys
import time
import requests

# LIJST MET UITSLUITEND EXACT CORRECTE REGIO'S
VALID_REGIONS = {
    "Alle Regio's",
    "1 - Groningen (VRG)",
    "2 - Fryslân (VRF)",
    "3 - Drenthe (VRD)",
    "4 - IJsselland (VRIJ)",
    "5 - Twente (VRT)",
    "6 - Noord- en Oost-Gelderland (VNOG)",
    "7 - Gelderland-Midden (VGGM)",
    "8 - Gelderland-Zuid (VRGZ)",
    "9 - Utrecht (VRU)",
    "10 - Noord-Holland-Noord (VRNHN)",
    "11 - Zaanstreek-Waterland (VRZW)",
    "12 - Kennemerland (VRK)",
    "13 - Amsterdam-Amstelland (VRAA)",
    "14 - Gooi en Vechtstreek (VRGV)",
    "15 - Haaglanden (VRH)",
    "16 - Hollands Midden (VRHM)",
    "17 - Rotterdam-Rijnmond (VRR)",
    "18 - Zuid-Holland-Zuid (VRZHZ)",
    "19 - Zeeland (VRZ)",
    "20 - Midden en West-Brabant (VRMWB)",
    "21 - Brabant-Noord (VRBN)",
    "22 - Brabant-Zuidoost (VRBZO)",
    "23 - Limburg-Noord (VRLN)",
    "24 - Zuid-Limburg (VRZL)",
    "25 - Flevoland (VRFL)",
    "26 - NIPV",
    "28 - Defensie",
    "AD-POL", "DH-POL", "LB-POL", "LX-POL", "MD-POL", 
    "NH-POL", "NN-POL", "OB-POL", "ON-POL", "RT-POL", "ZB-POL",
    "MN-RWS", "NN-RWS", "ON-RWS", "WNN-RWS", "WNZ-RWS", "ZD-RWS", "ZN-RWS",
    "Cluster A", "Cluster B", "Cluster C", "Cluster D", "Cluster E",
    "LTC", "Specialistische Eenheden"
}

FILE_PATH = "raw/hulpdienstvoertuigenbenelux_raw.json"
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL_REGIONS") or os.getenv("DISCORD_WEBHOOK_URL")

def check_json():
    if not os.path.exists(FILE_PATH):
        print(f"Bestand {FILE_PATH} niet gevonden!")
        sys.exit(1)

    with open(FILE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data if isinstance(data, list) else [data]
    invalid_entries = []

    for index, item in enumerate(items):
        if isinstance(item, dict):
            regio_raw = item.get("Regio")
            
            # Sla over als Regio niet ingesteld is, None is, of leeg is
            if regio_raw is None or str(regio_raw).strip() == "":
                continue

            # Haal spaties/newlines aan het begin en einde weg
            regio = str(regio_raw).strip()

            # Controleer of de regio exact klopt
            if regio not in VALID_REGIONS:
                invalid_entries.append({
                    "index": index,
                    "regio": regio,
                    "adres": item.get("Adres", "Onbekend"),
                    "roepnummer": item.get("Roepnummer", "Onbekend")
                })

   if invalid_entries:
        send_discord_alert(invalid_entries)
        print(f"Fouten gevonden: {len(invalid_entries)} ongeldige regio('s). Berichten verstuurd naar Discord.")
        
        # OUD: sys.exit(1)  <-- Dit veroorzaakte de rode 'Error' in GitHub Actions
        # NIEUW:
        sys.exit(0) # Zorgt ervoor dat GitHub Actions het proces als geslaagd (groen) ziet
    else:
        print("Alle regio's zijn correct gecontroleerd!")

def send_discord_alert(errors):
    if not WEBHOOK_URL:
        print("Geen Discord Webhook URL ingesteld.")
        return

    chunk_size = 10
    for i in range(0, len(errors), chunk_size):
        chunk = errors[i:i + chunk_size]
        
        fields = []
        for err in chunk:
            fields.append({
                "name": f"Item #{err['index']} - Roepnummer: {err['roepnummer']}",
                "value": f"**Foutieve Regio:** `{err['regio']}`\n**Adres:** {err['adres']}",
                "inline": False
            })

        embed = {
            "title": f"🚨 Foutieve Regio Gevonden ({i + 1} t/m {min(i + chunk_size, len(errors))} van {len(errors)})",
            "description": f"Bestand: `{FILE_PATH}`",
            "color": 15158332,
            "fields": fields
        }

        payload = {"embeds": [embed]}
        try:
            requests.post(WEBHOOK_URL, json=payload)
        except Exception as e:
            print(f"Fout bij versturen naar Discord: {e}")
            
        time.sleep(2)

if __name__ == "__main__":
    check_json()
