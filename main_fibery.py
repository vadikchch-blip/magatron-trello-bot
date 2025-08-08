import os
import json
import logging
from datetime import datetime, timezone
from flask import Flask, request
import requests
import openai

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIBERY_API_TOKEN = os.getenv("FIBERY_API_TOKEN")
# ОБЯЗАТЕЛЬНО: вот так и только так
FIBERY_API_URL = os.getenv("FIBERY_API_URL")  # https://magatron-lab.fibery.io/api/graphql/space/Magatron_space

openai.api_key = OPENAI_API_KEY

SYSTEM_PROMPT = (
    "Ты помощник по задачам. Извлеки из текста:\n"
    "title, description, due_date (если есть, в ISO 8601), labels (список).\n"
    "Отвечай строго JSON:\n"
    "{\n"
    '  "title": "...",\n'
    '  "description": "",\n'
    '  "due_date": "YYYY-MM-DDTHH:MM:SSZ (или с .000Z)",\n'
    '  "labels": []\n'
    "}"
)

def ask_gpt(text: str) -> dict:
    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    user_content = f"Сегодня (UTC): {now_iso}\n\nЗадача: {text}"
    resp = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0
    )
    content = resp["choices"][0]["message"]["content"]
    logging.debug("[DEBUG] GPT RESPONSE: %s", content)
    return json.loads(content)

def iso_to_utc_z(due_str: str) -> str | None:
    if not due_str:
        return None
    try:
        # Пытаемся распарсить любой валидный ISO
        # Добавим поддержку без Z/offset
        dt = None
        try:
            dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        except ValueError:
            return None
        # В UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        # Формат строго с миллисекундами и Z
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except Exception:
        return None

def send_telegram(chat_id, text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        logging.error("TG error: %s", e)

@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    logging.debug("[DEBUG] Входящее сообщение: %s", data)

    if not data or "message" not in data or "text" not in data["message"]:
        return "ok"

    msg = data["message"]
    text = msg["text"]
    chat_id = str(msg["chat"]["id"])
    message_id = str(msg["message_id"])

    try:
        parsed = ask_gpt(text)
        title = (parsed.get("title") or "").strip()
        description = parsed.get("description") or ""
        due_raw = parsed.get("due_date")
        due = iso_to_utc_z(due_raw)

        if not title:
            send_telegram(chat_id, "⚠️ Не смог распознать название задачи.")
            return "ok"

        # Готовим GraphQL мутацию
        mutation = """
        mutation CreateTask($name: String!, $due: String, $chat: String!, $msg: String!) {
          tasks {
            create(
              name: $name
              due: $due
              createdInTelegram: true
              telegramChatId: $chat
              telegramMessageId: $msg
            ) { message }
          }
        }
        """

        variables = {
            "name": title,
            "due": due,  # может быть None — это ок
            "chat": chat_id,
            "msg": message_id,
        }

        headers = {
            "Authorization": f"Token {FIBERY_API_TOKEN}",
            "Content-Type": "application/json",
        }

        payload = {"query": mutation, "variables": variables}
        logging.debug("[DEBUG] 📤 GraphQL payload: %s", json.dumps(payload, ensure_ascii=False, indent=2))

        r = requests.post(FIBERY_API_URL, headers=headers, json=payload, timeout=20)
        logging.debug("[DEBUG] 📥 Fibery response: %s", r.text)

        if r.status_code != 200:
            send_telegram(chat_id, f"❌ Fibery HTTP {r.status_code}")
            return "ok"

        j = r.json()
        if "errors" in j:
            send_telegram(chat_id, f"❌ Fibery error: {j['errors'][0].get('message')}")
            return "ok"

        send_telegram(chat_id, f"✅ Задача добавлена: {title}" + (f"\n⏰ {due}" if due else ""))

    except Exception as e:
        logging.exception("Ошибка обработки")
        send_telegram(chat_id, f"❌ Ошибка: {e}")

    return "ok"
