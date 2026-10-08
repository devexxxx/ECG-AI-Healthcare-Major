int beepsLeft = 0;
bool beepOn = false;
uint32_t beepOnMs = 0;
uint32_t beepOffMs = 0;
uint32_t beepNext = 0;
int lastCountSecond = -1;
uint32_t lastDrawMs = 0;
uint32_t lastSerialMs = 0;
uint32_t lastSimBeatMs = 0;

void uiBegin() {
  pinMode(PIN_GREEN, OUTPUT);
  pinMode(PIN_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  digitalWrite(PIN_BUZZER, LOW);

  Wire.begin(PIN_SDA, PIN_SCL);
  // 400 kHz makes a full screen refresh take about 25 ms instead of 100 ms
  Wire.setClock(400000);
  // the display is used only if something answers at its address
  Wire.beginTransmission(OLED_ADDRESS);
  bool answered = Wire.endTransmission() == 0;
  oledOk = OLED_ENABLED && answered && oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS);
  Serial.println(oledOk ? "OLED ok" : "OLED not used, status goes to serial and dashboard");
}

void beep(int count, int onMs, int offMs) {
  beepsLeft = count;
  beepOnMs = onMs;
  beepOffMs = offMs;
  beepNext = millis();
}

void buzzerLoop(uint32_t now) {
  // beeps are stepped with millis() so loop() never waits for the buzzer
  if ((int32_t)(now - beepNext) < 0) return;
  if (beepOn) {
    digitalWrite(PIN_BUZZER, LOW);
    beepOn = false;
    beepNext = now + beepOffMs;
  } else if (beepsLeft > 0) {
    digitalWrite(PIN_BUZZER, HIGH);
    beepOn = true;
    beepsLeft--;
    beepNext = now + beepOnMs;
  }
}

void updateIndicators(uint32_t now) {
  // green: short flash on every detected beat
  digitalWrite(PIN_GREEN, (int32_t)(now - greenUntil) < 0 ? HIGH : LOW);
  // red: solid = alert, fast blink = countdown, slow blink = leads off or SD problem.
  // In simulation mode the electrodes are not the signal source, so leads-off does not blink.
  bool red = false;
  if (state == ST_SENDING || state == ST_ALERT) red = true;
  else if (state == ST_COUNTDOWN) red = (now / 125) % 2;
  else if ((leadsOff && !simMode) || !sdOk) red = (now / 500) % 2;
  digitalWrite(PIN_RED, red ? HIGH : LOW);
}

void buttonLoop() {
  bool pressed = digitalRead(PIN_BUTTON) == LOW;
  int event = buttonStep(button, pressed, millis(), state, PANIC_HOLD_MS, CLEAR_HOLD_MS);
  if (event == BTN_PRESS && state == ST_COUNTDOWN) cancelCountdown("local");
  if (event == BTN_PANIC) startPanic();
  if (event == BTN_CLEAR) {
    setState(ST_COOLDOWN);
    beep(1, 60, 0);
  }
}

const char *statusLine() {
  if (leadsOff && !simMode) return "LEADS OFF";
  if (simMode) return "SIM MODE";
  if (!wsUp) return "OFFLINE";
  return "OK";
}

const char *linkFlag() {
  // WS = dashboard link, NET = WiFi only, GSM = the modem is registered
  if (wsUp) return "WS";
  if (wifiUp) return "NET";
  if (modemOk) return "GSM";
  return "--";
}

void drawOled(uint32_t now) {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(0, 0);
  if (state == ST_COUNTDOWN) {
    int left = COUNTDOWN_S - (int)((now - stateSince) / 1000);
    oled.setTextSize(2);
    oled.println("ALERT");
    oled.setTextSize(3);
    oled.println(left < 0 ? 0 : left);
    oled.setTextSize(1);
    oled.println("press to cancel");
    oled.println(alertReason);
  } else if (state == ST_SENDING) {
    oled.setTextSize(2);
    oled.println("SENDING");
    oled.setTextSize(1);
    oled.println(alertReason);
  } else if (state == ST_ALERT) {
    oled.setTextSize(2);
    if (alertSent) {
      oled.println("SENT via");
      oled.println(strcmp(sentVia, "sms") == 0 ? "SMS" : "TG");
    } else {
      oled.println("SEND FAIL");
      oled.printf("retry %d\n", notifyRounds);
    }
    oled.setTextSize(1);
    oled.println("hold 3 s to clear");
  } else {
    int bpm = shownBpm();
    oled.setTextSize(4);
    if (bpm > 0) oled.println(bpm);
    else oled.println("--");
    oled.setTextSize(2);
    oled.println(statusLine());
    oled.setTextSize(1);
    oled.printf("GPS:%s SD:%s %s", gpsFixed() ? "fix" : (haveFix ? "old" : "no"), sdOk ? "ok" : "FAIL", linkFlag());
    if (modemOk) oled.printf(" G%d", modemCsq / 7);
  }
  oled.display();
}

void uiLoop() {
  uint32_t now = millis();
  // one beep per second while the countdown runs
  if (state == ST_COUNTDOWN) {
    int second = (now - stateSince) / 1000;
    if (second != lastCountSecond) {
      lastCountSecond = second;
      beep(1, 100, 0);
    }
  } else {
    lastCountSecond = -1;
  }
  // in simulation mode the green LED blinks at the simulated heart rate the dashboard sends
  if (simMode && simBpm > 0 && now - lastSimBeatMs >= 60000UL / simBpm) {
    lastSimBeatMs = now;
    greenUntil = now + 60;
  }
  buzzerLoop(now);
  updateIndicators(now);

  // the screen is redrawn at most 4 times a second
  if (oledOk && now - lastDrawMs >= 250) {
    lastDrawMs = now;
    drawOled(now);
  }
  // without a display the same status goes to the serial monitor every 5 s
  if (!oledOk && now - lastSerialMs >= 5000) {
    lastSerialMs = now;
    Serial.printf("BPM %d | %s | %s | link %s | GPS %s | SD %s | last notify: %s\n", shownBpm(), statusLine(),
                  stateName(state), linkFlag(), gpsFixed() ? "fix" : (haveFix ? "old" : "no"),
                  sdOk ? "ok" : "FAIL", lastNotify);
  }
}
