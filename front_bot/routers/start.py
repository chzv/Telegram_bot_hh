# front_bot/routers/start.py
from __future__ import annotations

import os
import httpx
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, ContextTypes, Application, CallbackQueryHandler
from utils.api_client import referrals_track
from telegram.constants import ParseMode

from urllib.parse import parse_qs

def _parse_utm_from_payload(payload: str) -> tuple[str|None, str|None, str|None]:
    """
    Ожидаем строку вида:
      "utm_source=tect&utm_medium=user&utm_campaign=111"
    Допускаем короткие ключи: s/m/c.
    Возвращаем (source, medium, campaign) или (None, None, None)
    """
    if not payload:
        return None, None, None
    qs = parse_qs(payload, keep_blank_values=True)
    def get(k, alt=None):
        v = qs.get(k) or (qs.get(alt) if alt else None)
        return (v[0] if v else None) or None
    return (
        get("utm_source", "s"),
        get("utm_medium", "m"),
        get("utm_campaign", "c"),
    )

# -------- конфиг бэка --------
def _resolve_backend() -> str:
    url = (
        os.getenv("BACKEND_URL")
        or os.getenv("BACKEND_BASE_URL")
        or "http://backend:8000"
    )
    return url.rstrip("/")

BACKEND_URL = _resolve_backend()
API_BASE = os.getenv("API_BASE_URL", f"{BACKEND_URL}/api/v1").rstrip("/")

# -------- маленький HTTP-клиент --------
async def _api(method: str, path: str, **kw):
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout) as cli:
        r = await cli.request(method, f"{API_BASE}{path}", **kw)
        r.raise_for_status()
        if r.headers.get("content-type","").startswith("application/json"):
            return r.json()
        return r.text

async def _get_auth_url(tg_id: int) -> str | None:
    try:
        data = await _api("GET", "/hh/login", params={"tg_id": tg_id})
        return data.get("auth_url")
    except Exception:
        return None

async def _get_link_status(tg_id: int) -> dict:
    try:
        return await _api("GET", "/hh/link-status", params={"tg_id": tg_id})
    except Exception:
        return {"linked": False}

async def _users_seen(u) -> None:
    payload = {
        "tg_id": u.id,
        "username": u.username,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "is_premium": getattr(u, "is_premium", False),
        "lang": getattr(u, "language_code", None),
        "ref": None,
    }
    try:
        await _api("POST", "/users/seen", json=payload)
    except Exception:
        pass
    
async def _users_set_utm(tg_id: int, s: str|None, m: str|None, c: str|None) -> None:
    """Идемпотентная запись UTM (на бэке через COALESCE, чтобы не перетирать первичную метку)."""
    if not any([s, m, c]):
        return
    try:
        await _api("POST", "/users/utm", json={"tg_id": tg_id, "utm_source": s, "utm_medium": m, "utm_campaign": c})
    except Exception:
        pass
# -------- локальное хранилище для «одного напоминания» --------
_REMINDER_SCHEDULED: set[int] = set()
# -------- клавиатуры --------
WELCOME = (
    "Для того, чтобы начать, необходимо привязать твой аккаунт на hh.ru.\n\n"
)

def _kb_start(auth_url: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if auth_url:
        rows.append([InlineKeyboardButton("🔗 Привязать аккаунт на НН", url=auth_url)])
    else:
        rows.append([InlineKeyboardButton("🔗 Привязать аккаунт на НН", callback_data="link_account")])
    rows.append([InlineKeyboardButton("⭐ Это безопасно?", callback_data="is_safe")])
    return InlineKeyboardMarkup(rows)

def _kb_cases_nudge() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("▶ Запустить отклики", callback_data="start_responses")]]
    )    
# -------- вспомогательное: отложенное разовое напоминание --------
async def _remind_once_after_30m(application: Application, chat_id: int, tg_id: int):
    # Спим 30 минут, затем проверяем статус и, если нужно, шлём РОВНО один раз.
    try:
        await asyncio.sleep(30 * 60)
        status = await _get_link_status(tg_id)
        if status.get("linked"):
            return  # был привязан — ничего не шлём

        auth_url = await _get_auth_url(tg_id)
        await application.bot.send_message(
            chat_id=chat_id,
            text=(
                "⏳ Вы так и не начали пользоваться ботом…\n\n"
                "Посмотрите на кейсы пользователей:\n"
                "<a href='https://'>+4 проекта на 50–120k за 3 недели</a>\n"
                "<a href='https://'>Работа за 2 недели вместо 2 месяцев</a>\n"
                "<a href='https://'>Оффер на 72% выше прошлого места</a>\n\n"
                "Чтобы начать, привяжите аккаунт на HH."
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Привязать аккаунт на НН",
                                      url=auth_url) if auth_url else
                 InlineKeyboardButton("🔗 Привязать аккаунт на НН", callback_data="link_account")]
            ]),
            disable_web_page_preview=True,
            parse_mode="HTML",
        )
    finally:
        _REMINDER_SCHEDULED.discard(tg_id)

# -------- хендлеры --------
async def in_development(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Этот раздел ещё в разработке. Пока доступна привязка hh.ru через /start."
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    await _users_seen(user)

    args = context.args or []
    payload = (args[0] if args and isinstance(args[0], str) else "").strip()
    if not payload and update.message and isinstance(update.message.text, str):
        txt = update.message.text.strip()
        payload = txt.partition(" ")[2] if " " in txt else ""

    # 1.1) Реф-код 
    if payload.startswith("ref_") and len(payload) > 4:
        code = payload[4:].strip()
        try:
            await referrals_track(chat_id, code)
        except Exception:
            pass

    # 1.2) UTM-метки
    utm_s, utm_m, utm_c = _parse_utm_from_payload(payload)
    await _users_set_utm(user.id, utm_s, utm_m, utm_c)

    # 2) Проверяем привязку HH
    status = await _get_link_status(user.id)
    linked = bool(status.get("linked"))

    if not linked:
        auth_url = await _get_auth_url(user.id)
        await update.effective_message.reply_text(
            WELCOME,
            reply_markup=_kb_start(auth_url),
            disable_web_page_preview=True,
        )

        # Планируем ОДНО напоминание через 30 минут только при самом первом старте
        if user.id not in _REMINDER_SCHEDULED:
            _REMINDER_SCHEDULED.add(user.id)
            # запускаем фоновую задачу в event-loop бота
            context.application.create_task(
                _remind_once_after_30m(context.application, chat_id, user.id)
            )
        return  # важно: не показываем меню «уже привязан»

    await update.effective_message.reply_text(
        "✅ Аккаунт привязан. Готовы откликаться на вакансии!",
        disable_web_page_preview=True,
    )

    # вставка НОВОГО сообщения между «Аккаунт привязан…» и меню
    cases_text = (
        "🙌 С ботом поиск работы будет идти быстрее и легче. Истории пользователей:\n"
        "<a href=''>👉 +4 проекта на 50–120k за 3 недели</a>\n"
        "<a href=''>👉 Работа за 2 недели вместо 2 месяцев</a>\n"
        "<a href=''>👉 Оффер на 72% выше прошлого места</a>"
    )
    await update.effective_message.reply_text(
        cases_text,
        reply_markup=_kb_cases_nudge(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    from routers import menu
    await menu.main_menu(update, context)
    
async def start_over(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start(update, context)

async def link_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    query = update.callback_query
    if query:
        await query.answer()

    auth_url = await _get_auth_url(tg_id)
    text = "Откройте ссылку ниже, авторизуйтесь в hh.ru и вернитесь в чат:"
    if not auth_url:
        msg = text + "\n\nНе удалось получить ссылку авторизации. Попробуйте позже."
        if query and query.message:
            try:
                await query.message.edit_text(msg)
                return
            except Exception:
                pass
        await update.effective_chat.send_message(msg)
        return

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Войти через hh.ru", url=auth_url)]])
    if query and query.message:
        try:
            await query.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await update.effective_chat.send_message(text, reply_markup=kb)

async def is_safe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()

    txt = (
        "🔒 Подключать hh-аккаунт к нашему боту абсолютно безопасно.\n"
        "Мы не храним ваши данные и не имеем доступа к паролям — всё идёт через защищённое API.\n\n"
        "✅ Бот работает через официальное API hh.ru с разрешения площадки — переживать не о чем.\n"
        "Вы можете отключить свой аккаунт в настройках в любой момент.\n\n"
        "🙌 Мы имитируем человеческое поведение при откликах: десятки тысяч откликов без блокировок.\n\n"
        "Все вопросы по работе бота вы можете уточнить в поддержке @.\n\n"
        "🚀 Подключите аккаунт hh и протестируйте бота прямо сейчас!"
    )

    auth_url = await _get_auth_url(update.effective_user.id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔗 Привязать аккаунт на НН",
            url=auth_url
        ) if auth_url else InlineKeyboardButton(
            "🔗 Привязать аккаунт на НН",
            callback_data="link_account"
        )]
    ])

    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(
                txt, reply_markup=kb, disable_web_page_preview=True
            )
            return
        except Exception:
            pass

    await update.effective_chat.send_message(
        txt, reply_markup=kb, disable_web_page_preview=True
    )

async def start_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    auth_url = await _get_auth_url(update.effective_user.id)
    await update.effective_message.reply_text(
        WELCOME,
        reply_markup=_kb_start(auth_url),
        disable_web_page_preview=True,
    )

def setup(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(is_safe,      pattern=r"^is_safe$"))
    app.add_handler(CallbackQueryHandler(start_again,  pattern=r"^start_again$"))
    app.add_handler(CallbackQueryHandler(link_account, pattern=r"^link_account$"))

def handler() -> CommandHandler:
    return CommandHandler("start", start)