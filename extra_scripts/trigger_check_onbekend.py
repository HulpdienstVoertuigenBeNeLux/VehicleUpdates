import os
import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
REPO = "HulpdienstVoertuigenBeNeLux/VehicleUpdates"
WORKFLOW_FILE = "check_onbekend.yml"
BRANCH = "master"
SEND_SUCCESS_MESSAGES = False

def send_discord_message(message):
    if not DISCORD_WEBHOOK_URL:
        print("Discord webhook URL not set, skipping Discord notification.")
        return
    data = {"content": f"{message}"}
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=data)
        if resp.status_code not in (200, 204):
            print(f"Failed to send Discord message: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"Exception sending Discord message: {e}")

def trigger_github_workflow():
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    data = {"ref": BRANCH}
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 204:
        print(f"Workflow {WORKFLOW_FILE} triggered successfully.")
        if SEND_SUCCESS_MESSAGES:
            send_discord_message(f":white_check_mark: GitHub workflow {WORKFLOW_FILE} triggered.")
    else:
        msg = f"Failed to trigger workflow {WORKFLOW_FILE}: {response.status_code} {response.text}"
        print(msg)
        send_discord_message(f":x: {msg}")

def main():
    if not GITHUB_TOKEN:
        msg = "Error: GITHUB_TOKEN environment variable not set."
        print(msg)
        send_discord_message(f":x: {msg}")
        return
    trigger_github_workflow()

if __name__ == "__main__":
    main()
