import os
import json
import requests
from flask import Flask, request
from datetime import datetime
import openai
import dateparser
import pytz

openai.api_key = os.environ["OPENAI_API_KEY"]
app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ZAPIER_WEBHOOK_URL = os.environ["ZAPIER_WEBHOOK_URL"]

def ask_gpt_to_parse_task(text, current_date_iso):
    system_prompt = (
        f"Сегодня {current_date_iso}. "
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

def get_relative_base(telegram_timestamp):
    utc_dt = datetime.utcfromtimestamp(telegram_timestamp).replace(tzinfo=pytz.UTC)
    return utc_dt.astimezone(pytz.timezone("Europe/Moscow"))

def parse_due_date(text, relative_base):
    parsed_date = dateparser.parse(
        text,
        settings={
            "TIMEZONE": "Europe/Moscow",
            "TO_TIMEZONE": "Europe/Moscow",
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
            "RELATIVE_BASE": relative_base
        }
    )

    if not parsed_date:
        print("[DEBUG] ⚠️ Дата не распознана")
        return None

    now = relative_base.replace(tzinfo=None)
    if parsed_date < now and parsed_date.year < now.year:
        parsed_date = parsed_date.replace(year=now.year)
        if parsed_date < now:
            parsed_date = parsed_date.replace(year=now.year + 1)

    return parsed_date.isoformat()

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
        timestamp = data["message"]["date"]
        relative_base = get_relative_base(timestamp)
        current_date_iso = relative_base.date().isoformat()

        gpt_response = ask_gpt_to_parse_task(message, current_date_iso)
        print("[DEBUG] GPT RESPONSE:", gpt_response)

        try:
            parsed = json.loads(gpt_response)
        except Exception as e:
            send_message(chat_id, f"❌ Ошибка парсинга JSON: {e}\n{gpt_response}")
            return "ok"

        if not parsed.get("title"):
            send_message(chat_id, "⚠️ Не удалось распознать задачу")
            return "ok"

        # Заменяем due_date, если его нет или GPT дал старую дату
        due_date = parsed.get("due_date")
        if due_date:
            try:
                dt = datetime.fromisoformat(due_date)
                if dt.year < 2023:  # фильтрируем старые даты
                    print(f"[DEBUG] ⚠️ GPT дал старую дату: {due_date}, заменяем")
                    parsed["due_date"] = parse_due_date(message, relative_base)
            except Exception:
                parsed["due_date"] = parse_due_date(message, relative_base)
        else:
            parsed["due_date"] = parse_due_date(message, relative_base)

        print("[DEBUG] 📤 Отправка в Zapier:\n", json.dumps(parsed, indent=2, ensure_ascii=False))
        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)
        send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")

    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {e}")
    return "ok"
