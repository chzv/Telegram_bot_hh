# front_bot/main.py
import asyncio
import logging
import os
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)

import config
import httpx
from telegram.ext import ContextTypes
from utils.api_client import users_seen

from telegram.constants import ParseMode
from telegram.ext import Defaults

from routers import menu, start, letters, responses, auto_responses, stats
from routers.responses import (
    start_responses_entry,
    on_camp_stop,
    on_camp_start,
)

BACKEND_URL = os.getenv("BACKEND_PASSTHROUGH_URL", "http://backend:8000/bot/process")
ENABLE_BACKEND_PASSTHROUGH = os.getenv("ENABLE_BACKEND_PASSTHROUGH", "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


async def call_backend(chat_id: int, text: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(BACKEND_URL, json={"chat_id": chat_id, "text": text})
        r.raise_for_status()
        data = r.json()
        
        return (data.get("reply") if isinstance(data, dict) else None) or ""


async def passthrough_to_backend(update: Update, _context) -> None:
    
    if not ENABLE_BACKEND_PASSTHROUGH:
        return

    if not update.message:
        return
    chat_id = update.effective_chat.id
    text = update.message.text or ""
    if not text.strip():
        return
    try:
        reply = await call_backend(chat_id, text)
    except Exception as e:
        logging.exception("Backend passthrough failed: %s", e)
        reply = "⚠️ Временная ошибка соединения с сервером. Попробуйте позже."
    if reply:
        await update.message.reply_text(reply)

async def _touch_seen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    
    await users_seen(user.id, getattr(user, "username", None))

async def main() -> None:
    """Run the bot."""
    # --- Persistence ---
    persistence = PicklePersistence(filepath="demo_bot_persistence")

    # --- Application Setup ---
    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .persistence(persistence)
        .defaults(Defaults(parse_mode=ParseMode.HTML)) 
        .build()
    )

    # --- Command setup ---
    commands = [
        BotCommand("start", "Начать/перезапустить"),
        BotCommand("menu", "Главное меню 🧭"),
        BotCommand("responses", "Запустить отклики 🚀"),
        BotCommand("subscription", "Подписка 💳"),
        BotCommand("referral", "Реферальная программа 👥"),
        BotCommand("support", "Поддержка 🛟"),
        BotCommand("settings", "Настройки ⚙️"),
    ]
    await application.bot.set_my_commands(commands)
    responses_conv = responses.get_responses_conv_handler()
    cover_letter_conv = letters.get_cover_letter_conv_handler()
    auto_responses_conv = auto_responses.get_auto_responses_conv_handler()

    application.add_handler(CommandHandler("start", start.start))
    application.add_handler(CommandHandler("menu", menu.main_menu))
    application.add_handler(CommandHandler("subscription", menu.show_subscription))
    application.add_handler(CommandHandler("referral", menu.show_referral_program))
    application.add_handler(CommandHandler("payment", menu.show_subscription))
    application.add_handler(CommandHandler("support", menu.show_support))
    application.add_handler(CommandHandler("settings", menu.show_settings))
    
    application.add_handler(responses_conv)
    application.add_handler(cover_letter_conv)
    application.add_handler(auto_responses_conv)


    application.add_handler(CallbackQueryHandler(start.link_account, pattern=r"^link_account$"))
    application.add_handler(CallbackQueryHandler(menu.show_subscription, pattern=r"^subscription$"))
    application.add_handler(CallbackQueryHandler(menu.handle_payment, pattern=r"^pay_(week|month)$"))
    application.add_handler(CallbackQueryHandler(menu.show_support, pattern=r"^support$"))
    application.add_handler(CallbackQueryHandler(stats.stats_entry, pattern=r"^stats$"))
    application.add_handler(CallbackQueryHandler(stats.stats_show,  pattern=r"^stat_r_\d+$"))
    application.add_handler(CallbackQueryHandler(menu.show_settings, pattern=r"^settings$"))
    application.add_handler(CallbackQueryHandler(start.is_safe,      pattern=r"^is_safe$"))
    application.add_handler(CallbackQueryHandler(start.start_again,  pattern=r"^start_again$"))
    application.add_handler(CallbackQueryHandler(menu.unlink_confirm, pattern=r"^unlink_confirm$"))
    application.add_handler(CallbackQueryHandler(menu.unlink_yes,     pattern=r"^unlink_yes$"))

    application.add_handler(CallbackQueryHandler(start_responses_entry, pattern="^start_responses$"))
    application.add_handler(CallbackQueryHandler(on_camp_stop,        pattern="^camp_stop:\\d+$"))
    application.add_handler(CallbackQueryHandler(on_camp_start,       pattern="^camp_start:\\d+$"))

    application.add_handler(MessageHandler(filters.ALL, _touch_seen), group=-100)
    application.add_handler(CallbackQueryHandler(_touch_seen), group=-100)

    if hasattr(menu, "show_referral_program"):
        application.add_handler(CallbackQueryHandler(menu.show_referral_program, pattern=r"^referral$"))
    else:
        logging.warning("routers.menu.show_referral_program is missing — referral button disabled")

    application.add_handler(CallbackQueryHandler(menu.main_menu, pattern=r"^main_menu$"))

    if ENABLE_BACKEND_PASSTHROUGH:
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, passthrough_to_backend),
            group=10,
        )

    # --- Start polling ---
    async with application:
        await application.initialize()
        logging.info("Bot started successfully")
        await application.start()
        await application.updater.start_polling()

        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logging.info("Received stop signal")
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()

logging.basicConfig(
    level=(os.getenv("LOG_LEVEL","INFO").upper()),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("utils.api_client").setLevel(logging.INFO)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
