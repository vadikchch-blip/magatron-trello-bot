import os
import json
import logging
from flask import Flask, request
from dotenv import load_dotenv
import openai
import requests
from datetime import datetime
import dateparser

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIBERY_API_KEY = os.getenv("FIBERY_API_KEY")
FIBERY_BASE_URL = "https://magatron-lab.fibery.io/api/entities"

openai.api_key = OPENAI_API_KEY


def parse_task_with_gpt(message_text):
    try:
        system_prompt = (
            "Ты помощник по организации задач. Извлеки из текста:\n"
            "1. Название задачи (title)\n"
            "2. Описание (description) — если есть\n"
            "3. Срок (due_date) — если указан, в формате YYYY-MM-DDTHH:MM:SS\n"
            "4. Метки (labels) — список, если есть\n"
            "Ответ верни строго в JSON:\n"
            "{\n"
            "  \"title\": \"...\",\n"
            "  \"description\": \"...\",\n"
            "  \"due_date\": \"...\",\n"
            "  \"labels\": [\"...\"]\n"
            "}"
        )

        today = datetime.now().isoformat()
        full_prompt = f"Сегодня: {today}\n\nЗадача: {message_text}"

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": full_prompt}
            ]
        )

        gpt_reply = response.choices[0].message.content.strip()
        logging.debug(f"[DEBUG] GPT RESPONSE: {gpt_reply}")

        return json.loads(gpt_reply)

    except Exception as e:
        logging.error(f"Ошибка при обработке GPT-ответа: {e}")
        return None


def send_task_to_fibery(parsed_task, chat_id, message_id):
    headers = {
        "Authorization": f"Token {FIBERY_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "fibery/type": "Magatron space/Task",
        "Name": parsed_task.get("title"),
        "Description": parsed_task.get("description", ""),
        "Due Date": parsed_task.get("due_date"),
        "Labels": parsed_task.get("labels", []),
        "Telegram Chat ID": str(chat_id),
        "Telegram Message ID": str(message_id),
        "Created in Telegram": True
    }

    logging.debug(f"[DEBUG] 📤 Отправка в Fibery:\n\n {json.dumps(data, indent=2, ensure_ascii=False)}")

    response = requests.post(f"{FIBERY_BASE_URL}", headers=headers, data=json.dumps(data))

    if response.status_code == 200:
        return True
    else:
        logging.error(f"❌ Fibery не принял задачу: {response.text}")
        return False


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    logging.debug(f"[DEBUG] Входящее сообщение: {data}")

    message = data.get('message')
    if not message:
        return "ok"

    chat_id = message['chat']['id']
    message_id = message['message_id']
    text = message.get('text', '')

    if 'добавить задачу' in text.lower():
        clean_text = text.split(':', 1)[-1].strip()
        parsed = parse_task_with_gpt(clean_text)

        if parsed:
            success = send_task_to_fibery(parsed, chat_id, message_id)
            send_telegram_message(chat_id, "✅ Задача добавлена в Fibery." if success else "❌ Ошибка при отправке в Fibery.")
        else:
            send_telegram_message(chat_id, "❌ Ошибка при разборе задачи")
    else:
        send_telegram_message(chat_id, "⚠️ Отправь задачу в формате: Добавить задачу: <текст>")

    return "ok"


def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)
