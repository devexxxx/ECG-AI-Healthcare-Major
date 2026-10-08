#include <Arduino.h>

#if !defined(ESP_ARDUINO_VERSION_MAJOR) || ESP_ARDUINO_VERSION_MAJOR < 3
#error "this firmware needs ESP32 core 3.x (Boards Manager -> esp32 by Espressif Systems -> 3.3.12)"
#endif

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <Preferences.h>
#include <time.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <TinyGPS++.h>
#include <ArduinoJson.h>
#include <WebSocketsClient.h>

#include "config.h"
#include "ecg_logic.h"

const int PIN_ECG = 34;
const int PIN_LO_PLUS = 32;
const int PIN_LO_MINUS = 33;
const int PIN_SDA = 21;
const int PIN_SCL = 22;
const int PIN_SD_CS = 5;
const int PIN_SD_SCK = 18;
const int PIN_SD_MISO = 19;
const int PIN_SD_MOSI = 23;
const int PIN_GPS_RX = 16;
const int PIN_SIM_RX = 25;
const int PIN_SIM_TX = 26;
const int PIN_SIM_RST = 27;
const int PIN_GREEN = 13;
const int PIN_RED = 14;
const int PIN_BUZZER = 4;
const int PIN_BUTTON = 15;

struct Sample {
  uint32_t index;
  uint16_t adc;
  uint8_t leadOff;
};

Adafruit_SSD1306 oled(128, 64, &Wire, -1);
TinyGPSPlus gps;
HardwareSerial gpsSerial(2);
HardwareSerial simSerial(1);
WebSocketsClient ws;
WiFiUDP udp;
Preferences prefs;
QueueHandle_t sampleQueue;
volatile uint32_t droppedSamples = 0;

BeatDetector beat;
RuleState ruleState;
Button button;
const RuleLimits ruleLimits = {FLATLINE_PTP_COUNTS, FLATLINE_HOLD_S, BRADY_MIN_BPM,
                               BRADY_MAX_BPM,       TACHY_MIN_BPM,   RULE_HOLD_S};

int bpmNow = 0;
bool leadsOff = true;
uint32_t lastIndex = 0;
uint32_t greenUntil = 0;

int state = ST_IDLE;
uint32_t stateSince = 0;
char alertLevel[12] = "";
char alertReason[48] = "";
const char *alertSrc = "local";
bool alertPanic = false;
bool alertDemo = true;
bool alertSent = false;
int alertBpm = 0;
int notifyRounds = 0;
uint32_t lastRoundAt = 0;
char lastNotify[40] = "none";

// shared with the notify task: the loop asks for a round, the task reports when it is done
char notifyText[200] = "";
volatile int roundId = 0;
volatile int doneId = 0;
volatile bool doneOk = false;
int handledId = 0;
char sentVia[12] = "";
volatile bool relayWanted = false;
volatile int relayAnswer = 0;
volatile int relayId = 0;
volatile bool modemOk = false;
volatile int modemCsq = 0;
const char *volatile gsmText = "off";

bool wifiUp = false;
bool wsUp = false;
bool simMode = false;
int simBpm = 0;
uint32_t lastSimMsg = 0;

bool haveFix = false;
bool fixThisBoot = false;
double lastLat = 0;
double lastLon = 0;
uint32_t lastFixMs = 0;
uint32_t savedFixEpoch = 0;

bool sdOk = false;
bool oledOk = false;

bool timeTrusted() {
  // before NTP has answered the clock starts near 1970
  return time(nullptr) > 1700000000;
}

int shownBpm() {
  return simMode ? simBpm : bpmNow;
}

bool gpsFixed() {
  return fixThisBoot && millis() - lastFixMs < 5000;
}

long fixAgeSeconds() {
  // -1 = there is a fix but its age is not known, callers check haveFix first
  if (fixThisBoot) return (millis() - lastFixMs) / 1000;
  if (savedFixEpoch > 0 && timeTrusted()) return (long)(time(nullptr) - savedFixEpoch);
  return -1;
}

void setState(int next) {
  state = next;
  stateSince = millis();
  Serial.printf("state -> %s\n", stateName(state));
}

void startRound() {
  char age[24];
  fixAgeText(age, sizeof(age), fixAgeSeconds());
  buildMessage(notifyText, sizeof(notifyText), alertDemo || DEMO_ONLY_FIRST_CONTACT, alertPanic, alertLevel,
               PATIENT_NAME, alertReason, alertBpm, haveFix, lastLat, lastLon, age);
  notifyRounds++;
  lastRoundAt = millis();
  // the text is ready before the number changes, the notify task watches the number
  roundId = roundId + 1;
}

void beginAlert(const char *level, const char *reason, bool demo, bool panic, const char *src) {
  strlcpy(alertLevel, level, sizeof(alertLevel));
  strlcpy(alertReason, reason, sizeof(alertReason));
  alertSrc = src;
  alertPanic = panic;
  alertDemo = demo;
  alertSent = false;
  alertBpm = shownBpm();
  notifyRounds = 0;
  lockSdEvent(reason);
}

void startCountdown(const char *level, const char *reason, bool demo, const char *src) {
  beginAlert(level, reason, demo, false, src);
  setState(ST_COUNTDOWN);
  sendEvent("countdown", reason, src, "");
}

void cancelCountdown(const char *src) {
  setState(ST_IDLE);
  rulesReset(ruleState);
  sendEvent("cancel", "", src, "");
}

void startPanic() {
  beginAlert("CRITICAL", "panic button", DEMO_ONLY_FIRST_CONTACT, true, "local");
  setState(ST_SENDING);
  beep(1, 150, 0);
  sendEvent("panic", "", "local", "");
  startRound();
}

void alertLoop() {
  uint32_t now = millis();
  if (state == ST_COUNTDOWN && now - stateSince >= COUNTDOWN_S * 1000UL) {
    setState(ST_SENDING);
    startRound();
  }

  // a finished round is handled once, a round for an alert that was cleared is dropped
  if (doneId == roundId && handledId != roundId) {
    handledId = roundId;
    if (state == ST_SENDING || state == ST_ALERT) {
      if (doneOk) {
        alertSent = true;
        snprintf(lastNotify, sizeof(lastNotify), "sent via %s", sentVia);
        beep(1, 800, 0);
        sendEvent("notify_ok", lastNotify, alertSrc, sentVia);
      } else {
        snprintf(lastNotify, sizeof(lastNotify), "SEND FAIL round %d", notifyRounds);
        beep(3, 120, 120);
        sendEvent("notify_fail", lastNotify, alertSrc, "");
      }
      Serial.println(lastNotify);
      if (state == ST_SENDING) setState(ST_ALERT);
    }
  }

  // store and forward: try again every 30 s until it is sent or 6 rounds are used
  bool idle = handledId == roundId;
  if (state == ST_ALERT && !alertSent && idle && notifyRounds < NOTIFY_MAX_ROUNDS &&
      now - lastRoundAt >= NOTIFY_RETRY_S * 1000UL) {
    startRound();
  }
  if (state == ST_COOLDOWN && now - stateSince >= COOLDOWN_S * 1000UL) setState(ST_IDLE);
}

void warnPlaceholders() {
  // every value still left as in config.example.h gets a line on the serial monitor
  if (strstr(WIFI_SSID, "PUT_")) Serial.println("WARNING: WIFI_SSID is still a placeholder");
  if (strstr(WIFI_PASS, "PUT_")) Serial.println("WARNING: WIFI_PASS is still a placeholder");
  if (strstr(PATIENT_NAME, "PUT_")) Serial.println("WARNING: PATIENT_NAME is still a placeholder");
  if (strstr(TELEGRAM_BOT_TOKEN, "PUT_")) Serial.println("WARNING: TELEGRAM_BOT_TOKEN is still a placeholder");
  if (strstr(TELEGRAM_CHAT_IDS[0], "PUT_")) Serial.println("WARNING: TELEGRAM_CHAT_IDS is still a placeholder");
  if (NOTIFY_VIA_SMS && strchr(CONTACTS[0], 'X')) Serial.println("WARNING: CONTACTS is still a placeholder");
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nECG monitor, ESP32 core %d.%d.%d\n", ESP_ARDUINO_VERSION_MAJOR, ESP_ARDUINO_VERSION_MINOR,
                ESP_ARDUINO_VERSION_PATCH);
  warnPlaceholders();

  prefs.begin("ecg", false);
  beatReset(beat);
  rulesReset(ruleState);
  buttonReset(button);

  uiBegin();
  storageBegin();
  gpsBegin();
  netBegin();
  notifyBegin();
  samplingBegin();
}

void loop() {
  readSamples();
  netLoop();
  gpsLoop();
  buttonLoop();
  alertLoop();
  storageLoop();
  uiLoop();
}
