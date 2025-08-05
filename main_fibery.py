import os
import openai
import requests
from flask import Flask, request, jsonify
from datetime import datetime
from pytz import timezone
from dotenv import load_dotenv

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")
fibery_api_token = os.getenv("FIBERY_API_TOKEN")
fibery_workspace = os.getenv("FIBERY_WORKSPACE")

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Magatron 2.0 Fibery is running!"

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json()

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    text = message.get("text", "")

    print("[DEBUG] Входящее сообщение:", text)

    # Формируем system_prompt с текущим временем
    now = datetime.now(timezone("Europe/Moscow")).isoformat()
    system_prompt = (
        f"Сегодня {now}. "
        "Ты — ассистент, который помогает извлекать задачи из сообщений. "
        "Верни JSON с ключами: title, description (может быть пустым), due_date (в формате ISO 8601), labels (список строк). "
        "Если срок задачи не указан, поставь null. Пример:\n"
        "{\n"
        "  \"title\": \"Позвонить маме\",\n"
        "  \"description\": \"\",\n"
        "  \"due_date\": \"2025-08-06T15:00:00\",\n"
        "  \"labels\": [\"личное\"]\n"
        "}"
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ]
        )
        gpt_response = response.choices[0].message["content"]
        print("[DEBUG] GPT RESPONSE:", gpt_response)

        task_data = eval(gpt_response)

        # Проверяем дату
        due_date = task_data.get("due_date")
        if due_date and "2022" in due_date:
            print("[DEBUG] ⚠️ GPT дал старую дату", due_date, ", заменяем")
            due_date = None

        payload = {
            "fibery/type": "Magatron space/Задача",
            "Name": task_data.get("title"),
            "Description": task_data.get("description"),
            "Срок": due_date,
            "Метки": task_data.get("labels"),
            "Tr Telegram Chat ID": str(chat_id),
            "Tr Telegram Message ID": str(message_id),
            "Создано в Telegram": True
        }

        print("[DEBUG] 📤 Отправка в Fibery:")
        print(payload)

        headers = {
            "Authorization": f"Token {fibery_api_token}",
            "Content-Type": "application/json"
        }

        url = f"https://{fibery_workspace}.fibery.io/api/entities/Magatron%20space/%D0%97%D0%B0%D0%B4%D0%B0%D1%87%D0%B0"
        response = requests.post(url, json=[payload], headers=headers)

        if response.status_code == 200:
            return jsonify({"status": "ok"}), 200
        else:
            print("❌ Fibery не принял задачу:", response.text)
            return jsonify({"status": "error", "message": response.text}), 400

    except Exception as e:
        print("❌ Ошибка:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(port=8080)
