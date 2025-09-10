# bot_schedule_web.py
# Entrypoint: якщо є WEBHOOK_URL — запускає webhook, інакше polling.
# ВАЖЛИВО: url_path (шлях, який слухає сервер) має відповідати кінцю WEBHOOK_URL.
# Для PTB 21.x url_path треба передавати БЕЗ початкового "/".

import os
from bot_schedule_custom_v6d import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    on_cb, on_error, cmd_start, cmd_today, cmd_tomorrow, cmd_week,
    cmd_date, cmd_subject, cmd_next, cmd_help
)

def main():
    token = os.environ["BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    # Handlers
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

    webhook_url = os.getenv("WEBHOOK_URL")       # напр. https://<app>.onrender.com/telegram
    listen_addr = os.getenv("LISTEN_ADDR", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram")

    if webhook_url:
        # PTB очікує url_path без "/"
        url_path_clean = webhook_path[1:] if webhook_path.startswith("/") else webhook_path

        app.run_webhook(
            listen=listen_addr,
            port=port,
            url_path=url_path_clean,   # ← "telegram"
            webhook_url=webhook_url,   # ← повний URL, наприклад https://.../telegram
            drop_pending_updates=True,
            allowed_updates=None,
        )
    else:
        app.run_polling(allowed_updates=None, drop_pending_updates=True)

if __name__ == "__main__":
    main()
