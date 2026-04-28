import requests

SERVICE_URL = open("creds/service_url.txt").read().strip()
TELEGRAM_TOKEN = open("creds/telegram_token.txt").read().strip()
TELEGRAM_BOT_SECRET = open("creds/telegram_bot_secret.txt").read().strip()


def set_webhook():
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
        json={
            "url": SERVICE_URL, 
            "allowed_updates": '["message"]',
            "secret_token": TELEGRAM_BOT_SECRET,
        }
    )


def main():
    set_webhook()
    

if __name__ == "__main__":
    main()
