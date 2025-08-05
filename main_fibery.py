import os
import json
import requests
from flask import Flask, request
import openai

openai.api_key = os.environ["OPENAI_API_KEY"]
FIBERY_API_TOKEN = os.environ["FIBERY_API_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

app = Flask(__name__)

FIBERY_API_URL = "https://magatron-lab.fibery.io/api/entities/Задача"

HEADERS = {
    "Authorization": f"Token {FIBERY_API_TOKEN}",
    "Content-Type": "application/json"
}

def ask_gpt_to_parse_task(text, now_str):
    system_prompt = (
        "Ты помощник, который получает сообщение от пользователя и должен распознать задачу. "
        "Ответ возвращай строго в JSON с полями: title (строка), description (строка), "
        "due_date (строка в ISO 8601 или null), labels (список строк). "
        f"Сегодняшняя дата: {now_str}. Используй её как точку отсчета, если дата не указана явно."
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

def send_message(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        print(f"[Telegram Error] {e}")

def send_to_fibery(task):
    data = {
        "type": "Задача",
        "fields": {
            "Name": task.get("title"),
            "Описание": task.get("description", ""),
            "Срок": task.get("due_date"),
            "Метки": task.get("labels", [])
        }
    }
    response = requests.post(FIBERY_API_URL, headers=HEADERS, json=data)
    return response.status_code, response.text

@app.route("/", methods=["GET"])
def index():
    return "Fibery webhook is running."

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
        message = data["message"]["text"]
        chat_id = data["message"]["chat"]["id"]

        now_str = data["message"]["date"]  # это timestamp
        from datetime import datetime
        dt = datetime.utcfromtimestamp(now_str).isoformat()

        gpt_response = ask_gpt_to_parse_task(message, dt)
        print("[GPT RESPONSE]", gpt_response)

        try:
            task = json.loads(gpt_response)
        except Exception as e:
            send_message(chat_id, f"❌ Ошибка парсинга JSON: {e}\n{gpt_response}")
            return "ok"

        if not task.get("title"):
            send_message(chat_id, "⚠️ Не удалось распознать задачу")
            return "ok"

        status, response_text = send_to_fibery(task)

        if status == 200 or status == 201:
            send_message(chat_id, f"✅ Задача добавлена: {task['title']}")
        else:
            send_message(chat_id, f"❌ Fibery не принял задачу: {response_text}")

    except Exception as e:
        send_message(chat_id, f"❌ Общая ошибка: {e}")
    return "ok"
