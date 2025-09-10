# bot_schedule_web.py
# Entrypoint: якщо є WEBHOOK_URL — запускає webhook, інакше polling.
# ВАЖЛИВО: url_path має збігатися з кінцем WEBHOOK_URL.

import os
from bot_schedule_custom_v6d import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    on_cb, on_error, cmd_start, cmd_today, cmd_tomorrow, cmd_week,
    cmd_date, cmd_subject, cmd_next, cmd_help
)

def main():
    token = os.environ["8209753781:AAFSPwKn0wChJW_YB-QYbPcM6V_S4wtfJHY"]
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("date", cmd_date))
    app.add_handler(CommandHandler("subject", cmd_subject))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_error_handler(on_error)

    webhook_url = os.getenv("WEBHOOK_URL")  # напр. https://telegram-schedule-bot-81d0.onrender.com/telegram
    listen_addr = os.getenv("LISTEN_ADDR", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram")

    if webhook_url:
        # ВАЖЛИВО: Telegram звертається до WEBHOOK_URL, а сервер слухає url_path.
        # Вони мають збігатися по шляху (наприклад, обидва закінчуються на /telegram).
        app.run_webhook(
            listen=listen_addr,
            port=port,
            url_path=webhook_path,    # <- додано
            webhook_url=webhook_url,  # має містити той самий шлях
            drop_pending_updates=True,
            allowed_updates=None,
        )
    else:
        app.run_polling(allowed_updates=None, drop_pending_updates=True)

if __name__ == "__main__":
    main()
