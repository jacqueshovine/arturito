#!/usr/bin/env python3
"""
send_vocab.py — Arturito
Picks 5 Spanish words from Notion and sends them to Telegram.
Triggered by cron at 08:00 and 20:00.
"""

import os           # used to read environment variables
import json
import random
import requests
import pytz
from datetime import date, datetime, timedelta
from notion_client import Client

# ── Config ────────────────────────────────────────────────────────────────────
NOTION_TOKEN      = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
TIMEZONE          = os.environ.get("TZ", "Australia/Sydney")
SESSION_FILE      = "/home/ubuntu/arturito/session.json"  # maps position numbers → Notion page IDs
WORDS_PER_MESSAGE = 5 

# How many days must pass after sending a word before it can be sent again
COOLDOWN_DAYS = {
    "new":      0,
    "seen":     3,
    "familiar": 7,
    "known":    9999
}

# Relative probability weights for picking words — higher = more likely to be picked
STATUS_WEIGHT = {
    "new":      10,  # new words are 10x more likely to be picked than familiar ones
    "seen":     4,
    "familiar": 1,
}

# ── Notion helpers ─────────────────────────────────────────────────────────────
notion = Client(auth=NOTION_TOKEN)

def get_eligible_words():
    """Query Notion for words that are not 'known' and past their cooldown."""
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).date()
    results = []
    has_more = True                       # assume there's at least one page of results
    start_cursor = None

    while has_more:  # keep looping until Notion says there are no more pages
        kwargs = {
            "database_id": NOTION_DATABASE_ID,
            "filter": {
                "and": [
                    {
                        "property": "Status",
                        "select": {"does_not_equal": "known"}  # server-side: exclude known words
                    }
                ]
            }
        }
        if start_cursor:
            kwargs["start_cursor"] = start_cursor

        response = notion.data_sources.query(NOTION_DATABASE_ID, **kwargs)
        data = dict(response)
        results.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    eligible = []
    for page in results:  # iterate over every Notion row returned
        props = page["properties"]

        # Read the Status select field; default to "new" if the field is empty
        status = props["Status"]["select"]["name"] if props["Status"]["select"] else "new"
        cooldown = COOLDOWN_DAYS.get(status, 0)  # look up how many days this status must wait

        # Check last sent date
        last_sent_raw = props.get("Last Sent", {}).get("date")
        if last_sent_raw and last_sent_raw.get("start"):
            last_sent = date.fromisoformat(last_sent_raw["start"])
            # Skip if the last sent date is within the cooldown period for this word's status
            if (today - last_sent).days < cooldown:
                continue

        # Extract the text content from each Notion property (rich_text and title fields are arrays)
        word        = props["Word"]["title"][0]["text"]["content"]        if props["Word"]["title"]        else ""
        translation = props["Translation"]["rich_text"][0]["text"]["content"] if props["Translation"]["rich_text"] else ""
        example     = props["Example"]["rich_text"][0]["text"]["content"] if props["Example"]["rich_text"] else ""

        if not word:
            continue

        eligible.append({
            "id": page["id"],
            "word": word,
            "translation": translation,
            "example": example,
            "status": status,
        })

    return eligible


def pick_words(eligible):
    """Pick WORDS_PER_MESSAGE words with weighted random selection by status."""
    if len(eligible) <= WORDS_PER_MESSAGE:
        return eligible

    weights = [STATUS_WEIGHT.get(w["status"], 1) for w in eligible]  # build a parallel list of weights, one per word
    picked = []
    pool = list(zip(eligible, weights))                                # bundle each word with its weight into tuples

    for _ in range(min(WORDS_PER_MESSAGE, len(eligible))):
        if not pool:
            break
        words_only, w_only = zip(*pool)                                    # unzip the pool back into two separate sequences
        chosen = random.choices(words_only, weights=w_only, k=1)[0]        # pick 1 word using weighted probability
        picked.append(chosen)
        pool = [(w, wt) for w, wt in pool if w["id"] != chosen["id"]]      # remove the chosen word so it can't be picked again

    return picked


def update_word_in_notion(page_id):
    """Update Last Sent date for a sent word."""
    tz = pytz.timezone(TIMEZONE)
    today = datetime.now(tz).date()
    notion.pages.update(
        page_id=page_id,
        properties={
            "Last Sent": {
                "date": {"start": today.isoformat()}
            }
        }
    )


# ── Telegram helper ────────────────────────────────────────────────────────────
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()


# ── Session map ────────────────────────────────────────────────────────────────
def save_session(words):
    """Save position → Notion page ID mapping for the listener."""
    session = {str(i + 1): w["id"] for i, w in enumerate(words)}
    with open(SESSION_FILE, "w") as f:
        json.dump(session, f)


# ── Format message ─────────────────────────────────────────────────────────────
def format_message(words):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    period = "🌅 Buenos días" if now.hour < 12 else "🌙 Buenas tardes"
    lines = [f"<b>{period} — vocabulario del día</b>\n"]

    for i, w in enumerate(words, 1):
        lines.append(
            f"<b>{i}. {w['word']}</b> — {w['translation']}\n"
            f"<i>{w['example']}</i>\n"
        )

    lines.append("Responde con un número (1-5) para marcar esa palabra como aprendida.")
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    print(f"[{now}] Running send_vocab.py (timezone: {TIMEZONE})...")

    eligible = get_eligible_words()
    if not eligible:
        print("No eligible words found.")
        send_telegram_message("⚠️ Arturito: no hay palabras elegibles hoy. Revisa tu base de datos.")  # warn via Telegram
        return

    words = pick_words(eligible)       # weighted-randomly select up to 5 words
    message = format_message(words)    # build the HTML message string

    send_telegram_message(message)     # send the message to Telegram
    save_session(words)                # write session.json so the listener can map replies to words

    for w in words:
        update_word_in_notion(w["id"]) # stamp today's date into its Last Sent field in Notion

    print(f"Sent {len(words)} words: {[w['word'] for w in words]}")  # log which words were sent


if __name__ == "__main__":
    main()
