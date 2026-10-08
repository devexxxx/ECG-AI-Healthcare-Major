#pragma once

// Copy this file to config.h and fill in your own values. Never share config.h.

// phone hotspot (2.4 GHz, mobile data on)
#define WIFI_SSID "PUT_HOTSPOT_NAME"
#define WIFI_PASS "PUT_HOTSPOT_PASSWORD"

// shown in every alert message (first 16 characters are used)
#define PATIENT_NAME "PUT_NAME"

// Telegram bot token from BotFather and up to 3 chat ids, your own chat id first
#define TELEGRAM_BOT_TOKEN "PUT_BOT_TOKEN_HERE"
const char *TELEGRAM_CHAT_IDS[3] = {"PUT_YOUR_OWN_CHAT_ID_FIRST", "", ""};

// SMS numbers with country code, your own number first (only used when NOTIFY_VIA_SMS is 1)
const char *CONTACTS[3] = {"+91XXXXXXXXXX", "", ""};

// 1 = only the first chat id / number gets messages and every message starts with [DEMO]
#define DEMO_ONLY_FIRST_CONTACT 1

// notification ladder steps, tried in this order
#define NOTIFY_VIA_DASHBOARD 1
#define NOTIFY_VIA_TELEGRAM_DIRECT 1
#define NOTIFY_VIA_SMS 0

// set OLED_ENABLED to 0 if the display is missing or dead
#define OLED_ENABLED 1
#define OLED_ADDRESS 0x3C

// optional fixed dashboard address, used only when no discovery broadcast is heard ("" = off)
#define SERVER_IP ""

// these must match dashboard/config.py (tools/check_config.py compares them)
#define SERVER_PORT 8000
#define DISCOVERY_PORT 4210
#define SAMPLE_RATE 250
#define BATCH_SIZE 25
#define BRADY_MIN_BPM 20
#define BRADY_MAX_BPM 44
#define TACHY_MIN_BPM 130
#define RULE_HOLD_S 10
#define FLATLINE_PTP_COUNTS 40
#define FLATLINE_HOLD_S 5
#define COUNTDOWN_S 15
#define RELAY_WAIT_S 10
#define SIM_TIMEOUT_S 5

// device-only timings
#define PANIC_HOLD_MS 1500
#define CLEAR_HOLD_MS 3000
#define COOLDOWN_S 120
#define TELEGRAM_TIMEOUT_S 8
#define SMS_WAIT_S 20
#define SMS_RETRIES 2
#define NOTIFY_RETRY_S 30
#define NOTIFY_MAX_ROUNDS 6
