// Gate G0.3: SD mounts, a 1 KB file is written and read back with 0 wrong bytes

#include <SPI.h>
#include <SD.h>

void setup() {
  Serial.begin(115200);
  delay(500);
  SPI.begin(18, 19, 23, 5);
  if (!SD.begin(5, SPI, 4000000)) {
    Serial.println("FAIL: SD did not mount (card inserted? FAT format? 5 V on the module? CS=5)");
    return;
  }
  Serial.printf("t3: card type %d, size %llu MB\n", SD.cardType(), SD.cardSize() / (1024 * 1024));

  // write 1024 known bytes
  File f = SD.open("/t3_test.bin", FILE_WRITE);
  for (int i = 0; i < 1024; i++) f.write((uint8_t)(i * 7 + 3));
  f.close();

  // read them back and count the differences
  f = SD.open("/t3_test.bin", FILE_READ);
  int wrong = 0;
  int count = 0;
  while (f.available()) {
    if (f.read() != (uint8_t)(count * 7 + 3)) wrong++;
    count++;
  }
  f.close();
  SD.remove("/t3_test.bin");
  Serial.printf("t3: read %d bytes, wrong bytes %d\n", count, wrong);
  Serial.println(count == 1024 && wrong == 0 ? "PASS: G0.3 SD write/read" : "FAIL: G0.3");
}

void loop() {
}
