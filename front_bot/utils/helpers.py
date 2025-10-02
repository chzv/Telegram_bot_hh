from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from typing import List, Dict, Any, Optional

def build_multi_choice_keyboard(options: dict, selection_key: str, prefix: str, context: ContextTypes.DEFAULT_TYPE):
    selected = context.user_data.get(selection_key, set())
    all_selected = selected == set(options.keys())
    
    keyboard = [[InlineKeyboardButton(f"{'🟢' if all_selected else '🔴'} Выбрать все", callback_data=f"{prefix}_all")]]
    for key, text in options.items():
        status = "🟢" if key in selected else "🔴"
        keyboard.append([InlineKeyboardButton(f"{status} {text}", callback_data=f"{prefix}_{key}")])
    keyboard.append([InlineKeyboardButton("Далее", callback_data=f"{prefix}_next")])
    return InlineKeyboardMarkup(keyboard)


async def handle_multi_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, options: dict, selection_key: str, prefix: str):
    query = update.callback_query
    choice = query.data.replace(f"{prefix}_", "")
    await query.answer()

    selected = context.user_data.get(selection_key, set())
    all_options = set(options.keys())

    if choice == 'all':
        if selected == all_options:
            selected.clear()
        else:
            selected.update(all_options)
    elif choice in selected:
        selected.remove(choice)
    else:
        selected.add(choice)
    
    context.user_data[selection_key] = selected
    reply_markup = build_multi_choice_keyboard(options, selection_key, prefix, context)
    await query.edit_message_reply_markup(reply_markup=reply_markup)

def build_paginated_keyboard(
    items: list,
    page: int,
    prefix: str,
    selection_key: str = None,
    context: ContextTypes.DEFAULT_TYPE = None,
    *,
    rows: int = 10,       # сколько строк на странице
    columns: int = 2,     # сколько колонок в строке
    add_select_all: bool = False,
) -> InlineKeyboardMarkup:
    if rows < 1: rows = 1
    if columns < 1: columns = 1

    keyboard: List[List[InlineKeyboardButton]] = []

    # Режим множественного выбора (если нужен)
    if add_select_all and context:
        selected = context.user_data.get(selection_key, set())
        all_ids = {str(it['id']) for it in items}
        status = "🟢" if selected.issuperset(all_ids) else "🔴"
        keyboard.append([InlineKeyboardButton(f"{status} Выбрать все", callback_data=f"page_{prefix}_select_all")])

    per_page = rows * columns
    total_pages = (len(items) + per_page - 1) // per_page
    page = max(0, page)
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    # Элементы: по 'columns' в строке
    selected_on_page = context.user_data.get(selection_key, set()) if (context and add_select_all) else set()
    row_buf: List[InlineKeyboardButton] = []
    for it in page_items:
        item_id = str(it['id'])
        text = it['name']
        if add_select_all:
            mark = "🟢" if item_id in selected_on_page else "🔴"
            text = f"{mark} {text}"
        row_buf.append(InlineKeyboardButton(text, callback_data=f"{prefix}_{item_id}"))
        if len(row_buf) == columns:
            keyboard.append(row_buf)
            row_buf = []
    if row_buf:
        keyboard.append(row_buf)

    # Навигация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад",  callback_data=f"page_{prefix}_nav_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"page_{prefix}_nav_{page+1}"))
    if nav:
        keyboard.append(nav)

    if add_select_all:
        keyboard.append([InlineKeyboardButton("Далее", callback_data=f"{prefix}_next")])

    return InlineKeyboardMarkup(keyboard)
