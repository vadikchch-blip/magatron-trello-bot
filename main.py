import os
import json
import requests
from flask import Flask, request
from datetime import datetime, timedelta
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ZAPIER_WEBHOOK_URL = os.environ["ZAPIER_WEBHOOK_URL"]

def ask_gpt_to_parse_task(text):
    system_prompt = (
        "Ты помощник, который получает сообщение от пользователя и должен распознать задачу. "
        "Ответ возвращай строго в JSON с полями: title (строка), description (строка), due_date (строка в ISO 8601 или null), labels (список строк)."
    )

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        temperature=0.2,
    )

    return response.choices[0].message.content

def parse_due_date(text):
    if "завтра" in text.lower():
        return (datetime.now() + timedelta(days=1)).isoformat()
    elif "сегодня" in text.lower():
        return datetime.now().isoformat()
    else:
        return None

def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        response = requests.post(url, json={"chat_id": chat_id, "text": text})
        print(f"📨 Ответ Telegram: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Ошибка отправки в Telegram: {e}")

@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    try:
        message = data["message"]["text"]
        chat_id = data["message"]["chat"]["id"]
        print(f"📩 Получено сообщение: {message} от chat_id={chat_id}")

        gpt_response = ask_gpt_to_parse_task(message)
        print(f"🤖 Ответ GPT: {gpt_response}")

        try:
            parsed = json.loads(gpt_response)
        except Exception as e:
            print(f"❌ Ошибка парсинга JSON: {e}")
            send_message(chat_id, f"❌ Ошибка парсинга ответа от GPT:\n{e}\n{gpt_response}")
            return "ok"

        if not parsed or not parsed.get("title"):
            print("⚠️ Не удалось распознать задачу")
            send_message(chat_id, "Не удалось распознать задачу. Попробуй переформулировать.")
            return "ok"

        if not parsed.get("due_date"):
            parsed["due_date"] = parse_due_date(message)

        print(f"📤 Отправка в Zapier: {parsed}")
        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)

        send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")

    except Exception as e:
        print(f"❌ Общая ошибка: {e}")
        send_message(chat_id, f"❌ Ошибка обработки сообщения: {e}")

    return "ok"

if __name__ == "__main__":
    app.run(port=8080)
