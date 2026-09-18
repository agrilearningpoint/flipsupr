# bot/handlers.py
from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.llm_parser import parse_command
from bot.submitter import job_manager
from core.logger import setup_logger

logger = setup_logger("formpilot.handlers")

WELCOME = """*FormPilot* — user-owned form automation lab

I help you practice:
• Telegram webhooks
• Selenium sessions
• Proxy rotation
• Docker / Render deploy
• LLM command parsing

⚠️ Use *only* forms **you own** or are explicitly authorized to test.

Commands:
/submit `<url>` `<count>`
/status
/stop
/help

Or natural language:
`submit https://docs.google.com/forms/d/e/XXXX/viewform 5`
"""

HELP = """*Usage*

• `/submit https://YOUR_FORM_URL 3`
• `/status` — current/last job
• `/stop` — request stop
• Free text is parsed by LLM + fallback heuristics

Env knobs: `FORM_SELECTOR`, `SUBMIT_SELECTOR`, `PROXY_GATEWAY`, `SUCCESS_TEXT`

Max count per job: 100
"""


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, HELP)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, job_manager.get_status())


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, job_manager.request_stop())


async def submit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else 0
    args = context.args or []
    if len(args) < 1:
        await _reply(update, "Usage: `/submit <url> <count>`")
        return
    url = args[0]
    count = 1
    if len(args) >= 2:
        try:
            count = int(args[1])
        except ValueError:
            await _reply(update, "Count must be an integer.")
            return

    bot = context.application.bot

    def progress_sync(msg: str) -> None:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True,
                    ),
                    loop,
                )
            else:
                loop.run_until_complete(
                    bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True,
                    )
                )
        except Exception as exc:
            logger.warning("progress notify failed: %s", exc)

    msg = job_manager.start_job(chat_id, url, count, progress_cb=progress_sync)
    await _reply(update, msg)


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Natural language handler."""
    if not update.effective_message or not update.effective_message.text:
        return
    text = update.effective_message.text.strip()
    parsed = parse_command(text)
    intent = parsed["intent"]
    logger.info("text_router parsed=%s", parsed)

    if intent == "start":
        await start_cmd(update, context)
        return
    if intent == "help":
        await help_cmd(update, context)
        return
    if intent == "status":
        await status_cmd(update, context)
        return
    if intent == "stop":
        await stop_cmd(update, context)
        return
    if intent == "submit":
        context.args = [parsed["url"], str(parsed["count"] or 1)]
        await submit_cmd(update, context)
        return

    await _reply(
        update,
        "Sorry, I didn't understand.\nTry `/help` or:\n`submit https://YOUR_FORM_URL 3`",
    )
