import requests
from tts.google_tts import speak_text

RASA_REST_URL = "http://localhost:5005/webhooks/rest/webhook"

def run():
    while True:
        msg = input("🧑 You: ")
        response = requests.post(RASA_REST_URL, json={"sender": "user", "message": msg})
        for r in response.json():
            bot_reply = r.get("text")
            if bot_reply:
                print("🤖 Bot:", bot_reply)
                speak_text(bot_reply)  # Bot speaks reply

if __name__ == "__main__":
    run()
