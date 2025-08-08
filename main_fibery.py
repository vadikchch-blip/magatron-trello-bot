import os
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request
import openai
from dateparser import parse as dp_parse

# ----------------- Конфиг -----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FIBERY_API_TOKEN = os.getenv("FIBERY_API_TOKEN")
# ВАЖНО: именно space-эндпоинт!
# пример: https://magatron-lab.fibery.io/api/graphql/space/Magatron_space
FIBERY_API_URL = os.getenv("FIBERY_API_URL")

# 1 = писать в диапазонное поле due2 (DateRangeInput), иначе — в due (String)
FIBERY_USE_DUE2 = os.getenv("FIBERY_USE_DUE2", "0") == "1"

openai.api_key = OPENAI_API_KEY

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)

MSK = ZoneInfo("Europe/Moscow")

# ----------------- Вспомогалочки -----------------
def send_telegram_message(chat_id: int | str, text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        logging.error("Telegram send error: %s", e)

def ask_gpt_to_parse_task(user_text: str, now_msk_iso: str) -> dict:
    """
    Просим GPT вытащить title/description/due/labels.
    ВАЖНО: в промпт прокидываем «сегодня» (MSK), чтобы модель нормализовала относительные даты.
    """
    system_prompt = (
        "Ты помощник по организации задач. Извлеки из текста:\n"
        "1) title (строка)\n"
        "2) description (строка, можно пустую)\n"
        "3) due (строка формата YYYY-MM-DDTHH:MM:SS, локальное московское время; если нет — null)\n"
        "4) labels (массив строк)\n"
        "Верни СТРОГО JSON без пояснений.\n"
        "Пример:\n"
        "{\n"
        '  "title": "Позвонить в банк",\n'
        '  "description": "",\n'
        '  "due": "2025-08-07T14:00:00",\n'
        '  "labels": ["звонок"]\n'
        "}"
    )
    user_prompt = (
        f"Сегодня (МСК): {now_msk_iso}\n\n"
        f"Задача: {user_text}"
    )

    resp = openai.ChatCompletion.create(
        model="gpt-4",
        temperature=0.1,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )
    content = resp["choices"][0]["message"]["content"]
    logging.debug("[DEBUG] GPT RAW: %s", content)

    # Попробовать вычленить JSON даже если модель утащила лишние символы
    try:
        return json.loads(content)
    except Exception:
        # грубая зачистка обрамляющего текста
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start:end+1])
        raise

def parse_due_local_to_utc(due_local_str: str) -> datetime | None:
    """
    Принимает локальное (МСК) due в виде строки 'YYYY-MM-DDTHH:MM:SS'.
    Возвращает aware-дату в UTC.
    """
    if not due_local_str:
        return None
    # dateparser может скушать iso без таймзоны как naive → присвоим явно МСК
    dt_local = dp_parse(due_local_str)
    if not dt_local:
        return None

    # если naive — считаем это временем в МСК
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=MSK)
    else:
        # на всякий случай приводим всё в МСК, если GPT вдруг вернул таймзону
        dt_local = dt_local.astimezone(MSK)

    # → в UTC
    return dt_local.astimezone(timezone.utc)

def build_graphql_payload(name: str,
                          description: str,
                          due_utc: datetime | None,
                          chat_id: str,
                          msg_id: str,
                          labels: list[str] | None):
    """
    Возвращает (query, variables) для GraphQL под текущий режим:
      - FIBERY_USE_DUE2=1 → пишем в due2 (DateRangeInput) со start=end
      - иначе → due (String) в ISO UTC с 'Z'
    Замечание: labels мы пока не шлём (их тип — filters), чтобы не ловить ошибки.
    """
    if FIBERY_USE_DUE2:
        # Диапазон — обе точки одинаковые (point event)
        if due_utc:
            start_iso = due_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            end_iso = start_iso
        else:
            start_iso = None
            end_iso = None

        query = """
            mutation($name: String!, $desc: String, $range: DateRangeInput, $chatId: String!, $msgId: String!) {
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
            "name": name or "",
            "desc": description or "",
            "chatId": str(chat_id),
            "msgId": str(msg_id),
            "range": {"start": start_iso, "end": end_iso} if start_iso else None
        }
    else:
        # Одиночная дата-время: Fibery ждёт строку UTC
        due_iso = due_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z") if due_utc else None

        query = """
            mutation($name: String!, $desc: String, $due: String, $chatId: String!, $msgId: String!) {
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
            "name": name or "",
            "desc": description or "",
            "chatId": str(chat_id),
            "msgId": str(msg_id),
            "due": due_iso
        }

    return query, variables

def fibery_graphql(query: str, variables: dict) -> dict:
    headers = {
        "Authorization": f"Token {FIBERY_API_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {"query": query, "variables": variables}
    logging.debug("[DEBUG] ➜ Fibery GraphQL POST %s", FIBERY_API_URL)
    logging.debug("[DEBUG] Variables:\n%s", json.dumps(variables, ensure_ascii=False, indent=2))
    logging.debug("[DEBUG] Query:\n%s", query)
    resp = requests.post(FIBERY_API_URL, headers=headers, json=body, timeout=20)
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    logging.debug("[DEBUG] ⇦ Fibery response:\n%s", json.dumps(data, ensure_ascii=False, indent=2))
    return data

# ----------------- HTTP -----------------
@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route("/webhook", methods=["POST"])
def tg_webhook():
    data = request.json
    logging.debug("[DEBUG] Входящее сообщение: %s", data)

    if not data or "message" not in data or "text" not in data["message"]:
        return "ok"

    msg = data["message"]
    chat_id = msg["chat"]["id"]
    message_id = msg["message_id"]
    text = msg["text"]

    try:
        # Текущее «сегодня» в МСК в ISO без zюпок
        now_msk = datetime.now(MSK)
        now_msk_iso = now_msk.strftime("%Y-%m-%dT%H:%M:%S")

        # Парс от GPT
        g = ask_gpt_to_parse_task(text, now_msk_iso)
        title = g.get("title") or ""
        description = g.get("description") or ""
        labels = g.get("labels") or []
        due_local = g.get("due")  # локальная (МСК) строка

        # Перевод due → UTC (aware)
        due_utc = parse_due_local_to_utc(due_local) if due_local else None

        # GraphQL → Fibery
        query, variables = build_graphql_payload(
            name=title,
            description=description,
            due_utc=due_utc,
            chat_id=str(chat_id),
            msg_id=str(message_id),
            labels=labels
        )
        result = fibery_graphql(query, variables)

        if "errors" in result:
            send_telegram_message(chat_id, "❌ Ошибка при создании задачи в Fibery")
            return "ok"

        send_telegram_message(chat_id, f"✅ Задача добавлена: {title}")
    except Exception as e:
        logging.exception("Ошибка обработки сообщения: %s", e)
        send_telegram_message(chat_id, "❌ Ошибка: не смог распознать/создать задачу")

    return "ok"
