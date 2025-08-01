import os
import json
from flask import Flask, request, jsonify
import openai
import requests

app = Flask(__name__)

openai.api_key = os.environ.get("OPENAI_API_KEY")
ZAPIER_WEBHOOK_URL = os.environ.get("ZAPIER_WEBHOOK_URL")

@app.route("/")
def hello():
    return "Magatron is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    message = data.get("message", {}).get("text", "")
    print("Получено сообщение:", message)

    if message.startswith("Добавь задачу:"):
        user_task = message[len("Добавь задачу:"):].strip()
        print("Задача пользователя:", user_task)

        try:
            # Запрос к OpenAI с ожиданием ответа в JSON-формате
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{
                    "role": "user",
                    "content": f"""
Сформируй задачу для Trello на основе: "{user_task}". 
Ответ строго в JSON с полями:
- title: строка
- description: строка
- due: дата в формате YYYY-MM-DD
- labels: список строк
"""
                }],
                response_format="json"
            )

            reply = response.choices[0].message["content"]
            print("Ответ от OpenAI:", reply)

            task_data = json.loads(reply)

            # Отправка в Zapier
            zapier_response = requests.post(ZAPIER_WEBHOOK_URL, json=task_data)
            print("Отправка в Zapier:", zapier_response.status_code)

            return jsonify({"status": "ok", "reply": task_data})

        except Exception as e:
            print("Ошибка:", str(e))
            return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ignored", "message": "Не задача"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
