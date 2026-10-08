// Gate G0.6: green LED, red LED, buzzer and button each work

const int PIN_GREEN = 13;
const int PIN_RED = 14;
const int PIN_BUZZER = 4;
const int PIN_BUTTON = 15;

bool wasPressed = false;
uint32_t pressedAt = 0;
uint32_t lastStep = 0;
int step = 0;

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(PIN_GREEN, OUTPUT);
  pinMode(PIN_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  Serial.println("t6: green 1 s, red 1 s, short beep, repeating. Press and hold the button to see the times.");
}

void loop() {
  // one step per second: green, red, beep, pause
  if (millis() - lastStep >= 1000) {
    lastStep = millis();
    step = (step + 1) % 4;
    digitalWrite(PIN_GREEN, step == 0);
    digitalWrite(PIN_RED, step == 1);
    if (step == 2) {
      digitalWrite(PIN_BUZZER, HIGH);
      delay(80);
      digitalWrite(PIN_BUZZER, LOW);
    }
    Serial.printf("t6: step %s\n", step == 0 ? "GREEN" : step == 1 ? "RED" : step == 2 ? "BEEP" : "pause");
  }

  // the button connects GPIO15 to GND, so LOW means pressed
  bool pressed = digitalRead(PIN_BUTTON) == LOW;
  if (pressed && !wasPressed) {
    pressedAt = millis();
    Serial.println("t6: button PRESSED");
  }
  if (!pressed && wasPressed) Serial.printf("t6: button RELEASED after %lu ms\n", millis() - pressedAt);
  wasPressed = pressed;
  delay(10);
}
