import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "dashboard" / "notify_config.json"


def call(token, method, data=None):
    # the token is part of the address, so the address is never printed
    url = "https://api.telegram.org/bot" + token + "/" + method
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        try:
            return json.loads(err.read())
        except ValueError:
            return {"ok": False, "description": "HTTP " + str(err.code)}
    except (urllib.error.URLError, OSError) as err:
        return {"ok": False, "description": "no connection (" + type(err).__name__ + ")"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="send a test message to the first chat id")
    args = parser.parse_args()

    if not CONFIG.exists():
        print("Missing", CONFIG)
        print("Copy dashboard/notify_config.example.json to dashboard/notify_config.json and put the bot token in it.")
        sys.exit(1)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    token = str(cfg.get("bot_token", ""))
    if ":" not in token or "PUT_" in token:
        print("bot_token in notify_config.json is still the placeholder. Paste the token from BotFather there.")
        sys.exit(1)

    me = call(token, "getMe")
    if not me.get("ok"):
        print("TOKEN CHECK: FAIL (" + str(me.get("description")) + ")")
        sys.exit(1)
    print("TOKEN CHECK: PASS, bot is @" + me["result"]["username"])

    # every chat that wrote to the bot recently shows up here with its id
    updates = call(token, "getUpdates")
    seen = {}
    for item in updates.get("result", []):
        msg = item.get("message") or item.get("my_chat_member") or item.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            seen[str(chat["id"])] = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
    if seen:
        print("Chats the bot can see:")
        for chat_id, name in seen.items():
            print("  chat id", chat_id, "(" + name + ", " + ("group" if chat_id.startswith("-") else "private") + ")")
    else:
        print("No chats seen. Open the bot in Telegram, press Start (or send /start@botname in the group), run again.")

    chats = [str(c) for c in cfg.get("chat_ids", []) if str(c).strip() and "PUT_" not in str(c)]
    if not chats:
        print("chat_ids in notify_config.json is still empty. Put your own chat id first in the list.")
        sys.exit(1)
    print("First chat id in the config is", "one of the chats above." if chats[0] in seen else "NOT in the list above.")

    if args.send:
        answer = call(token, "sendMessage", {"chat_id": chats[0], "text": "[DEMO] Telegram setup test for the ECG monitor"})
        if answer.get("ok"):
            print("TEST MESSAGE: PASS, check the phone (gate G0.8)")
        else:
            print("TEST MESSAGE: FAIL (" + str(answer.get("description")) + ")")
            sys.exit(1)
    else:
        print("Run again with --send to send a test message to the first chat id.")


if __name__ == "__main__":
    main()
