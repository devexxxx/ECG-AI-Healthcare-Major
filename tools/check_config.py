import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

import config

FW_DIR = ROOT / "firmware" / "ecg_monitor"
SHARED = ["SERVER_PORT", "DISCOVERY_PORT", "SAMPLE_RATE", "BATCH_SIZE", "BRADY_MIN_BPM", "BRADY_MAX_BPM",
          "TACHY_MIN_BPM", "RULE_HOLD_S", "FLATLINE_PTP_COUNTS", "FLATLINE_HOLD_S", "COUNTDOWN_S", "RELAY_WAIT_S",
          "SIM_TIMEOUT_S"]

problems = []
warnings = []


def read_defines(path):
    # picks up lines like "#define NAME value"
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*#define\s+(\w+)\s+(.+?)\s*(//.*)?$", line)
        if m:
            found[m.group(1)] = m.group(2).strip()
    return found


def compare(path):
    defines = read_defines(path)
    for name in SHARED:
        if name not in defines:
            problems.append(f"{path.name}: {name} is missing")
        elif str(getattr(config, name)) != defines[name]:
            problems.append(f"{path.name}: {name} = {defines[name]} but dashboard/config.py has {getattr(config, name)}")
    return defines


example = compare(FW_DIR / "config.example.h")
example_text = (FW_DIR / "config.example.h").read_text(encoding="utf-8")
# the shared example must only hold placeholders
for name in ("WIFI_SSID", "WIFI_PASS", "TELEGRAM_BOT_TOKEN"):
    if "PUT_" not in example.get(name, ""):
        problems.append(f"config.example.h: {name} is not a placeholder (a real value must never be in the example)")
if "PUT_YOUR_OWN_CHAT_ID_FIRST" not in example_text or "+91XXXXXXXXXX" not in example_text:
    problems.append("config.example.h: chat id or phone number is not a placeholder")

real = FW_DIR / "config.h"
if real.exists():
    mine = compare(real)
    for name in sorted(set(example) - set(mine)):
        problems.append(f"config.h: {name} is missing (it is in config.example.h, copy the line over)")
    for name in ("WIFI_SSID", "WIFI_PASS", "PATIENT_NAME", "TELEGRAM_BOT_TOKEN"):
        if "PUT_" in mine.get(name, ""):
            warnings.append(f"config.h: {name} is still a placeholder")
    if mine.get("DEMO_ONLY_FIRST_CONTACT") != "1":
        warnings.append("config.h: DEMO_ONLY_FIRST_CONTACT is not 1 (messages can reach other contacts)")
else:
    warnings.append("firmware/ecg_monitor/config.h does not exist yet (copy config.example.h)")

example_json = json.loads((ROOT / "dashboard" / "notify_config.example.json").read_text(encoding="utf-8"))
if "PUT_" not in example_json["bot_token"] or "PUT_" not in str(example_json["chat_ids"][0]):
    problems.append("notify_config.example.json holds a real token or chat id")
if config.NOTIFY_CONFIG.exists():
    mine = json.loads(config.NOTIFY_CONFIG.read_text(encoding="utf-8"))
    if "PUT_" in str(mine.get("bot_token", "")):
        warnings.append("notify_config.json: bot_token is still a placeholder")
    if mine.get("dry_run"):
        warnings.append("notify_config.json: dry_run is true (nothing is really sent)")
    if not mine.get("demo_only_first", True):
        warnings.append("notify_config.json: demo_only_first is false (messages can reach other chats)")
else:
    warnings.append("dashboard/notify_config.json does not exist yet (copy notify_config.example.json)")

for w in warnings:
    print("WARNING:", w)
for p in problems:
    print("PROBLEM:", p)
print("checked %d shared values" % len(SHARED))
print("CONFIG CHECK: PASS" if not problems else "CONFIG CHECK: FAIL")
sys.exit(1 if problems else 0)
