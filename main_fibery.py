import os
import openai
import requests
from flask import Flask, request, jsonify
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

FIBERY_API_TOKEN = os.getenv("FIBERY_API_TOKEN")
FIBERY_WORKSPACE = os.getenv("FIBERY_WORKSPACE")

FIBERY_HEADERS = {
    "Authorization": f"Token {FIBERY_API_TOKEN}",
    "Content-Type": "application/json"
}

FIBERY_URL = f"https://{FIBERY_WORKSPACE}.fibery.io/api/entities/Task"

SYSTEM_PROMPT = """
Ты помощник, который превращает произвольные сообщения в структуру задачи. 
Твоя цель — выделить из текста:

1. title — короткое название задачи
2. description — если есть пояснение, добавь сюда
3. due_date — крайний срок задачи (в формате ISO 8601)
4. labels — список меток (по контексту)

Если дата указана как "завтра", "в пятницу", "через 2 дня", используй текущую дату как опорную: {{CURRENT_DATETIME}}.
"""

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.json

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    text = message.get("text", "")

    print(f"[DEBUG] Входящее сообщение: {text}")

    now = datetime.now().isoformat()
    prompt = SYSTEM_PROMPT.replace("{{CURRENT_DATETIME}}", now)

    gpt_response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text}
        ]
    )

    content = gpt_response.choices[0].message.content
    try:
        task_data = eval(content)
        print("[DEBUG] GPT RESPONSE:", task_data)
    except Exception as e:
        print("Ошибка при обработке GPT-ответа:", e)
        return jsonify({"ok": True})

    fibery_payload = {
        "fibery/type": "Task",
        "Name": task_data.get("title"),
        "Description": task_data.get("description", ""),
        "Due Date": task_data.get("due_date"),
        "Labels": task_data.get("labels", []),
        "Telegram Chat ID": str(chat_id),
        "Telegram Message ID": str(message_id),
        "Created in Telegram": True
    }

    print("[DEBUG] 📤 Отправка в Fibery:\n", fibery_payload)

    response = requests.post(
        FIBERY_URL,
        headers=FIBERY_HEADERS,
        json=fibery_payload
    )

    if response.status_code == 200:
        print("✅ Задача успешно добавлена в Fibery.")
    else:
        print("❌ Fibery не принял задачу:", response.text)

    return jsonify({"ok": True})
