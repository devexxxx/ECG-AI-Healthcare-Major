// Gate G0.2 (first half): I2C scan, then text with the Adafruit SSD1306 driver

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

Adafruit_SSD1306 oled(128, 64, &Wire, -1);
int found = 0;

void setup() {
  Serial.begin(115200);
  delay(500);
  Wire.begin(21, 22);

  // every address that answers is printed
  for (int addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("t2: device at 0x%02X\n", addr);
      if (addr == 0x3C || addr == 0x3D) found = addr;
    }
  }
  if (found == 0) {
    Serial.println("FAIL: no OLED at 0x3C or 0x3D, check SDA=21 SCL=22 VCC=3V3 GND");
    return;
  }
  // begin() returning true does not prove the panel works, only the eyes can
  bool started = oled.begin(SSD1306_SWITCHCAPVCC, found);
  Serial.printf("t2: begin() returned %d\n", started);
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(2);
  oled.setCursor(0, 0);
  oled.println("OLED OK");
  oled.setTextSize(1);
  oled.printf("address 0x%02X", found);
  oled.display();
  Serial.println("t2: look at the screen. Text 'OLED OK' visible = PASS G0.2. Blank = run t2b_oled_raw");
}

void loop() {
  // the screen inverts every second so a faint panel is easier to notice
  if (found == 0) return;
  oled.invertDisplay(true);
  delay(1000);
  oled.invertDisplay(false);
  delay(1000);
}
