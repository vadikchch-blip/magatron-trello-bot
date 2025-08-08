import os
import json
import logging
from datetime import datetime
from flask import Flask, request
import requests
import openai
from dateparser import parse as dp_parse
from pytz import timezone, UTC

# ----------------- Конфиг -----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIBERY_API_TOKEN = os.getenv("FIBERY_API_TOKEN")

# ДОЛЖЕН быть таким: https://magatron-lab.fibery.io/api/graphql/space/Magatron_space
FIBERY_API_URL = os.getenv("FIBERY_API_URL")

# Включаем режим due2 (диапазон) — у тебя в Railway уже FIBERY_USE_DUE2=1
USE_DUE2 = os.getenv("FIBERY_USE_DUE2", "0") == "1"

MOSCOW_TZ = timezone("Europe/Moscow")

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)

# ----------------- Вспомогалки -----------------
def send_telegram(chat_id: int | str, text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        logging.error("TG send error: %s", e)

def parse_due_local_msk_to_utc_iso(due_str: str | None) -> str | None:
    """
    Принимаем строку из GPT (локальное МСК), возвращаем ISO в UTC с миллисекундами '...000Z'.
    """
    if not due_str:
        return None
    dt_local = dp_parse(
        due_str,
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "Europe/Moscow",
            "TO_TIMEZONE": "Europe/Moscow",
            "PREFER_DATES_FROM": "future",
        },
    )
    if not dt_local:
        return None
    # Если внезапно naive — локализуем как МСК
    if dt_local.tzinfo is None:
        dt_local = MOSCOW_TZ.localize(dt_local)
    dt_utc = dt_local.astimezone(UTC)
    # С миллисекундами и суффиксом Z
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def build_graphql_headers():
    return {
        "Authorization": f"Token {FIBERY_API_TOKEN}",
        "Content-Type": "application/json",
    }

def fibery_create_task_range(name: str, start_iso_utc: str | None, end_iso_utc: str | None,
                             chat_id: str, msg_id: str) -> dict:
    """
    Создание задачи через due2 (диапазон). Если start/end None — шлём без due2.
    """
    # Мутация без description и labels — они у Task.create не принимаются напрямую
    if start_iso_utc and end_iso_utc:
        query = """
            mutation($name: String!, $range: DateRangeInput, $chatId: String!, $msgId: String!) {
              tasks {
                create(
                  name: $name
                  createdInTelegram: true
                  telegramChatId: $chatId
                  telegramMessageId: $msgId
                  due2: $range
                ) { message }
              }
            }
        """
        variables = {
            "name": name,
            "chatId": str(chat_id),
            "msgId": str(msg_id),
            "range": {"start": start_iso_utc, "end": end_iso_utc},
        }
    else:
        # Без срока вообще
        query = """
            mutation($name: String!, $chatId: String!, $msgId: String!) {
              tasks {
                create(
                  name: $name
                  createdInTelegram: true
                  telegramChatId: $chatId
                  telegramMessageId: $msgId
                ) { message }
              }
            }
        """
        variables = {
            "name": name,
            "chatId": str(chat_id),
            "msgId": str(msg_id),
        }

    payload = {"query": query, "variables": variables}
    logging.debug("[DEBUG] ➜ Fibery GraphQL POST %s", FIBERY_API_URL)
    logging.debug("[DEBUG] Variables:\n%s", json.dumps(variables, ensure_ascii=False, indent=2))
    logging.debug("[DEBUG] Query:\n%s", query)

    resp = requests.post(FIBERY_API_URL, headers=build_graphql_headers(), json=payload, timeout=20)
    logging.debug("[DEBUG] ⇦ Fibery response:\n%s", resp.text)
    try:
        return resp.json()
    except Exception:
        return {"error": resp.text}

def fibery_create_task_single(name: str, due_iso_utc: str | None, chat_id: str, msg_id: str) -> dict:
    """
    Создание задачи через одиночную дату due (String).
    """
    if due_iso_utc:
        query = """
            mutation($name: String!, $due: String, $chatId: String!, $msgId: String!) {
              tasks {
                create(
                  name: $name
                  createdInTelegram: true
                  telegramChatId: $chatId
                  telegramMessageId: $msgId
                  due: $due
                ) { message }
              }
            }
        """
        variables = {
            "name": name,
            "chatId": str(chat_id),
            "msgId": str(msg_id),
            "due": due_iso_utc,
        }
    else:
        query = """
            mutation($name: String!, $chatId: String!, $msgId: String!) {
              tasks {
                create(
                  name: $name
                  createdInTelegram: true
                  telegramChatId: $chatId
                  telegramMessageId: $msgId
                ) { message }
              }
            }
        """
        variables = {
            "name": name,
            "chatId": str(chat_id),
            "msgId": str(msg_id),
        }

    payload = {"query": query, "variables": variables}
    logging.debug("[DEBUG] ➜ Fibery GraphQL POST %s", FIBERY_API_URL)
    logging.debug("[DEBUG] Variables:\n%s", json.dumps(variables, ensure_ascii=False, indent=2))
    logging.debug("[DEBUG] Query:\n%s", query)

    resp = requests.post(FIBERY_API_URL, headers=build_graphql_headers(), json=payload, timeout=20)
    logging.debug("[DEBUG] ⇦ Fibery response:\n%s", resp.text)
    try:
        return resp.json()
    except Exception:
        return {"error": resp.text}

# ----------------- Telegram webhook -----------------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    logging.debug("[DEBUG] Входящее сообщение: %s", data)

    if "message" not in data or "text" not in data["message"]:
        return "ok"

    msg = data["message"]
    chat_id = msg["chat"]["id"]
    msg_id = msg["message_id"]
    text = msg["text"]

    try:
        # ——— Вытащим структуру задачи через GPT ———
        now_msk = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%dT%H:%M:%S")
        system_prompt = (
            "Ты помощник по организации задач. Извлеки из текста строго такой JSON:\n"
            "{\n"
            '  "title": "строка",\n'
            '  "description": "строка (может быть пустой)",\n'
            '  "due": "YYYY-MM-DDTHH:MM:SS (локальное московское время) или null",\n'
            '  "labels": ["..."]\n'
            "}\n"
            "Если срок не указан — верни due: null."
        )
        user_prompt = f"Сегодня (МСК): {now_msk}\n\nЗадача: {text}"

        gpt_resp = openai.ChatCompletion.create(
            model="gpt-4",
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = gpt_resp["choices"][0]["message"]["content"]
        logging.debug("[DEBUG] GPT RAW: %s", content)
        task = json.loads(content)

        title = task.get("title") or "Без названия"
        due_str_local = task.get("due")  # строка в локальном МСК, либо None

        # ——— Нормализуем дату ———
        due_utc_iso = parse_due_local_msk_to_utc_iso(due_str_local)

        # ——— Создаём в Fibery ———
        if USE_DUE2:
            # Диапазон: start=end= выбранное время
            res = fibery_create_task_range(
                name=title,
                start_iso_utc=due_utc_iso,
                end_iso_utc=due_utc_iso,
                chat_id=str(chat_id),
                msg_id=str(msg_id),
            )
        else:
            # Одиночная дата
            res = fibery_create_task_single(
                name=title,
                due_iso_utc=due_utc_iso,
                chat_id=str(chat_id),
                msg_id=str(msg_id),
            )

        # ——— Ответ пользователю ———
        if "errors" in res:
            logging.error("Fibery GraphQL errors: %s", res)
            send_telegram(chat_id, "❌ Ошибка при отправке в Fibery")
        else:
            send_telegram(chat_id, "✅ Задача добавлена в Fibery")

    except Exception as e:
        logging.exception("Ошибка обработки сообщения: %s", e)
        send_telegram(chat_id, "❌ Ошибка при разборе задачи")

    return "ok"

@app.route("/", methods=["GET"])
def root():
    return "OK"

# ----------------- Запуск -----------------
# Gunicorn старт: gunicorn main_fibery:app --bind 0.0.0.0:$PORT
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
