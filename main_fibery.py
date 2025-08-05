import os
import json
from flask import Flask, request
import openai
import requests
from datetime import datetime
import dateparser

app = Flask(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")
FIBERY_API_URL = "https://magatron-lab.fibery.io/api/entities"
FIBERY_API_TOKEN = os.getenv("FIBERY_API_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

HEADERS = {
    "Authorization": f"Token {FIBERY_API_TOKEN}",
    "Content-Type": "application/json"
}

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("[DEBUG] Входящее сообщение:", data)

    message = data.get("message", {}).get("text", "")
    chat_id = data.get("message", {}).get("chat", {}).get("id")
    message_id = data.get("message", {}).get("message_id")

    if not message or not chat_id:
        return "no message", 200

    # Отправляем в GPT
    prompt = f"""
Ты — парсер задач. На вход ты получаешь текст задачи, например: "Добавить задачу: Купить молоко завтра в 14:00".

Ты возвращаешь JSON с полями:
- title: краткое название задачи
- description: необязательное описание (пока оставляй пустым)
- due_date: ISO-формат даты и времени дедлайна (пример: 2025-08-06T14:00:00)
- labels: список меток (пока оставляй пустым)

СЕЙЧАС: {datetime.now().isoformat()}
ПОЛЬЗОВАТЕЛЬ: {message}
JSON:
"""

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Ты помощник, который структурирует задачи."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        gpt_reply = response.choices[0].message["content"]
        print("[DEBUG] GPT RESPONSE:", gpt_reply)
        parsed = json.loads(gpt_reply)

        title = parsed["title"]
        description = parsed.get("description", "")
        due_date = parsed.get("due_date", None)
        labels = parsed.get("labels", [])

        payload = {
            "fibery/type": "Magatron space/Task",  # <-- ВАЖНО: Заглавная T!
            "Name": title,
            "Description": description,
            "Due Date": due_date,
            "Labels": labels,
            "Telegram Chat ID": str(chat_id),
            "Telegram Message ID": str(message_id),
            "Created in Telegram": True
        }

        print("[DEBUG] 📤 Отправка в Fibery:\n\n", payload)

        fibery_response = requests.post(
            f"{FIBERY_API_URL}/Magatron space/Task",
            headers=HEADERS,
            data=json.dumps(payload)
        )

        if fibery_response.status_code == 200:
            print("✅ Успешно отправлено в Fibery")
        else:
            print("❌ Fibery не принял задачу:", fibery_response.text)

    except Exception as e:
        print("Ошибка при обработке GPT-ответа:", e)

    return "ok", 200
