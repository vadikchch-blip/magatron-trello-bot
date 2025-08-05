import os
import json
import openai
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import pytz

app = Flask(__name__)

# Настройки
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIBERY_API_TOKEN = os.getenv("FIBERY_API_TOKEN")
FIBERY_WORKSPACE = os.getenv("FIBERY_WORKSPACE")

openai.api_key = OPENAI_API_KEY

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print("[DEBUG] Входящее сообщение:", data)

    message = data.get('message', {})
    chat_id = message.get('chat', {}).get('id')
    message_id = message.get('message_id')
    text = message.get('text', '')

    if not text:
        return jsonify({"status": "no text"})

    system_prompt = f"""
Ты — помощник, который получает текст задачи из Telegram и должен вернуть JSON-объект с ключами:
- "title": короткое название задачи
- "description": подробное описание (если есть, иначе "")
- "due_date": ISO-дата (если есть срок), пример: "2025-08-07T15:00:00"
- "labels": список меток (может быть пустым)

Текущая дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}
    """

    user_prompt = f"Вот задача от пользователя: {text}"

    gpt_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.4,
    )

    content = gpt_response.choices[0].message.content
    print("[DEBUG] GPT RESPONSE:", content)

    try:
        json_start = content.find("{")
        json_data = content[json_start:]
        task_data = json.loads(json_data)
    except Exception as e:
        print("Ошибка при обработке GPT-ответа:", e)
        return jsonify({"error": "Ошибка GPT-ответа"})

    # Подготовка данных
    payload = {
        "fibery/type": "Magatron space/task",
        "Name": task_data.get("title", ""),
        "Description": task_data.get("description", ""),
        "Due Date": task_data.get("due_date"),
        "Labels": task_data.get("labels", []),
        "Telegram Chat ID": str(chat_id),
        "Telegram Message ID": str(message_id),
        "Created in Telegram": True
    }

    print("[DEBUG] 📤 Отправка в Fibery:\n", payload)

    fibery_url = f"https://{FIBERY_WORKSPACE}.fibery.io/api/entities/Magatron%20space/task"
    headers = {
        "Authorization": f"Token {FIBERY_API_TOKEN}",
        "Content-Type": "application/json"
    }

    response = requests.post(fibery_url, headers=headers, json=payload)

    if response.status_code != 200:
        print("❌ Fibery не принял задачу:", response.text)
    else:
        print("✅ Задача добавлена в Fibery")

    return jsonify({"status": "ok"})
