const int RING_FILES = 10;
const uint32_t SEGMENT_SAMPLES = 30UL * SAMPLE_RATE;

File logFile;
char sdBuf[4096];
int sdLen = 0;
int sdBuffered = 0;
int ringSlot = -1;
int ringSegments = 0;
uint32_t segSamples = 0;
int evtNum = 0;
int postLeft = 0;
bool evtOpen = false;
uint32_t lastMountTry = 0;

void ringName(char *out, size_t size, int slot) {
  snprintf(out, size, "/ring_%02d.csv", slot);
}

void evtName(char *out, size_t size, int num, const char *part) {
  // the event number is always 3 digits in every file name
  snprintf(out, size, "/evt_%03d_%s", num, part);
}

void openSegment(const char *name) {
  SD.remove(name);
  logFile = SD.open(name, FILE_WRITE);
  if (!logFile) {
    sdOk = false;
    return;
  }
  uint32_t epoch = timeTrusted() ? (uint32_t)time(nullptr) : 0;
  logFile.printf("fs=%d,start_ms=%lu,epoch=%lu\n", SAMPLE_RATE, (unsigned long)millis(), (unsigned long)epoch);
  segSamples = 0;
}

void nextSegment() {
  char name[32];
  if (logFile) logFile.close();
  // after an event the next two segments belong to it, then the ring carries on
  if (postLeft > 0) {
    evtName(name, sizeof(name), evtNum, postLeft == 2 ? "post1.csv" : "post2.csv");
    postLeft--;
  } else {
    evtOpen = false;
    ringSlot = (ringSlot + 1) % RING_FILES;
    ringSegments++;
    ringName(name, sizeof(name), ringSlot);
  }
  openSegment(name);
}

void mountSd() {
  SPI.begin(PIN_SD_SCK, PIN_SD_MISO, PIN_SD_MOSI, PIN_SD_CS);
  sdOk = SD.begin(PIN_SD_CS, SPI, 4000000);
  Serial.println(sdOk ? "SD ok" : "SD FAIL");
  if (sdOk) {
    postLeft = 0;
    evtOpen = false;
    sdLen = 0;
    sdBuffered = 0;
    nextSegment();
  }
}

void storageBegin() {
  mountSd();
  lastMountTry = millis();
}

void writeBuffered() {
  if (!sdOk || sdLen == 0) return;
  if (logFile.write((const uint8_t *)sdBuf, sdLen) != (size_t)sdLen) {
    Serial.println("SD write failed");
    logFile.close();
    sdOk = false;
  } else {
    logFile.flush();
  }
  segSamples += sdBuffered;
  sdLen = 0;
  sdBuffered = 0;
  if (sdOk && segSamples >= SEGMENT_SAMPLES) nextSegment();
}

void storeSample(int adc) {
  if (!sdOk) return;
  if (sdLen > (int)sizeof(sdBuf) - 8) writeBuffered();
  sdLen += snprintf(sdBuf + sdLen, sizeof(sdBuf) - sdLen, "%d\n", adc);
  sdBuffered++;
}

void storageLoop() {
  if (!sdOk) {
    // the device keeps running without the card and looks for it again every 30 s
    if (millis() - lastMountTry > 30000UL) {
      lastMountTry = millis();
      SD.end();
      mountSd();
    }
    return;
  }
  // one write and flush per second of signal
  if (sdBuffered >= SAMPLE_RATE) writeBuffered();
}

void writeMeta(const char *name, const char *reason) {
  File meta = SD.open(name, FILE_WRITE);
  if (!meta) return;
  char when[24] = "unknown";
  if (timeTrusted()) {
    time_t now = time(nullptr);
    strftime(when, sizeof(when), "%Y-%m-%d %H:%M:%S", localtime(&now));
  }
  meta.printf("event=%03d\ntime=%s\nuptime_ms=%lu\nreason=%s\nbpm=%d\n", evtNum, when, (unsigned long)millis(),
              reason, shownBpm());
  if (haveFix) meta.printf("lat=%.5f\nlon=%.5f\nfix_age_s=%ld\n", lastLat, lastLon, fixAgeSeconds());
  else meta.printf("location=unavailable\n");
  meta.close();
}

void lockSdEvent(const char *reason) {
  if (!sdOk) return;
  char from[32];
  char to[32];
  // a second alert while the first one is still recording only adds a note to its meta file
  if (evtOpen) {
    evtName(to, sizeof(to), evtNum, "meta.txt");
    File meta = SD.open(to, FILE_APPEND);
    if (meta) {
      meta.printf("note=%lu ms %s\n", (unsigned long)millis(), reason);
      meta.close();
    }
    return;
  }

  writeBuffered();
  if (!sdOk) return;
  logFile.close();
  evtNum = prefs.getInt("evt", 0) % 999 + 1;
  prefs.putInt("evt", evtNum);

  // rename is instant: the segment being written becomes pre1, the one before it pre2
  ringName(from, sizeof(from), ringSlot);
  evtName(to, sizeof(to), evtNum, "pre1.csv");
  SD.remove(to);
  SD.rename(from, to);
  evtName(to, sizeof(to), evtNum, "pre2.csv");
  SD.remove(to);
  ringName(from, sizeof(from), (ringSlot + RING_FILES - 1) % RING_FILES);
  if (ringSegments >= 2 && SD.exists(from)) SD.rename(from, to);

  evtName(to, sizeof(to), evtNum, "meta.txt");
  SD.remove(to);
  writeMeta(to, reason);
  Serial.printf("SD event %03d locked\n", evtNum);
  evtOpen = true;
  postLeft = 2;
  nextSegment();
}
