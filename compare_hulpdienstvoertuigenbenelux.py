import os
import datetime
import json
import requests
from typing import Any
import time

def download_json(url: str) -> list:
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    values = data.get('values') if isinstance(data, dict) and 'values' in data else data
    # Find the header row (first row with all non-empty values)
    header = None
    for row in values:
        if row and all(cell.strip() != '' for cell in row):
            header = row
            break
    if not header:
        # fallback: first non-empty row with at least 2 non-empty cells
        for row in values:
            if row and sum(1 for cell in row if cell.strip() != '') >= 2:
                header = row
                break
    if not header:
        raise ValueError("Could not find header row in online JSON file.")
    header_idx = values.index(header)
    data_rows = values[header_idx+1:]
    data_rows = [row for row in data_rows if any(cell.strip() != '' for cell in row)]

    # Map online headers to local headers by position
    # Local headers (fixed, as seen in the local file)
    local_headers = [
        "Adres",
        "Roepnummer",
        "Afkorting",
        "TypeVoertuig",
        "Kenteken",
        "Bijzonderheden",
        "Hulpdienst",
        "Regio",
        "Interne opmerking"
    ]
    # Only map as many columns as available in both
    n = len(local_headers)
    result = []
    for row in data_rows:
        row = row + [''] * (n - len(row))
        item = {local_headers[i]: row[i] for i in range(n)}
        result.append(item)
    return result

def load_local_json(filepath: str) -> list:
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # If the data is already a list of dicts, return as is (skip header logic)
    if isinstance(data, list) and all(isinstance(row, dict) for row in data):
        return data

    # Otherwise, treat as list of lists (spreadsheet style)
    values = data.get('values') if isinstance(data, dict) and 'values' in data else data
    header = None
    for row in values:
        if row and all(isinstance(cell, str) and cell.strip() != '' for cell in row):
            header = row
            break
    if not header:
        # fallback: first non-empty row with at least 2 non-empty cells
        for row in values:
            if row and sum(1 for cell in row if isinstance(cell, str) and cell.strip() != '') >= 2:
                header = row
                break
    if not header:
        raise ValueError("Could not find header row in local JSON file.")
    # Find the index of the header row
    header_idx = values.index(header)
    # All rows after header are data rows
    data_rows = values[header_idx+1:]
    # Only keep rows with at least one non-empty value
    data_rows = [row for row in data_rows if any(isinstance(cell, str) and cell.strip() != '' for cell in row)]
    n = len(header)
    result = []
    for row in data_rows:
        if not isinstance(row, list):
            continue
        row = row + [''] * (n - len(row))
        item = {header[i]: row[i] for i in range(n)}
        result.append(item)
    return result


def is_valid_kenteken(kenteken):
    return kenteken and kenteken.upper() not in ['GEEN', 'ONBEKEND', '-']

def compare_json(old: Any, new: Any) -> dict:
    """
    Compares two JSON objects (assumed to be lists of dicts) and returns added, removed, and changed items.
    """
    if not isinstance(old, list) or not isinstance(new, list):
        raise ValueError("Both JSON files must be lists of objects.")

    # Use a unique key for comparison, e.g., 'Roepnummer' or 'Kenteken' if present
    def normalize_dict(d):
        return {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in d.items()}

    old = [normalize_dict(item) for item in old]
    new = [normalize_dict(item) for item in new]

    key = None
    candidate_keys = ['Roepnummer', 'Kenteken', 'id', 'nummerplaat', 'license', 'license_plate']
    for k in candidate_keys:
        if old and k in old[0]:
            key = k
            break
        if new and k in new[0]:
            key = k
            break
    if not key:
        raise ValueError("Could not determine a unique key for comparison.")

    old_dict = {item[key]: item for item in old if key in item and item[key]}
    new_dict = {item[key]: item for item in new if key in item and item[key]}

    added = [new_dict[k] for k in new_dict if k not in old_dict]
    removed = [old_dict[k] for k in old_dict if k not in new_dict]
    changed = [
        {'key': k, 'old': old_dict[k], 'new': new_dict[k]}
        for k in new_dict if k in old_dict and old_dict[k] != new_dict[k]
    ]

    # Detect Roepnummer changes by checking if a removed Roepnummer's Kenteken still exists in the new data with a different Roepnummer
    removed_copy = removed[:]
    added_copy = added[:]
    kenteken_to_new = {item.get('Kenteken', '').strip(): item for item in new}
    kenteken_to_old = {item.get('Kenteken', '').strip(): item for item in old}
    for old_item in removed_copy:
        kenteken = old_item.get('Kenteken', '').strip()
        if not is_valid_kenteken(kenteken):
            continue
        if kenteken and kenteken in kenteken_to_new:
            new_item = kenteken_to_new[kenteken]
            # Only treat as a Roepnummer change if Roepnummer is different
            if old_item.get('Roepnummer', '').strip() != new_item.get('Roepnummer', '').strip():
                changed.append({'key': f"{old_item.get('Roepnummer','')}->{new_item.get('Roepnummer','')}", 'old': old_item, 'new': new_item})
                if old_item in removed:
                    removed.remove(old_item)
                if new_item in added:
                    added.remove(new_item)

    return {'added': added, 'removed': removed, 'changed': changed}


def main():
    # Prepare changelog for updates.json
    now = datetime.datetime.now()
    changelog = {"date": now.strftime("%Y-%m-%d"), "added": [], "removed": [], "changed": []}

    # Format for added/removed: Hulpdienst, Regio, Description
    def make_description(item, action):
        # Compose a short description for the changelog
        if action == "added":
            return f"{item.get('Roepnummer', '')} {item.get('Afkorting', '')} toegevoegd aan {item.get('Adres', '')}"
        elif action == "removed":
            return f"{item.get('Roepnummer', '')} {item.get('Afkorting', '')} verwijderd van {item.get('Adres', '')}"
        return ""

    # Discord webhook URL from environment variable or hardcoded (replace with your webhook if needed)
    DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")  # or set your webhook URL here

    def send_discord_embed(title, description, color):
        if not DISCORD_WEBHOOK_URL:
            print("No Discord webhook URL set. Skipping Discord notification.")
            return
        import requests
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat()
        }
        data = {"embeds": [embed]}
        try:
            response = requests.post(DISCORD_WEBHOOK_URL, json=data)
            if response.status_code >= 400:
                print(f"Failed to send Discord message: {response.status_code} {response.text}")
        except Exception as e:
            print(f"Error sending Discord message: {e}")
        time.sleep(3)

    url = "https://hulpdienstvoertuigenbenelux.nl/fetch-sheet?region=NL"
    local_file = "hulpdienstvoertuigenbenelux_raw.json"

    print("Downloading latest JSON...")
    new_json = download_json(url)
    print(f"Loaded {len(new_json)} records from online.")

    print("Loading local JSON...")
    old_json = load_local_json(local_file)
    print(f"Loaded {len(old_json)} records from local file.")



    def log(msg):
        print(msg)

    log("Comparing...")
    result = compare_json(old_json, new_json)
    log(f"Added: {len(result['added'])}")
    log(f"Removed: {len(result['removed'])}")
    log(f"Changed: {len(result['changed'])}")



    # Helper to format dict as 'Key: Value' lines

    def dict_to_lines(d):
        return '\n'.join([f"{k}: {v}" for k, v in d.items()])

    def changed_descriptions(old, new):
        old_roepnummer = old.get('Roepnummer', '')
        new_roepnummer = new.get('Roepnummer', '')
        descs = []
        for field in new:
            if field in old and new[field] != old[field]:
                if field == 'Roepnummer':
                    descs.append(f"'{old_roepnummer}' omgenummerd naar '{new_roepnummer}'")
                else:
                    descs.append(f"{old_roepnummer}: {field} van '{old[field]}' naar '{new[field]}' aangepast")
        return descs

    if result['added']:
        log("\nAdded items:")
        for item in result['added']:
            log(item)
            send_discord_embed(
                title="Voertuig toegevoegd",
                description=dict_to_lines(item),
                color=0x00ff00
            )
            changelog["added"].append({
                "Hulpdienst": item.get("Hulpdienst", ""),
                "Regio": item.get("Regio", ""),
                "Description": make_description(item, "added"),
                "Time": now.strftime("%d-%m-%Y %H:%M:%S")
            })
    if result['removed']:
        log("\nRemoved items:")
        for item in result['removed']:
            log(item)
            send_discord_embed(
                title="Voertuig verwijderd",
                description=dict_to_lines(item),
                color=0xff0000
            )
            changelog["removed"].append({
                "Hulpdienst": item.get("Hulpdienst", ""),
                "Regio": item.get("Regio", ""),
                "Description": make_description(item, "removed"),
                "Time": now.strftime("%d-%m-%Y %H:%M:%S")
            })
    if result['changed']:
        log("\nChanged items:")
        for item in result['changed']:
            log(f"Key: {item['key']}\nOld: {item['old']}\nNew: {item['new']}\n")
            # For Discord, show all changed fields in one message, but without 'Key:'
            def changed_fields_lines(old, new):
                lines = []
                # Show Adres change as old -> new
                if 'Adres' in old and 'Adres' in new and old['Adres'] != new['Adres']:
                    lines.append(f"Adres: {old['Adres']} -> {new['Adres']}")
                elif 'Adres' in old:
                    lines.append(f"Adres: {old['Adres']}")
                # Show Roepnummer change as old -> new
                if 'Roepnummer' in old and 'Roepnummer' in new and old['Roepnummer'] != new['Roepnummer']:
                    lines.append(f"Roepnummer: {old['Roepnummer']} -> {new['Roepnummer']}")
                elif 'Roepnummer' in old:
                    lines.append(f"Roepnummer: {old['Roepnummer']}")
                # Then show only changed fields (excluding Adres and Roepnummer)
                for k in old:
                    if k in new and old[k] != new[k] and k not in ["Adres", "Roepnummer"]:
                        lines.append(f"{k}: {old[k]} --> {new[k]}")
                return '\n'.join(lines)
            send_discord_embed(
                title="Voertuig gewijzigd",
                description=changed_fields_lines(item['old'], item['new']),
                color=0xffa500
            )
            # For updates.json, use the requested format
            descs = changed_descriptions(item['old'], item['new'])
            for desc in descs:
                changelog["changed"].append({
                    "Hulpdienst": item['old'].get("Hulpdienst", ""),
                    "Regio": item['old'].get("Regio", ""),
                    "Description": desc,
                    "Time": now.strftime("%d-%m-%Y %H:%M:%S")
                })



    # Insert changelog into updates.json, merging with today's entry if it exists
    updates_path = "updates.json"
    try:
        if os.path.exists(updates_path):
            with open(updates_path, "r", encoding="utf-8") as f:
                updates = json.load(f)
        else:
            updates = []
    except Exception:
        updates = []

    today = changelog["date"]
    found_today = False
    for entry in updates:
        if entry.get("date") == today:
            # Merge added, removed, changed
            entry["added"].extend(changelog["added"])
            entry["removed"].extend(changelog["removed"])
            entry["changed"].extend(changelog["changed"])
            found_today = True
            break
    if not found_today:
        updates.insert(0, changelog)


    with open(updates_path, "w", encoding="utf-8") as f:
        json.dump(updates, f, ensure_ascii=False, indent=2)

    # After all checks and logging, store the latest online version in the raw file
    with open(local_file, 'w', encoding='utf-8') as f:
        json.dump(new_json, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
