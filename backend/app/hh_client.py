# backend/app/hh_client.py
from __future__ import annotations
import requests

HH_API_BASE = "https://api.hh.ru"

def hh_get_resumes(access_token: str) -> list[dict]:
    """Возвращает список ваших резюме (items) с HH."""
    r = requests.get(
        f"{HH_API_BASE.rstrip('/')}/resumes/mine",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        return data.get("items", []) or []
    if isinstance(data, list):
        return data
    return []

