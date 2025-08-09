import os
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request
import openai

# -------------------- Конфиг --------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
FIBERY_API_TOKEN   = os.getenv("FIBERY_API_TOKEN", "")
# Пример: https://magatron-lab.fibery.io/api/graphql/space/Magatron_space
FIBERY_API_URL     = os.getenv("FIBERY_API_URL", "")

# "1" → всегда писать в due2 (диапазон). Иначе — одиночное поле due (строка).
FIBERY_USE_DUE2    = os.getenv("FIBERY_USE_DUE2", "1")

# Таймзона пользователя (по умолчанию МСК)
USER_TZ            = os.getenv("MSK_TZ", "Europe/Moscow")

openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)

# -------------------- Утилиты времени --------------------
def now_in_user_tz() -> datetime:
    return datetime.now(ZoneInfo(USER_TZ)).replace(microsecond=0)

def local_iso(dt: datetime) -> str:
    """YYYY-MM-DDTHH:MM:SS в локальной TZ (без Z)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")

def to_utc_z(dt_local: datetime) -> str:
    """Локальное (aware) → UTC .000Z"""
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=ZoneInfo(USER_TZ))
    dt_utc = dt_local.astimezone(ZoneInfo("UTC"))
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def parse_llm_iso_local(s: str) -> datetime | None:
    """
    Ждём строку вида YYYY-MM-DDTHH:MM:SS (ЛОКАЛЬНОЕ МСК),
    как мы просим в промпте. Возвращаем aware (МСК).
    """
    if not s:
        return None
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=ZoneInfo(USER_TZ))
    except Exception:
        return None

# -------------------- LLM парсер запроса --------------------
SYSTEM_PROMPT = (
    "Ты помощник по задачам. Верни СТРОГИЙ JSON без пояснений.\n"
    "Нужно извлечь:\n"
    "  - title (строка)\n"
    "  - description (строка, можно пустую)\n"
    "  - start (строка в формате YYYY-MM-DDTHH:MM:SS — ЛОКАЛЬНОЕ московское время) или null\n"
    "  - end (строка в том же формате; если в тексте указан диапазон «с … до …», иначе null)\n"
    "  - labels (массив строк)\n"
    "  - reminders (массив целых минут-оффсетов до начала: например [1440, 60, 15]; если пользователь сказал «напомни за 2 часа и за 10 минут», то [120, 10]; если не просил — пустой массив)\n"
    "Если указан только один момент времени (например, «завтра в 15:00»), заполни start, а end верни null.\n"
    "Верни ТОЛЬКО JSON вида:\n"
    "{\n"
    '  "title": "…",\n'
    '  "description": "…",\n'
    '  "start": "2025-08-10T12:00:00" | null,\n'
    '  "end": "2025-08-10T15:00:00" | null,\n'
    '  "labels": ["…"],\n'
    '  "reminders": [числа]\n'
    "}"
)

def llm_extract(text: str) -> dict:
    """
    Вызывает Chat Completions (openai==0.28 стиль) и возвращает dict:
    { title, description, start, end, labels }
    """
    now_local = local_iso(now_in_user_tz())
    user_prompt = (
        f"Сегодня (МСК): {now_local}\n\n"
        f"Задача: {text}\n\n"
        "Верни только JSON."
    )

    logging.debug("[DEBUG] LLM prompt now_local=%s", now_local)

    resp = openai.ChatCompletion.create(
        model="gpt-4",
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw = resp["choices"][0]["message"]["content"]
    logging.debug("[DEBUG] GPT RAW: %s", raw)

    data = json.loads(raw)
    for k in ["title", "description", "start", "end", "labels"]:
        if k not in data:
            data[k] = None if k in ("start", "end") else ("" if k in ("title","description") else [])
    if data.get("labels") is None:
        data["labels"] = []
    return data

# -------------------- GraphQL --------------------
def fibery_graphql(query: str, variables: dict) -> dict:
    headers = {
        "Authorization": f"Token {FIBERY_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables}
    logging.debug("[DEBUG] ➜ Fibery GraphQL POST %s", FIBERY_API_URL)
    logging.debug("[DEBUG] Variables:\n%s", json.dumps(variables, ensure_ascii=False, indent=2))
    logging.debug("[DEBUG] Query:\n\n%s\n", query)

    r = requests.post(FIBERY_API_URL, headers=headers, json=payload, timeout=20)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}

    logging.debug("[DEBUG] ⇦ Fibery response:\n%s", json.dumps(data, ensure_ascii=False, indent=2))
    return data

def create_task_due2(name: str, start_local: datetime, end_local: datetime,
                     chat_id: str, msg_id: str, reminder_offsets_str: str) -> tuple[bool, str]:
    variables = {
        "name": name,
        "chatId": str(chat_id),
        "msgId": str(msg_id),
        "range": {
            "start": to_utc_z(start_local),
            "end":   to_utc_z(end_local),
        },
        "reminders": reminder_offsets_str,
    }
    query = """
        mutation($name: String!, $range: DateRangeInput, $chatId: String!, $msgId: String!, $reminders: String) {
          tasks {
            create(
              name: $name
              createdInTelegram: true
              telegramChatId: $chatId
              telegramMessageId: $msgId
              due2: $range
              reminderOffsets: $reminders
            ) { message }
          }
        }
    """
    res = fibery_graphql(query, variables)
    if "errors" in res:
        logging.error("Fibery GraphQL errors: %s", res)
        return False, "❌ Fibery отклонил due2"
    return True, "✅ Задача (диапазон) добавлена"

def create_task_due(name: str, start_local: datetime,
                    chat_id: str, msg_id: str, reminder_offsets_str: str) -> tuple[bool, str]:
    variables = {
        "name": name,
        "chatId": str(chat_id),
        "msgId": str(msg_id),
        "due": to_utc_z(start_local),
        "reminders": reminder_offsets_str,
    }
    query = """
        mutation($name: String!, $due: String, $chatId: String!, $msgId: String!, $reminders: String) {
          tasks {
            create(
              name: $name
              createdInTelegram: true
              telegramChatId: $chatId
              telegramMessageId: $msgId
              due: $due
              reminderOffsets: $reminders
            ) { message }
          }
        }
    """
    res = fibery_graphql(query, variables)
    if "errors" in res:
        logging.error("Fibery GraphQL errors: %s", res)
        return False, "❌ Fibery отклонил due"
    return True, "✅ Задача добавлена"

# -------------------- Telegram --------------------
def send_telegram(chat_id: int | str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN пуст, не могу отправить сообщение")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logging.error("Ошибка отправки в Telegram: %s", e)

# -------------------- Flask --------------------
@app.route("/", methods=["GET"])
def root():
    return "ok"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json or {}
    logging.debug("[DEBUG] Входящее сообщение: %s", data)

    msg = data.get("message") or {}
    text = msg.get("text")
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    message_id = msg.get("message_id")

    if not text or not chat_id or message_id is None:
        return "ok"

    try:
        parsed = llm_extract(text)

        # 1) Собираем offsets напоминаний:
        #    базовые 24ч (1440) и 1ч (60) + пользовательские из LLM (если есть)
        base_offsets = {1440, 60}
        user_offsets = set()
        try:
            for x in (parsed.get("reminders") or []):
                v = int(x)
                if v >= 1:
                    user_offsets.add(v)
        except Exception:
            pass
        all_offsets = sorted(base_offsets | user_offsets)
        reminder_offsets_str = ",".join(str(x) for x in all_offsets)  # всегда непустая строка

        # 2) Поля задачи
        title = (parsed.get("title") or "").strip() or "Без названия"
        description = parsed.get("description") or ""
        start_str = parsed.get("start")
        end_str   = parsed.get("end")

        start_local = parse_llm_iso_local(start_str)
        end_local   = parse_llm_iso_local(end_str) if end_str else None

        if start_local is None:
            send_telegram(chat_id, "❌ Не понял дату/время. Скажи, например: «завтра в 15:00» или «встреча с 12 до 15».")
            return "ok"

        # 3) Выбор поля due/due2
        use_due2 = (FIBERY_USE_DUE2 == "1")

        if use_due2:
            # если диапазон не указан — дефолт 1 час
            if end_local is None:
                end_local = start_local + timedelta(hours=1)
            ok, msg_out = create_task_due2(
                title,
                start_local,
                end_local,
                chat_id,
                message_id,
                reminder_offsets_str
            )
        else:
            ok, msg_out = create_task_due(
                title,
                start_local,
                chat_id,
                message_id,
                reminder_offsets_str
            )

        send_telegram(chat_id, msg_out if ok else (msg_out + " (подробности в логах)"))

    except json.JSONDecodeError:
        logging.exception("Ошибка JSON от GPT")
        send_telegram(chat_id, "❌ GPT вернул невалидный JSON")
    except Exception as e:
        logging.exception("Ошибка в обработке")
        send_telegram(chat_id, f"❌ Неожиданная ошибка: {e}")

    return "ok"

app = app
