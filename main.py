import os
import re
import requests
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
ZAPIER_WEBHOOK_URL = os.environ.get("ZAPIER_WEBHOOK_URL")

@app.route("/")
def hello():
    return "Magatron is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    message = data["message"]["text"]
    print("Получено сообщение:", message)

    if not message.startswith("Добавь задачу:"):
        return jsonify({"status": "ignored", "message": "Не задача"})

    user_task = message[len("Добавь задачу:"):].strip()
    print("Задача пользователя:", user_task)

    # Новый синтаксис вызова ChatCompletion
    chat_response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{
            "role": "user",
            "content": f"Сформируй задачу для Trello на основе: {user_task}. Формат: "
                       f"Название:, Описание:, Срок:, Метки:"
        }]
    )

    reply = chat_response.choices[0].message.content
    print("Ответ от OpenAI:", reply)

    # Парсинг
    title = re.search(r"Название:\s*(.*)", reply)
    description = re.search(r"Описание:\s*(.*)", reply)
    due = re.search(r"Срок:\s*(.*)", reply)
    labels = re.search(r"Метки:\s*(.*)", reply)

    payload = {
        "title": title.group(1).strip() if title else "",
        "description": description.group(1).strip() if description else "",
        "due": due.group(1).strip() if due else "",
        "labels": labels.group(1).strip() if labels else ""
    }

    print("Payload в Zapier:", payload)
    zapier_response = requests.post(ZAPIER_WEBHOOK_URL, json=payload)
    print("Отправка в Zapier:", zapier_response.status_code)

    return jsonify({"status": "ok", "reply": reply, "parsed": payload})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
