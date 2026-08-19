import os
import datetime
import json
import requests
import sys
import shutil
from typing import Any, Optional
import time


RAW_DIR = "raw"


REGION_CONFIGS = {
    "NL": {
        "updates_path": "updates.json",
        "local_file": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_raw.json"),
        "webhook_env": "DISCORD_WEBHOOK_URL_NL",
        "discord_username": "[NL] HulpdienstVoertuigenBeNeLux",
    },
    "BE": {
        "updates_path": "updates.json",
        "local_file": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_be_raw.json"),
        "webhook_env": "DISCORD_WEBHOOK_URL_BE",
        "discord_username": "[BE] HulpdienstVoertuigenBeNeLux",
    },
    "LUX": {
        "updates_path": "updates.json",
        "local_file": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_lux_raw.json"),
        "webhook_env": "DISCORD_WEBHOOK_URL_LUX",
        "discord_username": "[LUX] HulpdienstVoertuigenBeNeLux",
    },
    "DE": {
        "updates_path": "updates_DE.json",
        "local_file": os.path.join(RAW_DIR, "hulpdienstvoertuigenbenelux_de_raw.json"),
        "webhook_env": "DISCORD_WEBHOOK_URL_DE",
        "discord_username": "[DE] HulpdienstVoertuigenBeNeLux",
    },
}


def ensure_raw_file_path(path: str) -> str:
    os.makedirs(RAW_DIR, exist_ok=True)
    basename = os.path.basename(path)
    raw_path = os.path.join(RAW_DIR, basename)
    root_path = basename
    if os.path.exists(root_path) and not os.path.exists(raw_path):
        shutil.move(root_path, raw_path)
    return raw_path

def download_json(url: str) -> list:
    headers = {
        # Some hosting layers reject non-browser clients unless common headers are present.
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://hulpdienstvoertuigenbenelux.nl/",
    }

    retryable_status_codes = {415, 429, 500, 502, 503, 504}
    max_attempts = 4
    retry_delay_seconds = 30
    data = None

    for attempt in range(1, max_attempts + 1):
        attempt_headers = headers
        if attempt > 1:
            # Include explicit content-type on retries for strict proxy/WAF paths.
            attempt_headers = {**headers, "Content-Type": "application/json"}

        try:
            response = requests.get(url, headers=attempt_headers, timeout=30)

            if response.status_code in retryable_status_codes and attempt < max_attempts:
                print(
                    f"Request failed with status {response.status_code}. "
                    f"Retrying in {retry_delay_seconds}s (attempt {attempt}/{max_attempts})..."
                )
                time.sleep(retry_delay_seconds)
                continue

            response.raise_for_status()
            data = response.json()
            break
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= max_attempts:
                raise
            print(
                f"Request error: {exc}. Retrying in {retry_delay_seconds}s "
                f"(attempt {attempt}/{max_attempts})..."
            )
            time.sleep(retry_delay_seconds)

    if data is None:
        raise RuntimeError(f"Failed to download JSON after {max_attempts} attempts: {url}")

    # Support both spreadsheet-style payloads ({"values": [[...], ...]}) and
    # direct list payloads from fallback/proxy responses.
    if isinstance(data, list) and all(isinstance(row, dict) for row in data):
        return data

    if isinstance(data, dict):
        if isinstance(data.get("values"), list):
            values = data["values"]
        else:
            # Some wrappers nest the rows under another top-level key.
            values = next((v for v in data.values() if isinstance(v, list)), None)
    elif isinstance(data, list):
        values = data
    else:
        values = None

    if not isinstance(values, list):
        raise ValueError(f"Unexpected online JSON shape: {type(data).__name__}")
    # Known local schema headers; only headers present in the source are emitted.
    local_headers = [
        "Adres",
        "DE Afkorting",
        "Roepnummer",
        "Afkorting",
        "TypeVoertuig",
        "Kenteken",
        "Bijzonderheden",
        "Hulpdienst",
        "Regio",
        "Interne opmerking"
    ]

    def normalize_header_name(value: Any) -> str:
        return str(value).strip().lower().replace("_", " ")

    header_aliases = {
        "adres": "Adres",
        "de afkorting": "DE Afkorting",
        "roepnummer": "Roepnummer",
        "roepnr": "Roepnummer",
        "afkorting": "Afkorting",
        "typevoertuig": "TypeVoertuig",
        "type voertuig": "TypeVoertuig",
        "kenteken": "Kenteken",
        "bijzonderheden": "Bijzonderheden",
        "hulpdienst": "Hulpdienst",
        "regio": "Regio",
        "interne opmerking": "Interne opmerking",
    }

    # Find the header row by looking for known column names.
    header = None
    for row in values:
        if not isinstance(row, list):
            continue
        normalized_cells = [normalize_header_name(cell) for cell in row if str(cell).strip() != ""]
        recognized = sum(1 for cell in normalized_cells if cell in header_aliases)
        # Require at least 3 recognized headers to avoid matching title rows.
        if recognized >= 3:
            header = row
            break

    if not header:
        # fallback: first row with multiple non-empty cells
        for row in values:
            if isinstance(row, list) and sum(1 for cell in row if str(cell).strip() != '') >= 3:
                header = row
                break

    if not header:
        raise ValueError("Could not find header row in online JSON file.")

    header_idx = values.index(header)
    data_rows = values[header_idx+1:]
    data_rows = [
        row for row in data_rows
        if isinstance(row, list) and any(str(cell).strip() != '' for cell in row)
    ]

    index_by_local_header: dict[str, int] = {}
    for i, col in enumerate(header):
        normalized = normalize_header_name(col)
        mapped = header_aliases.get(normalized)
        if not mapped and "type" in normalized and "voertuig" in normalized:
            mapped = "TypeVoertuig"
        if mapped and mapped not in index_by_local_header:
            index_by_local_header[mapped] = i

    active_headers = [h for h in local_headers if h in index_by_local_header]

    result = []
    for row in data_rows:
        item = {key: "" for key in active_headers}
        for key, idx in index_by_local_header.items():
            if idx < len(row):
                item[key] = row[idx]

        # Positional fallback for legacy/unknown headers.
        if not index_by_local_header:
            n = len(local_headers)
            row = row + [''] * (n - len(row))
            item = {local_headers[i]: row[i] for i in range(n)}

        if item.get("Interne opmerking") is None:
            item["Interne opmerking"] = ""
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

def compare_json(old: Any, new: Any, region: str = "") -> dict:
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
    region = region.upper()

    def get_unique_id(item):
        hulpdienst = item.get('Hulpdienst', '').strip().lower()
        roep = item.get('Roepnummer', '').strip().upper()
        afkorting = item.get('Afkorting', '').strip().upper()
        type_voertuig = item.get('TypeVoertuig', '').strip().upper()
        kenteken = item.get('Kenteken', '').strip().upper()
        adres = item.get('Adres', '').strip().upper()

        # Luxembourg must only match by Kenteken.
        if region == "LUX":
            if is_valid_kenteken(kenteken):
                return f"KENTEKEN:{kenteken}"
            return None

        # Ziekenhuizen use Afkorting as unique key, with TypeVoertuig as fallback.
        if hulpdienst == 'ziekenhuizen':
            if afkorting:
                return f"ZIEKENHUIZEN_AFKORTING:{afkorting}"
            elif type_voertuig:
                return f"ZIEKENHUIZEN_TYPEVOERTUIG:{type_voertuig}"
            return None

        # Penitentiaire Inrichting uses TypeVoertuig as unique key.
        if hulpdienst == 'penitentiaire inrichting':
            if type_voertuig:
                return f"PENITENTIAIRE_INRICHTING_TYPEVOERTUIG:{type_voertuig}"
            return None

        all_key_fields_empty = not roep and not afkorting and not type_voertuig and not kenteken
        if roep and roep not in ['GEEN', 'ONBEKEND', '-']:
            return f"ROEPNUMMER:{roep}"
        elif is_valid_kenteken(kenteken):
            return f"KENTEKEN:{kenteken}"
        elif adres and not all_key_fields_empty:
            return f"ADRES:{adres}"
        return None

    old_dict = {get_unique_id(item): item for item in old if get_unique_id(item)}
    new_dict = {get_unique_id(item): item for item in new if get_unique_id(item)}

    added = [new_dict[k] for k in new_dict if k not in old_dict]
    removed = [old_dict[k] for k in old_dict if k not in new_dict]
    changed = []
    for k in new_dict:
        if k in old_dict:
            old_item = old_dict[k]
            new_item = new_dict[k]
            old_hulpdienst = old_item.get('Hulpdienst', '').strip().lower()
            new_hulpdienst = new_item.get('Hulpdienst', '').strip().lower()

            # A vehicle change cannot cross hulpdienst boundaries.
            if old_hulpdienst != new_hulpdienst:
                removed.append(old_item)
                added.append(new_item)
            elif old_item != new_item:
                changed.append({'key': k, 'old': old_item, 'new': new_item})

    if region == "LUX":
        return {'added': added, 'removed': removed, 'changed': changed}

    # Detect Roepnummer changes by checking if a removed Roepnummer's Kenteken still exists in the new data with a different Roepnummer

    # Fallback for removals: if Roepnummer is ONBEKEND/GEEN, use Kenteken to match
    removed_copy = removed[:]
    added_copy = added[:]
    kenteken_to_new = {item.get('Kenteken', '').strip().upper(): item for item in new}
    kenteken_to_old = {item.get('Kenteken', '').strip().upper(): item for item in old}
    for old_item in removed_copy:
        hulpdienst = old_item.get('Hulpdienst', '').strip().lower()
        roep = old_item.get('Roepnummer', '').strip().upper()
        afkorting = old_item.get('Afkorting', '').strip().upper()
        type_voertuig = old_item.get('TypeVoertuig', '').strip().upper()
        kenteken = old_item.get('Kenteken', '').strip().upper()
        adres = old_item.get('Adres', '').strip().upper()
        all_key_fields_empty = not roep and not afkorting and not type_voertuig and not kenteken

        if hulpdienst == 'ziekenhuizen':
            new_item = None
            if afkorting:
                afkorting_to_new = {item.get('Afkorting', '').strip().upper(): item for item in new if item.get('Hulpdienst', '').strip().lower() == 'ziekenhuizen'}
                new_item = afkorting_to_new.get(afkorting)
            if not new_item and type_voertuig:
                type_to_new = {item.get('TypeVoertuig', '').strip().upper(): item for item in new if item.get('Hulpdienst', '').strip().lower() == 'ziekenhuizen'}
                new_item = type_to_new.get(type_voertuig)

            if new_item and old_item != new_item:
                changed.append({'key': f"{old_item.get('Afkorting','')}->{new_item.get('Afkorting','')}", 'old': old_item, 'new': new_item})
                if old_item in removed:
                    removed.remove(old_item)
                if new_item in added:
                    added.remove(new_item)
            continue

        # If Roepnummer is invalid
        if roep in ['GEEN', 'ONBEKEND', '-']:
            # If Kenteken is valid, try to match by Kenteken
            if is_valid_kenteken(kenteken) and kenteken in kenteken_to_new:
                new_item = kenteken_to_new[kenteken]
                if old_item.get('Hulpdienst', '').strip().lower() == new_item.get('Hulpdienst', '').strip().lower() and old_item.get('Roepnummer', '').strip() != new_item.get('Roepnummer', '').strip():
                    changed.append({'key': f"{old_item.get('Roepnummer','')}->{new_item.get('Roepnummer','')}", 'old': old_item, 'new': new_item})
                    if old_item in removed:
                        removed.remove(old_item)
                    if new_item in added:
                        added.remove(new_item)
            # If Kenteken is invalid, try to match by Adres
            elif adres and not all_key_fields_empty:
                adres_to_new = {item.get('Adres', '').strip().upper(): item for item in new}
                if adres in adres_to_new:
                    new_item = adres_to_new[adres]
                    if old_item.get('Hulpdienst', '').strip().lower() == new_item.get('Hulpdienst', '').strip().lower() and old_item.get('Roepnummer', '').strip() != new_item.get('Roepnummer', '').strip():
                        changed.append({'key': f"{old_item.get('Roepnummer','')}->{new_item.get('Roepnummer','')}", 'old': old_item, 'new': new_item})
                        if old_item in removed:
                            removed.remove(old_item)
                        if new_item in added:
                            added.remove(new_item)
        # If Roepnummer is valid, fallback to Kenteken as before
        elif is_valid_kenteken(kenteken) and kenteken in kenteken_to_new:
            new_item = kenteken_to_new[kenteken]
            if old_item.get('Hulpdienst', '').strip().lower() == new_item.get('Hulpdienst', '').strip().lower() and old_item.get('Roepnummer', '').strip() != new_item.get('Roepnummer', '').strip():
                changed.append({'key': f"{old_item.get('Roepnummer','')}->{new_item.get('Roepnummer','')}", 'old': old_item, 'new': new_item})
                if old_item in removed:
                    removed.remove(old_item)
                if new_item in added:
                    added.remove(new_item)

    return {'added': added, 'removed': removed, 'changed': changed}


def run_region(region: str) -> None:
    region = region.upper()
    if region not in REGION_CONFIGS:
        raise ValueError(f"Unsupported region '{region}'. Expected one of: {', '.join(REGION_CONFIGS)}")

    config = REGION_CONFIGS[region]
    updates_path = config["updates_path"]
    local_file = ensure_raw_file_path(config["local_file"])

    print(f"=== Processing {region} ===")

    # Remove all updates entries older than 1 month
    try:
        if os.path.exists(updates_path):
            with open(updates_path, "r", encoding="utf-8") as f:
                updates = json.load(f)
        else:
            updates = []
    except Exception:
        updates = []

    today = datetime.datetime.now().date()
    one_month_ago = today - datetime.timedelta(days=31)
    def parse_date(entry):
        try:
            return datetime.datetime.strptime(entry.get("date", ""), "%Y-%m-%d").date()
        except Exception:
            return None
    updates = [entry for entry in updates if parse_date(entry) and parse_date(entry) >= one_month_ago]
    with open(updates_path, "w", encoding="utf-8") as f:
        json.dump(updates, f, ensure_ascii=False, indent=2)

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

    discord_webhook_url = os.environ.get(config["webhook_env"], "")

    def send_discord_embed(title, description, color):
        if not discord_webhook_url:
            print("No Discord webhook URL set. Skipping Discord notification.")
            return
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat()
        }
        data = {
            "username": config["discord_username"],
            "embeds": [embed]
        }
        try:
            response = requests.post(discord_webhook_url, json=data)
            if response.status_code >= 400:
                print(f"Failed to send Discord message: {response.status_code} {response.text}")
        except Exception as e:
            print(f"Error sending Discord message: {e}")
        time.sleep(3)

    url = f"https://hulpdienstvoertuigenbenelux.nl/fetch-sheet?region={region}"


    print("Downloading latest JSON...")
    try:
        new_json = download_json(url)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(f"Failed to download or parse online JSON for region {region}: {exc}")
        print("Skipping this region for now so the workflow can continue.")
        return
    print(f"Loaded {len(new_json)} records from online.")
    # Filter out unwanted Hulpdienst categories
    exclude_hulpdiensten = {"hulpdienst", "alle hulpdiensten"}
    compare_new_json = [item for item in new_json if item.get('Hulpdienst', '').strip().lower() not in exclude_hulpdiensten]

    print("Loading local JSON...")
    if not os.path.exists(local_file):
        print("Local file not found. First run: saving current data and exiting.")
        with open(local_file, 'w', encoding='utf-8') as f:
            json.dump(new_json, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(new_json)} records to {local_file}. Run again to start tracking changes.")
        return
    old_json = load_local_json(local_file)
    print(f"Loaded {len(old_json)} records from local file.")
    # Filter out unwanted Hulpdienst categories
    compare_old_json = [item for item in old_json if item.get('Hulpdienst', '').strip().lower() not in exclude_hulpdiensten]



    def log(msg):
        print(msg)

    log("Comparing...")
    result = compare_json(compare_old_json, compare_new_json, region)
    removed_count = len(result['removed'])
    added_count = len(result['added'])
    if removed_count > 100 and added_count * 10 < removed_count:
        log(
            "Suspicious removal spike detected; skipping notifications and snapshot update "
            f"for {region} ({removed_count} removed, {added_count} added)."
        )
        return

    log(f"Added: {added_count}")
    log(f"Removed: {removed_count}")
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
                if field == 'Regio':
                    continue
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
                "Land": region,
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
                "Land": region,
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
                old_regio = old.get("Regio", "")
                new_regio = new.get("Regio", "")
                if old_regio != new_regio:
                    lines.append(f"Regio: {old_regio} --> {new_regio}")

                # Then show changed fields from both sides, excluding already handled fields.
                ignore_fields = {"Adres", "Roepnummer", "Regio"}
                all_fields = sorted(set(old.keys()) | set(new.keys()))
                for k in all_fields:
                    if k in ignore_fields:
                        continue
                    old_value = old.get(k, "")
                    new_value = new.get(k, "")
                    if old_value != new_value:
                        lines.append(f"{k}: {old_value} --> {new_value}")
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
                    "Land": region,
                    "Hulpdienst": item['old'].get("Hulpdienst", ""),
                    "Regio": item['old'].get("Regio", ""),
                    "Description": desc,
                    "Time": now.strftime("%d-%m-%Y %H:%M:%S")
                })



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


def parse_regions(args: list[str]) -> list[str]:
    if not args:
        return ["NL"]

    normalized = [arg.upper() for arg in args]
    if "ALL" in normalized:
        return list(REGION_CONFIGS.keys())

    invalid_regions = [region for region in normalized if region not in REGION_CONFIGS]
    if invalid_regions:
        raise ValueError(
            f"Unsupported region(s): {', '.join(invalid_regions)}. Use NL, BE, or ALL."
        )

    return normalized


def main(args: Optional[list[str]] = None) -> None:
    regions = parse_regions(sys.argv[1:] if args is None else args)
    for region in regions:
        run_region(region)

if __name__ == "__main__":
    main()
