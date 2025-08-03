import os
import json
import requests
from flask import Flask, request
from datetime import datetime, timedelta
import openai

openai.api_key = os.environ["OPENAI_API_KEY"]
app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ZAPIER_WEBHOOK_URL = os.environ["ZAPIER_WEBHOOK_URL"]

def ask_gpt_to_parse_task(text):
    system_prompt = (
        "Ты помощник, который получает сообщение от пользователя и должен распознать задачу. "
        "Ответ возвращай строго в JSON с полями: title (строка), description (строка), due_date (строка в ISO 8601 или null), labels (список строк)."
    )
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        temperature=0.2,
    )
    return response["choices"][0]["message"]["content"]

def parse_due_date(text, gpt_date):
    now = datetime.now()
    
    # 1. Если GPT прислал дату — парсим её
    if gpt_date:
        try:
            parsed = datetime.fromisoformat(gpt_date)
            if parsed >= now:
                return parsed.isoformat()
        except Exception:
            pass  # если формат неправильный — идём дальше

    # 2. Если дата не пришла или слишком старая — подменяем на "сегодня" или "завтра"
    text_lower = text.lower()
    if "завтра" in text_lower:
        return (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
    if "сегодня" in text_lower:
        return now.replace(hour=12, minute=0, second=0, microsecond=0).isoformat()

    # 3. Если ничего не подходит
    return None

def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        print(f"Ошибка Telegram: {e}")

@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
        message = data["message"]["text"]
        chat_id = data["message"]["chat"]["id"]

        gpt_response = ask_gpt_to_parse_task(message)

        try:
            parsed = json.loads(gpt_response)
        except Exception as e:
            send_message(chat_id, f"❌ Ошибка парсинга JSON: {e}\n{gpt_response}")
            return "ok"

        if not parsed.get("title"):
            send_message(chat_id, "⚠️ Не удалось распознать задачу")
            return "ok"

        parsed["due_date"] = parse_due_date(message, parsed.get("due_date"))

        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)
        send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")

    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {e}")
    return "ok"

if __name__ == "__main__":
    app.run(port=8080)
