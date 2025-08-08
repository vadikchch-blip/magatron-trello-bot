import os
import json
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request
import openai

# -------------------- Конфиг из ENV --------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
FIBERY_API_TOKEN   = os.getenv("FIBERY_API_TOKEN")

# ВАЖНО: это должен быть endpoint пространства (space), а не общий:
# Пример: https://magatron-lab.fibery.io/api/graphql/space/Magatron_space
FIBERY_API_URL     = os.getenv("FIBERY_API_URL")

# Локальный часовой пояс пользователя (по умолчанию Москва)
LOCAL_TZ_NAME      = os.getenv("LOCAL_TZ", "Europe/Moscow")

# Если true/1 — шлём в поле due2 (DateRangeInput), иначе — в due (String)
USE_DUE2           = os.getenv("FIBERY_USE_DUE2", "0").lower() in ("1", "true", "yes")

openai.api_key = OPENAI_API_KEY

app = Flask(__name__)
logging.basicConfig(level=logging.DEBUG)


# -------------------- Утилиты времени --------------------
def _safe_fromiso(dt_str: str) -> datetime | None:
    """Пытаемся распарсить ISO-строку от GPT. Поддерживаем:
       - 'YYYY-MM-DDTHH:MM:SS'
       - 'YYYY-MM-DDTHH:MM:SSZ'
       - 'YYYY-MM-DD'
       - с/без смещения (+03:00).
    """
    if not dt_str:
        return None

    s = dt_str.strip()
    # Нормализуем Z -> +00:00
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        # Если только дата — добавим дефолтное время 09:00
        if len(s) == 10 and s.count("-") == 2 and "T" not in s:
            s = f"{s}T09:00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def local_to_utc_z(local_dt: datetime, tz_name: str) -> str:
    """Интерпретируем naive dt как локальное в tz_name, затем переводим в UTC с суффиксом Z."""
    if local_dt.tzinfo is None:
        local_dt = local_dt.replace(tzinfo=ZoneInfo(tz_name))
    utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
    # Формат ровно как любит Fibery: сек + .000Z
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def coerce_gpt_due_to_utc_z(gpt_due: str | None, tz_name: str) -> str | None:
    """Берём строку даты от GPT, приводим к UTC Z. Если нераспознается — None."""
    if not gpt_due:
        return None
    dt = _safe_fromiso(gpt_due)
    if not dt:
        return None
    return local_to_utc_z(dt, tz_name)


# -------------------- Telegram --------------------
def tg_send(chat_id: int | str, text: str) -> None:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
    except Exception as e:
        logging.error("Ошибка Telegram send: %s", e)


# -------------------- GPT парсер --------------------
def extract_task_via_gpt(user_text: str) -> dict:
    """Просим GPT вернуть строго JSON:
       { "title": str, "description": str, "due": str|null, "labels": [str] }
       Где 'due' — локальное (МСК) время ISO-8601 без Z.
    """
    now = datetime.now(ZoneInfo(LOCAL_TZ_NAME)).isoformat(timespec="seconds")

    system_prompt = (
        "Ты помощник по задачам. Возвращай СТРОГО JSON без комментариев и пояснений.\n"
        "Требуется вытащить:\n"
        "title: строка (краткое название)\n"
        "description: строка (может быть пустой)\n"
        "due: строка в ISO-8601 (локальное время пользователя) или null, если срока нет.\n"
        "labels: массив строк.\n"
        "ВАЖНО:\n"
        "- Если дата относительная (сегодня/завтра/в пятницу), используй текущую дату/время:\n"
        f"  now_local = {now}\n"
        "- НЕ ставь суффикс 'Z' и не конвертируй в UTC. Верни локальное время.\n"
        "- Если указана только дата без времени — поставь 09:00 локального.\n"
        "Формат ответа строго:\n"
        '{\"title\":\"...\",\"description\":\"...\",\"due\":\"YYYY-MM-DDTHH:MM:SS\"|null,\"labels\":[\"...\"]}'
    )

    user_prompt = f"Текст пользователя: {user_text}"

    resp = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )

    content = resp["choices"][0]["message"]["content"]
    logging.debug("[DEBUG] GPT RAW: %s", content)

    # Без eval — только json.loads
    parsed = json.loads(content)

    # sanity-guard
    parsed.setdefault("title", user_text.strip()[:120] or "Без названия")
    parsed.setdefault("description", "")
    parsed.setdefault("labels", [])
    if parsed.get("due") is None:
        pass
    else:
        # гарантируем секундную гранулярность (если GPT дал минутную)
        d = _safe_fromiso(parsed["due"])
        if d:
            parsed["due"] = d.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            parsed["due"] = None

    return parsed


# -------------------- Fibery GraphQL --------------------
def fibery_create_task(name: str,
                       due_local_iso: str | None,
                       chat_id: str,
                       msg_id: str) -> tuple[bool, str]:
    """Создаёт Task в Fibery. Возвращает (ok, message)."""

    # Превращаем локальное в UTC Z для Fibery
    due_utc_z = coerce_gpt_due_to_utc_z(due_local_iso, LOCAL_TZ_NAME)

    if USE_DUE2:
        # Режим поля due2 (DateRangeInput): ждёт объект {start, end} (строки ISO с Z)
        # Если end нам не нужен, отдадим одинаковые start=end.
        variables = {
            "name": name,
            "chatId": chat_id,
            "msgId": msg_id,
            "range": {"start": due_utc_z, "end": due_utc_z} if due_utc_z else None
        }
        query = """
        mutation($name: String!, $range: DateRangeInput, $chatId: String!, $msgId: String!) {
          tasks {
            create(
              name: $name
              due2: $range
              createdInTelegram: true
              telegramChatId: $chatId
              telegramMessageId: $msgId
            ) {
              message
            }
          }
        }
        """
    else:
        # Обычное поле due: String (ожидает UTC ISO с Z)
        variables = {
            "name": name,
            "chatId": chat_id,
            "msgId": msg_id,
            "due": due_utc_z
        }
        query = """
        mutation($name: String!, $due: String, $chatId: String!, $msgId: String!) {
          tasks {
            create(
              name: $name
              due: $due
              createdInTelegram: true
              telegramChatId: $chatId
              telegramMessageId: $msgId
            ) {
              message
            }
          }
        }
        """

    payload = {"query": query, "variables": variables}
    headers = {
        "Authorization": f"Token {FIBERY_API_TOKEN}",
        "Content-Type": "application/json",
    }

    logging.debug("[DEBUG] ➜ Fibery GraphQL POST %s", FIBERY_API_URL)
    logging.debug("[DEBUG] Query:\n%s", query)
    logging.debug("[DEBUG] Variables:\n%s", json.dumps(variables, ensure_ascii=False, indent=2))

    r = requests.post(FIBERY_API_URL, headers=headers, json=payload, timeout=30)

    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}

    logging.debug("[DEBUG] ⇦ Fibery response:\n%s", json.dumps(data, ensure_ascii=False, indent=2))

    if r.status_code == 200 and isinstance(data, dict) and "errors" not in data:
        msg = (
            data.get("data", {})
               .get("tasks", {})
               .get("create", {})
               .get("message", "Created")
        )
        return True, msg

    return False, json.dumps(data, ensure_ascii=False)


# -------------------- Flask endpoints --------------------
@app.route("/", methods=["GET"])
def index():
    return "OK"

@app.route("/webhook", methods=["POST"])
def tg_webhook():
    body = request.json
    logging.debug("[DEBUG] Incoming update: %s", json.dumps(body, ensure_ascii=False))

    if "message" not in body or "text" not in body["message"]:
        return "ok"

    chat_id = str(body["message"]["chat"]["id"])
    msg_id  = str(body["message"]["message_id"])
    text    = body["message"]["text"]

    try:
        # 1) Парсим текст через GPT
        parsed = extract_task_via_gpt(text)
        title  = parsed.get("title") or "Без названия"
        due_local = parsed.get("due")  # локальная строка, без Z

        # 2) Создаём задачу в Fibery
        ok, info = fibery_create_task(title, due_local, chat_id, msg_id)

        if ok:
            tg_send(chat_id, f"✅ Задача добавлена: {title}")
        else:
            tg_send(chat_id, f"❌ Fibery ответил ошибкой:\n{info}")

    except json.JSONDecodeError:
        tg_send(chat_id, "❌ Ошибка парсинга JSON от GPT. Скажи проще, плиз 🙏")
    except Exception as e:
        logging.exception("Ошибка обработки апдейта")
        tg_send(chat_id, f"❌ Не смог добавить задачу: {e}")

    return "ok"
