
# bot_schedule_web.py
# Entrypoint that runs our v6d bot in webhook mode if WEBHOOK_URL is set,
# otherwise falls back to polling (useful for local testing).
#
# Works with python-telegram-bot v21.x

import os
from bot_schedule_custom_v6d import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    on_cb, on_error, cmd_start, cmd_today, cmd_tomorrow, cmd_week,
    cmd_date, cmd_subject, cmd_next, cmd_help
)

def main():
    token = os.environ["BOT_TOKEN"]
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

    webhook_url = os.getenv("WEBHOOK_URL")  # e.g. https://your-service.onrender.com/telegram
    listen_addr = os.getenv("LISTEN_ADDR", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram")

    if webhook_url:
        # Ensure webhook_url ends with webhook_path
        if not webhook_url.endswith(webhook_path):
            if webhook_url.endswith('/') and webhook_path.startswith('/'):
                hook = webhook_url[:-1] + webhook_path
            else:
                hook = webhook_url + webhook_path
        else:
            hook = webhook_url
        # PTB will set webhook automatically with run_webhook when webhook_url is passed
        app.run_webhook(
            listen=listen_addr,
            port=port,
            webhook_url=hook,
            drop_pending_updates=True,
            allowed_updates=None,
        )
    else:
        app.run_polling(allowed_updates=None, drop_pending_updates=True)

if __name__ == "__main__":
    main()
