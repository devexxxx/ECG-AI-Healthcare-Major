// Gate G0.4: NMEA text from the NEO-6M at 9600 baud, and a fix with 4 or more satellites outdoors

#include <TinyGPS++.h>

TinyGPSPlus gps;
HardwareSerial gpsSerial(2);
uint32_t lastReport = 0;

void setup() {
  Serial.begin(115200);
  delay(500);
  // GPS TX goes to GPIO16, the GPS RX pin is not connected
  gpsSerial.begin(9600, SERIAL_8N1, 16, -1);
  Serial.println("t4: raw NMEA is echoed below, a summary is printed every 5 s");
}

void loop() {
  while (gpsSerial.available()) {
    char c = gpsSerial.read();
    Serial.write(c);
    gps.encode(c);
  }
  if (millis() - lastReport < 5000) return;
  lastReport = millis();
  Serial.printf("\nt4: chars %lu, good sentences %lu, bad checksums %lu, satellites %d\n",
                (unsigned long)gps.charsProcessed(), (unsigned long)gps.passedChecksum(),
                (unsigned long)gps.failedChecksum(), (int)gps.satellites.value());
  if (gps.charsProcessed() < 10) {
    Serial.println("FAIL so far: no data, check GPS TX -> GPIO16 and 3V3 power");
  } else if (gps.location.isValid() && gps.satellites.value() >= 4) {
    Serial.printf("t4: lat %.6f lon %.6f\n", gps.location.lat(), gps.location.lng());
    Serial.println("PASS: G0.4 fix with 4 or more satellites");
  } else {
    Serial.println("t4: NMEA ok, no fix yet (needs open sky, can take several minutes)");
  }
}
