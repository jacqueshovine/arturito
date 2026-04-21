#!/usr/bin/env python3
"""
bot_listener.py — Arturito
Polls Telegram for replies and updates word status in Notion.
Runs as a persistent systemd service.
"""

import os
import time
import requests
from datetime import datetime
from notion_client import Client

# ── Config ────────────────────────────────────────────────────────────────────
NOTION_TOKEN    = os.environ["NOTION_TOKEN"]
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
OFFSET_FILE     = "/home/ubuntu/arturito/offset.txt"
POLL_INTERVAL   = 2  # seconds

STATUS_LADDER = ["new", "seen", "familiar", "known"]

# ── Notion ─────────────────────────────────────────────────────────────────────
notion = Client(auth=NOTION_TOKEN)

def change_status(page_id, direction):
    """Move a word one step up or down the status ladder. Returns (word, old_status, new_status)."""
    page = notion.pages.retrieve(page_id)
    props = page["properties"]
    current = props["Status"]["select"]["name"] if props["Status"]["select"] else "new"
    idx = STATUS_LADDER.index(current) if current in STATUS_LADDER else 0
    if direction == "up":
        new_status = STATUS_LADDER[min(idx + 1, len(STATUS_LADDER) - 1)]
    else:
        new_status = STATUS_LADDER[max(idx - 1, 0)]
    word = props["Word"]["title"][0]["text"]["content"] if props["Word"]["title"] else "?"

    notion.pages.update(
        page_id=page_id,
        properties={
            "Status": {
                "select": {"name": new_status}
            }
        }
    )
    return word, current, new_status


# ── Telegram ───────────────────────────────────────────────────────────────────
def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 30, "allowed_updates": ["callback_query"]}
    if offset:
        params["offset"] = offset
    response = requests.get(url, params=params, timeout=40)
    response.raise_for_status()
    return response.json().get("result", [])


def answer_callback_query(callback_query_id, text):
    """Acknowledge a button tap — shows a brief popup to the user."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_query_id, "text": text})



def load_offset():
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


# ── Callback handler ───────────────────────────────────────────────────────────
def handle_callback_query(callback_query):
    data              = callback_query.get("data", "")
    callback_query_id = callback_query["id"]
    message           = callback_query.get("message", {})
    chat_id           = str(message.get("chat", {}).get("id", ""))

    # Only respond to messages from your own chat
    if chat_id != TELEGRAM_CHAT_ID:
        return

    if ":" not in data:
        return

    direction, page_id = data.split(":", 1)  # e.g. "up:abc-123" or "down:abc-123"

    try:
        word, old_status, new_status = change_status(page_id, direction)

        if old_status == new_status:
            edge = "top ✅" if direction == "up" else "bottom"
            popup_text = f"{word} is already at the {edge}"
        else:
            status_emoji = {"seen": "👁", "familiar": "🔁", "known": "✅", "new": "🆕"}.get(new_status, "")
            popup_text = f"{status_emoji} {word}: {old_status} → {new_status}"
            print(f"[{datetime.now()}] '{word}' (ID: {page_id}): {old_status} → {new_status}")

        answer_callback_query(callback_query_id, popup_text)

    except Exception as e:
        print(f"[{datetime.now()}] Error updating word {page_id}: {e}")
        answer_callback_query(callback_query_id, "❌ Error updating word. Check logs.")


# ── Main loop ──────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now()}] bot_listener.py started.")
    offset = load_offset()

    while True:
        try:
            updates = get_updates(offset)

            for update in updates:
                offset = update["update_id"] + 1
                save_offset(offset)

                if "callback_query" in update:
                    handle_callback_query(update["callback_query"])

        except requests.exceptions.RequestException as e:
            print(f"[{datetime.now()}] Network error: {e}. Retrying in 10s...")
            time.sleep(10)
        except Exception as e:
            print(f"[{datetime.now()}] Unexpected error: {e}. Retrying in 5s...")
            time.sleep(5)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
