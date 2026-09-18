# FormPilot

Telegram bot for automating **user-owned or explicitly authorized test forms**. Use it only on resources you control or have permission to test.

## Features

- Telegram webhooks with `python-telegram-bot` v21
- Selenium/Chromium sessions with fresh profiles
- Optional authenticated proxy gateway
- OpenRouter command parsing with offline fallback
- Docker + Gunicorn + Render deployment files
- `/start`, `/submit`, `/status`, `/stop`, and `/help`

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env. Never commit .env or paste secrets into source files.
python app.py
```

For webhook testing, expose the local server with a trusted HTTPS tunnel and set `WEBHOOK_URL`. Polling is not included in this webhook-oriented deployment.

## Environment variables

At minimum for Telegram deployment:

```text
TELEGRAM_BOT_TOKEN=<new token stored in Render secrets>
WEBHOOK_URL=https://<your-service>.onrender.com
```

OpenRouter and proxy settings are optional. Keep proxy use limited to authorized test traffic.

## Telegram commands

```text
/submit https://YOUR_OWN_FORM_URL 3
/status
/stop
/help
```

## Render deployment

1. Create a private GitHub repository and push this project.
2. In Render, create a Blueprint from the repository or deploy the Dockerfile.
3. Set `TELEGRAM_BOT_TOKEN`, `WEBHOOK_URL`, and optional secrets directly in Render Environment Variables.
4. Confirm `GET /healthz` returns `{"ok": true}`.
5. Send `/start` to the bot.

Never commit `.env`, tokens, cloud credentials, or proxy credentials. Rotate any credential that has been pasted into chat, a public repository, or logs.

## Design limitations

- In-memory state and one active worker are intentional for the demo.
- Restarting the service loses active jobs and history.
- Generic selectors may not support complex multi-page forms.
- Chromium and `undetected-chromedriver` versions may need pinning on deployment.
- Use small counts for authorized test runs.

## License

MIT — use responsibly and only with authorization.
