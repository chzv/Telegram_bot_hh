from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler
from utils import texts
from utils.api_client import subscription_current
from utils.api_client import referrals_me, referrals_generate, register_user
import logging
import httpx
import os
from datetime import datetime, timezone

BACKEND_BASE = os.getenv("BACKEND_BASE_URL", "").rstrip("/")

BACKEND_URL = os.getenv("BACKEND_URL") or os.getenv("BACKEND_BASE_URL") or "http://backend:8000"
API_BASE = f"{BACKEND_URL.rstrip('/')}/api/v1"

async def _api(method: str, path: str, **kw):
    async with httpx.AsyncClient(timeout=20.0) as cli:
        r = await cli.request(method, f"{API_BASE}{path}", **kw)
        r.raise_for_status()
        return r.json() if r.headers.get("content-type","").startswith("application/json") else r.text

async def _get_link_status(tg_id: int) -> dict:
    try:
        return await _api("GET", "/hh/link-status", params={"tg_id": tg_id})
    except Exception:
        return {"linked": False}

async def _get_auth_url(tg_id: int) -> str | None:
    try:
        data = await _api("GET", "/hh/login", params={"tg_id": tg_id})
        return data.get("auth_url")
    except Exception:
        return None

async def _unlink_hh(tg_id: int) -> bool:
    try:
        data = await _api("POST", "/hh/unlink", params={"tg_id": tg_id})
        return bool(data.get("unlinked"))
    except Exception:
        return False
        
def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("▶️ Запустить отклики",       callback_data="start_responses")],
        [InlineKeyboardButton("📝 Сопроводительные письма", callback_data="cover_letters")],
        [InlineKeyboardButton("💳 Подписка",                callback_data="subscription")],
        [InlineKeyboardButton("👥 Реферальная программа",   callback_data="referral")],
        [InlineKeyboardButton("🛟 Поддержка",               callback_data="support")],
        [InlineKeyboardButton("⚙️ Настройки",               callback_data="settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


def _fmt_expires(expires_at):
    if not expires_at:
        return "—"
    try:
        s = str(expires_at)
        if s.isdigit():
            dt = datetime.fromtimestamp(int(s), tz=timezone.utc)
        else:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(expires_at)

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int = None) -> None:
    """Displays the main menu."""
    reply_markup = get_main_menu_keyboard()
    text = texts.MAIN_MENU_TITLE
    
    if update and update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    elif update:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    elif chat_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await main_menu(update, context)
    return ConversationHandler.END

async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает реальное состояние подписки с бэка."""
    tg_id = update.effective_chat.id

    link_status = await _get_link_status(tg_id)
    if not link_status.get("linked"):
        auth_url = await _get_auth_url(tg_id)
        text = (
            "Чтобы управлять подпиской, сначала привяжите аккаунт hh.ru.\n\n"
            "Нажмите кнопку ниже и завершите авторизацию."
        )
        kb_rows = []
        if auth_url:
            kb_rows.append([InlineKeyboardButton("Привязать аккаунт на НН", url=auth_url)])
        kb_rows.append([InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")])
        await (update.callback_query.message.edit_text if update.callback_query else update.message.reply_text)(
            text, reply_markup=InlineKeyboardMarkup(kb_rows)
        )
        if update.callback_query:
            await update.callback_query.answer()
        return
    try:
        data = await subscription_current(tg_id)
    except Exception:
        data = None

    if not data:
        text = "⚠️ Не удалось получить состояние подписки. Попробуйте позже."
        keyboard = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
    else:
        plan = (data.get("plan") or "—")
        raw_status = (data.get("status") or "inactive").lower()
        status_map = {
            "active": "активна ✅",
            "paid": "активна ✅",
            "inactive": "не активна ⛔",
            "expired": "просрочена ⛔",
        }
        status = status_map.get(raw_status, raw_status)
        expires_at = _fmt_expires(data.get("expires_at"))
        days_left = data.get("days_left")

        def _plural_days(n: int) -> str:
            n = abs(int(n))
            if n % 10 == 1 and n % 100 != 11:
                return f"{n} день"
            if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
                return f"{n} дня"
            return f"{n} дней"

        days_left_str = (_plural_days(days_left) if isinstance(days_left, int) else "—")

        if not data or raw_status in ("inactive", "expired"):
            text = (
                "📄 Подписка\n\n"
                "В платных тарифах доступно:\n"
                "• до 200 откликов в сутки\n"
                "• умные паузы и фильтры по вакансиям\n"
                "• приоритетная поддержка\n\n"
                "Статус: ⛔ Не оплачено\n\n"
                "Оплата разовая, без автосписаний."
            )
            keyboard = [
                [InlineKeyboardButton("Неделя — 590₽", callback_data="pay_week")],
                [InlineKeyboardButton("Месяц — 1390₽", callback_data="pay_month")],
                [InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")],
            ]
        else:
            days_left_str = _plural_days(days_left) if isinstance(days_left, int) else "—"
            text = (
                "📄 Подписка\n\n"
                f"Статус: ✅ Оплачено\n"
                f"Тариф: {plan}\n"
                f"Действует до: {expires_at}\n"
                f"Осталось: {days_left_str}\n\n"
                "Продлить подписку:"
            )
            keyboard = [
                [InlineKeyboardButton("Неделя — 590₽", callback_data="pay_week")],
                [InlineKeyboardButton("Месяц — 1390₽", callback_data="pay_month")],
                [InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")],
            ]
        reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

from urllib.parse import urlparse

def _is_http_url(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if any(ch in s for ch in ("<html", "<!doctype", "</html", "\n")):
        return False
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False
async def handle_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    tg_id = update.effective_user.id
    plan = "week" if (query and query.data == "pay_week") else "month"

    pay_url = f"{BACKEND_BASE}/pay?plan={plan}&tg_id={tg_id}"
    text = (
        "✅ <b>Счёт сформирован</b>.\n"
        "Нажмите «Оплатить», чтобы перейти на защищённую страницу оплаты.\n\n"
        "После оплаты вернитесь сюда и нажмите «🔄 Обновить статус»."
    )
    kb = [
        [InlineKeyboardButton("Оплатить", url=pay_url)],
        [InlineKeyboardButton("🔄 Обновить статус", callback_data="subscription")],
        [InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")],
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def show_referral_program(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает информацию по партнёрской программе."""
    tg_id = update.effective_chat.id
    username = getattr(update.effective_user, "username", None)

    try:
        try:
            await register_user(tg_id, username)
        except Exception:
            pass  

        try:
            await referrals_generate(tg_id)  
        except Exception:
            pass

        data = await referrals_me(tg_id)

    except httpx.HTTPStatusError as e:
        logging.exception("referrals_me HTTP error: %s", e)
        data = None
    except Exception:
        logging.exception("referral_program failed")
        data = None

    if not data:
        text = "⚠️ Не удалось получить данные программы. Попробуйте позже."
        keyboard = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.edit_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
        return

    ref_link = (data.get("link") or "").strip()

    balance_rub = float(data.get("balance", 0.0))
    lvl1 = int(data.get("level1", 0))
    lvl2 = int(data.get("level2", 0))
    lvl3 = int(data.get("level3", 0))
    
    text = (
        "👥 Партнёрская программа\n\n"
        "Делитесь ссылкой и получайте вознаграждение за оплаченные подписки ваших приглашённых.\n\n"
        f"🔗 Ваша ссылка: <a href=\"{ref_link}\">{ref_link}</a>\n\n"
        "Уровни начислений:\n"
        "1-й уровень — 20% от всех платежей\n"
        "2-й уровень — 10% от всех платежей\n"
        "3-й уровень — 5% от всех платежей\n\n"
        "📊 Твоя структура:\n\n"
        f"👤 1 уровень: {lvl1}\n"
        f"👥 2 уровень: {lvl2}\n"
        f"👥 3 уровень: {lvl3}\n\n"
        f"Баланс: {balance_rub:.2f} руб.\n\n"
        "📘 Подробнее: <a href=\"http:/\">как работает программа</a>"
    )
    
    keyboard = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True
        )

async def handle_payment_stub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the stub for payment processing (demo mode)."""
    query = update.callback_query
    await query.answer()
    
    context.user_data["subscription_status"] = "active"
    
    text = texts.SUBSCRIPTION_SUCCESS
    keyboard = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.message.edit_text(text, reply_markup=reply_markup)

async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays support information (demo mode)."""
    support_text = texts.SUPPORT_INFO
    
    keyboard = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(support_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(support_text, reply_markup=reply_markup)
        
async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
 
    txt = (
        "Вы можете в любой момент отвязать свой HH-аккаунт от бота.\n\n"
        "Важно: если у вас активна подписка, она действует только на тот аккаунт hh.ru, "
        "к которому была привязана при оплате."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Отвязать аккаунт на НН", callback_data="unlink_confirm")],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
    ])

    q = update.callback_query
    if q:
        await q.answer()
        await q.message.edit_text(txt, reply_markup=kb)
    else:
        await update.effective_message.reply_text(txt, reply_markup=kb)


async def unlink_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Экран подтверждения отвязки. Если уже не привязан — сразу показываем подсказку привязки.
    """
    q = update.callback_query
    if q:
        await q.answer()

    tg_id = update.effective_user.id
    linked = False
    try:
        info = await _get_link_status(tg_id)
        linked = bool(info and info.get("linked"))
    except Exception as e:
        logging.warning("link-status failed in unlink_confirm: %s", e)

    if not linked:
        auth_url = await _get_auth_url(tg_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Привязать аккаунт на НН",
              url=auth_url) if auth_url else
             InlineKeyboardButton("🔗 Привязать аккаунт на НН", callback_data="link_account")],
            [InlineKeyboardButton("🔙 Назад", callback_data="settings")],
        ])
        txt = ("Сейчас аккаунт hh.ru не привязан — отвязывать нечего.\n\n"
               "Привяжите аккаунт, чтобы управлять соединением.")
        if q:
            await q.message.edit_text(txt, reply_markup=kb, disable_web_page_preview=True)
        else:
            await update.effective_message.reply_text(txt, reply_markup=kb, disable_web_page_preview=True)
        return

    # Если привязан — показываем обычное подтверждение
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Да, отвязать аккаунт", callback_data="unlink_yes")],
        [InlineKeyboardButton("🔙 Назад", callback_data="settings")],
    ])
    txt = "Вы уверены, что хотите отвязать аккаунт hh.ru?"
    if q:
        await q.message.edit_text(txt, reply_markup=kb)
    else:
        await update.effective_message.reply_text(txt, reply_markup=kb)

async def unlink_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Выполняем отвязку только если статус «linked=True» на текущий момент.
    """
    q = update.callback_query
    if q:
        await q.answer()

    tg_id = update.effective_user.id
    linked = False
    try:
        info = await _get_link_status(tg_id)
        linked = bool(info and info.get("linked"))
    except Exception as e:
        logging.warning("link-status failed in unlink_yes: %s", e)

    if not linked:
        auth_url = await _get_auth_url(tg_id)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Привязать аккаунт на НН",
              url=auth_url) if auth_url else
             InlineKeyboardButton("🔗 Привязать аккаунт на НН", callback_data="link_account")],
            [InlineKeyboardButton("🔙 Назад", callback_data="settings")],
        ])
        txt = ("Аккаунт уже не привязан — отвязывать нечего.\n\n"
               "При необходимости можете привязать его снова.")
        await q.message.edit_text(txt, reply_markup=kb, disable_web_page_preview=True)
        return

    ok = await _unlink_hh(tg_id)

    if ok:
        txt = (
            "✅ Аккаунт успешно отвязан.\n\n"
            "При необходимости вы можете привязать его снова."
        )
    else:
        txt = (
            "⚠️ Не удалось отвязать аккаунт. Попробуйте ещё раз чуть позже.\n\n"
            "Вы можете в любой момент повторить попытку."
        )

    auth_url = await _get_auth_url(tg_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Привязать аккаунт на НН",
          url=auth_url) if auth_url else
         InlineKeyboardButton("🔗 Привязать аккаунт на НН", callback_data="link_account")]
    ])

    await q.message.edit_text(txt, reply_markup=kb, disable_web_page_preview=True)
    