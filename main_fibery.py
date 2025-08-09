import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request

# --------- ЛОГИ ---------
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

# --------- ENV ---------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
FIBERY_API_TOKEN   = os.getenv("FIBERY_API_TOKEN")

# ДОЛЖЕН БЫТЬ именно space-эндпоинт:
# https://<workspace>.fibery.io/api/graphql/space/<SpaceName>
FIBERY_API_URL     = os.getenv("FIBERY_API_URL")

# Фича-флаг диапазона: если "1" — пишем в due2 (DateRange)
USE_DUE2           = os.getenv("FIBERY_USE_DUE2", "1") == "1"   # по умолчанию включено
# Длительность диапазона в минутах
DUE2_SPAN_MIN      = int(os.getenv("DUE2_SPAN_MIN", "60"))      # по умолчанию 60 минут

# GPT (openai==0.28.*)
import openai
openai.api_key = OPENAI_API_KEY

MSK = ZoneInfo("Europe/Moscow")
UTC = ZoneInfo("UTC")

app = Flask(__name__)


# ---------- ВСПОМОГАТЕЛЬНОЕ ----------

def to_utc_iso_z(local_naive_yyyy_mm_dd_hh_mm_ss: str) -> datetime:
    """
    Принимает строку 'YYYY-MM-DDTHH:MM:SS' в МСК.
    Возвращает datetime в UTC.
    """
    dt_local = datetime.strptime(local_naive_yyyy_mm_dd_hh_mm_ss, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=MSK)
    return dt_local.astimezone(UTC)

def iso_z(dt: datetime) -> str:
    """
    В ISO 8601, с миллисекундами и Z-хвостом.
    """
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

def send_telegram_message(chat_id: int | str, text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception as e:
        log.exception("Telegram send error: %s", e)

def gpt_extract(text: str) -> dict:
    """
    Просим GPT вернуть строгий JSON:
    {
      "title": "...",
      "description": "...",
      "due": "YYYY-MM-DDTHH:MM:SS" | null  (локальное МСК),
      "labels": ["..."]
    }
    """
    system_prompt = (
        "Ты помощник по организации задач. Извлеки из текста строгий такой JSON:\n"
        "{\n"
        '  "title": "строка",\n'
        '  "description": "строка (может быть пустой)",\n'
        '  "due": "YYYY-MM-DDTHH:MM:SS (локальное московское время) или null",\n'
        '  "labels": ["..."]\n'
        "}\n"
        "Если срок не указан — верни due: null."
    )
    now_msk = datetime.now(MSK).strftime("%Y-%m-%dT%H:%M:%S")
    user_prompt = f"Сегодня (МСК): {now_msk}\n\nЗадача: {text}"

    resp = openai.ChatCompletion.create(
        model="gpt-4",
        temperature=0.1,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    )
    raw = resp["choices"][0]["message"]["content"]
    log.debug("[DEBUG] GPT RAW: %s", raw)
    return json.loads(raw)


def fibery_graphql(query: str, variables: dict) -> dict:
    headers = {
        "Authorization": f"Token {FIBERY_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables}

    log.debug("[DEBUG] ➜ Fibery GraphQL POST %s", FIBERY_API_URL)
    log.debug("[DEBUG] Variables:\n%s", json.dumps(variables, ensure_ascii=False, indent=2))
    log.debug("[DEBUG] Query:\n%s", query)

    r = requests.post(FIBERY_API_URL, headers=headers, json=payload, timeout=15)
    log.debug("[DEBUG] ⇦ HTTP %s", r.status_code)
    try:
        data = r.json()
    except Exception:
        data = {"text": r.text}
    log.debug("[DEBUG] ⇦ Fibery response:\n%s", json.dumps(data, ensure_ascii=False, indent=2))
    return data


# ---------- ROUTES ----------

@app.route("/", methods=["GET"])
def root():
    return "OK"

@app.route("/health", methods=["GET"])
def health():
    return "ok"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json or {}
        log.debug("[DEBUG] Входящее сообщение: %s", data)

        msg = data.get("message")
        if not msg or "text" not in msg:
            return "ok"

        text      = msg["text"]
        chat_id   = msg["chat"]["id"]
        message_id= msg["message_id"]

        # 1) парсим через GPT
        try:
            task = gpt_extract(text)
        except Exception as e:
            log.exception("GPT parse failed: %s", e)
            send_telegram_message(chat_id, "❌ Ошибка при разборе задачи")
            return "ok"

        title = task.get("title") or ""
        desc  = task.get("description") or ""
        due_s = task.get("due")  # 'YYYY-MM-DDTHH:MM:SS' (MSK) или None
        labels= task.get("labels") or []

        # 2) Готовим мутацию
        if USE_DUE2 and due_s:
            # Диапазон: час по умолчанию (или DUE2_SPAN_MIN)
            start_utc = to_utc_iso_z(due_s)
            end_utc   = start_utc + timedelta(minutes=DUE2_SPAN_MIN)

            gql = """
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
                "name": title,
                "range": {
                    "start": iso_z(start_utc),
                    "end":   iso_z(end_utc),
                },
                "chatId": str(chat_id),
                "msgId":  str(message_id),
            }
        else:
            # Одиночная дата — пишем в due (String)
            gql = """
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
            due_utc_str = None
            if due_s:
                due_utc_str = iso_z(to_utc_iso_z(due_s))

            variables = {
                "name":  title,
                "due":   due_utc_str,
                "chatId": str(chat_id),
                "msgId":  str(message_id),
            }

        # 3) Вызов Fibery
        resp = fibery_graphql(gql, variables)

        # 4) Ответ пользователю
        if isinstance(resp, dict) and resp.get("errors"):
            log.error("Fibery GraphQL errors: %s", json.dumps(resp, ensure_ascii=False))
            send_telegram_message(chat_id, "❌ Ошибка при отправке в Fibery")
        else:
            send_telegram_message(chat_id, "✅ Задача добавлена в Fibery")

    except Exception as e:
        log.exception("Webhook error: %s", e)
        # стараемся ответить, чтобы бот не молчал
        try:
            msg = request.json.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            if chat_id:
                send_telegram_message(chat_id, "❌ Внутренняя ошибка")
        except Exception:
            pass

    return "ok"


if __name__ == "__main__":
    # Для локального запуска
    port = int(os.getenv("PORT", "8080"))
    log.info("[BOOT] USE_DUE2=%s, DUE2_SPAN_MIN=%s", USE_DUE2, DUE2_SPAN_MIN)
    app.run(host="0.0.0.0", port=port)
