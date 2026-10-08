// Gates G1.1a / G1.1b: ADC min/max over 5 s on GPIO34 and the two leads-off flags.
// With electrodes on a person run from the power bank only (USB unplugged) and read the LEDs.

const int PIN_ECG = 34;
const int PIN_LO_PLUS = 32;
const int PIN_LO_MINUS = 33;
const int PIN_GREEN = 13;
const int PIN_RED = 14;

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(PIN_LO_PLUS, INPUT);
  pinMode(PIN_LO_MINUS, INPUT);
  pinMode(PIN_GREEN, OUTPUT);
  pinMode(PIN_RED, OUTPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_ECG, ADC_11db);
}

void loop() {
  int low = 4095;
  int high = 0;
  long sum = 0;
  // 1250 samples at 4 ms = 5 s
  for (int i = 0; i < 1250; i++) {
    int v = analogRead(PIN_ECG);
    if (v < low) low = v;
    if (v > high) high = v;
    sum += v;
    delay(4);
  }
  int loPlus = digitalRead(PIN_LO_PLUS);
  int loMinus = digitalRead(PIN_LO_MINUS);
  Serial.printf("t7: min=%d max=%d spread=%d mean=%ld LO+=%d LO-=%d\n", low, high, high - low, sum / 1250, loPlus,
                loMinus);
  // red = a lead is off, green = leads on and the signal moves by more than 200 counts
  digitalWrite(PIN_RED, loPlus || loMinus);
  digitalWrite(PIN_GREEN, !loPlus && !loMinus && (high - low) > 200);
}
