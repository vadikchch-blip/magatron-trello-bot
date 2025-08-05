import os
import json
import logging
from flask import Flask, request
import requests
import openai
from datetime import datetime
from dateparser import parse

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIBERY_API_TOKEN = os.getenv("FIBERY_API_TOKEN")
FIBERY_API_URL = os.getenv("FIBERY_API_URL")  # https://magatron-lab.fibery.io

openai.api_key = OPENAI_API_KEY

FIBERY_GRAPHQL_URL = f"{FIBERY_API_URL}/api/graphql"

@app.route("/webhook", methods=["POST"])
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
        due_date = task_data.get("due_date")
        if due_date:
            parsed = parse(due_date)
            due_date = parsed.strftime("%Y-%m-%dT%H:%M:%S") if parsed else None

        graphql_query = {
            "query": """
                mutation CreateTask($data: [Magatron_space_TaskInput!]) {
                  create_Magatron_space_Task_batch(entityBatch: $data) {
                    id
                  }
                }
            """,
            "variables": {
                "data": [
                    {
                        "Name": task_data.get("title", ""),
                        "Description": task_data.get("description", ""),
                        "Due Date": due_date,
                        "Labels": task_data.get("labels", []),
                        "Telegram Chat ID": str(chat_id),
                        "Telegram Message ID": str(message_id),
                        "Created in Telegram": True,
                    }
                ]
            }
        }

        headers = {
            "Authorization": f"Token {FIBERY_API_TOKEN}",
            "Content-Type": "application/json"
        }

        logging.debug("[DEBUG] 📤 GraphQL запрос в Fibery:\n%s", json.dumps(graphql_query, indent=2, ensure_ascii=False))

        response = requests.post(FIBERY_GRAPHQL_URL, headers=headers, json=graphql_query)
        logging.debug("[DEBUG] 📥 Ответ Fibery: %s", response.text)

        if response.status_code == 200 and "errors" not in response.json():
            send_telegram_message(chat_id, "✅ Задача добавлена в Fibery")
        else:
            send_telegram_message(chat_id, "❌ Ошибка при отправке в Fibery")

    except Exception as e:
        logging.exception("Ошибка при обработке GPT-ответа")
        send_telegram_message(chat_id, "❌ Ошибка при разборе задачи")

    return "ok"

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    requests.post(url, json=payload)
