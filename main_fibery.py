import os
import json
import requests
import datetime
from flask import Flask, request
from dateparser import parse

app = Flask(__name__)

# ENV
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIBERY_TOKEN = os.getenv("FIBERY_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {FIBERY_TOKEN}",
    "Content-Type": "application/json"
}

# TG
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

# GPT
def parse_task(text):
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    today_iso = datetime.datetime.now().isoformat()

    system_prompt = f"""
Ты — парсер задач. На вход получаешь текст задачи. Нужно выделить:
- title: короткое название задачи
- description: необязательное описание (если есть)
- due_date: срок (если указан). Преобразуй фразы вроде "завтра", "в пятницу", "через 3 дня в 15:00" в ISO 8601 (UTC+3).
- labels: массив меток (если указаны)

Сегодня: {today_iso}

Ответ в JSON строго в этом формате:
{{
  "title": "...",
  "description": "...",
  "due_date": "...",
  "labels": ["..."]
}}
"""

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        temperature=0,
    )

    return response.choices[0].message.content

# API
@app.route('/webhook', methods=["POST"])
def webhook():
    data = request.get_json()
    print("[DEBUG] Входящее сообщение:", data)

    if "message" not in data or "text" not in data["message"]:
        return "ok"

    message = data["message"]
    text = message["text"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]

    try:
        gpt_response = parse_task(text)
        print("[DEBUG] GPT RESPONSE:", gpt_response)
        parsed = json.loads(gpt_response)

        title = parsed.get("title", "Без названия")
        description = parsed.get("description", "")
        due_date = parsed.get("due_date", None)
        labels = parsed.get("labels", [])

        # Сборка payload для Fibery
        payload = [
            {
                "command": "fibery.entity/create",
                "args": {
                    "type": "Magatron space/Task",
                    "entity": {
                        "Name": title,
                        "Description": description,
                        "Due Date": due_date,
                        "Labels": labels,
                        "Telegram Chat ID": str(chat_id),
                        "Telegram Message ID": str(message_id),
                        "Created in Telegram": True
                    }
                }
            }
        ]

        print("[DEBUG] 📤 Отправка в Fibery (через /api/commands):\n\n", payload)

        fibery_response = requests.post(
            "https://magatron-lab.fibery.io/api/commands",
            headers=HEADERS,
            data=json.dumps(payload)
        )

        print("[DEBUG] Ответ Fibery:", fibery_response.text)

        if fibery_response.status_code == 200:
            send_message(chat_id, "✅ Задача добавлена в Fibery")
        else:
            send_message(chat_id, "❌ Не удалось добавить задачу в Fibery")

    except Exception as e:
        print("Ошибка при обработке GPT-ответа:", e)
        send_message(chat_id, "❌ Ошибка при разборе задачи")

    return "ok"

# Flask app
if __name__ == "__main__":
    app.run()
