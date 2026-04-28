# IG Event Bot

Creates Google calendar events from Instagram post URLs

## Setup

Secrets

```
gcloud secrets create telegram-bot-secret --data-file=creds/telegram_bot_secret.txt
...
```

Create Cloud run service:
- continuous deploy from github project
- set service account
- mount secrets as env vars

Create telegram webhook:

```
python telegram_webhook_setup.py
```

## Test webhook

```
curl -d '{"message": {"text": "https://www.instagram.com/p/DXrZluQjmCO/?utm_source=ig_web_button_native_share"}}' \
     -X POST \
     -H "Content-Type: application/json" \
     -H "X-Telegram-Bot-Api-Secret-Token: $(cat creds/telegram_bot_secret.txt)" \
     $(cat creds/service_url.txt)
```

## Local dev

start server

```
gcloud auth application-default login --impersonate-service-account=event-bot@ig-event-bot.iam.gserviceaccount.com

REPLICATE_API_TOKEN=$(cat creds/replicate_token.txt) \
CALENDAR_ID=$(cat creds/calendar_id.txt) \
TELEGRAM_TOKEN=$(cat creds/telegram_token.txt) \
TELEGRAM_CHAT=$(cat creds/telegram_chat.txt) \
TELEGRAM_BOT_SECRET=$(cat creds/telegram_bot_secret.txt) \
functions-framework --target=app
```

send request

good event
```
curl -d '{"message": {"text": "https://www.instagram.com/p/DXrZluQjmCO/?utm_source=ig_web_button_native_share"}}' \
     -X POST \
     -H "Content-Type: application/json" \
     -H "X-Telegram-Bot-Api-Secret-Token: $(cat creds/telegram_bot_secret.txt)" \
     localhost:8080
```

not event
```
curl -d '{"message": {"text": "https://www.instagram.com/p/DAGpeTNpCJt/"}}' \
     -X POST \
     -H "Content-Type: application/json" \
     -H "X-Telegram-Bot-Api-Secret-Token: $(cat creds/telegram_bot_secret.txt)" \
     localhost:8080
```

