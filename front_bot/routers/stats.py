# front_bot/routers/stats.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from utils import texts
from utils.api_client import stats_resumes, stats_resume


async def stats_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список резюме для выбора статистики."""
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_chat.id

    try:
        data = await stats_resumes(tg_id)
    except Exception:
        kb = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
        await q.message.edit_text("⚠️ Не удалось получить список резюме. Попробуйте позже.", reply_markup=InlineKeyboardMarkup(kb))
        return

    items = data.get("items", [])
    if not items:
        kb = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
        await q.message.edit_text("Пока нет резюме для статистики.", reply_markup=InlineKeyboardMarkup(kb))
        return

    kb = [[InlineKeyboardButton(i["name"], callback_data=f"stat_r_{i['id']}")] for i in items]
    kb.append([InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")])
    await q.message.edit_text("Выберите резюме для отображения статистики:", reply_markup=InlineKeyboardMarkup(kb))


async def stats_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику по выбранному резюме."""
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_chat.id

    try:
        rid = q.data.split("_")[-1]
        rid = int(rid) if rid.isdigit() else rid
    except Exception:
        kb = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
        await q.message.edit_text("Неверный идентификатор резюме.", reply_markup=InlineKeyboardMarkup(kb))
        return

    try:
        s = await stats_resume(tg_id, rid)
    except Exception:
        kb = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
        await q.message.edit_text("⚠️ Не удалось получить статистику. Попробуйте позже.", reply_markup=InlineKeyboardMarkup(kb))
        return

    text_out = (
        f"📊 Статистика для резюме \"{s.get('name','—')}\":\n"
        f"• Всего откликов через бота: {int(s.get('total_responses',0))}\n"
        f"• Сегодня: {int(s.get('responses_today',0))}\n"
        f"• Приглашений: {int(s.get('invites',0))}\n"
        f"• Отказов: {int(s.get('declines',0))}\n\n"
        f"Конверсия из отклика в приглашение — {s.get('conversion',0)}%"
    )

    kb = [[InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")]]
    await q.message.edit_text(text_out, reply_markup=InlineKeyboardMarkup(kb))
