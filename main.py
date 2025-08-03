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
    text = data.get("message", {}).get("text", "")
    if text:
        try:
            requests.post(ZAPIER_WEBHOOK_URL, json={"raw": text})
        except Exception as e:
            print(f"Error sending to Zapier: {e}")
    return {"ok": True}

@app.route('/')
def index():
    return "Magatron is alive."
