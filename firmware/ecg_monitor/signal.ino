int batch[BATCH_SIZE];
int batchCount = 0;
uint32_t batchStart = 0;
int batchLeadOff = 0;

int secCount = 0;
int secMin = 4095;
int secMax = 0;
bool secLeadOff = false;

void flushBatch() {
  sendBatch(batchStart, batch, batchCount, batchLeadOff);
  batchCount = 0;
}

void secondTick() {
  leadsOff = secLeadOff;
  bpmNow = leadsOff ? 0 : beatBpm(beat, lastIndex);
  if (leadsOff) beatReset(beat);

  // local rules run only in live mode, with leads on and no alert active or cooling down
  bool rulesOn = state == ST_IDLE && !simMode && !leadsOff;
  if (!rulesOn) {
    rulesReset(ruleState);
  } else {
    int rule = rulesStep(ruleState, ruleLimits, bpmNow, secMin, secMax);
    if (rule != RULE_NONE) startCountdown("CRITICAL", ruleReason(rule), DEMO_ONLY_FIRST_CONTACT, "local");
  }
  secCount = 0;
  secMin = 4095;
  secMax = 0;
  secLeadOff = false;
}

void readSamples() {
  Sample s;
  int taken = 0;
  // take at most 150 samples per pass so the rest of loop() keeps running
  while (taken < 150 && xQueueReceive(sampleQueue, &s, 0) == pdTRUE) {
    taken++;
    // a jump in the index means samples were dropped: close the batch so its n stays true
    if (batchCount > 0 && s.index != batchStart + batchCount) flushBatch();
    if (batchCount == 0) {
      batchStart = s.index;
      batchLeadOff = 0;
    }
    batch[batchCount++] = s.adc;
    batchLeadOff |= s.leadOff;
    if (batchCount == BATCH_SIZE) flushBatch();

    lastIndex = s.index;
    if (s.leadOff) {
      secLeadOff = true;
    } else if (beatAddSample(beat, s.adc, s.index) && !simMode) {
      greenUntil = millis() + 60;
    }
    if (s.adc < secMin) secMin = s.adc;
    if (s.adc > secMax) secMax = s.adc;
    storeSample(s.adc);
    if (++secCount >= SAMPLE_RATE) secondTick();
  }
}
