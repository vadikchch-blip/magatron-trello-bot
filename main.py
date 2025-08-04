import os
import json
import requests
from flask import Flask, request
from datetime import datetime
import openai
import dateparser
from dateutil import parser as dateutil_parser
import pytz

openai.api_key = os.environ["OPENAI_API_KEY"]
app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ZAPIER_WEBHOOK_URL = os.environ["ZAPIER_WEBHOOK_URL"]
moscow_tz = pytz.timezone("Europe/Moscow")

def ask_gpt_to_parse_task(text):
    now_str = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M:%S")
    system_prompt = (
        f"Сегодня: {now_str}. Ты помощник, который получает сообщение от пользователя и должен распознать задачу. "
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
        return parsed_date.isoformat()
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

        # Проверка даты от GPT и замена, если она в прошлом
        if parsed.get("due_date"):
            try:
                gpt_dt = dateutil_parser.parse(parsed["due_date"])
                now = datetime.now(moscow_tz)

                if gpt_dt.tzinfo is None:
                    gpt_dt = gpt_dt.replace(tzinfo=None)
                    now = now.replace(tzinfo=None)

                if gpt_dt < now:
                    send_message(chat_id, f"⚠️ GPT дал старую дату {gpt_dt}, заменяем")
                    parsed["due_date"] = parse_due_date(message)

            except Exception as e:
                send_message(chat_id, f"⚠️ Ошибка при проверке даты: {e}")
                parsed["due_date"] = parse_due_date(message)

        if not parsed.get("due_date"):
            parsed["due_date"] = parse_due_date(message)

        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)
        send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")

    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {e}")
    return "ok"
