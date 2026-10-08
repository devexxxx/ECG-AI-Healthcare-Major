bool serverChosen = false;
bool wsLost = false;
bool bootSent = false;
uint32_t chosenAt = 0;
uint32_t listenSince = 0;
uint32_t lastWifiTry = 0;
uint32_t lastStatusMs = 0;
uint32_t lastFixSaveMs = 0;
int sentRelayId = 0;

void netBegin() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  // reconnecting is done by hand in netLoop, so the two do not fight
  WiFi.setAutoReconnect(false);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  lastWifiTry = millis();
  ws.onEvent(wsEvent);
}

void gpsBegin() {
  gpsSerial.begin(9600, SERIAL_8N1, PIN_GPS_RX, -1);
  // the last fix from an earlier run is kept in flash
  haveFix = prefs.getBool("fix", false);
  lastLat = prefs.getDouble("lat", 0);
  lastLon = prefs.getDouble("lon", 0);
  savedFixEpoch = prefs.getULong("fixtime", 0);
}

void gpsLoop() {
  while (gpsSerial.available()) gps.encode(gpsSerial.read());
  if (!gps.location.isUpdated() || !gps.location.isValid()) return;
  lastLat = gps.location.lat();
  lastLon = gps.location.lng();
  lastFixMs = millis();
  // write to flash on the first fix and then at most every 5 minutes (flash wear)
  if (!fixThisBoot || millis() - lastFixSaveMs > 300000UL) {
    lastFixSaveMs = millis();
    savedFixEpoch = timeTrusted() ? time(nullptr) : 0;
    prefs.putBool("fix", true);
    prefs.putDouble("lat", lastLat);
    prefs.putDouble("lon", lastLon);
    prefs.putULong("fixtime", savedFixEpoch);
  }
  haveFix = true;
  fixThisBoot = true;
}

void chooseServer(const String &ip, int port) {
  Serial.printf("dashboard at %s:%d\n", ip.c_str(), port);
  ws.begin(ip, port, "/ws/device");
  ws.setReconnectInterval(2000);
  ws.enableHeartbeat(5000, 3000, 2);
  serverChosen = true;
  chosenAt = millis();
}

void dropServer() {
  // back to listening for the discovery broadcast
  ws.disconnect();
  serverChosen = false;
  wsUp = false;
  wsLost = false;
  listenSince = millis();
}

void sendEvent(const char *what, const char *info, const char *src, const char *via) {
  Serial.printf("event %s %s\n", what, info);
  if (!wsUp) return;
  JsonDocument doc;
  doc["t"] = "event";
  doc["what"] = what;
  doc["info"] = info;
  doc["src"] = src;
  if (via[0] != '\0') doc["via"] = via;
  String out;
  serializeJson(doc, out);
  ws.sendTXT(out);
}

void sendBatch(uint32_t first, const int *samples, int count, int lo) {
  if (!wsUp || count == 0) return;
  // built with snprintf in a fixed buffer, this runs 10 times a second
  char out[320];
  int len = snprintf(out, sizeof(out), "{\"t\":\"ecg\",\"n\":%lu,\"lo\":%d,\"s\":[", (unsigned long)first, lo ? 1 : 0);
  for (int i = 0; i < count; i++) {
    len += snprintf(out + len, sizeof(out) - len, i == 0 ? "%d" : ",%d", samples[i]);
  }
  snprintf(out + len, sizeof(out) - len, "]}");
  ws.sendTXT(out);
}

void sendStatus() {
  JsonDocument doc;
  doc["t"] = "status";
  doc["bpm"] = shownBpm();
  doc["leads"] = leadsOff ? 0 : 1;
  doc["lat"] = lastLat;
  doc["lon"] = lastLon;
  // -1 = never had a fix, -2 = old fix with unknown age
  long age = fixAgeSeconds();
  doc["fix_age"] = !haveFix ? -1 : (age < 0 ? -2 : age);
  doc["sd"] = sdOk ? 1 : 0;
  doc["oled"] = oledOk ? 1 : 0;
  doc["csq"] = (int)modemCsq;
  doc["gsm"] = (const char *)gsmText;
  doc["state"] = stateName(state);
  doc["sim"] = simMode ? 1 : 0;
  doc["up"] = millis() / 1000;
  doc["notify"] = lastNotify;
  doc["drop"] = (uint32_t)droppedSamples;
  String out;
  serializeJson(doc, out);
  ws.sendTXT(out);
}

void handleCommand(uint8_t *payload, size_t length) {
  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) return;
  const char *cmd = doc["c"] | "";

  if (strcmp(cmd, "alert") == 0) {
    // an alert is accepted only when nothing else is going on
    if (state == ST_IDLE) {
      startCountdown(doc["level"] | "CRITICAL", doc["reason"] | "alert", doc["demo"] | true, "dash");
    } else {
      sendEvent("alert_ignored", stateName(state), "dash", "");
    }
  } else if (strcmp(cmd, "cancel") == 0) {
    if (state == ST_COUNTDOWN) cancelCountdown("dash");
  } else if (strcmp(cmd, "sim") == 0) {
    simMode = doc["on"] | false;
    lastSimMsg = millis();
  } else if (strcmp(cmd, "bpm") == 0) {
    simBpm = doc["v"] | 0;
    lastSimMsg = millis();
  } else if (strcmp(cmd, "notify_result") == 0) {
    int id = doc["id"] | -1;
    bool ok = doc["ok"] | false;
    if (id == relayId) relayAnswer = ok ? 1 : 2;
  }
}

void wsEvent(WStype_t type, uint8_t *payload, size_t length) {
  if (type == WStype_CONNECTED) {
    wsUp = true;
    Serial.println("dashboard link up");
    if (!bootSent) {
      bootSent = true;
      sendEvent("boot", "ecg_monitor", "local", "");
    }
  } else if (type == WStype_DISCONNECTED) {
    if (wsUp) wsLost = true;
    wsUp = false;
  } else if (type == WStype_TEXT) {
    handleCommand(payload, length);
  }
}

void netLoop() {
  uint32_t now = millis();
  // simulation mode ends by itself when the dashboard stops talking for 5 s
  if (simMode && now - lastSimMsg > SIM_TIMEOUT_S * 1000UL) simMode = false;

  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !wifiUp) {
    // WiFi just came up: start listening for the dashboard and ask for the time
    Serial.printf("WiFi up, IP %s\n", WiFi.localIP().toString().c_str());
    udp.begin(DISCOVERY_PORT);
    configTime(19800, 0, "pool.ntp.org", "time.google.com");
    listenSince = now;
  }
  if (!up && wifiUp) {
    Serial.println("WiFi lost");
    udp.stop();
    dropServer();
  }
  wifiUp = up;
  if (!up) {
    if (now - lastWifiTry > 15000) {
      lastWifiTry = now;
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
    return;
  }

  if (!serverChosen) {
    char text[32];
    if (udp.parsePacket() > 0) {
      int n = udp.read(text, sizeof(text) - 1);
      text[n > 0 ? n : 0] = '\0';
      // the dashboard address is the sender of the broadcast, so no IP is typed anywhere
      if (strncmp(text, "ECG_SERVER ", 11) == 0) chooseServer(udp.remoteIP().toString(), atoi(text + 11));
    } else if (strlen(SERVER_IP) > 0 && now - listenSince > 10000) {
      chooseServer(SERVER_IP, SERVER_PORT);
    }
    return;
  }

  ws.loop();
  // a lost link, or a server that never answered, sends the device back to listening
  if (wsLost || (!wsUp && now - chosenAt > 6000)) {
    Serial.println("dashboard link down");
    dropServer();
    return;
  }
  if (!wsUp) return;

  if (now - lastStatusMs >= 1000) {
    lastStatusMs = now;
    sendStatus();
  }
  // ladder step 1: the notify task asked for the text to be sent through the dashboard
  if (relayWanted && sentRelayId != relayId) {
    sentRelayId = relayId;
    JsonDocument doc;
    doc["t"] = "notify";
    doc["id"] = (int)relayId;
    doc["level"] = alertLevel;
    doc["text"] = notifyText;
    String out;
    serializeJson(doc, out);
    ws.sendTXT(out);
  }
}
