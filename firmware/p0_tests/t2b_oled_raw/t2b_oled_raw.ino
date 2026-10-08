// Gate G0.2 (second half): raw controller commands, no display library.
// If the panel stays dark through every step, the module is faulty (replace it).

#include <Wire.h>

const int OLED_ADDR = 0x3C;

bool command(uint8_t c) {
  // 0x00 = the next byte is a command; the ACK is printed for every one
  Wire.beginTransmission(OLED_ADDR);
  Wire.write(0x00);
  Wire.write(c);
  bool ack = Wire.endTransmission() == 0;
  Serial.printf("  cmd 0x%02X %s\n", c, ack ? "ACK" : "NO ACK");
  return ack;
}

void fillRam(uint8_t value, int columnOffset) {
  // page addressing works on both SSD1306 and SH1106 (the SH1106 needs column offset 2)
  for (int page = 0; page < 8; page++) {
    command(0xB0 + page);
    command(0x00 + (columnOffset & 0x0F));
    command(0x10);
    for (int col = 0; col < 132; col += 16) {
      Wire.beginTransmission(OLED_ADDR);
      Wire.write(0x40);
      for (int i = 0; i < 16; i++) Wire.write(value);
      Wire.endTransmission();
    }
  }
}

void initPanel(bool chargePump) {
  command(0xAE);
  command(0xD5); command(0x80);
  command(0xA8); command(0x3F);
  command(0xD3); command(0x00);
  command(0x40);
  // 0x8D 0x14 turns on the built-in charge pump that makes the panel voltage
  command(0x8D); command(chargePump ? 0x14 : 0x10);
  command(0xA1);
  command(0xC8);
  command(0xDA); command(0x12);
  command(0x81); command(0xFF);
  command(0xD9); command(0xF1);
  command(0xDB); command(0x40);
  command(0xA4);
  command(0xA6);
  command(0xAF);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Wire.begin(21, 22);
  Wire.beginTransmission(OLED_ADDR);
  Serial.printf("\nt2b: address 0x%02X %s\n", OLED_ADDR, Wire.endTransmission() == 0 ? "answers" : "DOES NOT ANSWER");
}

void loop() {
  Serial.println("STEP 1: SSD1306 init with charge pump, then ALL PIXELS ON (0xA5). Whole screen should be white.");
  initPanel(true);
  command(0xA5);
  delay(6000);

  Serial.println("STEP 2: back to RAM display (0xA4), RAM filled with 0xFF. Whole screen should be white.");
  command(0xA4);
  fillRam(0xFF, 0);
  delay(6000);

  Serial.println("STEP 3: SH1106 style: charge pump command 0xAD 0x8B, column offset 2, RAM filled.");
  command(0xAD); command(0x8B);
  fillRam(0xFF, 2);
  delay(6000);

  Serial.println("STEP 4: stripes (0xAA) and inverted display. Any light at all means the panel is alive.");
  fillRam(0xAA, 0);
  command(0xA7);
  delay(6000);
  command(0xA6);

  Serial.println("RESULT: screen lit in some step -> note the step number in NOTES.md (init problem, G0.2 fixable).");
  Serial.println("RESULT: dark in every step while every cmd shows ACK -> panel is dead, replace the module.\n");
  delay(3000);
}
