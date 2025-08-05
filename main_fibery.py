import os
import json
import requests
from flask import Flask, request
from datetime import datetime
import openai
import dateparser

openai.api_key = os.environ["OPENAI_API_KEY"]
app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
FIBERY_API_TOKEN = os.environ["FIBERY_API_TOKEN"]
FIBERY_WORKSPACE = "magatron-lab"
FIBERY_SPACE = "Magatron"
FIBERY_ENTITY = "Задача"

def ask_gpt_to_parse_task(text):
    now = datetime.now().isoformat()
    system_prompt = (
        f"Ты помощник, который получает сообщение от пользователя в Telegram и должен распознать задачу. "
        f"Текущая дата: {now}. Ответ возвращай строго в JSON с полями: title (строка), description (строка), "
        f"due_date (строка в ISO 8601 или null), labels (список строк)."
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
    now = datetime.now()
    parsed_date = dateparser.parse(
        text,
        settings={
            "TIMEZONE": "Europe/Moscow",
            "TO_TIMEZONE": "Europe/Moscow",
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": False,
            "RELATIVE_BASE": now
        }
    )
    if not parsed_date:
        return None

    if parsed_date.year < now.year:
        parsed_date = parsed_date.replace(year=now.year)
        if parsed_date < now:
            parsed_date = parsed_date.replace(year=now.year + 1)

    return parsed_date.isoformat()

def send_to_fibery(parsed):
    headers = {
        "Authorization": f"Token {FIBERY_API_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "entity": f"{FIBERY_SPACE}/{FIBERY_ENTITY}",
        "values": {
            "Name": parsed["title"],
            "Описание": parsed.get("description", ""),
            "Срок": parsed.get("due_date", None),
            "Метки": parsed.get("labels", []),
            "Создано в Telegram": True
        }
    }

    url = f"https://{FIBERY_WORKSPACE}.fibery.io/api/data"
    response = requests.post(url, headers=headers, data=json.dumps(payload))

    return response.status_code, response.text

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

        if not parsed.get("due_date"):
            parsed["due_date"] = parse_due_date(message)

        status_code, response_text = send_to_fibery(parsed)
        if status_code == 200:
            send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")
        else:
            send_message(chat_id, f"❌ Fibery ошибка: {status_code}\n{response_text}")

    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {e}")
    return "ok"
