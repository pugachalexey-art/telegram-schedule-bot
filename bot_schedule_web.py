# bot_schedule_web.py
# Вебхуки для Render. Працює навіть якщо у core немає notify_loop (не впаде).

import os
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
import bot_schedule_custom_v6d as core  # імпортуємо модуль цілком

def main():
    token = os.environ["BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    # хендлери з core
    app.add_handler(CommandHandler("start", core.cmd_start))
    app.add_handler(CommandHandler("today", core.cmd_today))
    app.add_handler(CommandHandler("tomorrow", core.cmd_tomorrow))
    app.add_handler(CommandHandler("week", core.cmd_week))
    app.add_handler(CommandHandler("date", core.cmd_date))
    app.add_handler(CommandHandler("subject", core.cmd_subject))
    app.add_handler(CommandHandler("next", core.cmd_next))
    app.add_handler(CommandHandler("help", core.cmd_help))
    app.add_handler(CallbackQueryHandler(core.on_cb))
    app.add_error_handler(core.on_error)

    # якщо в core є notify_loop — запускаємо повторювану джобу
    if hasattr(core, "notify_loop"):
        app.job_queue.run_repeating(core.notify_loop, interval=60, first=10)

    webhook_url = os.getenv("WEBHOOK_URL")  # напр. https://<app>.onrender.com/telegram
    listen_addr = os.getenv("LISTEN_ADDR", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram")

    if webhook_url:
        url_path_clean = webhook_path[1:] if webhook_path.startswith("/") else webhook_path
        app.run_webhook(
            listen=listen_addr,
            port=port,
            url_path=url_path_clean,
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=None,
        )
    else:
        app.run_polling(allowed_updates=None, drop_pending_updates=True)

if __name__ == "__main__":
    main()