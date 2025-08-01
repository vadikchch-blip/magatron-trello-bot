import os
from flask import Flask, request, jsonify
from openai import OpenAI

app = Flask(__name__)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

@app.route("/")
def hello():
    return "Magatron is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    message = data["message"]["text"]
    print("Получено сообщение:", message)

    if message.startswith("Добавь задачу:"):
        user_task = message[len("Добавь задачу:"):].strip()
        print("Задача пользователя:", user_task)

        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": f"Добавь в Trello задачу: {user_task}"}]
            )
            reply = response.choices[0].message.content
            print("Ответ от OpenAI:", reply)
            return jsonify({"status": "ok", "reply": reply})
        except Exception as e:
            print("Ошибка:", str(e))
            return jsonify({"status": "error", "message": str(e)})
    
    return jsonify({"status": "ignored", "message": "Не задача"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
