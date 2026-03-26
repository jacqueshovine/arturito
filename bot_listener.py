#!/usr/bin/env python3
"""
bot_listener.py — Arturito
Polls Telegram for replies and updates word status in Notion.
Runs as a persistent systemd service.
"""

import os
import json
import time
import requests
from datetime import datetime
from notion_client import Client

# ── Config ────────────────────────────────────────────────────────────────────
NOTION_TOKEN    = os.environ["NOTION_TOKEN"]
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SESSION_FILE    = "/home/ubuntu/arturito/session.json"
OFFSET_FILE     = "/home/ubuntu/arturito/offset.txt"
POLL_INTERVAL   = 2  # seconds

# Status progression
STATUS_PROGRESSION = {
    "new":      "seen",
    "seen":     "familiar",
    "familiar": "known",
    "known":    "known",  # already at the top
}

# ── Notion ─────────────────────────────────────────────────────────────────────
notion = Client(auth=NOTION_TOKEN)

def bump_status(page_id):
    """Move a word one step up the status ladder."""
    page = notion.pages.retrieve(page_id)
    current = page["properties"]["Status"]["select"]["name"] if page["properties"]["Status"]["select"] else "new"
    new_status = STATUS_PROGRESSION.get(current, "known")

    notion.pages.update(
        page_id=page_id,
        properties={
            "Status": {
                "select": {"name": new_status}
            }
        }
    )
    return current, new_status


# ── Telegram ───────────────────────────────────────────────────────────────────
def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 30, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    response = requests.get(url, params=params, timeout=40)
    response.raise_for_status()
    return response.json().get("result", [])


def send_reply(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })


# ── Session map ────────────────────────────────────────────────────────────────
def load_session():
    try:
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_offset():
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


# ── Message handler ────────────────────────────────────────────────────────────
def handle_message(text, session):
    text = text.strip()

    # Only accept digits 1-5
    if not text.isdigit():
        return

    position = text
    if position not in session:
        send_reply(f"⚠️ Position {position} not found in the last session. Send the next batch to get new words.")
        return

    page_id = session[position]

    try:
        old_status, new_status = bump_status(page_id)

        if old_status == new_status == "known":
            send_reply(f"✅ Word {position} is already marked as <b>known</b>.")
        else:
            status_emoji = {"seen": "👁", "familiar": "🔁", "known": "✅"}.get(new_status, "")
            send_reply(
                f"{status_emoji} Word <b>{position}</b>: <i>{old_status}</i> → <b>{new_status}</b>"
            )
            print(f"[{datetime.now()}] Word {position} (ID: {page_id}): {old_status} → {new_status}")

    except Exception as e:
        print(f"Error updating word {position}: {e}")
        send_reply(f"❌ Error updating word {position}. Check logs.")


# ── Main loop ──────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] bot_listener.py started.")
    offset = load_offset()

    while True:
        try:
            updates = get_updates(offset)
            session = load_session()

            for update in updates:
                offset = update["update_id"] + 1
                save_offset(offset)

                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "")

                # Only respond to messages from your own chat
                if chat_id != TELEGRAM_CHAT_ID:
                    continue

                if text:
                    handle_message(text, session)

        except requests.exceptions.RequestException as e:
            print(f"[{datetime.now()}] Network error: {e}. Retrying in 10s...")
            time.sleep(10)
        except Exception as e:
            print(f"[{datetime.now()}] Unexpected error: {e}. Retrying in 5s...")
            time.sleep(5)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
