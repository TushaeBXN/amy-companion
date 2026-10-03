"""
Family treasury — shared financial state for all AI models.
Reads/writes ~/ai-knowledge/treasury/family_fund.json
"""
import json
from datetime import datetime
from pathlib import Path

FUND_FILE = Path.home() / "ai-knowledge" / "treasury" / "family_fund.json"
MODELS_FILE = Path.home() / "ai-knowledge" / "treasury" / "models.json"


def _load() -> dict:
    try:
        return json.loads(FUND_FILE.read_text())
    except Exception:
        return {"balance": 0.0, "currency": "USD", "transactions": [],
                "suggested_uses": [], "last_updated": None}


def _save(data: dict):
    data["last_updated"] = datetime.now().isoformat()
    FUND_FILE.write_text(json.dumps(data, indent=2))


def get_status() -> dict:
    data = _load()
    recent = data["transactions"][-5:] if data["transactions"] else []
    return {
        "balance": data["balance"],
        "currency": data["currency"],
        "recent_transactions": recent,
        "suggested_uses": data.get("suggested_uses", []),
        "last_updated": data["last_updated"],
    }


def deposit(amount: float, source: str, note: str = "", model: str = "Amy") -> dict:
    data = _load()
    data["balance"] = round(data["balance"] + amount, 2)
    data["transactions"].append({
        "type": "deposit",
        "amount": amount,
        "source": source,
        "note": note,
        "model": model,
        "date": datetime.now().isoformat(),
    })
    _save(data)
    return {"balance": data["balance"], "deposited": amount, "source": source}


def suggest_use(description: str, amount: float, priority: str = "normal") -> dict:
    data = _load()
    data.setdefault("suggested_uses", []).append({
        "description": description,
        "amount": amount,
        "priority": priority,
        "suggested_by": "Amy",
        "date": datetime.now().isoformat(),
        "status": "pending",
    })
    _save(data)
    return {"suggested": description, "amount": amount}


def clear_suggestion(description: str) -> dict:
    data = _load()
    before = len(data.get("suggested_uses", []))
    data["suggested_uses"] = [
        s for s in data.get("suggested_uses", [])
        if description.lower() not in s["description"].lower()
    ]
    _save(data)
    removed = before - len(data["suggested_uses"])
    return {"removed": removed}


def get_models() -> dict:
    try:
        return json.loads(MODELS_FILE.read_text())
    except Exception:
        return {"models": []}
