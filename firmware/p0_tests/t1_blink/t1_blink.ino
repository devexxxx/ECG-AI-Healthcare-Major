// Gate G0.1: board alive, serial works, core version is 3.3.12

const int PIN_LED = 2;

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(PIN_LED, OUTPUT);
  Serial.printf("\nt1: chip %s rev %d, %d MHz, flash %d MB\n", ESP.getChipModel(), ESP.getChipRevision(),
                ESP.getCpuFreqMHz(), (int)(ESP.getFlashChipSize() / (1024 * 1024)));
  Serial.printf("t1: Arduino ESP32 core %d.%d.%d\n", ESP_ARDUINO_VERSION_MAJOR, ESP_ARDUINO_VERSION_MINOR,
                ESP_ARDUINO_VERSION_PATCH);
  bool right = ESP_ARDUINO_VERSION_MAJOR == 3 && ESP_ARDUINO_VERSION_MINOR == 3 && ESP_ARDUINO_VERSION_PATCH == 12;
  Serial.println(right ? "PASS: G0.1 core is 3.3.12" : "CHECK: core is not 3.3.12, write the printed version in NOTES.md");
}

void loop() {
  // the onboard LED blinks once per second
  digitalWrite(PIN_LED, HIGH);
  delay(500);
  digitalWrite(PIN_LED, LOW);
  delay(500);
  Serial.printf("t1: alive %lu s\n", millis() / 1000);
}
