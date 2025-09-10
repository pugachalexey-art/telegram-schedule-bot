webhook_url = os.getenv("WEBHOOK_URL")  # напр. https://...onrender.com/telegram
listen_addr = os.getenv("LISTEN_ADDR", "0.0.0.0")
port = int(os.getenv("PORT", "8080"))
webhook_path = os.getenv("WEBHOOK_PATH", "/telegram")

if webhook_url:
    # PTB очікує url_path БЕЗ початкового слеша.
    url_path_clean = webhook_path[1:] if webhook_path.startswith("/") else webhook_path

    app.run_webhook(
        listen=listen_addr,
        port=port,
        url_path=url_path_clean,   # ← без "/"
        webhook_url=webhook_url,   #https://telegram-schedule-bot-81d0.onrender.com/telegram
        drop_pending_updates=True,
        allowed_updates=None,
    )
else:
    app.run_polling(allowed_updates=None, drop_pending_updates=True)
