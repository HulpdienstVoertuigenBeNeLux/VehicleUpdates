import os
from typing import Any

import requests

ORANGE_COLOR = 16753920
_notified_kentekens: set[str] = set()


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_kenteken(value: Any) -> str:
    return _safe_text(value).replace("-", "").replace(" ", "").upper()


def _is_exported(record: dict[str, Any]) -> bool:
    export_indicator = _safe_text(record.get("export_indicator"))
    return export_indicator.lower() == "ja"


def _resolve_webhook_url() -> str:
    # Prefer DISCORD_APK for operational vehicle checks; fallback to RDW changes webhook.
    return _safe_text(os.getenv("DISCORD_APK")) or _safe_text(os.getenv("RDW_API_CHANGES_WEBHOOK"))


def notify_if_exported(record: dict[str, Any], source: str = "RDW lookup") -> bool:
    if not isinstance(record, dict):
        return False
    if not _is_exported(record):
        return False

    kenteken = _safe_text(record.get("kenteken"))
    normalized = _normalize_kenteken(kenteken)
    if not normalized:
        return False

    if normalized in _notified_kentekens:
        return False

    webhook_url = _resolve_webhook_url()
    if not webhook_url:
        return False

    payload = {
        "username": "HulpdienstVoertuigenBeNeLux RDW Export",
        "embeds": [
            {
                "title": "RDW export_indicator",
                "description": f"Voertuig met kenteken {kenteken} staat als geexporteerd (export_indicator=Ja). Bron: {source}.",
                "color": ORANGE_COLOR,
            }
        ],
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if 200 <= response.status_code < 300:
            _notified_kentekens.add(normalized)
            return True
        return False
    except requests.RequestException:
        return False
