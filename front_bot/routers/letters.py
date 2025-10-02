from __future__ import annotations

import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)

from utils import texts
from utils.states import COVER_LETTER_MENU, CL_ASK_TITLE, CL_SAVE_BODY, CL_VIEW
from utils.api_client import (
    cover_letters_list_sync,
    cover_letters_create_sync,  
    cover_letters_delete_sync,
    cover_letters_update_sync,
)

# ───────────────────────── helpers ─────────────────────────

async def _refresh_letters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Подтягиваем письма из бэка и кладём в context.user_data['cover_letters']."""
    tg_id = update.effective_user.id
    try:
        letters = await asyncio.to_thread(cover_letters_list_sync, tg_id)
    except Exception:
        letters = []
    context.user_data['cover_letters'] = letters
    return letters

def _letters_kb(letters: list[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("✏️ Новое сопроводительное письмо", callback_data="cl_new")]]
    for i, letter in enumerate(letters):
        title = letter.get('title') or '(без названия)'
        rows.append([InlineKeyboardButton(f"📄 {title}", callback_data=f"cl_view_{i}")])
    rows.append([InlineKeyboardButton(texts.BACK_TO_MAIN_MENU, callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)

# ───────────────────────── screens ─────────────────────────

async def show_cover_letters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Главный экран писем: список из БД + кнопка «Новое»."""
    letters = await _refresh_letters(update, context)
    text = texts.CL_MENU_HEADER
    reply_markup = _letters_kb(letters)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
    return COVER_LETTER_MENU


async def ask_new_cover_letter_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 1: спросить заголовок."""
    await _refresh_letters(update, context)
    if len(context.user_data.get('cover_letters', [])) >= 5:
        kb = [[InlineKeyboardButton("🔙 Вернуться в список писем", callback_data="cl_back_to_list")]]
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(texts.CL_LIMIT_REACHED, reply_markup=InlineKeyboardMarkup(kb))
        return COVER_LETTER_MENU

    await update.callback_query.answer()
    await update.callback_query.message.edit_text(texts.CL_ASK_TITLE)
    return CL_ASK_TITLE


async def ask_new_cover_letter_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 2: сохранить заголовок, спросить текст письма."""
    context.user_data['new_cl_title'] = (update.message.text or "").strip()[:200]
    await update.message.reply_text(texts.CL_ASK_BODY)
    return CL_SAVE_BODY


async def save_cover_letter_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохранить письмо в БД и вернуться к списку."""
    title = (context.user_data.pop('new_cl_title', '') or 'Письмо').strip()
    body = (update.message.text or '').strip()
    tg_id = update.effective_user.id

    try:
        await asyncio.to_thread(cover_letters_create_sync, tg_id, title, body)  
        # Обновим список из БД
        await _refresh_letters(update, context)
        kb = [[InlineKeyboardButton("🔙 Вернуться в список писем", callback_data="cl_back_to_list")]]
        await update.message.reply_text(texts.CL_SAVED, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось сохранить письмо: {e}")

    return COVER_LETTER_MENU


async def view_cover_letter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показ конкретного письма с возможностью удаления."""
    q = update.callback_query
    await q.answer()
    letters = context.user_data.get('cover_letters') or await _refresh_letters(update, context)

    try:
        idx = int(q.data.split('_')[-1])
        letter = letters[idx]
    except Exception:
        await q.message.edit_text(
            "Письмо не найдено.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="cl_back_to_list")]]),
        )
        return COVER_LETTER_MENU

    context.user_data['current_cl_index'] = idx
    text = texts.get_cl_view_text(letter.get('body', ''))
    kb = [
        [InlineKeyboardButton("🗑 Удалить сопроводительное письмо", callback_data="cl_delete")],
        [InlineKeyboardButton("🔙 Назад", callback_data="cl_back_to_list")],
    ]
    await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return CL_VIEW


async def delete_cover_letter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Удалить выбранное письмо в БД и вернуться к списку."""
    q = update.callback_query
    await q.answer()
    tg_id = update.effective_user.id
    letters = context.user_data.get('cover_letters', [])
    idx = context.user_data.pop('current_cl_index', None)

    if idx is None or idx >= len(letters):
        await q.message.edit_text(
            "Письмо не найдено.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="cl_back_to_list")]]),
        )
        return COVER_LETTER_MENU

    try:
        await asyncio.to_thread(cover_letters_delete_sync, tg_id, int(letters[idx]['id']))
        await _refresh_letters(update, context)
        await q.message.edit_text(texts.CL_DELETED, reply_markup=_letters_kb(context.user_data['cover_letters']))
    except Exception as e:
        await q.message.edit_text(
            f"❌ Не удалось удалить: {e}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="cl_back_to_list")]]),
        )

    return COVER_LETTER_MENU

# ───────────────────────── Conversation ─────────────────────────

def get_cover_letter_conv_handler():
    """Конверсейшн для писем."""
    from . import menu, start
    return ConversationHandler(
        entry_points=[
            CommandHandler("response_message", show_cover_letters),
            CallbackQueryHandler(show_cover_letters, pattern=r"^(cover_letters|letters|response_message)$"),
        ],
        states={
            COVER_LETTER_MENU: [
                CallbackQueryHandler(ask_new_cover_letter_title, pattern=r"^cl_new$"),
                CallbackQueryHandler(view_cover_letter, pattern=r"^cl_view_\d+$"),
                CallbackQueryHandler(show_cover_letters, pattern=r"^cl_back_to_list$"),
            ],
            CL_ASK_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_new_cover_letter_body),
            ],
            CL_SAVE_BODY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_cover_letter_body),
            ],
            CL_VIEW: [
                CallbackQueryHandler(delete_cover_letter, pattern=r"^cl_delete$"),
                CallbackQueryHandler(show_cover_letters, pattern=r"^cl_back_to_list$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start.start_over),
            CallbackQueryHandler(menu.back_to_main_menu, pattern=r"^main_menu$"),
        ],
        allow_reentry=True,
    )