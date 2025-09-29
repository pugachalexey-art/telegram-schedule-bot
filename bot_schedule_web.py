# bot_schedule_web.py (robust import)
# Р†РјРїРѕСЂС‚СѓС”РјРѕ РјРѕРґСѓР»СЊ С†С–Р»РєРѕРј С– РґС–СЃС‚Р°С”РјРѕ С…РµРЅРґР»РµСЂРё С‡РµСЂРµР· getattr,
# С‰РѕР± СѓРЅРёРєРЅСѓС‚Рё ImportError, СЏРєС‰Рѕ СЏРєР°СЃСЊ С„СѓРЅРєС†С–СЏ РІС–РґСЃСѓС‚РЅСЏ.

import os
import bot_schedule_custom_v6d as core
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler

def _get(name, fallback=None):
    return getattr(core, name, fallback)

def main():
    token = os.environ["BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()

    # РљРѕРјР°РЅРґРё (СѓСЃС– С‡РµСЂРµР· getattr)
    app.add_handler(CommandHandler("start",   _get("cmd_start")))
    app.add_handler(CommandHandler("today",   _get("cmd_today")))
    app.add_handler(CommandHandler("tomorrow",_get("cmd_tomorrow")))
    app.add_handler(CommandHandler("week",    _get("cmd_week")))

    # /weeknext: СЏРєС‰Рѕ РЅРµРјР°С” cmd_weeknext, Р·Р°РіРѕСЂС‚Р°С”РјРѕ handle_week_next
    cmd_weeknext = _get("cmd_weeknext")
    if cmd_weeknext is None and _get("handle_week_next"):
        async def _cmd_weeknext(update, ctx):
            await core.handle_week_next(update, ctx)
        cmd_weeknext = _cmd_weeknext
    app.add_handler(CommandHandler("weeknext", cmd_weeknext))

    app.add_handler(CommandHandler("subject", _get("cmd_subject")))
    app.add_handler(CommandHandler("next",    _get("cmd_next")))
    app.add_handler(CommandHandler("help",    _get("cmd_help")))

    # Callback & errors
    app.add_handler(CallbackQueryHandler(_get("on_cb")))
    app.add_error_handler(_get("on_error"))

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
