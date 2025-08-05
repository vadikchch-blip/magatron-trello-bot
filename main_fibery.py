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
FIBERY_WORKSPACE = os.environ["FIBERY_WORKSPACE"]  # Пример: magatron-lab

def ask_gpt_to_parse_task(text):
    now = datetime.now().isoformat()
    system_prompt = (
        f"Сегодняшняя дата: {now}.\n"
        "Ты помощник, который получает задачу от пользователя. "
        "Ответ верни строго в JSON с полями: title (строка), description (строка), "
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
    now = datetime.now()
    parsed_date = dateparser.parse(
        text,
        settings={
            "TIMEZONE": "Europe/Moscow",
            "TO_TIMEZONE": "Europe/Moscow",
            "RETURN_AS_TIMEZONE_AWARE": False,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now
        }
    )
    return parsed_date.isoformat() if parsed_date else None

def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        print(f"[ERROR] Telegram error: {e}")

@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
        message = data["message"]["text"]
        chat_id = data["message"]["chat"]["id"]
        message_id = data["message"]["message_id"]

        gpt_response = ask_gpt_to_parse_task(message)
        print("[DEBUG] GPT RESPONSE:", gpt_response)

        try:
            parsed = json.loads(gpt_response)
        except Exception as e:
            send_message(chat_id, f"❌ Ошибка парсинга JSON: {e}\n\n{gpt_response}")
            return "ok"

        if not parsed.get("title"):
            send_message(chat_id, "⚠️ Не удалось распознать задачу")
            return "ok"

        if not parsed.get("due_date"):
            parsed["due_date"] = parse_due_date(message)

        entity = {
            "type": "Magatron_space/Задача",
            "fields": {
                "Name": parsed["title"],
                "Description": parsed.get("description", ""),
                "Метки": parsed.get("labels", []),
                "Создано в Telegram": True,
                "Tr Telegram Chat ID": str(chat_id),
                "Tr Telegram Message ID": str(message_id),
                "Срок": parsed["due_date"]
            }
        }

        print("[DEBUG] 📤 Отправка в Fibery:\n", json.dumps(entity, indent=2, ensure_ascii=False))

        response = requests.post(
            f"https://{FIBERY_WORKSPACE}.fibery.io/api/entities/Magatron_space/Задача",
            headers={"Authorization": f"Token {FIBERY_API_TOKEN}"},
            json=[entity]
        )

        if response.status_code == 200:
            send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")
        else:
            send_message(chat_id, f"❌ Fibery не принял задачу: {response.text}")

    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {e}")
    return "ok"
