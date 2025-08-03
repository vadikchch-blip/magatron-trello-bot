import os
import openai
import requests
from flask import Flask, request
from openai import OpenAI

app = Flask(__name__)

# Инициализация клиента OpenAI
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ZAPIER_WEBHOOK_URL = os.environ["ZAPIER_WEBHOOK_URL"]

def parse_task(text):
    print("🔍 Отправка текста в OpenAI:", text)
    response = client.chat.completions.create(
        model="gpt-4o",  # Можно заменить через переменную среды
        messages=[
            {"role": "system", "content": "Ты парсер задач. Принимаешь текст, возвращаешь JSON с ключами: title, description, due_date, labels."},
            {"role": "user", "content": text}
        ]
    )
    content = response.choices[0].message.content
    print("✅ Ответ от OpenAI:", content)
    return content

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("📩 Получен запрос от Telegram:", data)

    if "message" in data and "text" in data["message"]:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"]["text"]

        if text.lower().startswith("добавь задачу:"):
            try:
                parsed = parse_task(text[14:].strip())
                requests.post(ZAPIER_WEBHOOK_URL, json={"raw": text, "parsed": parsed})
                send_telegram_message(chat_id, "✅ Задача отправлена в Trello")
            except Exception as e:
                print("❌ Ошибка при разборе:", e)
                send_telegram_message(chat_id, f"⚠️ Ошибка: {e}")
        else:
            send_telegram_message(chat_id, "👋 Привет! Напиши 'Добавь задачу: ...'")

    return {"ok": True}

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

@app.route('/')
def root():
    return 'Magatron is alive.'
