import os
import json
import requests
from flask import Flask, request
from datetime import datetime, timedelta
import openai

# Настройки
openai.api_key = os.environ["OPENAI_API_KEY"]
ZAPIER_WEBHOOK_URL = os.environ["ZAPIER_WEBHOOK_URL"]

app = Flask(__name__)

def ask_gpt_to_parse_task(text):
    system_prompt = (
        "Ты помощник, который получает сообщение от пользователя и должен распознать задачу. "
        "Ответ возвращай строго в JSON с полями: title (строка), description (строка), due_date (строка в ISO 8601 или null), labels (список строк)."
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
    if "завтра" in text.lower():
        return (datetime.now() + timedelta(days=1)).isoformat()
    elif "сегодня" in text.lower():
        return datetime.now().isoformat()
    return None

@app.route("/", methods=["GET"])
def index():
    return "Magatron is alive."

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
        message = data["message"]["text"]
        chat_id = data["message"]["chat"]["id"]

        gpt_response = ask_gpt_to_parse_task(message)
        parsed = json.loads(gpt_response)

        if not parsed.get("title"):
            return "⚠️ Не удалось распознать задачу", 200

        if not parsed.get("due_date"):
            parsed["due_date"] = parse_due_date(message)

        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)
        return "✅ Задача добавлена", 200

    except Exception as e:
        return f"❌ Ошибка: {e}", 200

if __name__ == "__main__":
    app.run(port=8080)
