import os
import openai
from flask import Flask, request
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

# Настройки из переменных окружения
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ZAPIER_WEBHOOK_URL = os.getenv("ZAPIER_WEBHOOK_URL")

openai.api_key = OPENAI_API_KEY

def parse_due_date(text):
    today = datetime.today()
    if "сегодня" in text.lower():
        return today.strftime("%Y-%m-%d")
    elif "завтра" in text.lower():
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    return None

def ask_gpt_to_parse_task(message):
    prompt = f"""
Ты помощник, который парсит задачи из сообщений пользователя.
Ответь в формате JSON с полями:
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

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    message = data["message"]["text"]
    chat_id = data["message"]["chat"]["id"]

    # Обработка
    try:
        gpt_response = ask_gpt_to_parse_task(message)
        parsed = eval(gpt_response) if gpt_response.startswith("{") else {}

        if not parsed or not parsed.get("title"):
            send_message(chat_id, "Не удалось распознать задачу. Попробуй переформулировать.")
            return "ok"

        # Подставим дедлайн, если GPT не распознал
        if not parsed.get("due_date"):
            parsed["due_date"] = parse_due_date(message)

        # Отправка в Zapier
        requests.post(ZAPIER_WEBHOOK_URL, json=parsed)

        send_message(chat_id, f"✅ Задача добавлена: {parsed['title']}")
    except Exception as e:
        send_message(chat_id, f"❌ Ошибка обработки: {e}")
    return "ok"

def send_message(chat_id, text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

if __name__ == "__main__":
    app.run()
