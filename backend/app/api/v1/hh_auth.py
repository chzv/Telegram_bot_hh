# backend/app/api/v1/hh_auth.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, TypedDict
from urllib.parse import urlencode
import os
import re
import time
import secrets
import string

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.db import SessionLocal

from app.hh_client import hh_get_resumes
from app.services.resumes import upsert_resumes
from app.services.referrals import attach_pending_ref_on_link_sync
import logging

from fastapi.responses import RedirectResponse

import json
from urllib.request import Request, urlopen

router = APIRouter(prefix="/hh", tags=["hh"])

# ---------- конфиг ----------
HH_OAUTH_BASE    = getattr(settings, "hh_oauth_base",    None) or os.getenv("HH_OAUTH_BASE", "https://hh.ru")
HH_CLIENT_ID     = getattr(settings, "hh_client_id",     None) or os.getenv("HH_CLIENT_ID", "")
HH_CLIENT_SECRET = getattr(settings, "hh_client_secret", None) or os.getenv("HH_CLIENT_SECRET", "")
HH_REDIRECT_URI  = getattr(settings, "hh_redirect_uri",  None) or os.getenv("HH_REDIRECT_URI", "")
HH_API_BASE      = getattr(settings, "hh_api_base",      None) or os.getenv("HH_API_BASE", "https://api.hh.ru")
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage" if BOT_TOKEN else None

HH_SCOPE = os.getenv("HH_SCOPE", "applicant_resumes offline")
# ---------- utils ----------
def _tg_send(chat_id: int, text_msg: str, reply_markup: dict | None = None, parse_mode: str | None = None) -> None:
    if not API_URL or not text_msg:
        return
    payload = {
        "chat_id": int(chat_id),
        "text": text_msg,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    data = urlencode(payload).encode("utf-8")
    try:
        req = Request(API_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(req, timeout=10):
            pass
    except Exception:
        pass

def _cases_kb() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "▶ Запустить отклики", "callback_data": "start_responses"}],
        ]
    }

def _main_menu_kb() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "▶️ Запустить отклики",         "callback_data": "start_responses"}],
            [{"text": "📝 Сопроводительные письма",   "callback_data": "cover_letters"}],
            [{"text": "💳 Подписка",                  "callback_data": "subscription"}],
            [{"text": "👥 Реферальная программа",     "callback_data": "referral"}],
            [{"text": "🛟 Поддержка",                 "callback_data": "support"}],
            [{"text": "⚙️ Настройки",                 "callback_data": "settings"}],
        ]
    }
    
class TokenRow(TypedDict):
    user_id: int
    access_token: str
    refresh_token: Optional[str]
    exp: Optional[int]  

def _nonce(n: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _get_tokens_by_tg(tg_id: int) -> Optional[TokenRow]:
    """Возвращает токены HH и user_id по tg_id."""
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT
                    u.id          AS user_id,
                    ht.access_token,
                    ht.refresh_token,
                    EXTRACT(EPOCH FROM ht.expires_at)::bigint AS exp
                FROM hh_tokens ht
                JOIN users u ON u.id = ht.user_id
                WHERE u.tg_id = :tg_id
                """
            ),
            {"tg_id": tg_id},
        ).mappings().first()

        if not row:
            return None

        return {
            "user_id": int(row["user_id"]),
            "access_token": row["access_token"],
            "refresh_token": row.get("refresh_token"),
            "exp": int(row["exp"]) if row.get("exp") is not None else None,
        }

def _get_hh_account_id_by_tg(tg_id: int) -> Optional[str]:
    with SessionLocal() as db:
        row = db.execute(
            text("SELECT hh_account_id FROM users WHERE tg_id = :tg_id"),
            {"tg_id": tg_id},
        ).first()
        return (row[0] if row and row[0] else None)


def _save_hh_account_info(tg_id: int, account_id: str, full_name: str) -> None:
    """Сохраняем в users: hh_account_id, hh_account_name."""
    full_name = (full_name or "").strip()
    account_id = (account_id or "").strip()

    with SessionLocal() as db:
        db.execute(
            text(
                """
                UPDATE users
                   SET hh_account_id   = :account_id,
                       hh_account_name = :full_name
                 WHERE tg_id = :tg_id
                """
            ),
            {"account_id": account_id, "full_name": full_name, "tg_id": tg_id},
        )
        db.commit()


def _upsert_token_for_tg(
    tg_id: int,
    access_token: str,
    refresh_token: Optional[str],
    token_type: str,
    expires_in: int,
) -> bool:
    """Создаёт/обновляет запись в hh_tokens по tg_id пользователя.
       Если пользователя с таким tg_id нет – создаёт users(tg_id, created_at)."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    with SessionLocal() as db:
        db.execute(
            text("""
                INSERT INTO users (tg_id, created_at)
                VALUES (:tg, now())
                ON CONFLICT (tg_id) DO NOTHING
            """),
            {"tg": tg_id},
        )
        # 2) узнаём user_id
        row = db.execute(text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": tg_id}).first()
        if not row:
            db.rollback()
            return False
        user_id = int(row[0])

        # 3) UPSERT токенов
        db.execute(
            text("""
                INSERT INTO hh_tokens (user_id, access_token, refresh_token, token_type, expires_at, updated_at)
                VALUES (:uid, :access, :refresh, :tt, :exp, now())
                ON CONFLICT (user_id) DO UPDATE
                   SET access_token  = EXCLUDED.access_token,
                       refresh_token = EXCLUDED.refresh_token,
                       token_type    = EXCLUDED.token_type,
                       expires_at    = EXCLUDED.expires_at,
                       updated_at    = now()
            """),
            {
                "uid": user_id,
                "access": access_token,
                "refresh": refresh_token,
                "tt": token_type,
                "exp": expires_at,
            },
        )
        db.commit()
        return True


# ---------- схемы ----------

class LoginOut(BaseModel):
    auth_url: str

class CallbackOut(BaseModel):
    ok: bool = True
    hh_user_id: int = 0
    saved: bool = False

class LinkStatus(BaseModel):
    linked: bool
    hh_user_id: int | None = None


# ---------- эндпоинты ----------

@router.get("/authorize-url")
def authorize_url(state: str = Query(..., min_length=1)):
    """Вернём готовую ссылку на hh.ru/oauth/authorize (если хочешь строить state сам)."""
    if not HH_CLIENT_ID or not HH_REDIRECT_URI:
        raise HTTPException(500, "HH client not configured")
    qs = urlencode({
        "response_type": "code",
        "client_id": HH_CLIENT_ID,
        "redirect_uri": HH_REDIRECT_URI,
        "state": state,
        "scope": HH_SCOPE,
    })
    return {"url": f"{HH_OAUTH_BASE.rstrip('/')}/oauth/authorize?{qs}"}


@router.get("/login", response_model=LoginOut)
def hh_login(tg_id: int = Query(..., description="Telegram user id")):
    """Строим state сами: tg:<id>:<nonce> и отдаём ссылку для авторизации."""
    if not HH_CLIENT_ID or not HH_REDIRECT_URI:
        raise HTTPException(500, "HH client not configured")
    state = f"tg:{tg_id}:{_nonce()}"
    qs = urlencode({
        "response_type": "code",
        "client_id": HH_CLIENT_ID,
        "redirect_uri": HH_REDIRECT_URI,
        "state": state,
        "scope": HH_SCOPE,
    })
    return LoginOut(auth_url=f"{HH_OAUTH_BASE.rstrip('/')}/oauth/authorize?{qs}")


@router.get("/callback", response_model=CallbackOut)
def hh_callback(code: Optional[str] = None, state: Optional[str] = None):
    """Обмен кода на токены + сохранение профиля и резюме."""
    if not code:
        raise HTTPException(400, "missing code")

    tg_id: Optional[int] = None
    if state:
        m = re.match(r"^tg:(\d+):", state)
        if m:
            tg_id = int(m.group(1))
        elif state.isdigit():
            tg_id = int(state)

    token_url = f"{HH_OAUTH_BASE.rstrip('/')}/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": HH_CLIENT_ID,
        "client_secret": HH_CLIENT_SECRET,
        "redirect_uri": HH_REDIRECT_URI,
    }
    try:
        resp = requests.post(token_url, data=data, timeout=10)
    except requests.RequestException as e:
        raise HTTPException(502, f"hh token exchange failed: {e}")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, resp.text)

    p = resp.json()
    access = p.get("access_token") or ""
    refresh = p.get("refresh_token")
    token_type = (p.get("token_type") or "bearer").lower()
    expires_in = int(p.get("expires_in") or 3600)

    hh_user_id = int(p.get("user_id") or 0)
    saved = False

    if tg_id is not None:
        # 1) токены
        saved = _upsert_token_for_tg(tg_id, access, refresh, token_type, expires_in)

        # 2) профиль HH
    # --- сразу подтянем профиль и резюме, чтобы админка и бот видели данные ---
        try:
            # 2.1 профиль /me
            me_resp = requests.get(
                f"{HH_API_BASE.rstrip('/')}/me",
                headers={"Authorization": f"Bearer {access}"},
                timeout=10,
            )
            if me_resp.status_code == 200:
                me_json = me_resp.json()
                full_name = " ".join(
                    x for x in [(me_json.get("first_name") or "").strip(),
                                (me_json.get("last_name") or "").strip()]
                    if x
                ).strip()
                _save_hh_account_info(
                    tg_id=tg_id if tg_id is not None else 0,
                    account_id=str(me_json.get("id") or "").strip(),
                    full_name=full_name,
                )

            # 2.2 резюме /resumes/mine
            res_resp = requests.get(
                f"{HH_API_BASE.rstrip('/')}/resumes/mine",
                headers={"Authorization": f"Bearer {access}"},
                timeout=10,
            )
            if res_resp.status_code == 200:
                items = res_resp.json().get("items", [])
                
                try:
                    upsert_resumes(SessionLocal, tg_id, items)
                except Exception:
                    with SessionLocal() as db:
                        uid = db.execute(text("SELECT id FROM users WHERE tg_id=:tg"), {"tg": tg_id}).scalar()
                        if uid is not None:
                            for it in items:
                                db.execute(
                                    text("""
                                        INSERT INTO resumes (user_id,resume_id,title,area,updated_at,visible)
                                        VALUES (:uid,:rid,:title,:area,:upd,:vis)
                                        ON CONFLICT (resume_id) DO UPDATE
                                        SET title = EXCLUDED.title,
                                            area = EXCLUDED.area,
                                            updated_at = EXCLUDED.updated_at,
                                            visible = EXCLUDED.visible
                                    """),
                                    {
                                        "uid": int(uid),
                                        "rid": str(it.get("id") or ""),
                                        "title": it.get("title"),
                                        "area": (it.get("area") or {}).get("name"),
                                        "upd": it.get("updated_at"),
                                        "vis": bool(it.get("visible", True)),
                                    },
                                )
                            db.commit()
        except Exception:
            pass
        
        try:
            with SessionLocal() as db:
                user_id = db.execute(text("SELECT id FROM users WHERE tg_id=:tg"), {"tg": tg_id}).scalar()
                if user_id:
                    attach_pending_ref_on_link_sync(db, int(user_id))
                    db.commit()
        except Exception:
            logging.exception("attach_pending_ref_on_link_sync failed")
        if tg_id is not None and saved:
            # 1) Успех
            _tg_send(tg_id, "✅ Аккаунт привязан. Готовы откликаться на вакансии!")
        
            # 2) Блок с кейсами (HTML + кликабельные ссылки)
            cases_text = (
                "🙌 С ботом поиск работы будет идти быстрее и легче. Истории пользователей:\n\n"
            )
            _tg_send(tg_id, cases_text, reply_markup=_cases_kb(), parse_mode="HTML")
        
            # 3) Главное меню (ссылка на доку)
            _tg_send(
                tg_id,
                "📋 Главное меню. Выбери, что хочешь сделать:\n\n"
                "<a href=''>Документация</a>",
                reply_markup=_main_menu_kb(), parse_mode="HTML"
            )

    return RedirectResponse(url="", status_code=302)

@router.get("/link-status", response_model=LinkStatus)
def link_status(tg_id: int = Query(..., description="Telegram user id")):
    """Статус привязки HH: есть ли токен (и не важно, надо ли рефрешить)."""
    row = _get_tokens_by_tg(tg_id)
    if not row:
        return LinkStatus(linked=False, hh_user_id=None)

    hh_id_str = _get_hh_account_id_by_tg(tg_id)
    hh_id_int: Optional[int] = None
    if hh_id_str and hh_id_str.isdigit():
        hh_id_int = int(hh_id_str)

    return LinkStatus(linked=True, hh_user_id=hh_id_int)

@router.get("/me")
def hh_me(tg_id: int = Query(..., description="Telegram user id")):
    """
    Проксируем GET /me в HH API и параллельно сохраняем ФИО/ID и резюме.
    """
    tok = _get_tokens_by_tg(tg_id)
    if not tok:
        raise HTTPException(status_code=404, detail="no tokens")

    if tok["exp"] and tok["exp"] - int(time.time()) < 60:
        return {"ok": False, "need_refresh": True}

    r = requests.get(
        f"{HH_API_BASE.rstrip('/')}/me",
        headers={"Authorization": f"Bearer {tok['access_token']}"},
        timeout=10,
    )
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    me = r.json()

    first = (me.get("first_name") or "").strip()
    last = (me.get("last_name") or "").strip()
    full_name = " ".join(x for x in (first, last) if x).strip()
    account_id = (me.get("id") or "").strip()
    _save_hh_account_info(tg_id=tg_id, account_id=account_id, full_name=full_name)

    try:
        items = hh_get_resumes(tok["access_token"])   
        saved = upsert_resumes(SessionLocal, tg_id, items)
    except Exception as e:
        print(f"[hh.me] resumes upsert failed: {e}")

    return {"ok": True, "me": me}

@router.post("/refresh")
def hh_refresh(tg_id: int = Query(..., description="Telegram user id")):
    row = _get_tokens_by_tg(tg_id)
    if not row:
        raise HTTPException(404, "no tokens")

    token_url = f"{HH_OAUTH_BASE.rstrip('/')}/oauth/token"
    try:
        r = requests.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": row["refresh_token"],
                "client_id": HH_CLIENT_ID,
                "client_secret": HH_CLIENT_SECRET,
            },
            timeout=10,
        )
    except requests.RequestException as e:
        raise HTTPException(502, f"hh refresh failed: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)

    p = r.json()
    access = p["access_token"]
    refresh = p.get("refresh_token", row["refresh_token"])
    token_type = (p.get("token_type") or "bearer").lower()
    expires_in = int(p.get("expires_in", 3600) or 3600)

    _upsert_token_for_tg(tg_id, access, refresh, token_type, expires_in)
    return {"ok": True, "refreshed": True, "expires_in": expires_in}

@router.post("/unlink")
def hh_unlink(tg_id: int = Query(..., description="Telegram user id")):
    """
    Отвязывает HH-аккаунт от пользователя:
    - удаляет токены из hh_tokens
    - очищает users.hh_account_id / hh_account_name
    """
    with SessionLocal() as db:
        uid = db.execute(text("SELECT id FROM users WHERE tg_id = :tg"), {"tg": tg_id}).scalar()
        if not uid:
            return {"ok": False, "unlinked": False, "reason": "user_not_found"}

        db.execute(text("DELETE FROM hh_tokens WHERE user_id = :uid"), {"uid": int(uid)})
        db.execute(
            text("""UPDATE users
                       SET hh_account_id = NULL,
                           hh_account_name = NULL
                     WHERE id = :uid"""),
            {"uid": int(uid)},
        )
        db.commit()
    return {"ok": True, "unlinked": True}
