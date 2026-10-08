void samplingTask(void *unused) {
  TickType_t lastWake = xTaskGetTickCount();
  Sample s;
  uint32_t index = 0;
  for (;;) {
    // one sample every 4 ms gives exactly 250 Hz
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(1000 / SAMPLE_RATE));
    // average of 4 readings to reduce ADC noise
    int sum = 0;
    for (int i = 0; i < 4; i++) sum += analogRead(PIN_ECG);
    s.index = index++;
    s.adc = sum / 4;
    s.leadOff = digitalRead(PIN_LO_PLUS) == HIGH || digitalRead(PIN_LO_MINUS) == HIGH;
    // if the queue is full the sample is lost, and the index jump shows it to the dashboard
    if (xQueueSend(sampleQueue, &s, 0) != pdTRUE) droppedSamples = droppedSamples + 1;
  }
}

void samplingBegin() {
  pinMode(PIN_LO_PLUS, INPUT);
  pinMode(PIN_LO_MINUS, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_ECG, ADC_11db);
  // the queue holds about 4 s of samples
  sampleQueue = xQueueCreate(4 * SAMPLE_RATE, sizeof(Sample));
  xTaskCreatePinnedToCore(samplingTask, "sampling", 4096, NULL, 3, NULL, 1);
}
