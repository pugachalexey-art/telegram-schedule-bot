# bot_schedule_web.py (clean minimal)
# Р РµС”СЃС‚СЂСѓС” С‚С–Р»СЊРєРё РїРѕС‚СЂС–Р±РЅС– РєРѕРјР°РЅРґРё (+ callback) РґР»СЏ РІРµР±С…СѓРєР°/РїРѕР»Р»С–РЅРіСѓ.

import os
from bot_schedule_custom_v6d import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    on_cb, on_error, cmd_start, cmd_today, cmd_tomorrow, cmd_week,
    cmd_subject, cmd_next, cmd_help, cmd_weeknext
)

def main():
    token = os.environ["BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("weeknext", cmd_weeknext))
    app.add_handler(CommandHandler("subject", cmd_subject))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_error_handler(on_error)

    webhook_url = os.getenv("WEBHOOK_URL")
    listen_addr = os.getenv("LISTEN_ADDR", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram")

    if webhook_url:
        app.run_webhook(
            listen=listen_addr,
            port=port,
            url_path=webhook_path,
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=None,
        )
    else:
        app.run_polling(allowed_updates=None, drop_pending_updates=True)

if __name__ == "__main__":
    main()
