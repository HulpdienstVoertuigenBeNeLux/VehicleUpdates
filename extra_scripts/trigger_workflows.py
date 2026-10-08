import os
import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
REPO = "HulpdienstVoertuigenBeNeLux/VehicleUpdates"
WORKFLOW_FILES = [
    "run-hulpdienstvoertuigenbenelux.yml",
    "check_onbekend.yml",
]
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

def trigger_github_workflow(workflow_file):
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    data = {"ref": BRANCH}
    try:
        response = requests.post(url, headers=headers, json=data)
    except Exception as e:
        msg = f"Exception triggering workflow {workflow_file}: {e}"
        print(msg)
        send_discord_message(f":x: {msg}")
        return
    if response.status_code == 204:
        print(f"Workflow {workflow_file} triggered successfully.")
        if SEND_SUCCESS_MESSAGES:
            send_discord_message(f":white_check_mark: GitHub workflow {workflow_file} triggered.")
    else:
        msg = f"Failed to trigger workflow {workflow_file}: {response.status_code} {response.text}"
        print(msg)
        send_discord_message(f":x: {msg}")

def main():
    if not GITHUB_TOKEN:
        msg = "Error: GITHUB_TOKEN environment variable not set."
        print(msg)
        send_discord_message(f":x: {msg}")
        return
    for workflow_file in WORKFLOW_FILES:
        trigger_github_workflow(workflow_file)

if __name__ == "__main__":
    main()
