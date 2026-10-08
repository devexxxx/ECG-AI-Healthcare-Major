import json
import urllib.error
import urllib.request

import config


def load_config():
    # read the file on every send so an edited config works without a restart
    try:
        return json.loads(config.NOTIFY_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def token_is_set(cfg):
    token = str(cfg.get("bot_token", ""))
    return ":" in token and "PUT_" not in token


def post_message(token, chat_id, text):
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    url = "https://api.telegram.org/bot" + token + "/sendMessage"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=config.TELEGRAM_TIMEOUT_S) as resp:
            answer = json.loads(resp.read())
        return bool(answer.get("ok")), "delivered"
    except urllib.error.HTTPError as err:
        # Telegram explains the problem in the body, for example "chat not found"
        try:
            return False, json.loads(err.read()).get("description", "HTTP " + str(err.code))
        except ValueError:
            return False, "HTTP " + str(err.code)
    except (urllib.error.URLError, OSError) as err:
        return False, "no connection (" + type(err).__name__ + ")"


def send_telegram(text):
    cfg = load_config()
    if cfg is None:
        return {"ok": False, "sent": 0, "info": "notify_config.json missing or not valid JSON"}

    chats = [str(c) for c in cfg.get("chat_ids", []) if str(c).strip() and "PUT_" not in str(c)]
    # demo rule: only the first chat (the student's own) gets messages, and they are marked
    if cfg.get("demo_only_first", True):
        chats = chats[:1]
        if not text.startswith("[DEMO]"):
            text = "[DEMO] " + text
    if not chats:
        return {"ok": False, "sent": 0, "info": "no chat_ids set"}

    if cfg.get("dry_run", False):
        print("DRY RUN, not sent to", len(chats), "chat(s):", text)
        return {"ok": True, "sent": len(chats), "info": "dry run"}
    if not token_is_set(cfg):
        return {"ok": False, "sent": 0, "info": "bot_token not set"}

    sent = 0
    info = ""
    for chat in chats:
        ok, info = post_message(cfg["bot_token"], chat, text)
        # one retry per chat
        if not ok:
            ok, info = post_message(cfg["bot_token"], chat, text)
        if ok:
            sent += 1
    if sent:
        info = "delivered to " + str(sent) + " chat(s)"
    return {"ok": sent > 0, "sent": sent, "info": info}
