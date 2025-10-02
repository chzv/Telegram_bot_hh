# backend/app/api/router.py
from __future__ import annotations
import importlib, logging
import app.core.compat             
from fastapi import APIRouter

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/api/v1")  

V1_MODULES = [
    "users", "hh_auth", "applications", "applications_dispatch", "jobs",
    "payments_cp", "cp_webhooks", "referrals", "metrics", "export", "subscriptions",
    "admin_dashboard",
    "admin_profile",     
    "admin_listings",    
    "billing",
    "hh_resumes", "saved_requests", "auto_responses", "auto",
    "cover_letters", "stats", "admin_subscriptions", "admin_applications", "admin_auto",
    "admin_analytics", "admin_notifications", "admin_tariffs", "admin_users", "admin_logs", "hh_webhook",
    "quota", "campaigns",
]

def _include_if_ok(mod_name: str) -> None:
    dotted = f"app.api.v1.{mod_name}" 
    try:
        mod = importlib.import_module(dotted)
        api_router.include_router(mod.router)
    except Exception as e:
        logger.warning("router %s import failed: %s", dotted, e)

for _m in V1_MODULES:
    _include_if_ok(_m)
