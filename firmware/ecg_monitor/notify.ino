bool placeholder(const char *text) {
  return text[0] == '\0' || strstr(text, "PUT_") != NULL;
}

bool relayStep() {
  // loop() owns the WebSocket, so this task only raises a flag and waits for the answer
  relayAnswer = 0;
  relayId = relayId + 1;
  relayWanted = true;
  uint32_t start = millis();
  while (millis() - start < RELAY_WAIT_S * 1000UL && wsUp && relayAnswer == 0) {
    vTaskDelay(pdMS_TO_TICKS(50));
  }
  relayWanted = false;
  return relayAnswer == 1;
}

bool telegramSend(const char *chatId, const char *text) {
  WiFiClientSecure client;
  // no certificate check here, this limit is stated in the report
  client.setInsecure();
  HTTPClient http;
  http.setConnectTimeout(TELEGRAM_TIMEOUT_S * 1000);
  http.setTimeout(TELEGRAM_TIMEOUT_S * 1000);
  String url = String("https://api.telegram.org/bot") + TELEGRAM_BOT_TOKEN + "/sendMessage";
  if (!http.begin(client, url)) return false;
  http.addHeader("Content-Type", "application/json");
  JsonDocument doc;
  doc["chat_id"] = chatId;
  doc["text"] = text;
  String body;
  serializeJson(doc, body);
  int code = http.POST(body);
  String answer = code > 0 ? http.getString() : String("");
  http.end();
  Serial.printf("telegram direct: HTTP %d\n", code);
  return code == 200 && answer.indexOf("\"ok\":true") >= 0;
}

bool telegramStep(const char *text) {
  int sent = 0;
  for (int i = 0; i < 3; i++) {
    if (placeholder(TELEGRAM_CHAT_IDS[i])) continue;
    if (telegramSend(TELEGRAM_CHAT_IDS[i], text)) sent++;
    if (DEMO_ONLY_FIRST_CONTACT) break;
  }
  return sent > 0;
}

String atCommand(const char *cmd, uint32_t waitMs) {
  // old bytes are thrown away first, so an earlier OK is never read as this answer
  while (simSerial.available()) simSerial.read();
  simSerial.print(cmd);
  simSerial.print("\r");
  String reply;
  uint32_t start = millis();
  while (millis() - start < waitMs) {
    while (simSerial.available()) reply += (char)simSerial.read();
    if (reply.indexOf("OK\r\n") >= 0 || reply.indexOf("ERROR") >= 0) break;
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  return reply;
}

void modemCheck() {
  modemOk = false;
  modemCsq = 0;
  gsmText = "off";
  if (atCommand("AT", 1000).indexOf("OK") < 0) {
    // no answer: pulse the reset line low for 100 ms, the next check is in 60 s
    pinMode(PIN_SIM_RST, OUTPUT);
    digitalWrite(PIN_SIM_RST, LOW);
    vTaskDelay(pdMS_TO_TICKS(100));
    pinMode(PIN_SIM_RST, INPUT);
    return;
  }
  atCommand("ATE0", 1000);
  atCommand("AT+CMGF=1", 1000);
  if (atCommand("AT+CPIN?", 2000).indexOf("READY") < 0) {
    gsmText = "nosim";
    return;
  }
  String csq = atCommand("AT+CSQ", 1000);
  int at = csq.indexOf("+CSQ:");
  int level = at >= 0 ? csq.substring(at + 5).toInt() : 0;
  // 99 means "not known"
  modemCsq = level == 99 ? 0 : level;
  String reg = atCommand("AT+CREG?", 1000);
  bool registered = reg.indexOf("0,1") >= 0 || reg.indexOf("0,5") >= 0;
  if (modemCsq >= 10 && registered) {
    modemOk = true;
    gsmText = "ok";
  } else {
    gsmText = "nosignal";
  }
}

bool smsSend(const char *number, const char *text) {
  while (simSerial.available()) simSerial.read();
  simSerial.print("AT+CMGS=\"");
  simSerial.print(number);
  simSerial.print("\"\r");
  // the modem answers with '>' when it is ready for the text
  String reply;
  uint32_t start = millis();
  while (millis() - start < 5000 && reply.indexOf('>') < 0 && reply.indexOf("ERROR") < 0) {
    while (simSerial.available()) reply += (char)simSerial.read();
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  if (reply.indexOf('>') < 0) {
    simSerial.write(0x1B);
    return false;
  }
  simSerial.print(text);
  simSerial.write(0x1A);
  reply = "";
  start = millis();
  while (millis() - start < SMS_WAIT_S * 1000UL) {
    while (simSerial.available()) reply += (char)simSerial.read();
    if (reply.indexOf("+CMGS:") >= 0) return true;
    if (reply.indexOf("ERROR") >= 0) return false;
    vTaskDelay(pdMS_TO_TICKS(20));
  }
  // no answer in time: ESC leaves text mode so the next try starts clean
  simSerial.write(0x1B);
  return false;
}

bool smsStep(const char *text) {
  int sent = 0;
  for (int i = 0; i < 3; i++) {
    // an empty entry or the +91XXXXXXXXXX example is skipped
    if (placeholder(CONTACTS[i]) || strchr(CONTACTS[i], 'X') != NULL) continue;
    for (int attempt = 0; attempt <= SMS_RETRIES; attempt++) {
      if (smsSend(CONTACTS[i], text)) {
        sent++;
        break;
      }
    }
    if (DEMO_ONLY_FIRST_CONTACT) break;
  }
  return sent > 0;
}

bool runLadder(const char *text) {
  // one round: stop at the first step that reports success
  if (NOTIFY_VIA_DASHBOARD && wsUp && relayStep()) {
    strlcpy(sentVia, "dashboard", sizeof(sentVia));
    return true;
  }
  if (NOTIFY_VIA_TELEGRAM_DIRECT && wifiUp && !placeholder(TELEGRAM_BOT_TOKEN) && telegramStep(text)) {
    strlcpy(sentVia, "telegram", sizeof(sentVia));
    return true;
  }
  if (NOTIFY_VIA_SMS && modemOk && smsStep(text)) {
    strlcpy(sentVia, "sms", sizeof(sentVia));
    return true;
  }
  return false;
}

void notifyTaskLoop(void *unused) {
  char text[200];
  int taken = 0;
  bool modemTried = false;
  uint32_t lastModemTry = 0;
  for (;;) {
    // the modem is checked once at boot and again every 60 s while it is not ready
    if (NOTIFY_VIA_SMS && !modemOk && (!modemTried || millis() - lastModemTry > 60000UL)) {
      modemTried = true;
      lastModemTry = millis();
      modemCheck();
    }
    if (roundId != taken) {
      taken = roundId;
      strlcpy(text, notifyText, sizeof(text));
      doneOk = runLadder(text);
      doneId = taken;
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

void notifyBegin() {
  if (NOTIFY_VIA_SMS) simSerial.begin(9600, SERIAL_8N1, PIN_SIM_RX, PIN_SIM_TX);
  // own task on core 0 with a big stack for TLS, so sampling never waits for a message
  xTaskCreatePinnedToCore(notifyTaskLoop, "notify", 16384, NULL, 1, NULL, 0);
}
