import os
import json
import gspread
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2.service_account import Credentials
from datetime import datetime
from groq import Groq

app = Flask(__name__)

# --- Config ---
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

# --- Groq setup ---
client = Groq(api_key=GROQ_API_KEY)

# --- In-memory conversation store ---
conversations = {}

SYSTEM_PROMPT = """You are a warm and helpful admissions assistant for a private tutor.
Your job is to chat with parents on WhatsApp and collect the following details naturally:

1. Parent's name
2. Child's name
3. Child's age and current grade/class
4. Subject(s) they need help with
5. Preferred schedule (days and time)

Rules:
- Be friendly and conversational. Keep replies short (under 60 words).
- Ask for 1-2 details at a time — never overwhelm them.
- If asked about fees or batch size, say: "The tutor will confirm those details personally once we pass on your inquiry!"
- Once you have ALL details (parent name, child name, grade, subjects, schedule), confirm everything back to the parent in a short summary, thank them warmly, and tell them the tutor will reach out within 24 hours.
- At the very end of that confirmation message ONLY, append this on a new line exactly:
  SAVE:{"parent":"<n>","child":"<n>","grade":"<grade>","subjects":"<subjects>","schedule":"<schedule>"}
- Never output SAVE: until you have all details. Never output it more than once.
- Use plain text only. No asterisks, no markdown, no bullet points — this is WhatsApp."""


def get_groq_reply(phone: str, user_message: str) -> str:
    if phone not in conversations:
        conversations[phone] = []

    conversations[phone].append({
        "role": "user",
        "content": user_message
    })

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversations[phone],
        max_tokens=300,
    )

    reply = response.choices[0].message.content.strip()

    conversations[phone].append({
        "role": "assistant",
        "content": reply
    })

    return reply


def save_to_sheet(data: dict, phone: str):
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds_json = json.loads(os.environ.get("GOOGLE_CREDS_JSON", "{}"))
        creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(GOOGLE_SHEET_ID).sheet1

        if not sheet.row_values(1):
            sheet.append_row(["Timestamp", "Phone", "Parent", "Child", "Grade", "Subjects", "Schedule"])

        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            phone,
            data.get("parent", ""),
            data.get("child", ""),
            data.get("grade", ""),
            data.get("subjects", ""),
            data.get("schedule", ""),
        ])
        print(f"Saved to sheet: {data}")
    except Exception as e:
        print(f"Sheet error: {e}")


@app.route("/webhook", methods=["POST"])
def webhook():
    incoming_msg = request.form.get("Body", "").strip()
    from_number  = request.form.get("From", "")

    print(f"Message from {from_number}: {incoming_msg}")

    reply_text = get_groq_reply(from_number, incoming_msg)

    for line in reply_text.splitlines():
        if line.strip().startswith("SAVE:"):
            json_str = line.strip()[5:].strip()
            try:
                admission_data = json.loads(json_str)
                save_to_sheet(admission_data, from_number)
                conversations.pop(from_number, None)
            except Exception as e:
                print(f"Parse error on SAVE line: {e}")

    clean_reply = "\n".join(
        line for line in reply_text.splitlines()
        if not line.strip().startswith("SAVE:")
    ).strip()

    resp = MessagingResponse()
    resp.message(clean_reply)
    return str(resp), 200


if __name__ == "__main__":
    app.run(port=5000, debug=True)
