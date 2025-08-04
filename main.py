import os
import json
import requests
from flask import Flask, request
from datetime import datetime
import openai
import dateparser
import pytz

# Настройка
openai.api_key = os.environ["OPENAI_API_KEY"]
app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ZAPIER_WEBHOOK_URL = os.environ["ZAPIER_WEBHOOK_URL"]
moscow_tz = pytz.timezone("Europe/Moscow")

# Запрос к GPT
def ask_gpt_to_parse_task(text):
    system_prompt = (
        "Ты помощник, который получает сообщение от пользователя и должен распознать задачу. "
        "Ответ возвращай строго в JSON с полями: title (строка), description (строка), "
        "due_date (строка в ISO 8601 или null), labels (список строк)."
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

# Парсинг даты
def parse_due_date(text):
    now = datetime.now(moscow_tz)
    parsed_date = dateparser.parse(
        text,
        settings={
            "TIMEZONE": "Europe/Moscow",
            "TO_TIMEZONE": "Europe/Moscow",
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "RELATIVE_BASE": now
        }
    )
    if parsed_date:
        print(f"[DEBUG] 📅 Распознано как: {parsed_date.isoformat()}")

        # Если GPT дал старую дату без указания года
        if parsed_date < now:
            parsed_date = parsed_date.replace(year=now.year + 1)
            print(f"[DEBUG] ⚠️ Дата была в прошлом, заменили на: {parsed_date.isoformat()}")
        return parsed_date.isoformat()
    print("[DEBUG] ⚠️ Дата не распознана")
    return None

# Отправка сообщения в Telegram
def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        print(f"[ERROR] Telegram: {e}")

# Корень
@app.route("/", methods=["GET"])
def index():
    return "OK"

# Вебхук
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

        # Перепарсить дату, если GPT дал неадекватную
        if not parsed.get("due_date") or "2022" in str(parsed["due_date"]):
            print(f"[DEBUG] ⚠️ GPT дал странную дату: {parsed.get('due_date')}, заменяем")
            parsed["due_date"] = parse_due_date(message)

        print(f"[DEBUG] 📤 Отправка в Zapier:\n{json.dumps(parsed, indent=2, ensure_ascii=False)}")
        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)
        send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")

    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {e}")
    return "ok"
