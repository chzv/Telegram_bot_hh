# front_bot/routers/responses.py
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, urlencode

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from utils import texts, states, buttons
from utils.api_client import (
    get_link_status,
    hh_resumes,
    hh_areas,
    hh_search,
    queue_applications,
    saved_requests_list,
    saved_requests_create,
    saved_requests_delete,
    cover_letters_list_sync,
    quota_current,
    campaigns_list,
    campaign_upsert,
    campaign_start,
    campaign_stop,
    campaign_delete,
    campaign_send_now
)

from utils.helpers import (
    build_multi_choice_keyboard,
    handle_multi_choice,
    build_paginated_keyboard,
)
from utils.states import (
    SELECTING_ACTION,
    ASK_RESUME,
    ASK_COUNTRY,
    ASK_REGION,
    ASK_SCHEDULE,
    ASK_EMPLOYMENT,
    ASK_PROFESSION,
    ASK_KEYWORD,
    ASK_SEARCH_FIELD,
    ASK_COVER_LETTER,
    CONFIRMATION,
    ASK_SEARCH_METHOD,
    ASK_HH_URL,
    ASK_WORK_FORMAT,
)
import asyncio
from typing import Tuple
import re
import aiohttp
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

def _format_time(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        dt_msk = dt.astimezone(ZoneInfo("Europe/Moscow"))
        return dt_msk.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ts
        
def _normalize_quota(q: Optional[dict]) -> tuple[int, int, int, str, Optional[str]]:
    """
    Приводит квоту к единому виду.
    Возвращает: used, limit, remaining, tariff, reset_time_msk
    Поддерживает схемы:
      - {tariff, limit, used, remaining, reset_time_msk}
      - {tariff, tariff_limit, hard_cap, used_today, remaining, reset_time_msk}
      - частичные варианты (строки -> int)
    """
    if not q:
        return 0, 10, 10, "free", None

    tariff = str(q.get("tariff") or "").strip().lower()

    limit = q.get("limit")
    if limit is None:
        limit = q.get("tariff_limit") 
    if limit is None:
        limit = q.get("hard_cap")     
    try:
        limit = int(limit)
    except Exception:
        limit = 200 if tariff == "paid" else 10

    used = q.get("used")
    if used is None:
        used = q.get("used_today")
    try:
        used = int(used) if used is not None else None
    except Exception:
        used = None

    remaining = q.get("remaining")
    try:
        remaining = int(remaining) if remaining is not None else None
    except Exception:
        remaining = None

    if used is None and remaining is not None:
        used = max(0, int(limit) - int(remaining))
    if remaining is None and used is not None:
        remaining = max(0, int(limit) - int(used))
    if used is None and remaining is None:
        used, remaining = 0, int(limit)

    if not tariff:
        tariff = "paid" if int(limit) >= 200 else "free"

    reset_time = q.get("reset_time_msk") or q.get("reset_time")

    return int(used), int(limit), int(remaining), tariff, reset_time

AREAS_KEY = "areas_cache"
AREAS_BY_PARENT_KEY = "areas_by_parent"
WORLD_NODE_ID = 1001 

HH_SEARCH_ALLOWED_KW = {
    "schedules",          # массив schedule id (если используете)
    "employment",         # ["full", "part", "project", ...]
    "professional_roles", # список int
    "search_field",       # строка: name | company_name | description
    "search_fields",      # допускаем и это имя, преобразуем ниже в одиночный search_field
    "experience", "salary", "only_with_salary", "currency",
    "area", "per_page", "page",
}

def get_daily_responses_key(user_id: int) -> str:
    today = date.today().isoformat()
    return f"daily_responses_{user_id}_{today}"

def get_daily_response_count(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> int:
    key = get_daily_responses_key(user_id)
    return int(context.bot_data.get(key, 0))

def increment_daily_response_count(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, inc: int = 1
) -> int:
    key = get_daily_responses_key(user_id)
    new_val = int(context.bot_data.get(key, 0)) + int(inc)
    context.bot_data[key] = new_val
    return new_val

async def _quota_fresh(tg_id: int):
    q = await quota_current(tg_id)
    used, limit_cap, remaining, tariff, reset_time = _normalize_quota(q)
    return int(used), int(limit_cap), int(remaining), str(tariff), reset_time

async def get_quota_safe(context, user_id: int) -> tuple[int, int, int]:
    try:
        q = await quota_current(user_id)  
        used, limit_cap, remaining, _tariff, _rt = _normalize_quota(q)
        return int(used), int(limit_cap), int(remaining)
    except Exception as e:
        logger.exception("quota_current failed for user %s: %s; fallback=10", user_id, e)
        used = get_daily_response_count(context, user_id)  
        limit_cap = 10
        remaining = max(0, limit_cap - used)
        return used, limit_cap, remaining

PROF_PAGE_SIZE = 10

def _all_prof_categories() -> list[dict]:
    from config import DEMO_PROFESSIONS
    return DEMO_PROFESSIONS

def _render_prof_page(context, page: int):
    cats = _all_prof_categories()                       
    chosen = {str(x) for x in (context.user_data.get("profession_selection") or set())}

    all_ids = {str(c["id"]) for c in cats}
    all_selected = (all_ids and all_ids.issubset(chosen))

    start = page * PROF_PAGE_SIZE
    chunk = cats[start:start + PROF_PAGE_SIZE]

    kb = []
    kb.append([InlineKeyboardButton(
        f'{"🟢" if all_selected else "🔴"} Выбрать все',
        callback_data="prof_all"             
    )])

    for c in chunk:
        cid = str(c["id"])
        mark = "🟢 " if cid in chosen else "🔴 "
        kb.append([InlineKeyboardButton(f"{mark}{c['name']}", callback_data=f"prof_toggle_{cid}")])

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("◀︎ Назад",  callback_data=f"prof_page_{page-1}"))
    if start + PROF_PAGE_SIZE < len(cats):
        nav.append(InlineKeyboardButton("Вперёд ▶︎", callback_data=f"prof_page_{page+1}"))
    if nav:
        kb.append(nav)

    kb.append([InlineKeyboardButton("Далее", callback_data="profession_next")])
    return InlineKeyboardMarkup(kb)

async def handle_prof_toggle(update, context):
    q = update.callback_query
    await q.answer()
    cid = q.data.replace("profession_", "").replace("prof_toggle_", "")
    sel = context.user_data.get("profession_selection", set())
    sel = set(sel) 
    if cid in sel: sel.remove(cid)
    else: sel.add(cid)
    context.user_data["profession_selection"] = sel
    page = int(context.user_data.get("prof_page", 0))
    await q.edit_message_reply_markup(_render_prof_page(context, page))
    return states.ASK_PROFESSION

async def handle_prof_all(update, context):
    q = update.callback_query
    await q.answer()

    cats = _all_prof_categories()
    all_ids = {str(c["id"]) for c in cats}

    sel = set(str(x) for x in context.user_data.get("profession_selection", set()))
    if all_ids.issubset(sel):
        sel -= all_ids
    else:
        sel |= all_ids

    context.user_data["profession_selection"] = sel

    page = int(context.user_data.get("prof_page", 0))
    await q.edit_message_reply_markup(_render_prof_page(context, page))
    return states.ASK_PROFESSION

async def handle_prof_page(update, context):
    q = update.callback_query
    await q.answer()
    page = int(q.data.replace("prof_page_", ""))
    context.user_data["prof_page"] = page
    await q.edit_message_reply_markup(_render_prof_page(context, page))
    return states.ASK_PROFESSION

# --- Формат работы (как на hh.ru) ---
WORK_FORMAT_OPTIONS = {
    "ON_SITE":   "На месте работодателя",
    "REMOTE":    "Удалённо",
    "HYBRID":    "Гибрид",
    "FIELD_WORK":"Разъездной",
}

async def ask_work_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    if q.data.startswith("region_"):
        region_token = q.data.replace("region_", "", 1)
        if "_" in region_token:
            parts = region_token.split("_", 1)
            region_token = parts[1] if len(parts) == 2 else parts[0]
        context.user_data.setdefault("new_request", {})
        context.user_data["new_request"]["region"] = region_token

    context.user_data["work_format_selection"] = set(context.user_data.get("work_format_selection", set()))
    reply_markup = build_multi_choice_keyboard(
        WORK_FORMAT_OPTIONS, "work_format_selection", "workfmt", context
    )
    await q.message.edit_text("📌 Шаг 4/10:\nВыберите формат работы и нажмите «Далее».",
                              reply_markup=reply_markup)
    return ASK_WORK_FORMAT

async def handle_work_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await handle_multi_choice(update, context, WORK_FORMAT_OPTIONS, "work_format_selection", "workfmt")
    return ASK_WORK_FORMAT

def _derive_professional_roles_from_categories(context, data: dict) -> list[str]:
    sel = data.get("profession") or []
    return [str(x) for x in _roles_from_categories(sel)]

from urllib.parse import parse_qs

def _extract_resume_preview(item: dict) -> str | None:
    """
    Достаёт resume_id из объекта сохранённого запроса:
    1) item['resume_id'] | item['resume']
    2) из item['query_params'] (resume=... | resume_id=...)
    Возвращает строковый ID или None (НИКОГДА не '—').
    """
    rid = item.get("resume_id") or item.get("resume")
    if not rid:
        qp = item.get("query_params") or ""
        if isinstance(qp, str) and qp:
            try:
                qs = parse_qs(qp, keep_blank_values=True)
                lst = qs.get("resume") or qs.get("resume_id") or []
                if lst:
                    rid = lst[0]
            except Exception:
                rid = None
    rid = (str(rid).strip() if rid is not None else None)
    return rid or None

async def _ensure_areas_cache(context: ContextTypes.DEFAULT_TYPE) -> None:
    need_load = not (AREAS_KEY in context.bot_data and AREAS_BY_PARENT_KEY in context.bot_data)
    if need_load:
        areas = await hh_areas()  # [{id,name,parent_id}]
        id2area, by_parent = {}, {}
        for a in areas:
            aid = int(a.get("id"))
            parent = a.get("parent_id")
            parent_id = int(parent) if parent is not None else None
            item = {"id": aid, "name": a.get("name", ""), "parent_id": parent_id}
            id2area[aid] = item
            by_parent.setdefault(parent_id, []).append(item)
        # локальная сортировка по имени, чтобы дети были ровно по алфавиту
        for lst in by_parent.values():
            lst.sort(key=lambda x: x["name"].lower())
        context.bot_data[AREAS_KEY] = id2area
        context.bot_data[AREAS_BY_PARENT_KEY] = by_parent
    else:
        by_parent = context.bot_data[AREAS_BY_PARENT_KEY]

    top = [c for c in by_parent.get(None, []) if c["id"] != WORLD_NODE_ID]
    
    world_children = list(by_parent.get(WORLD_NODE_ID, []))

    # Россия отдельно, остальной верх по алфавиту
    russia = [c for c in top if c["id"] == 113]
    others_top = [c for c in top if c["id"] != 113]
    others_top.sort(key=lambda x: x["name"].lower())

    # дети -1 уже отсортированы выше; при желании ещё раз:
    world_children.sort(key=lambda x: x["name"].lower())

    # объединяем: Россия → верхние → мир
    raw = russia + others_top + world_children

    seen, countries_full = set(), []
    for c in (russia + others_top + world_children):
        cid, name = c.get("id"), (c.get("name") or "")
        if cid == WORLD_NODE_ID or name == "Другие регионы":
            continue
        if cid not in seen:
            countries_full.append(c); seen.add(cid)

    # сохраняем под двумя ключами (совместимость)
    context.bot_data["countries_full"] = countries_full
    context.bot_data["countries"] = list(countries_full)
    
def _resolve_area_id_from_request(data: Dict[str, Any]) -> Optional[int]:
    """
    Возвращает числовой area_id для поиска:
    - если указан region = "<число>" -> это регион
    - если region = "all_<country_id>" -> берём country_id
    - если есть data["area"] (после разбора URL) -> берём первый int
    Приоритет: area (URL) -> region (кнопки).
    """
    if "area" in data and data["area"]:
        try:
            return int(str(data["area"][0]))
        except Exception:
            pass

    region = str(data.get("region", "")).strip()
    if not region:
        return None
    if region.startswith("all_"):
        try:
            return int(region.split("_", 1)[1])
        except Exception:
            return None
    try:
        return int(region)
    except Exception:
        return None

def _area_name(context: ContextTypes.DEFAULT_TYPE, area_id: Optional[int]) -> str:
    if area_id is None:
        return "Не указано"
    id2 = context.bot_data.get(AREAS_KEY) or {}
    item = id2.get(int(area_id))
    return item["name"] if item else str(area_id)


# ===== Диалог =====
def _kb_campaign_running(cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏹ Остановить отклики", callback_data=f"camp_stop:{cid}")],
        [InlineKeyboardButton("✏️ Редактировать кампанию", callback_data=f"camp_edit:{cid}")],
        [InlineKeyboardButton("🗑 Удалить кампанию", callback_data=f"camp_delete_confirm:{cid}")],
    ])

def _kb_campaign_stopped(cid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Запустить отклики", callback_data=f"camp_start:{cid}")],
        [InlineKeyboardButton("✏️ Редактировать кампанию", callback_data=f"camp_edit:{cid}")],
        [InlineKeyboardButton("🗑 Удалить кампанию", callback_data=f"camp_delete_confirm:{cid}")],
    ])

async def on_camp_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_user.id
    try:
        cid = int(q.data.split(":", 1)[1])
    except Exception:
        return

    try:
        await campaign_stop(tg_id, cid)

        try:
            data = await campaigns_list(tg_id, page=1, page_size=20)
            cmap = {int(i["id"]): i for i in (data.get("items") or [])}
            camp = cmap.get(cid) or {"id": cid, "status": "stopped"}
        except Exception:
            camp = {"id": cid, "status": "stopped"}
        await _ensure_resumes_cache(context, tg_id)
        camp = await _enrich_campaign_for_render(context, camp)

        await q.message.edit_text(
            _render_campaign_card(camp),
            reply_markup=_kb_campaign_stopped(cid),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return states.SELECTING_ACTION
    except Exception as e:
        await q.message.edit_text(f"Ошибка остановки: {e}")

async def on_camp_start(update, context):
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_user.id
    cid = int(q.data.split(":", 1)[1])

    try:
        await campaign_start(tg_id, cid)
        try:
            await campaign_send_now(tg_id, cid, limit=150)
            # сразу пнуть автотик
            try:
                await _req("POST", "/hh/campaigns/auto_tick", json={})
            except Exception as e:
                logger.warning("auto_tick best-effort failed: %s", e)
        except Exception as e:
            logger.warning("send_now failed: %s", e)
            
        # подтянуть свежую кампанию и отрисовать карточку
        data = await campaigns_list(tg_id, page=1, page_size=20)
        c = next((i for i in (data.get("items") or []) if int(i.get("id")) == cid), None)
        if not c:
            await q.message.edit_text("✅ Кампания запущена. Автоотклики работают 24/7.",
                                      reply_markup=_kb_campaign_running(cid))
            return

        c = await _enrich_campaign_for_render(context, c)
        await q.message.edit_text(
            _render_campaign_card(c),
            reply_markup=_kb_campaign_running(cid),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            await q.message.edit_text(
                "⚠️ Одновременно может работать только одна кампания.\n"
                "Чтобы запустить новую, сначала остановите текущую.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="resp_back")]])
            )
        else:
            await q.message.edit_text(f"Ошибка запуска: {e}")

def _render_campaigns_list(items: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    rows = []
    kb_rows = []

    def _ts(i: dict):
        return i.get("updated_at") or i.get("started_at") or i.get("created_at") or ""
    items = sorted(items, key=_ts, reverse=True)

    for it in items:
        ts = _format_time(it.get("started_at") or it.get("created_at") or "")
        title = (it.get("title") or it.get("query") or "Поиск").strip()
        status = str(it.get("status", "")).lower()
        dot = "🟢 " if status == "active" else "🔴 "

        label = f"{dot}{title} (от {ts})"
        rows.append(label)
        cid = int(it["id"])
        kb_rows.append([InlineKeyboardButton(label, callback_data=f"camp_open:{cid}")])

    text = (
        "Настройте бота, чтобы рассылать отклики 24/7\n\n"
        "1. Вы один раз настраиваете фильтры под ваши вакансии.\n"
        "2. Бот сам отправляет до 200 откликов в день — без спама и без лишних кликов.\n"
        "3. Когда все подходящие вакансии обработаны, бот продолжает работать в фоне: "
        "следит за новыми объявлениями и сразу откликается.\n\n"
    )
    kb_rows.append([InlineKeyboardButton("➕ Новая кампания", callback_data="new_request")])
    return text, InlineKeyboardMarkup(kb_rows)

def _render_campaign_card(c: dict) -> str:
    is_active = str(c.get("status", "")).lower() == "active"
    head = "🟢 Кампания запущена" if is_active else "🔴 Кампания остановлена"

    # время старта/создания — в МСК
    started_raw = c.get("started_at") or c.get("created_at") or ""
    started = _format_time(started_raw)

    resume_title = (c.get("resume_title") or c.get("resume") or "—")
    sent = int(c.get("sent_count") or 0)
    search_url = c.get("search_url") or c.get("hh_link") or "#"

    # поля «Запросы» 
    country = c.get("country") or "—"
    region  = c.get("region")  or "—"
    
    # отображение «все»
    if isinstance(country, str) and country.lower() == "all":
        country = "все"
        region  = "все"
    if isinstance(region, str) and (region.lower() == "all" or region.startswith("all_")):
        region = "все"

    # график работы — из work_format
    wf = c.get("work_format") or []
    schedule = ", ".join(WORK_FORMAT_OPTIONS.get(x, x) for x in wf) or "Все"

    # тип занятости по словарю из config
    empl_ids = set(c.get("employment") or [])
    employment = ", ".join([e["name"] for e in config.DEMO_EMPLOYMENT if e["id"] in empl_ids]) or "Все"

    prof_area = c.get("prof_area") or _prof_roles_to_label(c.get("professional_roles"))
    query = c.get("query") or c.get("keyword") or "—"
    sources = _search_fields_to_label(c.get("search_fields"))
    cover   = _cover_to_label(c.get("cover_letter"))

    lines = [
        f"<b>{head}</b>",
        "",
        f"Старт: {started}",
        f"Резюме: {resume_title}",
        f"Отправлено: {sent} откликов",
        "",
        "<b>Настройки фильтров:</b>",
        f'Запрос на hh.ru — <a href="{search_url}">посмотреть</a>',
        "",
        "<b>Запросы:</b>",
        f"1) Страна: {country}",
        f"2) Регион: {region}",
        f"3) График работы: {schedule}",
        f"4) Тип занятости: {employment}",
        f"5) Проф. область: {prof_area}",
        f"6) Запрос: {query}",
        f"7) Где ищем: {sources}",
        f"8) Сопроводительное письмо: {cover}",
    ]
    return "\n".join(lines)

async def on_camp_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_user.id
    cid = int(q.data.split(":",1)[1])

    try:
        data = await campaigns_list(tg_id, page=1, page_size=20)
        m = {int(i["id"]): i for i in (data.get("items") or [])}
        c = m.get(cid)
    except Exception:
        c = None

    if not c:
        await q.message.edit_text("Кампания не найдена.")
        return SELECTING_ACTION
    await _ensure_resumes_cache(context, tg_id)
    c = await _enrich_campaign_for_render(context, c)
    
    await q.message.edit_text(
        _render_campaign_card(c),
        reply_markup=_kb_campaign_running(cid) if str(c.get("status","")).lower()=="active" else _kb_campaign_stopped(cid),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return SELECTING_ACTION

async def on_camp_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показываем подтверждение удаления кампании."""
    q = update.callback_query
    await q.answer()
    try:
        cid = int(q.data.split(":", 1)[1])
    except Exception:
        return states.SELECTING_ACTION

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Удалить кампанию", callback_data=f"camp_delete:{cid}")],
        [InlineKeyboardButton("↩️ Назад", callback_data=f"camp_open:{cid}")],
    ])
    await q.message.edit_text("Вы уверены, что хотите удалить кампанию?", reply_markup=kb)
    return states.SELECTING_ACTION


async def on_camp_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляем кампанию и показываем список кампаний/кнопку «Новая кампания»."""
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_user.id
    try:
        cid = int(q.data.split(":", 1)[1])
    except Exception:
        await q.message.edit_text("Не удалось удалить: неверный идентификатор кампании.")
        return states.SELECTING_ACTION

    try:
        await campaign_delete(tg_id, cid)
    except Exception as e:
        await q.message.edit_text(f"Не удалось удалить: {e}")
        return states.SELECTING_ACTION

    try:
        data = await campaigns_list(tg_id, page=1, page_size=20)
        items = list(data.get("items", []))
    except Exception:
        items = []

    if items:
        text, kb = _render_campaigns_list(items)
        await q.message.edit_text("✅ Кампания удалена\n\n" + text, reply_markup=kb)
    else:
        await q.message.edit_text(
            "✅ Кампания удалена",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Новая кампания", callback_data="new_request")]])
        )
    return states.SELECTING_ACTION

async def on_camp_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_user.id

    try:
        cid = int(q.data.split(":", 1)[1])
    except Exception:
        return states.SELECTING_ACTION

    data = await campaigns_list(tg_id, page=1, page_size=50)
    camp = next((i for i in (data.get("items") or []) if str(i.get("id")) == str(cid)), None)
    if not camp:
        await q.message.edit_text("Кампания не найдена.")
        return states.SELECTING_ACTION
    prefill = {
        "title":       camp.get("title") or camp.get("query") or "Поиск",
        "keyword":     camp.get("query") or "",
        "area":        camp.get("areas") or ([str(camp["area"])] if camp.get("area") else []),
        "work_format": camp.get("work_format") or [],
        "employment":  camp.get("employment") or [],
        "profession":  [str(x) for x in (camp.get("professional_roles") or [])],
        "search_fields": camp.get("search_fields") or [],
        "cover_letter": camp.get("cover_letter") or "",
        "resume":      camp.get("resume") or context.user_data.get("resume_id"),
    }

    prefill["cover_letter"] = _normalize_cover(prefill["cover_letter"])

    context.user_data["new_request"] = prefill
    context.user_data["resume_id"]   = prefill["resume"]
    context.user_data["edit_campaign_id"] = cid
    context.user_data["edit_saved_request_id"] = camp.get("saved_request_id")

    return await ask_resume(update, context)
    
async def start_responses_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.callback_query.message if update.callback_query else update.effective_message
    tg_id = update.effective_user.id

    # 1) привязка HH
    try:
        link = await get_link_status(tg_id)
        if not link or not link.get("linked"):
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Привязать HH", callback_data="link_account")]])
            await msg.edit_text("Чтобы запустить отклики, привяжите аккаунт HeadHunter.", reply_markup=kb)
            return ConversationHandler.END
    except Exception:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Привязать HH", callback_data="link_account")]])
        await msg.edit_text("Не удалось проверить привязку HH. Попробуйте ещё раз.", reply_markup=kb)
        return ConversationHandler.END

    # 2) тянем кампании
    try:
        data = await campaigns_list(tg_id, page=1, page_size=20)
        items = list(data.get("items", []))
    except Exception as e:
        logging.warning("campaigns_list error: %s", e)
        items = []

    # 3) ВСЕГДА показываем экран «Уже запущено»: c существующими кампаниями
    text, kb = _render_campaigns_list(items)
    await msg.edit_text(text, reply_markup=kb)
    return states.SELECTING_ACTION

# ——— история запросов ———
async def _render_saved_list_message(message, items: list[dict]) -> None:
    if not items:
        await message.edit_text("Сохранённых запросов пока нет.")
        return

    lines = ["📂 Сохранённые запросы:\n"]
    kb = []

    for it in items:
        saved_id = int(it.get("id"))
        title = (it.get("title") or it.get("query") or "Запрос").strip()
        if len(title) > 70:
            title = title[:67] + "…"

        lines.append(f"• {title}")
        kb.append([
            InlineKeyboardButton("▶️ Использовать", callback_data=f"resp_saved_{saved_id}"),
            InlineKeyboardButton("🗑 Удалить",       callback_data=f"resp_del_{saved_id}"),
        ])

    kb.append([InlineKeyboardButton("↩️ Назад", callback_data="resp_back")])
    await message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def choose_from_saved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    if 'saved_requests_list' not in globals() or saved_requests_list is None:
        await q.message.edit_text("Этот раздел ещё в разработке. Пока доступна привязка /start.")
        return SELECTING_ACTION

    try:
        tg_id = update.effective_user.id
        items = await saved_requests_list(tg_id)  
    except Exception:
        await q.message.edit_text("Не удалось загрузить сохранённые запросы. Попробуйте позже.")
        return SELECTING_ACTION

    context.user_data["saved_requests"] = items or []
    context.user_data["saved_requests_map"] = {int(it["id"]): it for it in (items or [])}

    await _render_saved_list_message(q.message, items or [])
    return SELECTING_ACTION

def _extract_text_from_qs(qs: str) -> str:
    try:
        params = parse_qs(qs or "", keep_blank_values=True)
        return (params.get("text", [""])[0] or "").strip()
    except Exception:
        return ""
        
async def use_saved(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    tg_id = update.effective_user.id
    saved_id = int(q.data.rsplit("_", 1)[-1])
    context.user_data["used_saved_id"] = saved_id 
    saved_map = context.user_data.get("saved_requests_map") or {}
    it = saved_map.get(saved_id)
    if not it:
        if 'saved_requests_list' in globals() and saved_requests_list is not None:
            items = await saved_requests_list(tg_id)
            context.user_data["saved_requests"] = items or []
            context.user_data["saved_requests_map"] = {int(x["id"]): x for x in (items or [])}
            it = context.user_data["saved_requests_map"].get(saved_id)

    if not it:
        await q.message.reply_text("Сохранённый запрос не найден.")
        return SELECTING_ACTION

    new_req: dict = {}

    kw = (it.get("query") or "").strip()
    if not kw:
        kw = _extract_text_from_qs(it.get("query_params") or "")

    if kw:
        new_req["keyword"] = kw
        new_req["query"] = kw

    area = it.get("area")
    if area not in (None, "", []):
        try:
            new_req["area"] = [int(area)]
        except Exception:
            new_req["area"] = [area]

    sched = it.get("schedule") or []
    if sched:
        new_req["schedule"] = list(sched)
    wf = it.get("work_format") or []
    if wf:
        new_req["work_format"] = list(wf)

    empl = it.get("employment") or []
    if empl:
        new_req["employment"] = list(empl)

    roles = it.get("professional_roles") or []
    if roles:
        new_req["professional_roles"] = [str(r) for r in roles]

    sf = it.get("search_fields") or []
    if sf:
        new_req["search_fields"] = list(sf)

    cover = it.get("cover_letter")
    if cover:
        new_req["cover_letter"] = cover

    rid = it.get("resume_id") or it.get("resume")
    if rid in (None, "", []):
        qp = it.get("query_params") or ""
        if isinstance(qp, str) and qp:
            try:
                qs = parse_qs(qp, keep_blank_values=True)
                rlist = qs.get("resume") or qs.get("resume_id") or []
                if rlist:
                    rid = rlist[0]
            except Exception:
                pass

    if rid not in (None, "", []):
        rid = str(rid).strip()
        new_req["resume"] = rid
        context.user_data["resume_id"] = rid

    context.user_data["new_request"] = new_req

    return await confirmation(update, context, message=q.message)

async def delete_saved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    if ('saved_requests_delete' not in globals() or saved_requests_delete is None or
        'saved_requests_list' not in globals() or saved_requests_list is None):
        await q.message.edit_text("Удаление пока недоступно.")
        return SELECTING_ACTION

    req_id = int(q.data.split("_")[-1])
    tg_id = update.effective_user.id

    try:
        await saved_requests_delete(tg_id, req_id)
        items = await saved_requests_list(tg_id)
        # обновим кэш
        context.user_data["saved_requests"] = items or []
        context.user_data["saved_requests_map"] = {int(it["id"]): it for it in (items or [])}
        if items:
            await _render_saved_list_message(q.message, items)
        else:
            await q.message.edit_text("Сохранённых запросов больше нет.")
    except Exception:
        await q.message.edit_text("Не удалось удалить. Попробуйте позже.")
    return SELECTING_ACTION

# ——— основной флоу ———

async def ask_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    context.user_data["new_request"] = {}
    tg_id = update.effective_user.id
    linked = False
    
    try:
        link_info = await get_link_status(tg_id)
        linked = bool(link_info and link_info.get("linked"))
    except Exception as e:
        logger.warning("get_link_status failed: %s", e)

    if not linked:
        keyboard = [
            [InlineKeyboardButton("🔗 Привязать HH", callback_data="link_account")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")],
        ]
        await q.message.edit_text(
            "Сначала привяжите аккаунт hh.ru, чтобы выбрать резюме.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ConversationHandler.END

    # 2) тянем резюме с бэка
    try:
        resumes = await asyncio.to_thread(hh_resumes, tg_id)
    except Exception as e:
        logger.error("hh_resumes failed: %s", e, exc_info=True)
        resumes = []

    if not resumes:
        keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
        await q.message.edit_text(
            "В вашем hh.ru не найдено ни одного резюме.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ConversationHandler.END

    # 3) клавиатура выбора, текст остаётся прежним (texts.ASK_RESUME)
    keyboard = [
        [InlineKeyboardButton((r.get("title") or "Резюме").strip(), callback_data=f"resume_{r.get('id')}")]
        for r in resumes if r.get("id") is not None
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await q.message.edit_text(texts.ASK_RESUME, reply_markup=reply_markup)
    return ASK_RESUME

async def ask_search_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    context.user_data["new_request"]["resume"] = q.data.replace("resume_", "")
    await q.answer()

    keyboard = [
        [InlineKeyboardButton("Настроить фильтры", callback_data="configure_filters")],
        [InlineKeyboardButton("Вставить ссылку hh.ru", callback_data="paste_link")],
    ]
    await q.message.edit_text("Выберите способ настройки поиска:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_SEARCH_METHOD
    
async def ask_country_for_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    try:
        await _ensure_areas_cache(context)
    except Exception as e:
        logger.exception("failed to load areas: %s", e)
        await q.message.edit_text("Не удалось загрузить список стран. Попробуйте позже.")
        return SELECTING_ACTION

    # сносим старую витрину на всякий
    context.bot_data.pop("countries_ui", None)

    full = context.bot_data.get("countries_full", [])
    countries_ui = [{"id": "all", "name": "Все страны", "parent_id": None}] + full
    context.bot_data["countries_ui"] = countries_ui

    reply_markup = build_paginated_keyboard(
        countries_ui, page=0, prefix="country", rows=10, columns=2
    )
    await q.message.edit_text(texts.ASK_COUNTRY, reply_markup=reply_markup)
    return ASK_REGION

async def handle_country_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    parts = q.data.split("_")  # ожидаем page_country_nav_{page}
    page = int(parts[3]) if len(parts) >= 4 and parts[1] == "country" and parts[2] == "nav" else 0

    # используем витрину как есть; при отсутствии — восстанавливаем из countries_full
    countries_ui = context.bot_data.get("countries_ui")
    if not countries_ui:
        full = context.bot_data.get("countries_full", [])
        countries_ui = [{"id": "all", "name": "Все страны", "parent_id": None}] + list(full)
        context.bot_data["countries_ui"] = countries_ui

    await q.edit_message_reply_markup(
        build_paginated_keyboard(countries_ui, page=page, prefix="country", rows=10, columns=2)
    )
    return ASK_REGION

async def ask_region(update, context):
    q = update.callback_query
    await q.answer()
    token = q.data.replace("country_", "")

    context.user_data.setdefault("new_request", {})
    context.user_data["new_request"]["country"] = str(token)

    if token.lower() == "all":
        context.user_data["new_request"]["region"] = "all"
        return await ask_work_format(update, context)

    try:
        country_id = int(token)
    except Exception:
        logger.warning("Invalid country token: %s", token)
        await q.message.edit_text("Не удалось определить страну. Попробуйте снова.")
        return ASK_REGION

    if country_id == -1:
        await q.message.edit_text(texts.ASK_COUNTRY,
            reply_markup=build_paginated_keyboard(context.bot_data.get("countries_ui", []), page=0, prefix="country", rows=10, columns=2))
        return ASK_REGION

    by_parent = context.bot_data.get(AREAS_BY_PARENT_KEY, {})
    regions = [{"id": f"all_{country_id}", "name": "По всей стране", "parent_id": country_id}]
    regions += list(by_parent.get(country_id, []))

    context.bot_data[f"regions_{country_id}"] = regions
    reply_markup = build_paginated_keyboard(regions, page=0, prefix=f"region_{country_id}",
                                        rows=10, columns=1,
                                        add_select_all=False,
                                        selection_key=f"region_sel_{country_id}",
                                        context=context)
    await q.message.edit_text(texts.ASK_REGION, reply_markup=reply_markup)
    return ASK_SCHEDULE

async def handle_region_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    parts = q.data.split("_")  # ["page","region","{countryId}","nav","{page}"]
    country_id = parts[2]
    page = int(parts[-1]) if parts and parts[-1].isdigit() else 0
    regions = context.bot_data.get(f"regions_{country_id}", [])
    reply_markup = build_paginated_keyboard(regions, page=page, prefix=f"region_{country_id}", rows=10, columns=1)
    await q.edit_message_reply_markup(reply_markup)
    return ASK_SCHEDULE

async def handle_schedule_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    schedules = config.DEMO_SCHEDULES
    schedule_options = {item["id"]: item["name"] for item in schedules}
    await handle_multi_choice(update, context, schedule_options, "schedule_selection", "schedule")
    return ASK_EMPLOYMENT

async def ask_employment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    context.user_data["new_request"]["schedule"] = list(context.user_data.get("schedule_selection", []))
    await q.answer()

    employment = config.DEMO_EMPLOYMENT
    context.bot_data["dictionaries"]["employment"] = employment

    employment_options = {item["id"]: item["name"] for item in employment}
    context.user_data["new_request"]["work_format"] = list(context.user_data.get("work_format_selection", []))
    reply_markup = build_multi_choice_keyboard(
        employment_options, "employment_selection", "employment", context
    )
    await q.message.edit_text(texts.ASK_EMPLOYMENT, reply_markup=reply_markup)
    return ASK_PROFESSION

from config import DEMO_PROF_ROLE_MAP

def _roles_from_categories(selected_category_ids: list[str]) -> list[int]:
    """Берём из config.DEMO_PROF_ROLE_MAP все role id для выбранных категорий."""
    out, seen = [], set()
    for cid in selected_category_ids:
        for rid in DEMO_PROF_ROLE_MAP.get(str(cid), []):
            iri = int(rid)
            if iri not in seen:
                seen.add(iri)
                out.append(iri)
    return out
    
def _get_professional_role_ids(context, data: dict) -> list[int]:
    """
    Берём ID ролей:
    - если уже есть data['professional_roles'] (из URL) — используем их;
    - иначе строим по выбранным категориям (data['profession'] или текущий выбор в мастере).
    """
    roles = data.get("professional_roles") or []
    if roles:
        out = []
        for x in roles:
            try:
                out.append(int(x))
            except Exception:
                pass
        return out

    cats = data.get("profession") or list(context.user_data.get("profession_selection", []))
    return _roles_from_categories([str(c) for c in cats])

async def handle_employment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    employment = config.DEMO_EMPLOYMENT
    employment_options = {item["id"]: item["name"] for item in employment}
    await handle_multi_choice(update, context, employment_options, "employment_selection", "employment")
    return ASK_PROFESSION

async def ask_profession(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    context.user_data["new_request"]["employment"] = list(context.user_data.get("employment_selection", []))
    await q.answer()

    # Категории для клавиатуры 
    context.bot_data["prof_categories"] = config.DEMO_PROFESSION
    context.bot_data["prof_role_map"] = getattr(config, "DEMO_PROF_ROLE_MAP", {})

    # состояние выбора
    context.user_data["new_request"]["employment"] = list(context.user_data.get("employment_selection", []))
    context.user_data["prof_page"] = 0

    await q.message.edit_text(texts.ASK_PROFESSION, reply_markup=_render_prof_page(context, 0))
    return ASK_PROFESSION

async def handle_prof_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    page = int(q.data.split("_")[-1])
    context.user_data["prof_page"] = page
    await q.edit_message_reply_markup(_render_prof_page(context, page))
    return ASK_PROFESSION

async def handle_prof_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    rid = q.data.split("_")[-1]
    sel = context.user_data.get("profession_selection", set())
    if rid in sel:
        sel.remove(rid)
    else:
        sel.add(rid)
    context.user_data["profession_selection"] = sel
    page = int(context.user_data.get("prof_page", 0))
    await q.edit_message_reply_markup(_render_prof_page(context, page))
    return ASK_PROFESSION

async def handle_profession_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    prof_options = context.bot_data.get("prof_options", {})
    await handle_multi_choice(update, context, prof_options, "profession_selection", "profession")
    context.user_data["new_request"]["profession"] = list(context.user_data.get("profession_selection", []))
    return ASK_PROFESSION

async def ask_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sel = list(context.user_data.get("profession_selection", set()))
    context.user_data.setdefault("new_request", {})
    context.user_data["new_request"]["profession"] = sel
    context.user_data["new_request"]["professional_roles"] = [str(x) for x in _roles_from_categories(sel)]

    q = update.callback_query
    await q.answer()
    await q.message.edit_text(
        "📍 Шаг 7/10:\nВведите ключевое слово для поиска так, как вы бы искали вакансию на HH.\n\n"
        "Например: Таргетолог, Менеджер маркетплейсов, SMM специалист, Веб дизайнер и тд\n\n"
        "На следующем шаге можно будет выбрать настройки поиска:"
        "\n– В названии компании"
        "\n– В названии описания вакансии"
        "\n– В описании вакансии"
        "\n– Везде"
    )
    return states.ASK_KEYWORD

async def ask_search_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils import buttons

    msg = update.message
    kw = (msg.text or "").strip()
    if not kw:
        await msg.reply_text("⚠️ Введите ключевое слово текстом.")
        return states.ASK_KEYWORD

    context.user_data["keyword"] = kw
    context.user_data.setdefault("new_request", {})
    context.user_data["new_request"]["query"] = kw      
    context.user_data["new_request"]["keyword"] = kw   

    context.user_data["search_field_selection"] = set()
    reply_markup = build_multi_choice_keyboard(
        buttons.SEARCH_FIELD_OPTIONS, 
        "search_field_selection",
        "search",
        context
    )
    await msg.reply_text("Выберите область, где искать ключевое слово:", reply_markup=reply_markup)
    return states.ASK_SEARCH_FIELD

async def handle_search_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await handle_multi_choice(update, context, buttons.SEARCH_FIELD_OPTIONS, "search_field_selection", "search")
    return ASK_SEARCH_FIELD

def get_cover_letter_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    cover_letters = context.user_data.get("cover_letters", [])
    keyboard: List[List[InlineKeyboardButton]] = []

    if cover_letters:
        for i, letter in enumerate(cover_letters):
            keyboard.append(
                [InlineKeyboardButton(f"📄 {letter['title']}", callback_data=f"cl_select_{i}")]
            )
    keyboard.append([InlineKeyboardButton("✏️ Написать новое письмо", callback_data="cl_write_new")])
    keyboard.append([InlineKeyboardButton("📭 Без сопроводительного письма", callback_data="no_letter")])

    return InlineKeyboardMarkup(keyboard)

async def ask_cover_letter_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        context.user_data["new_request"]["search_fields"] = list(
            context.user_data.get("search_field_selection", [])
        )
        await q.answer()
    kw = (context.user_data.get("keyword") or "").strip()
    context.user_data["new_request"]["query"] = kw
    context.user_data["new_request"]["keyword"] = kw

    tg_id = update.effective_user.id
    try:
        letters = await asyncio.to_thread(cover_letters_list_sync, tg_id)
    except Exception:
        letters = []
    context.user_data["cover_letters"] = letters

    text = texts.ASK_COVER_LETTER
    reply_markup = get_cover_letter_keyboard(context)
    msg = q.message if q else None
    try:
        if msg:
            await msg.edit_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)
    except Exception:
        await (q.message.reply_text if q else update.message.reply_text)(text, reply_markup=reply_markup)

    return ASK_COVER_LETTER

async def handle_cover_letter_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get("waiting_for_new_cover_letter", False):
        context.user_data["new_request"]["cover_letter"] = update.message.text
        context.user_data.pop("waiting_for_new_cover_letter", None)
        await confirmation(update, context, message=update.message)
        return CONFIRMATION
    return ASK_COVER_LETTER

async def handle_cl_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    idx = int(q.data.split("_")[-1])
    letter = context.user_data["cover_letters"][idx]
    context.user_data["new_request"]["cover_letter"] = letter["body"]
    await q.answer()
    await confirmation(update, context, message=q.message)
    return CONFIRMATION

async def handle_no_cover_letter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    context.user_data["new_request"]["cover_letter"] = "Без сопроводительного письма"
    await q.answer()
    await confirmation(update, context, message=q.message)
    return CONFIRMATION

async def ask_new_cover_letter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.edit_text("Напишите сопроводительное письмо для этого отклика:")
    context.user_data["waiting_for_new_cover_letter"] = True
    return ASK_COVER_LETTER

async def ask_hh_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    await q.message.edit_text("Вставьте ссылку поиска с hh.ru (из адресной строки).")
    return ASK_HH_URL

async def handle_hh_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url_text = update.message.text.strip()
    try:
        parsed = urlparse(url_text)
        params = parse_qs(parsed.query)

        data = context.user_data.setdefault("new_request", {})

        empl_map = {"FULL": "full", "PART": "part", "PROJECT": "project", "VOLUNTEER": "volunteer", "INTERNSHIP": "probation"}
        employment = set(params.get("employment", []))
        employment |= {empl_map[x] for x in params.get("employment_form", []) if x in empl_map}
        data["employment"] = list(employment)
        
        # --- формат работы: отдельное поле, как на сайте ---
        raw_wf = set(params.get("work_format", []))
        normalized = []
        for x in raw_wf:
            if x == "EMPLOYER_SITE":
                x = "ON_SITE"
            elif x == "TRAVEL":
                x = "FIELD_WORK"
            # оставляем только допустимые значения hh
            if x in ("ON_SITE", "REMOTE", "HYBRID", "FIELD_WORK"):
                normalized.append(x)
        data["work_format"] = normalized
        data["schedule"] = params.get("schedule", [])

        # Ключевое слово
        kw = (params.get("text", [""])[0] or "").strip()
        data["keyword"] = kw
        data["query"] = kw

        # area: берём первый и приводим к int, если возможно
        if params.get("area"):
            a = params["area"][0]
            try:
                data["area"] = [int(a)]
            except Exception:
                data["area"] = [a]

        # фильтры (уже нормализованные)
        data["employment"] = list(employment)
        data["search_fields"] = params.get("search_field", [])

        # роли → только реальные ID
        roles = params.get("professional_role", [])
        data["professional_roles"] = [str(int(x)) for x in roles if str(x).strip().isdigit()]

        data["search_by_url"] = True

        await update.message.reply_text(
            texts.ASK_COVER_LETTER,
            reply_markup=get_cover_letter_keyboard(context)
        )
        return ASK_COVER_LETTER

    except Exception as e:
        logger.error("Failed to parse HH URL: %s; err=%s", url_text, e, exc_info=True)
        await update.message.reply_text(
            "Не удалось распознать ссылку. Проверьте адрес и попробуйте снова."
        )
        return ASK_HH_URL

from typing import Iterable, Optional, List

def _get_professional_role_ids(context, data: dict) -> List[int]:
    """Берём готовые professional_roles, иначе собираем из выбранных категорий."""
    roles = data.get("professional_roles")
    if roles:
        return [int(x) for x in roles if str(x).strip().isdigit()]
    cids = [str(x) for x in (data.get("profession") or [])]
    return _roles_from_categories(cids)  # уже int

async def _hh_search_safe(
    keyword: str,
    area_id: Optional[int],
    page: int,
    per_page: int,
    *,
    schedules: Optional[Iterable[str]] = None,
    employment: Optional[Iterable[str]] = None,
    professional_roles: Optional[Iterable[int]] = None,
    search_fields: Optional[Iterable[str]] = None,
    **_ignored,  
):
    clean = {}
    if schedules:
        clean["schedules"] = list(schedules)
    if employment:
        clean["employment"] = list(employment)
    if professional_roles:
        clean["professional_roles"] = [int(x) for x in professional_roles if str(x).strip().isdigit()]
    if search_fields:
        clean["search_fields"] = list(search_fields)

    return await hh_search(keyword, area_id, page, per_page, **clean)
    
async def _scrape_found_from_hh(url: str) -> Optional[int]:
    """
    Фолбэк: тянем HTML поиска HH и вытаскиваем "Найдено N вакансий".
    Возвращает int или None, если не удалось.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.get(url) as resp:
                html = await resp.text()

        # примеры фраз: "Найдена 1 вакансия", "Найдено 3 082 вакансии", "Найдено 45 123 вакансии"
        m = re.search(r"Найд(?:ена|ено|ены)\s+([\d\s\u00A0]+)\s+ваканси", html, re.IGNORECASE)
        if not m:
            # иногда число есть в JSON на странице
            m = re.search(r'"found"\s*:\s*(\d+)', html)
        if m:
            digits = re.sub(r"\D+", "", m.group(1))
            if digits:
                return int(digits)
    except Exception:
        pass
    return None

async def _scrape_vacancy_ids(url: str, limit: int = 20) -> list[int]:
    """
    Тянем HTML поиска HH и достаём id вакансий с SERP.
    Возвращает список уникальных id в порядке появления.
    """
    import re
    ids: list[int] = []
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.get(url) as resp:
                html = await resp.text()

        # 1) /vacancy/<id>
        for m in re.finditer(r'/vacancy/(\d+)', html):
            vid = int(m.group(1))
            if vid not in ids:
                ids.append(vid)
                if len(ids) >= max(1, int(limit)):
                    return ids

        # 2) Фолбэк: JSON-вкрапления vacancyId
        for m in re.finditer(r'vacancyId["\s:{\[]*(\d+)', html):
            vid = int(m.group(1))
            if vid not in ids:
                ids.append(vid)
                if len(ids) >= max(1, int(limit)):
                    return ids
    except Exception:
        pass
    return ids

async def confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, message=None):
    tg_id = update.effective_user.id
    data = context.user_data.get("new_request", {}) or {}
    area_id = _resolve_area_id_from_request(data)
    keyword = (data.get("keyword") or data.get("query") or "").strip()
    role_ids = _get_professional_role_ids(context, data)

    # «График работы» теперь показываем по work_format, а не по старому schedule
    work_format_names = [WORK_FORMAT_OPTIONS.get(w, w) for w in (data.get("work_format") or [])]
    schedule_names = work_format_names or ["Не указано"]

    # ----- Имена для подтверждения -----
    if data.get("search_by_url"):
        country_name       = "Из ссылки"
        region_name        = "Из ссылки"
        schedule_names     = work_format_names or ["Не указано"]  
        employment_names   = data.get("employment", []) or ["Не указано"]
        prof_category_names= data.get("profession", []) or ["Не указано"]
        search_field_names = data.get("search_fields", []) or ["Не указано"]
    else:
        try:
            await _ensure_areas_cache(context)
        except Exception:
            pass

        country_id   = data.get("country")
        country_name = _area_name(context, int(country_id) if country_id else None)

        region_token = str(data.get("region", "")).strip()
        if region_token.startswith("all_"):
            region_name = "Вся страна"
        else:
            try:
                region_name = _area_name(context, int(region_token))
            except Exception:
                region_name = region_token or "Не указано"

        # здесь ТОЖЕ показываем work_format вместо schedule
        schedule_names = work_format_names or ["Не указано"]

        employment_names = [
            e["name"] for e in config.DEMO_EMPLOYMENT if e["id"] in (data.get("employment") or [])
        ] or ["Не указано"]

        prof_category_names = [
            c["name"] for c in config.DEMO_PROFESSIONS if str(c["id"]) in (data.get("profession") or [])
        ] or ["Не указано"]

        search_field_names = [
            buttons.SEARCH_FIELD_OPTIONS.get(f, f) for f in (data.get("search_fields") or [])
        ] or ["Не указано"]
    # ----- Ссылка на hh.ru (нужна и для показа, и для фолбэка счётчика) -----
    link_kv: list[tuple[str, str]] = []
    if keyword:
        link_kv.append(("text", keyword))
    if area_id:
        link_kv.append(("area", str(area_id)))
    for r in (role_ids or []):
        link_kv.append(("professional_role", str(r)))
    for wf in (data.get("work_format") or []):
        link_kv.append(("work_format", str(wf)))
    for e in (data.get("employment") or []):
        link_kv.append(("employment", str(e)))
    for f in (data.get("search_fields") or []):
        link_kv.append(("search_field", str(f)))
    hh_ru_link = "https://hh.ru/search/vacancy?" + urlencode(link_kv, doseq=True)

    # ----- Получаем количество вакансий -----
    vacancy_count = 0
    scraped = None
    try:
        scraped = await _scrape_found_from_hh(hh_ru_link)
        if scraped is not None:
            vacancy_count = int(scraped)
    except Exception:
        pass

    # Если по каким-то причинам не удалось вытащить число со страницы — используем API как фолбэк.
    if scraped is None:
        try:
            res = await _hh_search_safe(
                keyword, area_id, 0, 1,
                schedules=None,  # work_format в API нет — используем только в ссылке
                employment=data.get("employment") or None,
                professional_roles=_get_professional_role_ids(context, data) or None,
                search_fields=data.get("search_fields") or None,
            )
            # у бэка бывает 'found' или 'total'
            vacancy_count = int(
                res.get("found")
                or res.get("total")
                or (len(res.get("items", [])) if isinstance(res.get("items"), list) else 0)
                or 0
            )
        except Exception as e:
            logger.warning("search for confirmation failed: %s", e)
    # Фолбэк: если API вернул 0/None, пробуем подтянуть число со страницы HH
    if not vacancy_count:
        try:
            scraped = await _scrape_found_from_hh(hh_ru_link)
            if scraped:
                vacancy_count = scraped
        except Exception:
            pass

    # ----- Лимиты -----
    daily_count, _limit, remaining_count, _tariff, _rt = await _quota_fresh(tg_id)

    summary_text = texts.get_confirmation_text(
        vacancy_count=vacancy_count,
        hh_ru_link=hh_ru_link,
        country_name=country_name,
        region_name=region_name,
        schedule=", ".join(schedule_names) or "Не указано",
        employment=", ".join(employment_names) or "Не указано",
        profession=", ".join(prof_category_names) or "Не указано",
        keyword=keyword or "Не указано",
        search_field=", ".join(search_field_names) or "Не указано",
        cover_letter=data.get("cover_letter", "Без сопроводительного письма"),
        daily_count=daily_count,
        remaining_count=remaining_count,
    )

    keyboard = [
        [InlineKeyboardButton("▶️ Запустить отклики", callback_data="send_responses")],
        [InlineKeyboardButton("Создать новый запрос", callback_data="restart_flow")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if message:
        try:
            await message.edit_text(
                summary_text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning("Failed to edit message: %s", e)
            await message.reply_text(
                summary_text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        return states.CONFIRMATION
    else:
        await update.message.reply_text(
            summary_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return states.CONFIRMATION

def _build_hh_query(saved: dict) -> dict:
    q = {}
    t = (saved.get("text") or saved.get("keyword") or "").strip()
    if t: q["text"] = t
    sf = saved.get("search_fields") or saved.get("sources") or []
    if sf: q["search_field"] = sf
    if saved.get("area_ids"):
        q["area"] = saved["area_ids"]
    elif saved.get("country_id"):
        q["area"] = [saved["country_id"]]
    if saved.get("employments"): q["employment"] = saved["employments"]
    # HH использует schedule; старый ключ work_format оставляем только во внутреннем объекте
    if saved.get("schedules"): q["schedule"] = saved["schedules"]
    if saved.get("professional_roles"): q["professional_role"] = saved["professional_roles"]
    return q

def _build_query_params(q: dict) -> str:
    pairs = []
    for k, v in q.items():
        if isinstance(v, (list, tuple)):
            for x in v: pairs.append((k, str(x)))
        else:
            pairs.append((k, str(v)))
    return urlencode(pairs, doseq=True)

_FALSEY_COVER = {"нет","без сопроводительного письма","no","none","-","false"}

def _normalize_cover(text: str | None) -> str:
    t = (text or "").strip()
    return "" if t.lower() in _FALSEY_COVER else t

def _short_resume_id(resume_id: str | None) -> str:
    rid = (resume_id or "").strip()
    if not rid:
        return "—"
    return rid if len(rid) <= 16 else (rid[:6] + "…" + rid[-6:])

async def _ensure_resumes_cache(context, tg_id: int):
    if context.bot_data.get("resumes_map_loaded_for") == tg_id:
        return
    try:
        data = await resumes_list(tg_id) 
        items = data.get("items") or []
        context.bot_data["resumes_map"] = {str(i.get("id")): (i.get("title") or "").strip() for i in items}
        context.bot_data["resumes_map_loaded_for"] = tg_id
    except Exception:
        context.bot_data.setdefault("resumes_map", {})
        context.bot_data["resumes_map_loaded_for"] = tg_id
        
async def _enrich_campaign_for_render(context, c: dict) -> dict:
    """Заполнить пропуски и подготовить кампанию к рендеру карточки."""
    # 1) areas → country/region
    try:
        await _ensure_areas_cache(context)
    except Exception:
        pass

    if not c.get("areas"):
        if c.get("area"):
            c["areas"] = [str(c["area"])]
        else:
            nr = (context.user_data.get("new_request") or {})
            ar = nr.get("area")
            if ar:
                c["areas"] = [str(x) for x in (ar if isinstance(ar, list) else [ar])]

    country, region = _areas_to_labels(context, c.get("areas") or [])
    c["country"] = country
    c["region"]  = region

    # 2) человекочитаемые поля
    c.setdefault("work_format", c.get("work_format") or [])
    c.setdefault("employment",  c.get("employment")  or [])
    c.setdefault("search_fields", c.get("search_fields") or [])
    c["cover_letter"] = _normalize_cover(c.get("cover_letter"))
    c["prof_area"] = c.get("prof_area") or _prof_roles_to_label(c.get("professional_roles"))

    rid = (c.get("resume") or c.get("resume_id") or context.user_data.get("resume_id") or "")
    rid = str(rid).strip()
    if rid:
        c["resume"] = rid  # унифицируем ключ
    
    # приоритет: то, что вернул бэк → кэш → короткий id
    rmap  = context.bot_data.get("resumes_map") or {}
    title = (c.get("resume_title") or rmap.get(rid) or "").strip()
    c["resume_title"] = title if title else _short_resume_id(rid)

    return c

def _cover_to_label(text: str | None) -> str:
    t = _normalize_cover(text)
    return "Нет" if not t else ("Да, " + (t if len(t) <= 120 else t[:120] + "…"))

def _areas_to_labels(context, areas: list[str]) -> tuple[str, str]:
    if not areas: return ("—", "—")
    id2 = context.bot_data.get(AREAS_KEY) or {}
    def _name(a):
        try: a = int(a)
        except: pass
        return (id2.get(a) or {}).get("name") or str(a)
    names = [_name(a) for a in areas]
    if len(areas) == 1:
        return (names[0], "—")
    if any(str(a) == "113" for a in areas):
        regions = [n for a, n in zip(areas, names) if str(a) != "113"]
        return ("Россия", ", ".join(regions) or "—")
    return ("—", ", ".join(names))

def _search_fields_to_label(fields: list[str]) -> str:
    try:
        from ..utils import buttons 
        mapping = buttons.SEARCH_FIELD_OPTIONS
    except Exception:
        mapping = {"name": "В названии", "description": "В описании", "company_name": "Название компании"}
    return ", ".join(mapping.get(f, f) for f in (fields or [])) or "Все"

from config import AREA_NAME_BY_ID, ROLE_TO_AREA_ID

def _prof_roles_to_label(professional_roles) -> str:
    """Преобразует список role_id -> человекочитаемые названия Проф. областей."""
    if not professional_roles:
        return "—"
 
    role_ids = [str(r) for r in professional_roles if str(r).strip()]
    area_ids = []
    for rid in role_ids:
        aid = ROLE_TO_AREA_ID.get(rid)
        if aid:
            area_ids.append(aid)
    if not area_ids:
        return "—"

    area_names = sorted({AREA_NAME_BY_ID.get(aid, f"[{aid}]") for aid in area_ids})
    return ", ".join(area_names) if area_names else "—"
    
async def send_responses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    edit_cid = context.user_data.pop("edit_campaign_id", None)
    edit_srid = context.user_data.pop("edit_saved_request_id", None)

    cbq = update.callback_query
    if cbq:
        await cbq.answer(cache_time=1)

    tg_id = update.effective_user.id

    used, limit_cap, remaining, tariff, reset_time = await _quota_fresh(tg_id)
    if remaining <= 0:
        msg = "❌ Дневной лимит откликов исчерпан. Попробуйте завтра."
        if cbq: await cbq.message.edit_text(msg)
        else:   await update.effective_chat.send_message(msg)
        return ConversationHandler.END

    rd = (context.user_data.get("new_request") or {}).copy()

    # --- резюме ---
    raw_resume = context.user_data.get("resume_id") or rd.get("resume")
    if not raw_resume:
        saved_id  = context.user_data.get("used_saved_id")
        saved_map = context.user_data.get("saved_requests_map") or {}
        if saved_id and saved_id in saved_map:
            saved = saved_map[saved_id] or {}
            raw_resume = saved.get("resume") or _extract_resume_preview(saved)
    resume_id = str(raw_resume or "").strip()
    if resume_id:
        context.user_data["resume_id"] = resume_id

    # --- текст запроса ---
    kw = (rd.get("keyword") or rd.get("query") or "").strip()

    area_ids: list[str] = []
    if isinstance(rd.get("area"), list) and rd["area"]:
        for a in rd["area"]:
            try:
                area_ids.append(str(int(a)))
            except Exception:
                area_ids.append(str(a))
    
    if not area_ids:
        country = rd.get("country") or rd.get("country_id")
        if country:
            # 'all' → поиск по всем странам (без area)
            if isinstance(country, str) and country.lower() == "all":
                country = None
            elif isinstance(country, str) and country.startswith("all_"):
                country = country.split("_", 1)[1]
    
            if country not in (None, "", []):
                try:
                    area_ids = [str(int(country))]
                except Exception:
                    area_ids = [str(country)]

    # ✅ СКАЛЯР для API (campaign_upsert ждёт один int | None)
    area_val = None
    if area_ids:
        try:
            area_val = int(area_ids[0])
        except Exception:
            area_val = area_ids[0]
    
    # --- проф. роли / schedule и т.д. ---
    search_fields = rd.get("search_fields") or []
    schedules     = rd.get("work_format")  or []      
    emps          = rd.get("employment")  or []
    roles         = _get_professional_role_ids(context, rd) or []
    
    # --- собираем HH-QS для ссылки/счётчика ---
    qp_pairs: list[tuple[str, str]] = []
    if kw:
        qp_pairs.append(("text", kw))
    for a in area_ids:
        qp_pairs.append(("area", a))
    for f in search_fields:
        qp_pairs.append(("search_field", f))
    for s in schedules:
        qp_pairs.append(("schedule", s))               
    for e in emps:
        qp_pairs.append(("employment", e))
    for r in roles:
        qp_pairs.append(("professional_role", str(int(r))))
    qs = urlencode(qp_pairs, doseq=True)
    cl_raw = _normalize_cover(rd.get("cover_letter"))
    payload = {
        "title": (rd.get("title") or kw or "Поиск").strip(),
        "query": kw,
        "country": rd.get("country"),
        "region":  rd.get("region"),
        "area":    area_val,                          
        "work_format": schedules,                     
        "employment":  emps,
        "professional_roles": roles,
        "search_fields": search_fields,
        "cover_letter": cl_raw,
        "resume_id": resume_id,
        "resume":    resume_id,
        "query_params": qs,
    }
    
    # 1) создаём/обновляем saved_request
    new_saved_id = None
    if edit_srid:
        try:
            await saved_requests_update(tg_id, id=edit_srid, payload=payload)
            new_saved_id = edit_srid
        except Exception:  # было NameError
            resp = await saved_requests_create(tg_id, payload)
            new_saved_id = int(resp.get("id"))

    else:
        resp = await saved_requests_create(tg_id, payload)
        new_saved_id = int(resp.get("id"))
    
    # 2) если это редактирование и апдейта нет — удаляем старую кампанию
    if edit_cid and new_saved_id != edit_srid:
        try:
            await campaign_delete(tg_id, edit_cid)
        except Exception:
            pass  # если уже удалена — ок
    
    # 3) upsert кампании с НОВЫМ (или старым) saved_request_id
    up = await campaign_upsert(
        tg_id,
        title=payload["title"],
        saved_request_id=new_saved_id,
        resume_id=resume_id,
        daily_limit=limit_cap or 200,
        query=kw,
        area=area_val,
        work_format=schedules,
        employment=emps,
        professional_roles=roles,
        search_fields=search_fields,
    )
    cid = int(up.get("id"))
    try:
        await campaign_start(tg_id, cid)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            await cbq.message.edit_text(
                "⚠️ Одновременно может работать только одна кампания.\n"
                "Чтобы запустить новую, сначала остановите текущую.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("↩️ Назад", callback_data="resp_back")]]
                )
            )
            return ConversationHandler.END
        raise
    # 4) берём кампанию из API (если доступно), иначе — обогащаем локально и рендерим
    try:
        data = await campaigns_list(tg_id, page=1, page_size=20)
        cmap = {int(i["id"]): i for i in (data.get("items") or [])}
        camp = cmap.get(cid) or (up if isinstance(up, dict) else {})
    except Exception:
        camp = up if isinstance(up, dict) else {}
    
    # Фолбэк-обогащение для мгновенного рендера карточки
    camp.setdefault("areas", area_ids)
    camp.setdefault("work_format", schedules)
    camp.setdefault("employment", emps)
    camp.setdefault("professional_roles", roles)
    camp.setdefault("search_fields", search_fields)
    camp.setdefault("cover_letter", payload.get("cover_letter") or "")
    if resume_id and not camp.get("resume"):
        camp["resume"] = resume_id
    camp.setdefault("search_url", "https://hh.ru/search/vacancy?" + qs)
    
    try:
        await _ensure_areas_cache(context)
    except Exception:
        pass

    country, region   = _areas_to_labels(context, camp.get("areas") or [])
    camp["country"]   = country
    camp["region"]    = region
    camp["prof_area"] = _prof_roles_to_label(camp.get("professional_roles"))
    
    await _ensure_resumes_cache(context, tg_id)
    camp = await _enrich_campaign_for_render(context, camp)
    
    await cbq.message.edit_text(
        _render_campaign_card(camp),
        reply_markup=_kb_campaign_running(cid),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    return states.SELECTING_ACTION

async def send_test_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отправляет тестовый отклик (ставит в очередь 1 вакансию, если есть выбор; иначе пытается взять из поиска)."""
    cbq = update.callback_query
    if cbq:
        await cbq.answer()
    
    user_id = update.effective_user.id
    request_data = context.user_data.get("new_request", {}) or {}
    used, limit_for_text, remaining = await get_quota_safe(context, user_id)

    if remaining <= 0:
        if cbq:
            await cbq.message.edit_text("❌ Дневной лимит откликов исчерпан. Попробуйте завтра.")
        else:
            await update.effective_chat.send_message("❌ Дневной лимит откликов исчерпан. Попробуйте завтра.")
        return CONFIRMATION
    
    if cbq:
        await cbq.message.edit_text("🧪 Отправляем тестовый отклик...")
    else:
        await update.effective_chat.send_message("🧪 Отправляем тестовый отклик...")

    chat_id = update.effective_chat.id

    selected = context.user_data.get("selected_vacancies", [])
    vacancy_ids: List[int] = [int(v["id"]) for v in selected][:1] if selected else []

    if not vacancy_ids:
        area_id = _resolve_area_id_from_request(request_data)
        keyword = request_data.get("keyword", "") or ""
        try:
            res = await _hh_search_safe(
                keyword, area_id, 0, 1,
                employment=request_data.get("employment") or None,
                professional_roles=[int(x) for x in _derive_professional_roles_from_categories(context, request_data)] or None,
                search_fields=request_data.get("search_fields") or None,
            )
            items = list(res.get("items", []))
            if items:
                vacancy_ids = [int(items[0]["id"])]
        except Exception as e:
            logger.exception("test search failed: %s", e)

    if not vacancy_ids:
        if cbq:
            await cbq.message.edit_text("Нет вакансий для тестового отклика.")
        else:
            await update.effective_chat.send_message("Нет вакансий для тестового отклика.")
        return CONFIRMATION

    cover = context.user_data.get("cover_letter_text") or request_data.get("cover_letter") or None
    try:
        resume_id = (context.user_data.get("new_request", {}) or {}).get("resume")
        resp = await queue_applications(chat_id, vacancy_ids, cover, "manual", resume_id)
        queued = int(resp.get("queued", 1))
        try:
            q2 = await quota_current(user_id)
            used2, lim2, left2, _ = _normalize_quota(q2)
            used = used2
            left = left2
            limit_for_text = lim2
        except Exception:
            used = increment_daily_response_count(context, user_id, queued)
            left = get_remaining_responses(context, user_id)

        msg_text = (
            f"✅ Тестовый отклик отправлен!\n\n"
            f"📊 Статистика:\n"
            f"Отправлено сегодня: {used}/{limit_for_text}\n"
            f"Осталось: {left}"
        )
        if cbq:
            await cbq.message.edit_text(msg_text)
        else:
            await update.effective_chat.send_message(msg_text)

    except Exception as e:
        logger.exception("queue_applications (test) failed: %s", e)
        if cbq:
            await cbq.message.edit_text("Ошибка постановки в очередь. Попробуйте позже.")
        else:
            await update.effective_chat.send_message("Ошибка постановки в очередь. Попробуйте позже.")

    return CONFIRMATION

def get_responses_conv_handler():
    from routers import menu, start

    return ConversationHandler(
            entry_points=[
                CallbackQueryHandler(start_responses_entry, pattern=r"^start_responses$"),
                CommandHandler("responses", start_responses_entry),
        
                CallbackQueryHandler(on_camp_edit,   pattern=r"^camp_edit:\d+$"),
                CallbackQueryHandler(on_camp_stop,   pattern=r"^camp_stop:\d+$"),
                CallbackQueryHandler(on_camp_start,  pattern=r"^camp_start:\d+$"),
                CallbackQueryHandler(on_camp_open,   pattern=r"^camp_open:\d+$"),
                CallbackQueryHandler(on_camp_delete_confirm, pattern=r"^camp_delete_confirm:\d+$"),
                CallbackQueryHandler(on_camp_delete, pattern=r"^camp_delete:\d+$"),
            ],
        states={
            states.SELECTING_ACTION: [
                CallbackQueryHandler(on_camp_edit, pattern=r"^camp_edit:\d+$"),
                CallbackQueryHandler(on_camp_open,           pattern=r"^camp_open:\d+$"),
                CallbackQueryHandler(on_camp_delete_confirm, pattern=r"^camp_delete_confirm:\d+$"),
                CallbackQueryHandler(on_camp_delete,         pattern=r"^camp_delete:\d+$"),
                CallbackQueryHandler(on_camp_stop,  pattern=r"^camp_stop:\d+$"),
                CallbackQueryHandler(on_camp_start, pattern=r"^camp_start:\d+$"),
                CallbackQueryHandler(ask_resume, pattern="^new_request$"),
                CallbackQueryHandler(choose_from_saved, pattern=r"^(responses_from_saved|past_requests)$"),
                CallbackQueryHandler(use_saved,        pattern=r"^resp_saved_\d+$"),
                CallbackQueryHandler(delete_saved,     pattern=r"^resp_del_\d+$"),
                CallbackQueryHandler(start_responses_entry, pattern=r"^resp_back$"),
            ],
            states.ASK_RESUME: [CallbackQueryHandler(ask_search_method, pattern="^resume_")],
            states.ASK_SEARCH_METHOD: [
                CallbackQueryHandler(ask_country_for_filters, pattern="^configure_filters$"),
                CallbackQueryHandler(ask_hh_url, pattern="^paste_link$"),
            ],
            states.ASK_HH_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_hh_url)],
            states.ASK_COUNTRY: [CallbackQueryHandler(ask_country_for_filters, pattern="^resume_"),
                                 CallbackQueryHandler(handle_region_page, pattern=r"^page_region_-?\d+_nav_\d+$"),
            ],
            states.ASK_REGION: [
                CallbackQueryHandler(ask_region, pattern="^country_"),
                CallbackQueryHandler(handle_country_page, pattern="^page_country_nav_"),
                CallbackQueryHandler(handle_region_page, pattern=r"^page_region_-?\d+_nav_\d+$"),
            ],
            states.ASK_SCHEDULE: [
                CallbackQueryHandler(ask_work_format, pattern="^region_"),
                CallbackQueryHandler(handle_region_page, pattern=r"^page_region_-?\d+_nav_\d+$"),
            ],
            states.ASK_WORK_FORMAT: [
                CallbackQueryHandler(handle_work_format_choice, pattern=r"^workfmt_(?!next)"),
                CallbackQueryHandler(ask_employment, pattern=r"^workfmt_next$"),
            ],
            states.ASK_EMPLOYMENT: [
                CallbackQueryHandler(handle_schedule_choice, pattern="^schedule_(?!next)"),
                CallbackQueryHandler(ask_employment, pattern="^schedule_next$"),
            ],
            states.ASK_PROFESSION: [
                CallbackQueryHandler(handle_employment_choice, pattern="^employment_(?!next)"),
                CallbackQueryHandler(ask_profession, pattern="^employment_next$"),
                CallbackQueryHandler(handle_profession_choice, pattern="^profession_(?!next)"),
                CallbackQueryHandler(ask_keyword, pattern="^profession_next$"),
                CallbackQueryHandler(handle_prof_toggle, pattern=r"^prof_toggle_\d+$"),
                CallbackQueryHandler(handle_prof_page,   pattern=r"^prof_page_\d+$"),
                CallbackQueryHandler(handle_prof_all,    pattern=r"^prof_all$"),
            ],
            states.ASK_KEYWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_search_field)],
            states.ASK_SEARCH_FIELD: [
                CallbackQueryHandler(handle_search_field_choice, pattern="^search_(?!next)"),
                CallbackQueryHandler(ask_cover_letter_options, pattern="^search_next$"),
            ],
            states.ASK_COVER_LETTER: [
                CallbackQueryHandler(handle_no_cover_letter, pattern="^no_letter$"),
                CallbackQueryHandler(handle_cl_selection, pattern=r"^cl_select_\d+$"),
                CallbackQueryHandler(ask_new_cover_letter, pattern="^cl_write_new$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cover_letter_text),
            ],
            states.CONFIRMATION: [
                CallbackQueryHandler(send_responses, pattern="^send_responses$"),
                CallbackQueryHandler(ask_resume, pattern="^restart_flow$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start.start_over),
            CallbackQueryHandler(menu.back_to_main_menu, pattern="^main_menu$"),
        ],
        allow_reentry=True,
    )
