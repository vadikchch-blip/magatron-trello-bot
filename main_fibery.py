import os
import json
import openai
import requests
from flask import Flask, request
from datetime import datetime

openai.api_key = os.environ["OPENAI_API_KEY"]
FIBERY_API_TOKEN = os.environ["FIBERY_API_TOKEN"]
FIBERY_WORKSPACE = os.environ["FIBERY_WORKSPACE"]

app = Flask(__name__)

def ask_gpt_to_parse_task(text):
    current_date = datetime.now().strftime("%Y-%m-%d")
    system_prompt = (
        f"Сегодня: {current_date}. Ты помощник, который получает сообщение от пользователя "
        "и должен распознать задачу. Ответ возвращай строго в JSON с полями: "
        "title (строка), description (строка), due_date (строка в ISO 8601 или null), "
        "labels (список строк)."
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

def send_to_fibery(data, chat_id, message_id):
    url = f"https://{FIBERY_WORKSPACE}.fibery.io/api/entities/Magatron%20space%2F%D0%97%D0%B0%D0%B4%D0%B0%D1%87%D0%B0"
    headers = {
        "Authorization": f"Token {FIBERY_API_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = [{
        "Name": data["title"],
        "Description": data.get("description", ""),
        "Tr Telegram Chat ID": str(chat_id),
        "Tr Telegram Message ID": str(message_id),
        "Метки": data.get("labels", []),
        "Срок": data.get("due_date"),
        "Создано в Telegram": True
    }]

    response = requests.post(url, headers=headers, data=json.dumps(payload))
    return response

@app.route("/", methods=["GET"])
def index():
    return "Magatron 2.0 — Fibery Integration is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
        message = data["message"]["text"]
        chat_id = data["message"]["chat"]["id"]
        message_id = data["message"]["message_id"]

        gpt_response = ask_gpt_to_parse_task(message)

        try:
            parsed = json.loads(gpt_response)
        except Exception as e:
            return f"❌ Ошибка парсинга JSON: {e}\n{gpt_response}"

        print("[DEBUG] GPT RESPONSE:", parsed)

        response = send_to_fibery(parsed, chat_id, message_id)
        if response.status_code == 200:
            return "✅ Задача успешно отправлена в Fibery"
        else:
            print("[DEBUG] Ответ Fibery:", response.text)
            return f"❌ Fibery не принял задачу: {response.text}"

    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

if __name__ == "__main__":
    app.run(debug=True)
