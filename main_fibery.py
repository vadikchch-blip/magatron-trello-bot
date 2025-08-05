import logging
import os
from flask import Flask, request
import requests
import openai
import json
from datetime import datetime
from dateparser import parse

app = Flask(__name__)

# --- Конфигурация ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIBERY_API_TOKEN = os.getenv("FIBERY_API_TOKEN")
FIBERY_API_URL = os.getenv("FIBERY_API_URL")  # Пример: https://magatron-lab.fibery.io

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.DEBUG)


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    logging.debug("[DEBUG] Входящее сообщение: %s", data)

    if "message" not in data or "text" not in data["message"]:
        return "ok"

    message = data["message"]
    text = message["text"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    try:
        now = datetime.utcnow().isoformat()

        system_prompt = (
            "Ты помощник по организации задач. Извлеки из текста:\n"
            "1. Название задачи (title)\n"
            "2. Описание (description) — если есть\n"
            "3. Срок (due_date) — если указан, в формате YYYY-MM-DDTHH:MM:SS\n"
            "4. Метки (labels) — список, если есть\n"
            "Ответ верни строго в JSON:\n"
            "{\n"
            '  "title": "...",\n'
            '  "description": "...",\n'
            '  "due_date": "...",\n'
            '  "labels": ["..."]\n'
            "}"
        )

        user_content = f"Сегодня: {now}\n\nЗадача: {text}"

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )

        gpt_reply = response["choices"][0]["message"]["content"]
        logging.debug("[DEBUG] GPT RESPONSE: %s", gpt_reply)

        task_data = json.loads(gpt_reply)

        # Парсим due_date
        due_date = task_data.get("due_date")
        if due_date:
            parsed_due = parse(due_date)
            due_date = parsed_due.strftime("%Y-%m-%dT%H:%M:%S") if parsed_due else None

        # Подготовка данных
        url = f"{FIBERY_API_URL}/api/entities"
        headers = {
            "Authorization": f"Token {FIBERY_API_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "fibery/type": "Magatron space/Task",
            "Name": task_data.get("title", ""),
            "Description": task_data.get("description", ""),
            "Due Date": due_date,
            "Labels": task_data.get("labels", []),
            "Telegram Chat ID": str(chat_id),
            "Telegram Message ID": str(message_id),
            "Created in Telegram": True,
        }

        logging.debug("[DEBUG] 📤 Отправка в Fibery:\n\n%s\n\n", json.dumps(payload, indent=4, ensure_ascii=False))

        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            send_telegram_message(chat_id, "✅ Задача добавлена в Fibery")
        else:
            logging.error("❌ Fibery не принял задачу: %s", response.text)
            send_telegram_message(chat_id, "❌ Ошибка при отправке в Fibery")

    except Exception as e:
        logging.error("Ошибка при обработке GPT-ответа: %s", e)
        send_telegram_message(chat_id, "❌ Ошибка при разборе задачи")

    return "ok"


def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)
