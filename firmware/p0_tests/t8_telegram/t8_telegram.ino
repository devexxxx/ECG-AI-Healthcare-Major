// Gate G0.9: the ESP32 itself sends a Telegram message over the hotspot.
// Copy firmware/ecg_monitor/config.h into this folder first (it holds WiFi and Telegram settings).

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

#include "config.h"

void setup() {
  Serial.begin(115200);
  delay(500);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("t8: connecting to WiFi");
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nFAIL: no WiFi (hotspot on? 2.4 GHz? WIFI_SSID / WIFI_PASS in config.h?)");
    return;
  }
  Serial.printf("\nt8: WiFi ok, IP %s, free heap %u\n", WiFi.localIP().toString().c_str(), ESP.getFreeHeap());

  // same request as the main firmware: HTTPS POST without certificate check
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setConnectTimeout(8000);
  http.setTimeout(8000);
  http.begin(client, String("https://api.telegram.org/bot") + TELEGRAM_BOT_TOKEN + "/sendMessage");
  http.addHeader("Content-Type", "application/json");
  JsonDocument doc;
  doc["chat_id"] = TELEGRAM_CHAT_IDS[0];
  doc["text"] = "[DEMO] Telegram test sent directly from the ESP32";
  String body;
  serializeJson(doc, body);
  int code = http.POST(body);
  String answer = code > 0 ? http.getString() : String("(no answer)");
  http.end();

  Serial.printf("t8: HTTP %d, free heap %u\n", code, ESP.getFreeHeap());
  Serial.println(answer.substring(0, 200));
  bool ok = code == 200 && answer.indexOf("\"ok\":true") >= 0;
  Serial.println(ok ? "PASS: G0.9 if the message is on the phone" : "FAIL: G0.9 (401 = bad token, 400 = bad chat id or Start not pressed, negative = no internet)");
}

void loop() {
}
