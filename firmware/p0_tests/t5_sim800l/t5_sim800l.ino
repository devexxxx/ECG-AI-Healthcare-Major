// Gate G0.5a: SIM800L answers (AT, ATI, supply voltage).
// Gate G0.5b (optional, needs a SIM): CPIN READY, CSQ >= 10, CREG 0,1 or 0,5, test SMS.

// leave the placeholder to skip the SMS, or write your own number here for G0.5b (do not share the edited file)
const char *TEST_NUMBER = "+91XXXXXXXXXX";

HardwareSerial simSerial(1);

String at(const char *cmd, uint32_t waitMs) {
  while (simSerial.available()) simSerial.read();
  simSerial.print(cmd);
  simSerial.print("\r");
  String reply;
  uint32_t start = millis();
  while (millis() - start < waitMs) {
    while (simSerial.available()) reply += (char)simSerial.read();
    if (reply.indexOf("OK\r\n") >= 0 || reply.indexOf("ERROR") >= 0) break;
  }
  reply.trim();
  Serial.printf("%s -> %s\n", cmd, reply.c_str());
  return reply;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  // ESP RX1 = GPIO25 (from SIM TXD), ESP TX1 = GPIO26 (through the divider to SIM RXD)
  simSerial.begin(9600, SERIAL_8N1, 25, 26);
  delay(3000);

  // the first ATs let the module find the baud rate, then it is fixed at 9600 once
  at("AT", 1000);
  bool alive = at("AT", 1000).indexOf("OK") >= 0;
  at("ATE0", 1000);
  at("AT+IPR=9600", 1000);
  at("AT&W", 1000);
  bool isSim800 = at("ATI", 1000).indexOf("SIM800") >= 0;

  // AT+CBC answers "+CBC: 0,<percent>,<millivolts>"
  String cbc = at("AT+CBC", 1000);
  int mv = cbc.substring(cbc.lastIndexOf(',') + 1).toInt();
  Serial.printf("t5: supply %d mV\n", mv);
  bool supplyOk = mv >= 3700 && mv <= 4300;
  Serial.println(alive && isSim800 && supplyOk ? "PASS: G0.5a module alive" : "FAIL: G0.5a (no answer, wrong module or supply outside 3700-4300 mV)");

  bool pin = at("AT+CPIN?", 2000).indexOf("READY") >= 0;
  if (!pin) {
    Serial.println("G0.5b: no usable SIM (CPIN not READY). SMS stays unverified.");
    return;
  }
  String csqText = at("AT+CSQ", 1000);
  int csq = csqText.substring(csqText.indexOf(':') + 1).toInt();
  String reg = at("AT+CREG?", 1000);
  bool registered = reg.indexOf("0,1") >= 0 || reg.indexOf("0,5") >= 0;
  Serial.printf("t5: CSQ %d, registered %d\n", csq, registered);
  if (csq < 10 || csq == 99 || !registered) {
    Serial.println("FAIL: G0.5b no network (move near a window, check the antenna, wait a minute and reset)");
    return;
  }
  if (strchr(TEST_NUMBER, 'X') != NULL) {
    Serial.println("G0.5b: network ok, SMS skipped because TEST_NUMBER is still the placeholder");
    return;
  }

  // text mode SMS: number, wait for '>', text, Ctrl+Z
  at("AT+CMGF=1", 1000);
  simSerial.printf("AT+CMGS=\"%s\"\r", TEST_NUMBER);
  delay(1000);
  simSerial.print("[DEMO] SIM800L test from ECG monitor");
  simSerial.write(0x1A);
  String reply;
  uint32_t start = millis();
  while (millis() - start < 20000 && reply.indexOf("+CMGS:") < 0 && reply.indexOf("ERROR") < 0) {
    while (simSerial.available()) reply += (char)simSerial.read();
  }
  Serial.println(reply);
  Serial.println(reply.indexOf("+CMGS:") >= 0 ? "PASS: G0.5b SMS accepted by the network, check the phone" : "FAIL: G0.5b SMS not sent");
}

void loop() {
}
