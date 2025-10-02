# front_bot/routers/auto_responses.py
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qs, parse_qsl

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
from utils import texts, buttons
from utils.helpers import build_paginated_keyboard, build_multi_choice_keyboard, handle_multi_choice
from utils.states import (
    AUTO_RESPONSE_MAIN,
    AUTO_RESPONSE_RESUME,
    AUTO_RESPONSE_SEARCH_METHOD,
    AUTO_RESPONSE_FILTERS,
    AUTO_RESPONSE_HH_URL,
    AUTO_RESPONSE_COVER_LETTER,
    AUTO_RESPONSE_CONFIRMATION,
    ASK_REGION,
    ASK_SCHEDULE,
    ASK_EMPLOYMENT,
    ASK_PROFESSION,
    ASK_KEYWORD,
    ASK_SEARCH_FIELD,
    ASK_WORK_FORMAT,
)
from utils.api_client import (
    hh_resumes,
    hh_resumes_sync,       
    link_status,          
    authorize_url,         
    auto_upsert,          
    auto_plan,             
)

from utils.api_client import cover_letters_list_sync
from utils.api_client import quota_current
from routers.responses import _compose_finish_notice
from utils.api_client import queue_applications, dispatch_now
from routers.responses import (
    _normalize_quota,
    _resolve_area_id_from_request,
    _get_professional_role_ids,
    _hh_search_safe,
    _scrape_vacancy_ids,
)
from routers.responses import _render_prof_page, handle_prof_toggle, handle_prof_all, handle_prof_page
from config import DEMO_PROF_ROLE_MAP
from utils.api_client import auto_status_sync, auto_set_active_sync

logger = logging.getLogger(__name__)

_ALLOW_KEYS = {
    "text", "area", "professional_role", "specialization",
    "experience", "employment", "schedule", "work_format",
    "only_with_salary", "salary", "currency",
    "search_field", "label", "order_by",
}
_DROP_VALUES = {"", None}

def normalize_hh_query(qs_or_url: str) -> str:
    """
    На вход — полный URL hh.ru или чистый querystring.
    На выход — «канонический» querystring:
      * только разрешённые ключи
      * без page/per_page
      * без пустых значений
      * стабильно отсортирован
    """
    raw = (qs_or_url or "").strip()
    if "?" in raw:
        raw = urlparse(raw).query
    pairs = parse_qsl(raw, keep_blank_values=True)

    kept = []
    for k, v in pairs:
        if k in {"page", "per_page"}:
            continue
        if k not in _ALLOW_KEYS:
            continue
        if v in _DROP_VALUES:
            continue
        kept.append((k, v))

    kept.sort(key=lambda kv: (kv[0], kv[1]))
    return urlencode(kept, doseq=True)
    
WORK_FORMAT_CHOICES = {
    "ON_SITE":    "На месте работодателя",
    "REMOTE":     "Удалённо",
    "HYBRID":     "Гибрид",
    "FIELD_WORK": "Разъездной",
}
# ───────────────────────── Главный экран ─────────────────────────
def _build_hh_search_url(qs: str) -> str:
    q = (qs or "").lstrip("?")
    return f"https://hh.ru/search/vacancy?{q}"
    
async def show_auto_responses_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tg_id = update.effective_user.id

    try:
        st = await asyncio.to_thread(auto_status_sync, tg_id)
        active = bool(st.get("active"))
        context.user_data["auto_response_active"] = active
        context.user_data["auto_response_settings"] = st or {}
    except Exception as e:
        logger.warning("auto_status_sync failed: %s", e)
        active = bool(context.user_data.get("auto_response_active", False))
        st = context.user_data.get("auto_response_settings", {}) or {}

    def _to_int(x, default=0):
        try:
            return int(x)
        except Exception:
            return default

    hh_url = st.get("hh_url") or None
    today_cnt = _to_int(st.get("today_count"), 0)
    total_cnt = _to_int(st.get("total_count"), 0)

    if active:
        text = texts.get_auto_response_active_status(
            st.get("start_date", "Не указано"),
            st.get("start_time", "Не указано"),
            today_cnt,
            total_cnt,
            f"Поиск по ссылке: {hh_url}" if st.get("search_by_url") and hh_url else "Настроенные фильтры в боте",
        )

        kb: list[list[InlineKeyboardButton]] = []

        kb.extend([
            [InlineKeyboardButton("⏹️ Остановить автоотклики", callback_data="auto_stop")],
            [InlineKeyboardButton("⚙️ Изменить параметры",     callback_data="auto_change_settings")],
            [InlineKeyboardButton("🔙 Главное меню",            callback_data="main_menu")],
        ])
    else:
        text = texts.AUTO_RESPONSE_MAIN
        kb = [
            [InlineKeyboardButton("▶️ Запустить снова",        callback_data="auto_activate")],  
            [InlineKeyboardButton("🔙 Главное меню",           callback_data="main_menu")],
        ]

    reply = InlineKeyboardMarkup(kb)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            text,
            reply_markup=reply,
            disable_web_page_preview=False,
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=reply,
            disable_web_page_preview=False,
        )

    return AUTO_RESPONSE_MAIN


# ───────────────────────── Шаг 1: выбор резюме ─────────────────────────
async def run_auto_batch(context, tg_id: int, saved_cfg: dict) -> None:
    """
    Автопачка: берём фильтры из сохранённых настроек (как в ручном),
    проверяем остаток, ставим в очередь не больше остатка.
    """
    q = await quota_current(tg_id)
    used, limit_cap, remaining, *_ = _normalize_quota(q)
    if remaining <= 0:
        return

    keyword = (saved_cfg.get("keyword") or saved_cfg.get("query") or "").strip()
    area_id = _resolve_area_id_from_request(saved_cfg)
    roles   = _get_professional_role_ids(context, saved_cfg)
    workfmt = saved_cfg.get("work_format") or []
    empl    = saved_cfg.get("employment") or []
    sfields = saved_cfg.get("search_fields") or []

    vacancy_ids: list[int] = []
    try:
        use_scrape = bool(workfmt) or bool(saved_cfg.get("search_by_url"))
        if use_scrape:
            kv: list[tuple[str,str]] = []
            if keyword: kv.append(("text", keyword))
            if area_id: kv.append(("area", str(area_id)))
            for r in roles:        kv.append(("professional_role", str(r)))
            for wf in workfmt:     kv.append(("work_format", str(wf)))
            for e in empl:         kv.append(("employment", str(e)))
            for f in sfields:      kv.append(("search_field", str(f)))
            hh_url = "https://hh.ru/search/vacancy?" + urlencode(kv, doseq=True)
            vacancy_ids = await _scrape_vacancy_ids(hh_url, limit=min(remaining, 20))
        else:
            res = await _hh_search_safe(
                keyword, area_id, 0, min(remaining, 20),
                schedules=None,
                employment=empl or None,
                professional_roles=roles or None,
                search_fields=sfields or None,
            )
            items = list(res.get("items", [])) if isinstance(res.get("items"), list) else []
            vacancy_ids = [int(it["id"]) for it in items if str(it.get("id")).isdigit()]
    except Exception:
        return 

    if not vacancy_ids:
        return

    resume_id = str(saved_cfg.get("resume") or saved_cfg.get("resume_id") or "").strip()
    cover     = saved_cfg.get("cover_letter") or ""
    try:
        resp = await queue_applications(tg_id, vacancy_ids[:remaining], cover, "auto", resume_id)
        queued = int(resp.get("queued", 0))
        if queued > 0:
            try:
                await dispatch_now(limit=max(1, queued), dry_run=False)
            except Exception:
                pass
    except Exception:
        pass
    
async def start_auto_response_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["auto_response_setup"] = {}
    return await ask_auto_response_resume(update, context)

async def ask_auto_response_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        await q.answer()

    tg_id = update.effective_user.id
    try:
        resumes = await asyncio.to_thread(hh_resumes, tg_id)
        if not resumes:
            try:
                await asyncio.to_thread(hh_resumes_sync, tg_id)
                resumes = await asyncio.to_thread(hh_resumes, tg_id)
            except Exception:
                pass
    except Exception as e:
        msg = f"❌ Не удалось получить список резюме: {e}"
        if q:
            await q.message.edit_text(msg)
        else:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    context.user_data["_resumes"] = resumes or []

    if not resumes:
        try:
            status = await asyncio.to_thread(link_status, tg_id)
        except Exception:
            status = {"linked": False}

        rows = []
        if not status.get("linked"):
            try:
                a = await asyncio.to_thread(authorize_url, tg_id)
                if a and a.get("url"):
                    rows.append([InlineKeyboardButton("🔗 Привязать HH", url=a["url"])])
            except Exception:
                pass
        rows.append([InlineKeyboardButton("↩️ Проверить снова", callback_data="auto_resume_reload")])
        rows.append([InlineKeyboardButton("🔙 Назад",             callback_data="auto_main")])

        text = (
            "В вашем hh.ru не найдено ни одного резюме.\n\n"
            "Нажмите «Привязать HH», авторизуйтесь и вернитесь в бота, затем нажмите «Проверить снова»."
        )
        reply = InlineKeyboardMarkup(rows)
        if q:
            await q.message.edit_text(text, reply_markup=reply)
        else:
            await update.message.reply_text(text, reply_markup=reply)
        return AUTO_RESPONSE_RESUME

    rows = []
    for r in resumes:
        rid = str(r.get("id"))
        title = (r.get("title") or rid).strip()
        rows.append([InlineKeyboardButton(title, callback_data=f"auto_resume_{rid}")])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="auto_main")])

    text = "📍 Шаг 1/10:\nВыберите резюме, с которого будут отправлены отклики"
    if q:
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(rows))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
    return AUTO_RESPONSE_RESUME

# ───────────────────────── Шаг 2: способ поиска ─────────────────────────

async def ask_auto_response_search_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    resume_id = q.data.replace("auto_resume_", "")
    context.user_data['auto_response_setup']['resume_id'] = resume_id

    resumes = context.user_data.get("_resumes", [])
    title = next((r.get("title") for r in resumes if str(r.get("id")) == str(resume_id)), resume_id)
    context.user_data['auto_response_setup']["resume"] = {"id": resume_id, "title": title}

    kb = [
        [InlineKeyboardButton("🔎 Настроить фильтры",      callback_data="auto_configure_filters")],
        [InlineKeyboardButton("🌐 Вставить ссылку hh.ru",   callback_data="auto_paste_link")],
        [InlineKeyboardButton("🔙 Назад",                  callback_data="auto_resume_back")],
    ]
    await q.message.edit_text(texts.AUTO_RESPONSE_ASK_SEARCH_METHOD, reply_markup=InlineKeyboardMarkup(kb))
    return AUTO_RESPONSE_SEARCH_METHOD


async def _ensure_areas_cache(context: ContextTypes.DEFAULT_TYPE):
    from routers.responses import _ensure_areas_cache as _inner
    return await _inner(context)

from routers.responses import AREAS_BY_PARENT_KEY  # константа с индексом регионов по parent_id

async def start_auto_response_filters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 2/10 → выбор страны (c пагинацией)."""
    q = update.callback_query
    await q.answer()

    try:
        await _ensure_areas_cache(context)
    except Exception as e:
        logger.exception("failed to load areas: %s", e)
        await q.message.edit_text("Не удалось загрузить список стран. Попробуйте позже.")
        return AUTO_RESPONSE_SEARCH_METHOD

    countries = context.bot_data.get("countries", [])
    reply = build_paginated_keyboard(countries, page=0, prefix="country")
    await q.message.edit_text("📍 Шаг 2/10:\nВыберите страну поиска", reply_markup=reply)
    return ASK_REGION

async def handle_country_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пагинация стран: page_country_nav_{page}."""
    q = update.callback_query
    await q.answer()
    try:
        page = int(q.data.split("_")[-1])
    except Exception:
        page = 0
    countries = context.bot_data.get("countries", [])
    await q.edit_message_reply_markup(build_paginated_keyboard(countries, page=page, prefix="country"))
    return ASK_REGION

async def ask_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    country_id = int(q.data.replace("country_", ""))
    context.user_data.setdefault("auto_response_setup", {})
    context.user_data["auto_response_setup"]["country"] = country_id

    by_parent = context.bot_data.get(AREAS_BY_PARENT_KEY, {})
    regions = list(by_parent.get(country_id, []))
    all_id = f"all_{country_id}"
    regions = [{"id": all_id, "name": "По всей стране", "parent_id": country_id}] + regions

    context.bot_data[f"regions_{country_id}"] = regions
    reply = build_paginated_keyboard(regions, page=0, prefix=f"region_{country_id}")
    await q.message.edit_text("📍 Шаг 3/10:\nВыберите регион", reply_markup=reply)
    return ASK_REGION

async def handle_region_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    parts = q.data.split("_")  
    country_id = parts[2]
    try:
        page = int(parts[-1])
    except Exception:
        page = 0

    regions = context.bot_data.get(f"regions_{country_id}", [])
    await q.edit_message_reply_markup(
        build_paginated_keyboard(regions, page=page, prefix=f"region_{country_id}")
    )
    return ASK_REGION
    
async def ask_work_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    data = q.data.replace("region_", "", 1)
    region_token = data.split("_", 1)[1] if "_" in data else data
    context.user_data["auto_response_setup"]["region"] = region_token

    options = WORK_FORMAT_CHOICES
    context.user_data["workfmt_selection"] = set()
    reply = build_multi_choice_keyboard(options, "workfmt_selection", "work", context)
    await q.message.edit_text("📍 Шаг 4/10:\nВыберите формат работы", reply_markup=reply)
    return ASK_WORK_FORMAT

async def handle_work_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await handle_multi_choice(update, context, WORK_FORMAT_CHOICES, "workfmt_selection", "work")
    return ASK_WORK_FORMAT
    
async def ask_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    context.user_data["auto_response_setup"]["work_format"] = list(
        context.user_data.get("workfmt_selection", [])
    )

    schedules = config.DEMO_SCHEDULES
    context.bot_data["dictionaries"] = {"schedule": schedules}

    options = {item["id"]: item["name"] for item in schedules}
    context.user_data["schedule_selection"] = set()
    reply = build_multi_choice_keyboard(options, "schedule_selection", "schedule", context)
    await q.message.edit_text(texts.ASK_SCHEDULE, reply_markup=reply)
    return ASK_EMPLOYMENT

async def handle_schedule_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    schedules = config.DEMO_SCHEDULES
    options = {item["id"]: item["name"] for item in schedules}
    await handle_multi_choice(update, context, options, "schedule_selection", "schedule")
    return ASK_EMPLOYMENT

async def ask_employment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["auto_response_setup"]["work_format"] = list(
        context.user_data.get("workfmt_selection", [])
    )

    employment = config.DEMO_EMPLOYMENT
    context.bot_data["dictionaries"]["employment"] = employment
    options = {item["id"]: item["name"] for item in employment}
    context.user_data["employment_selection"] = set()
    reply = build_multi_choice_keyboard(options, "employment_selection", "employment", context)
    await q.message.edit_text(texts.ASK_EMPLOYMENT, reply_markup=reply)
    return ASK_EMPLOYMENT

async def handle_employment_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    employment = config.DEMO_EMPLOYMENT
    options = {item["id"]: item["name"] for item in employment}
    await handle_multi_choice(update, context, options, "employment_selection", "employment")
    return ASK_EMPLOYMENT

async def ask_profession(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    context.user_data["auto_response_setup"]["employment"] = list(
        context.user_data.get("employment_selection", [])
    )

    context.bot_data["prof_categories"] = config.DEMO_PROFESSIONS
    context.bot_data["prof_role_map"]   = getattr(config, "DEMO_PROF_ROLE_MAP", {})
    context.user_data["profession_selection"] = set()
    context.user_data["prof_page"] = 0

    await q.message.edit_text(texts.ASK_PROFESSION, reply_markup=_render_prof_page(context, 0))
    return ASK_PROFESSION
    
async def handle_profession_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    options = context.bot_data.get("prof_options", {})
    await handle_multi_choice(update, context, options, "profession_selection", "profession")
    context.user_data["auto_response_setup"]["profession"] = list(context.user_data.get("profession_selection", []))
    return ASK_PROFESSION

async def ask_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data["auto_response_setup"]["profession"] = list(context.user_data.get("profession_selection", []))
    await q.message.edit_text(texts.ASK_KEYWORD)
    return ASK_KEYWORD

async def ask_search_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text_value = (update.message.text or "").strip()
    context.user_data["auto_response_setup"]["text"] = text_value
    context.user_data["search_field_selection"] = set()

    search_field_text = texts.get_search_field_text(text_value)
    reply = build_multi_choice_keyboard(buttons.SEARCH_FIELD_OPTIONS, "search_field_selection", "search", context)
    await update.message.reply_text(search_field_text, reply_markup=reply)
    return ASK_SEARCH_FIELD

async def handle_search_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await handle_multi_choice(update, context, buttons.SEARCH_FIELD_OPTIONS, "search_field_selection", "search")
    return ASK_SEARCH_FIELD


# ───────────────────────── Вариант «по ссылке HH» ─────────────────────────

async def ask_auto_response_hh_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    kb = [[InlineKeyboardButton("🔙 Назад", callback_data="auto_search_method_back")]]
    await q.message.edit_text(texts.AUTO_RESPONSE_ASK_HH_URL, reply_markup=InlineKeyboardMarkup(kb))
    return AUTO_RESPONSE_HH_URL

async def handle_auto_response_hh_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url_text = update.message.text.strip()
    try:
        parsed = urlparse(url_text)
        q = parse_qs(parsed.query)
        
        empl_map = {"FULL": "full","PART":"part","PROJECT":"project","VOLUNTEER":"volunteer","INTERNSHIP":"probation"}
        employment = q.get("employment", [])
        employment += [empl_map[x] for x in q.get("employment_form", []) if x in empl_map]
        
        work_format = q.get("work_format", [])
        setup = context.user_data["auto_response_setup"]
        setup.update({
            "search_by_url": True,
            "hh_url": url_text,
            "keyword": q.get("text", [""])[0],
            "area": q.get("area", []),                    
            "work_format": work_format,                       
            "employment": employment,                     
            "profession": q.get("professional_role", []), 
            "search_fields": q.get("search_field", []),   
        })
        return await ask_auto_response_cover_letter(update, context)
    except Exception as e:
        logger.exception("Failed to parse auto-response HH URL: %s", e)
        await update.message.reply_text("Не удалось распознать ссылку. Проверьте адрес и попробуйте снова.")
        return AUTO_RESPONSE_HH_URL


# ───────────────────────── Письмо ─────────────────────────

async def ask_auto_response_cover_letter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tg_id = update.effective_user.id
    try:
        letters = await asyncio.to_thread(cover_letters_list_sync, tg_id)
    except Exception:
        letters = []
    context.user_data['cover_letters'] = letters

    kb = []
    if letters:
        for i, letter in enumerate(letters):
            kb.append([InlineKeyboardButton(f"📄 {letter['title']}", callback_data=f"auto_cl_select_{i}")])
    kb.extend([
        [InlineKeyboardButton("✏️ Написать новое письмо",       callback_data="auto_cl_write_new")],
        [InlineKeyboardButton("📭 Без сопроводительного письма", callback_data="auto_no_letter")],
        [InlineKeyboardButton("🔙 Назад",                        callback_data="auto_cover_letter_back")],
    ])

    text = "📍 Шаг 9/10:\nВыберите сопроводительное письмо для автооткликов:"
    reply = InlineKeyboardMarkup(kb)
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply)
    else:
        await update.message.reply_text(text, reply_markup=reply)
    return AUTO_RESPONSE_COVER_LETTER


async def handle_auto_response_cover_letter_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    setup = context.user_data.setdefault('auto_response_setup', {})
    letters = context.user_data.get('cover_letters', []) or []

    if q.data == "auto_no_letter":
        setup['cover_letter'] = ""  
    elif q.data.startswith("auto_cl_select_"):
        try:
            idx = int(q.data.rsplit("_", 1)[-1])
            letter = letters[idx]
            setup['cover_letter'] = letter.get('body', '') or ''
        except (ValueError, IndexError):
            await q.message.edit_text("Не удалось выбрать письмо. Попробуйте снова.")
            return await ask_auto_response_cover_letter(update, context)
    elif q.data == "auto_cl_write_new":
        await q.message.edit_text("Напишите сопроводительное письмо для автооткликов:")
        context.user_data['waiting_for_auto_cover_letter'] = True
        return AUTO_RESPONSE_COVER_LETTER

    return await show_auto_response_confirmation(update, context)

async def handle_auto_response_cover_letter_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get('waiting_for_auto_cover_letter', False):
        context.user_data['auto_response_setup']['cover_letter'] = update.message.text
        context.user_data.pop('waiting_for_auto_cover_letter', None)
        return await show_auto_response_confirmation(update, context, message=update.message)
    return AUTO_RESPONSE_COVER_LETTER


# ───────────────────────── Подтверждение ─────────────────────────

async def show_auto_response_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, message=None) -> int:
    setup = context.user_data['auto_response_setup']

    resume_id = setup.get('resume_id')
    resume_obj = setup.get('resume') or {}
    resume_title = resume_obj.get('title') or resume_id or 'Не указано'

    if setup.get('search_by_url'):
        search_method = "По ссылке hh.ru"
        filters_summary = f"Ссылка: {setup.get('hh_url', 'Не указано')}"
    else:
        search_method = "Настроенные фильтры"
        filters_summary = "Фильтры настроены в боте"

    cover_letter = (setup.get('cover_letter') or '').strip()
    cover_letter_status = "Да" if cover_letter else "Нет"

    text = texts.get_auto_response_confirmation(resume_title, search_method, filters_summary, cover_letter_status)
    kb = [
        [InlineKeyboardButton("🚀 Запустить автоотклики", callback_data="auto_start")],
        [InlineKeyboardButton("🔙 Изменить настройки",   callback_data="auto_change_settings")],
    ]
    reply = InlineKeyboardMarkup(kb)

    if message:
        await message.reply_text(text, reply_markup=reply)
    else:
        await update.callback_query.message.edit_text(text, reply_markup=reply)
    return AUTO_RESPONSE_CONFIRMATION


# ───────────────────────── Сохранение правила и планирование ─────────────────────────

def _area_from_setup(setup: dict) -> int | None:
    """Определяем area для hh по выбранной стране/региону."""
    region = str(setup.get("region", "")).strip()
    country = setup.get("country")
    if region.startswith("all_"):
        try:
            return int(region.split("_", 1)[1])
        except Exception:
            return int(country) if country else None
    try:
        return int(region)
    except Exception:
        return int(country) if country else None

async def start_auto_responses(update, context):
    q = update.callback_query
    await q.answer()

    setup = context.user_data.pop("auto_response_setup", {}) or {}
    context.user_data["auto_response_settings"] = setup
    context.user_data["auto_response_active"] = True

    tg_id = update.effective_user.id
    resume = setup.get("resume") or {}
    resume_id = str(resume.get("id") or setup.get("resume_id") or "").strip()
    if not resume_id:
        await q.message.edit_text("❌ Не выбрано резюме. Вернись и выбери резюме.")
        return ConversationHandler.END

    name         = (setup.get("name") or "Моё авто-правило").strip()
    daily_limit = int(setup.get("daily_limit")) if setup.get("daily_limit") is not None else 0
    run_at       = (setup.get("run_at") or "").strip() or None
    cover_letter = (setup.get("cover_letter") or "").strip()

    def _from_url(u: str) -> str:
        try:
            parsed = urlparse(u or "")
            qs = parse_qs(parsed.query, keep_blank_values=False)
    
            text_value = (qs.get("text", [""])[0] or "").strip()
            if not text_value:
                return ""
    
            try:
                area_id = int(qs.get("area", [1])[0])
            except Exception:
                area_id = 1
    
            params: dict[str, object] = {"text": text_value, "area": area_id}
    
            # --- employment ---
            empl_map = {"FULL": "full", "PART": "part", "PROJECT": "project", "VOLUNTEER": "volunteer", "INTERNSHIP": "probation"}
            employment = []
            employment += [empl_map[x] for x in qs.get("employment_form", []) if x in empl_map]
            if employment:
                params["employment"] = employment
    
            # --- work_format ---
            if qs.get("work_format"):
                params["work_format"] = [x.upper() for x in qs["work_format"]]
    
            # --- professional_role ---
            if qs.get("professional_role"):
                params["professional_role"] = [int(x) for x in qs["professional_role"] if x.isdigit()]
    
            if qs.get("search_field"):
                params["search_field"] = qs["search_field"]
    
            if qs.get("order_by"):
                params["order_by"] = qs["order_by"]
    
            return urlencode(params, doseq=True)
        except Exception:
            return ""
    
    def _from_filters(f: dict) -> str:
        params: dict[str, object] = {}
    
        text_value = (f.get("text") or f.get("keyword") or f.get("position") or "").strip()
        if text_value:
            params["text"] = text_value
    
        area_id = _area_from_setup(f)
        if area_id is not None:
            params["area"] = int(area_id)
    
        if f.get("work_format"):
            params["work_format"] = [str(x) for x in f["work_format"]]
    
        employment = list(f.get("employment") or [])
        if employment:
            params["employment"] = employment
    
        roles = _get_professional_role_ids(None, f)  
        if roles:
            params["professional_role"] = [int(r) for r in roles]
    
        sfs = list(f.get("search_fields") or [])
        if sfs:
            params["search_field"] = sfs
    
        if f.get("order_by"):
            params["order_by"] = f["order_by"]
    
        return urlencode(params, doseq=True)

    if setup.get("search_by_url"):
        query_params = _from_url(setup.get("hh_url", ""))
    else:
        query_params = _from_filters(setup)
        
    query_params = normalize_hh_query(query_params)
    
    if not query_params:
        await q.message.edit_text(
            "❌ Не смог собрать параметры поиска.\n"
            "Если вставляете ссылку hh.ru — убедитесь, что в ней есть непустой text=…\n"
            "Либо настройте фильтры в боте."
        )
        return ConversationHandler.END
    try:
        qc = await quota_current(tg_id)
        used, limit, remaining, tariff, rt = _normalize_quota(qc)
        quota_norm = {
            "tariff": tariff,
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "reset_time_msk": rt,
    }
    except Exception:
        quota_norm = None
        remaining = 1
    
    text_notice, is_exhausted = _compose_finish_notice(quota_norm, left_fallback=remaining, sent_now=0)
    if is_exhausted:
        await q.message.edit_text(text_notice)
        return ConversationHandler.END
    # сохраняем авто-правило
    try:
        await asyncio.to_thread(
            auto_upsert,
            tg_id=tg_id,
            name=name,
            resume_id=resume_id,
            query_params=query_params,
            daily_limit=daily_limit,
            run_at=run_at,
            cover_letter=cover_letter,
            active=True,
        )
    except Exception as e:
        await q.message.edit_text(f"❌ Ошибка сохранения авто-правила: {e}")
        return ConversationHandler.END

    try:
        stats = await asyncio.to_thread(auto_plan)
        queued = int(stats.get("queued", 0)) if isinstance(stats, dict) else 0
    except Exception:
        queued = 0
    
    when = run_at or "в течение дня"
    res_name = resume.get("title") or resume_id
    text = (
        "✅ Автоотклики включены.\n\n"
        f"• Правило: {name}\n"
        f"• Резюме: {res_name}\n"
        f"• Время запуска: {when}\n"
        f"• Лимит в день: {daily_limit}\n"
        f"• Новых заявок сегодня: {queued}\n\n"
        "Можно вернуться в меню или изменить настройки."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔁 Изменить настройки",  callback_data="auto_change_settings")],
            [InlineKeyboardButton("⏸ Выключить автоотклики", callback_data="auto_stop")],
            [InlineKeyboardButton("🏠 Главное меню",         callback_data="main_menu")],
        ]
    )
    await q.message.edit_text(text, reply_markup=kb)
    return ConversationHandler.END


# ───────────────────────── Стоп/рестарт ─────────────────────────

async def on_auto_stop(update, context):
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_user.id
    try:
        res = await asyncio.to_thread(auto_set_active_sync, tg_id, False)
        if int(res.get("affected", 0)) == 0:
            await q.message.edit_text(
                "Автоотклики уже были выключены или правил нет.\n"
                "Нажмите «Включить автоотклики», чтобы создать правило."
            )
            return await show_auto_responses_main(update, context)
    except Exception as e:
        logger.exception("auto_set_active(false) failed: %s", e)
    return await show_auto_responses_main(update, context)

async def on_auto_start(update, context):
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_user.id
    try:
        res = await asyncio.to_thread(auto_set_active_sync, tg_id, True)
        if int(res.get("affected", 0)) == 0:
            await q.message.edit_text(
                "У вас пока нет правил автооткликов. Давайте создадим?",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🛠 Создать правило", callback_data="auto_setup")],
                     [InlineKeyboardButton("🔙 Главное меню",   callback_data="main_menu")]]
                )
            )
            return AUTO_RESPONSE_MAIN
    except Exception as e:
        logger.exception("auto_set_active(true) failed: %s", e)
    return await show_auto_responses_main(update, context)
    
# ───────────────────────── ConversationHandler ─────────────────────────

def get_auto_responses_conv_handler():
    from routers import menu, start

    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(show_auto_responses_main,   pattern=r"^(auto_responses|auto)$"),
            CallbackQueryHandler(start_auto_response_setup,  pattern=r"^auto_change_settings$"), 
            CommandHandler("auto", show_auto_responses_main),
        ],
        states={
            AUTO_RESPONSE_MAIN: [
                CallbackQueryHandler(start_auto_response_setup, pattern="^auto_setup$"),
                CallbackQueryHandler(on_auto_stop,             pattern="^auto_stop$"),       
                CallbackQueryHandler(on_auto_start,            pattern="^auto_activate$"),   
                CallbackQueryHandler(start_auto_response_setup, pattern="^auto_change_settings$"),
            ],
            AUTO_RESPONSE_RESUME: [
                CallbackQueryHandler(ask_auto_response_search_method, pattern="^auto_resume_"),
                CallbackQueryHandler(ask_auto_response_resume,        pattern="^auto_resume_reload$"),
                CallbackQueryHandler(show_auto_responses_main,        pattern="^auto_main$"),
            ],
            AUTO_RESPONSE_SEARCH_METHOD: [
                CallbackQueryHandler(start_auto_response_filters, pattern="^auto_configure_filters$"),
                CallbackQueryHandler(ask_auto_response_hh_url,    pattern="^auto_paste_link$"),
                CallbackQueryHandler(ask_auto_response_resume,    pattern="^auto_resume_back$"),
            ],

            ASK_REGION: [
                CallbackQueryHandler(handle_country_page, pattern=r"^page_country_nav_\d+$"),
                CallbackQueryHandler(handle_region_page,  pattern=r"^page_region_-?\d+_nav_\d+$"),
                CallbackQueryHandler(ask_region,          pattern=r"^country_\d+$"),
                CallbackQueryHandler(ask_work_format,     pattern=r"^region_"),
            ],

            ASK_WORK_FORMAT: [
                CallbackQueryHandler(ask_employment,             pattern=r"^work_next$"),
                CallbackQueryHandler(handle_work_format_choice,  pattern=r"^work_"),
            ],
            ASK_EMPLOYMENT: [
                CallbackQueryHandler(ask_profession,          pattern=r"^employment(_)?next$"),
                CallbackQueryHandler(handle_employment_choice, pattern=r"^employment_"),
            ],
            ASK_PROFESSION: [
                CallbackQueryHandler(handle_prof_page,   pattern=r"^prof_page_\d+$"),
                CallbackQueryHandler(handle_prof_toggle, pattern=r"^prof_toggle_\d+$"),
                CallbackQueryHandler(handle_prof_all,    pattern=r"^prof_all$"),
                CallbackQueryHandler(ask_keyword,        pattern=r"^profession_next$"),
            ],
            ASK_KEYWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_search_field),
            ],
            ASK_SEARCH_FIELD: [
                CallbackQueryHandler(ask_auto_response_cover_letter, pattern=r"^search_next$"),
                CallbackQueryHandler(handle_search_field_choice,     pattern=r"^search_"),
            ],

            AUTO_RESPONSE_HH_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_auto_response_hh_url),
                CallbackQueryHandler(ask_auto_response_search_method, pattern="^auto_search_method_back$"),
            ],
            AUTO_RESPONSE_COVER_LETTER: [
                CallbackQueryHandler(handle_auto_response_cover_letter_selection, pattern=r"^auto_no_letter$"),
                CallbackQueryHandler(handle_auto_response_cover_letter_selection, pattern=r"^auto_cl_select_\d+$"),
                CallbackQueryHandler(handle_auto_response_cover_letter_selection, pattern=r"^auto_cl_write_new$"),
                CallbackQueryHandler(start_auto_response_filters,                   pattern=r"^auto_cover_letter_back$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_auto_response_cover_letter_text),
            ],
            AUTO_RESPONSE_CONFIRMATION: [
                CallbackQueryHandler(start_auto_responses,      pattern=r"^auto_start$"),
                CallbackQueryHandler(start_auto_response_setup, pattern=r"^auto_change_settings$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start.start_over),
            CallbackQueryHandler(menu.back_to_main_menu, pattern="^main_menu$"),
        ],
        allow_reentry=True,
    )

