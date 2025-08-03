import os
import openai
import requests
from flask import Flask, request

app = Flask(__name__)

openai.api_key = os.environ["OPENAI_API_KEY"]
ZAPIER_WEBHOOK_URL = os.environ["ZAPIER_WEBHOOK_URL"]

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if "message" in data and "text" in data["message"]:
        text = data["message"]["text"]
        # Отправляем просто текст задачи в Zapier, без парсинга
        requests.post(ZAPIER_WEBHOOK_URL, json={"raw": text})
    return {"ok": True}

@app.route('/')
def root():
    return 'Magatron is alive.'
