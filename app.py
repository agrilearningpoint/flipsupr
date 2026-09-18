# app.py
from __future__ import annotations

import asyncio
import os
import threading
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

from bot.handlers import start_cmd, help_cmd, status_cmd, stop_cmd, submit_cmd, text_router
from core.logger import setup_logger

logger = setup_logger("formpilot.app")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "10000"))

if not TELEGRAM_BOT_TOKEN:
    logger.warning("TELEGRAM_BOT_TOKEN is empty — set it before production use")

app = Flask(__name__)

ptb_app: Optional[Application] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_thread: Optional[threading.Thread] = None


def _build_ptb() -> Application:
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN or "0:invalid")
        .updater(None)
        .build()
    )
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("submit", submit_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    return application


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


def init_telegram() -> None:
    global ptb_app, _loop, _loop_thread
    if ptb_app is not None:
        return

    ptb_app = _build_ptb()
    _loop = asyncio.new_event_loop()
    _loop_thread = threading.Thread(target=_run_loop, args=(_loop,), daemon=True, name="ptb-loop")
    _loop_thread.start()

    async def _startup() -> None:
        await ptb_app.initialize()
        await ptb_app.start()
        if WEBHOOK_URL:
            webhook_endpoint = f"{WEBHOOK_URL}/webhook"
            await ptb_app.bot.set_webhook(
                url=webhook_endpoint,
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES,
            )
            info = await ptb_app.bot.get_webhook_info()
            logger.info("Webhook set to %s | info=%s", webhook_endpoint, info.url)
        else:
            logger.warning("WEBHOOK_URL not set — Telegram webhook not registered")

    fut = asyncio.run_coroutine_threadsafe(_startup(), _loop)
    try:
        fut.result(timeout=60)
    except Exception as exc:
        logger.exception("Telegram init failed: %s", exc)


try:
    init_telegram()
except Exception as exc:
    logger.exception("init_telegram error: %s", exc)


@app.get("/")
def health() -> tuple:
    return jsonify(
        {
            "service": "FormPilot",
            "status": "ok",
            "usage": "Telegram bot — send /help to your bot",
            "note": "Only automate forms you own / are authorized to test.",
        }
    ), 200


@app.get("/healthz")
def healthz() -> tuple:
    return jsonify({"ok": True}), 200


@app.post("/webhook")
def telegram_webhook():
    if ptb_app is None or _loop is None:
        return jsonify({"error": "bot not ready"}), 503
    try:
        data = request.get_json(force=True, silent=False)
        update = Update.de_json(data, ptb_app.bot)
        fut = asyncio.run_coroutine_threadsafe(ptb_app.process_update(update), _loop)
        fut.result(timeout=120)
        return jsonify({"ok": True}), 200
    except Exception as exc:
        logger.exception("webhook error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
