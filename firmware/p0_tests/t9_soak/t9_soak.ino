// Gate G0.7: 10 minutes on the power bank (USB unplugged) with WiFi connected and no reset.
// Copy firmware/ecg_monitor/config.h into this folder first. Results are kept in flash and shown by the LEDs.

#include <WiFi.h>
#include <Preferences.h>

#include "config.h"

const int PIN_GREEN = 13;
const int PIN_RED = 14;
const int PIN_BUTTON = 15;
const uint32_t SOAK_SECONDS = 600;

Preferences prefs;
int boots = 0;
int wifiDrops = 0;
bool wasConnected = false;
uint32_t lastTick = 0;

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(PIN_GREEN, OUTPUT);
  pinMode(PIN_RED, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  prefs.begin("soak", false);

  // the result of the run before this boot is printed first
  Serial.printf("\nt9: previous run: boots %d, longest uptime %d s, WiFi drops %d\n", prefs.getInt("boots", 0),
                prefs.getInt("best", 0), prefs.getInt("drops", 0));
  bool passed = prefs.getInt("boots", 0) == 1 && prefs.getInt("best", 0) >= (int)SOAK_SECONDS && prefs.getInt("drops", 0) == 0;
  Serial.println(passed ? "PASS: G0.7 (previous run)" : "previous run did not pass G0.7 (or there was none)");

  // holding the button while powering on starts a fresh test
  if (digitalRead(PIN_BUTTON) == LOW) {
    prefs.clear();
    Serial.println("t9: counters cleared, new test starts now");
    for (int i = 0; i < 6; i++) {
      digitalWrite(PIN_GREEN, i % 2);
      delay(150);
    }
  }
  boots = prefs.getInt("boots", 0) + 1;
  prefs.putInt("boots", boots);
  wifiDrops = prefs.getInt("drops", 0);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void loop() {
  bool connected = WiFi.status() == WL_CONNECTED;
  if (wasConnected && !connected) {
    wifiDrops++;
    prefs.putInt("drops", wifiDrops);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }
  wasConnected = connected;

  uint32_t seconds = millis() / 1000;
  // red on = there was a reset or a WiFi drop; green blinks while running, solid after 10 minutes
  bool clean = boots == 1 && wifiDrops == 0 && connected;
  digitalWrite(PIN_RED, clean ? LOW : HIGH);
  digitalWrite(PIN_GREEN, (seconds >= SOAK_SECONDS && clean) ? HIGH : (millis() / 500) % 2);

  if (millis() - lastTick >= 10000) {
    lastTick = millis();
    if ((int)seconds > prefs.getInt("best", 0)) prefs.putInt("best", seconds);
    Serial.printf("t9: up %lu s, boots %d, WiFi %s, drops %d, heap %u\n", (unsigned long)seconds, boots,
                  connected ? "ok" : "DOWN", wifiDrops, ESP.getFreeHeap());
    if (seconds >= SOAK_SECONDS && clean) Serial.println("PASS: G0.7 10 minutes, no reset, WiFi stayed connected");
  }
  delay(20);
}
