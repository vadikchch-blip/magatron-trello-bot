import os
import json
from flask import Flask, request
import requests
from datetime import datetime, timedelta
import openai

app = Flask(__name__)

# Загружаем переменные окружения
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ZAPIER_WEBHOOK_URL = os.getenv("ZAPIER_WEBHOOK_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

openai.api_key = OPENAI_API_KEY

# Простейший разбор даты
def parse_due_date(text):
    today = datetime.today()
    if "сегодня" in text.lower():
        return today.strftime("%Y-%m-%d")
    elif "завтра" in text.lower():
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    return None

# GPT-запрос
def ask_gpt_to_parse_task(message):
    prompt = f"""
Ты помощник, который парсит задачи из сообщений пользователя.
Ответь строго в формате JSON с полями:
- title — короткое название задачи,
- description — подробности (если есть),
- due_date — дата дедлайна (если указано: "сегодня", "завтра" и т.д., переведи в формат ГГГГ-ММ-ДД, иначе null),
- labels — массив меток, если встречаются.

Сообщение: "{message}"
"""
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content.strip()

# Основной маршрут
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    try:
        message = data["message"]["text"]
        chat_id = data["message"]["chat"]["id"]

        gpt_response = ask_gpt_to_parse_task(message)

        try:
            parsed = json.loads(gpt_response)
        except Exception as e:
            send_message(chat_id, f"❌ Ошибка парсинга ответа от GPT:\n{e}\n{gpt_response}")
            return "ok"

        if not parsed or not parsed.get("title"):
            send_message(chat_id, "Не удалось распознать задачу. Попробуй переформулировать.")
            return "ok"

        if not parsed.get("due_date"):
            parsed["due_date"] = parse_due_date(message)

        # Отправка в Zapier
        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)

        send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")

    except Exception as e:
        send_message(chat_id, f"❌ Ошибка обработки сообщения: {e}")
    return "ok"

# Ответ пользователю в Telegram
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

# Запуск Flask
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
