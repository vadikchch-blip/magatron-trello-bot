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

def parse_due_date(text):
    text = text.lower()
    now = datetime.now()

    if "завтра" in text:
        return (now + timedelta(days=1)).isoformat()
    elif "сегодня" in text:
        return now.isoformat()
    elif "через неделю" in text:
        return (now + timedelta(weeks=1)).isoformat()
    elif "в понедельник" in text:
        # пример: ближайший понедельник
        days_ahead = (0 - now.weekday() + 7) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (now + timedelta(days=days_ahead)).isoformat()

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

        # ✅ Обновлённая проверка due_date
        if not parsed.get("due_date") or parsed["due_date"] in ["", "null", None]:
            parsed["due_date"] = parse_due_date(message)

        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)
        send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")

    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {e}")
    return "ok"

if __name__ == "__main__":
    app.run(port=8080)
