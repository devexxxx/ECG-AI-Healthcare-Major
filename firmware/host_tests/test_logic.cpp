#include <math.h>
#include <stdio.h>
#include <string.h>

#include "ecg_logic.h"

int failed = 0;
int checked = 0;

// prints the line of every check that fails, the program result is the number of failures
#define CHECK(cond)                                         \
  do {                                                      \
    checked++;                                              \
    if (!(cond)) {                                          \
      failed++;                                             \
      printf("FAIL line %d: %s\n", __LINE__, #cond);        \
    }                                                       \
  } while (0)

const RuleLimits LIMITS = {40, 5, 20, 44, 130, 10};

// fake ECG in ADC counts: a narrow R spike every beat, a smaller T wave, slow drift and a little noise
int fakeEcg(uint32_t index, float bpm, int drift) {
  float t = index / 250.0f;
  float period = 60.0f / bpm;
  float phase = fmodf(t, period);
  float r = 600.0f * expf(-powf((phase - 0.1f) / 0.012f, 2) / 2);
  float tw = 150.0f * expf(-powf((phase - 0.1f - 0.4f * period) / 0.04f, 2) / 2);
  float wander = drift * sinf(2 * 3.14159f * 0.3f * t);
  int noise = (int)((index * 7919u) % 7) - 3;
  return 1860 + (int)(r + tw + wander) + noise;
}

int runBpm(float bpm, int seconds, int drift) {
  BeatDetector d;
  beatReset(d);
  uint32_t i = 0;
  for (; i < (uint32_t)(seconds * 250); i++) beatAddSample(d, fakeEcg(i, bpm, drift), i);
  return beatBpm(d, i);
}

void testMedian() {
  int a[] = {5, 1, 3};
  int b[] = {4, 2, 8, 6};
  int c[] = {9};
  CHECK(medianOf(a, 3) == 3);
  CHECK(medianOf(b, 4) == 5);
  CHECK(medianOf(c, 1) == 9);
  CHECK(medianOf(c, 0) == 0);
}

void testBpm() {
  float rates[] = {30, 40, 60, 72, 100, 150, 200};
  for (int k = 0; k < 7; k++) {
    int got = runBpm(rates[k], 20, 0);
    CHECK(fabsf(got - rates[k]) <= 2);
    int drifting = runBpm(rates[k], 20, 300);
    CHECK(fabsf(drifting - rates[k]) <= 3);
  }
}

void testBpmDropsToZero() {
  BeatDetector d;
  beatReset(d);
  uint32_t i = 0;
  for (; i < 10 * 250; i++) beatAddSample(d, fakeEcg(i, 72, 0), i);
  CHECK(beatBpm(d, i) == 72);
  // the beats stop: only small noise for 4 s
  for (; i < 14 * 250; i++) beatAddSample(d, 1860 + (int)(i % 5) - 2, i);
  CHECK(beatBpm(d, i) == 0);
  // noise alone must never look like a pulse
  beatReset(d);
  for (i = 0; i < 20 * 250; i++) beatAddSample(d, 1860 + (int)((i * 31) % 11) - 5, i);
  CHECK(beatBpm(d, i) == 0);
}

void testSampleIndexClock() {
  // the detector only sees sample numbers, so a dropped block shows up as one long interval
  BeatDetector d;
  beatReset(d);
  uint32_t i = 0;
  for (; i < 10 * 250; i++) beatAddSample(d, fakeEcg(i, 60, 0), i);
  i += 250;
  for (; i < 21 * 250; i++) beatAddSample(d, fakeEcg(i, 60, 0), i);
  CHECK(beatBpm(d, i) == 60);
}

void testRules() {
  RuleState r;
  rulesReset(r);
  int fired = 0;
  // normal signal: big spread, normal rate
  for (int s = 0; s < 60; s++) CHECK(rulesStep(r, LIMITS, 72, 1500, 2400) == RULE_NONE);
  // flat signal: fires on the 5th flat second and not before
  for (int s = 1; s <= 5; s++) {
    int res = rulesStep(r, LIMITS, 0, 1850, 1870);
    if (res != RULE_NONE) fired = s;
    CHECK(res == (s == 5 ? RULE_FLATLINE : RULE_NONE));
  }
  CHECK(fired == 5);
  // a spread of exactly the limit is not flat
  for (int s = 0; s < 20; s++) CHECK(rulesStep(r, LIMITS, 0, 1850, 1890) == RULE_NONE);

  // slow rate: 10 s, and a single normal second starts the count again
  for (int s = 1; s <= 9; s++) CHECK(rulesStep(r, LIMITS, 40, 1500, 2400) == RULE_NONE);
  CHECK(rulesStep(r, LIMITS, 60, 1500, 2400) == RULE_NONE);
  for (int s = 1; s <= 9; s++) CHECK(rulesStep(r, LIMITS, 40, 1500, 2400) == RULE_NONE);
  CHECK(rulesStep(r, LIMITS, 44, 1500, 2400) == RULE_BRADY);

  // the edges of the limits
  for (int s = 0; s < 30; s++) CHECK(rulesStep(r, LIMITS, 45, 1500, 2400) == RULE_NONE);
  for (int s = 0; s < 30; s++) CHECK(rulesStep(r, LIMITS, 130, 1500, 2400) == RULE_NONE);
  for (int s = 0; s < 30; s++) CHECK(rulesStep(r, LIMITS, 19, 1500, 2400) == RULE_NONE);
  for (int s = 1; s <= 9; s++) CHECK(rulesStep(r, LIMITS, 131, 1500, 2400) == RULE_NONE);
  CHECK(rulesStep(r, LIMITS, 150, 1500, 2400) == RULE_TACHY);

  // a saturated signal never raises a rate alarm
  for (int s = 0; s < 30; s++) CHECK(rulesStep(r, LIMITS, 200, 0, 4095) == RULE_NONE);
  CHECK(strcmp(ruleReason(RULE_BRADY), "very slow heart rate") == 0);
}

void testButtonPanic() {
  Button b;
  buttonReset(b);
  int panics = 0;
  int presses = 0;
  // bouncing contact for 20 ms, then held for 4 s in IDLE
  for (uint32_t t = 0; t < 4000; t++) {
    bool raw = t < 20 ? (t % 2 == 0) : true;
    int ev = buttonStep(b, raw, t, ST_IDLE, 1500, 3000);
    if (ev == BTN_PANIC) {
      panics++;
      CHECK(t >= 1500 && t <= 1560);
    }
    if (ev == BTN_PRESS) presses++;
  }
  CHECK(panics == 1);
  CHECK(presses == 1);

  // a 1 s press is not a panic
  buttonReset(b);
  for (uint32_t t = 0; t < 3000; t++) CHECK(buttonStep(b, t < 1000, t, ST_IDLE, 1500, 3000) != BTN_PANIC);

  // panic also works during cooldown, and nothing happens while sending
  buttonReset(b);
  panics = 0;
  for (uint32_t t = 0; t < 2000; t++) panics += buttonStep(b, true, t, ST_COOLDOWN, 1500, 3000) == BTN_PANIC;
  CHECK(panics == 1);
  buttonReset(b);
  for (uint32_t t = 0; t < 5000; t++) CHECK(buttonStep(b, true, t, ST_SENDING, 1500, 3000) <= BTN_PRESS);
}

void testButtonCancelNeverPanics() {
  Button b;
  buttonReset(b);
  int state = ST_COUNTDOWN;
  int presses = 0;
  // the press cancels the countdown (state goes IDLE) and the finger stays down for 5 s
  for (uint32_t t = 0; t < 5000; t++) {
    int ev = buttonStep(b, true, t, state, 1500, 3000);
    if (ev == BTN_PRESS) {
      presses++;
      state = ST_IDLE;
    }
    CHECK(ev != BTN_PANIC && ev != BTN_CLEAR);
  }
  CHECK(presses == 1);
  // after releasing, a new hold is a normal panic again
  int panics = 0;
  for (uint32_t t = 5000; t < 5100; t++) buttonStep(b, false, t, state, 1500, 3000);
  for (uint32_t t = 5100; t < 7000; t++) panics += buttonStep(b, true, t, state, 1500, 3000) == BTN_PANIC;
  CHECK(panics == 1);
}

void testButtonClearNeverPanics() {
  Button b;
  buttonReset(b);
  int state = ST_ALERT;
  int clears = 0;
  // hold 3 s to clear the alert, keep holding 5 more seconds while the state is COOLDOWN
  for (uint32_t t = 0; t < 8000; t++) {
    int ev = buttonStep(b, true, t, state, 1500, 3000);
    if (ev == BTN_CLEAR) {
      clears++;
      CHECK(t >= 3000 && t <= 3060);
      state = ST_COOLDOWN;
    }
    CHECK(ev != BTN_PANIC);
  }
  CHECK(clears == 1);
}

void testMessages() {
  char msg[200];
  char age[24];
  fixAgeText(age, sizeof(age), 42);
  CHECK(strcmp(age, "(fix 42 s old)") == 0);
  fixAgeText(age, sizeof(age), 600);
  CHECK(strcmp(age, "(fix 10 min old)") == 0);
  fixAgeText(age, sizeof(age), 3 * 3600);
  CHECK(strcmp(age, "(fix 3 h old)") == 0);
  fixAgeText(age, sizeof(age), -1);
  CHECK(strcmp(age, "(older fix)") == 0);

  fixAgeText(age, sizeof(age), 42);
  int len = buildMessage(msg, sizeof(msg), true, false, "WARNING", "Asha", "irregular rhythm", 88, true, 28.61390,
                         77.20900, age);
  CHECK(strcmp(msg, "[DEMO] ECG WARNING Asha: irregular rhythm. HR 88. Loc: "
                    "https://maps.google.com/?q=28.61390,77.20900 (fix 42 s old)") == 0);
  CHECK(len == (int)strlen(msg));

  buildMessage(msg, sizeof(msg), true, false, "CRITICAL", "Asha", "no heartbeat signal", 0, false, 0, 0, age);
  CHECK(strcmp(msg, "[DEMO] ECG EMERGENCY Asha: no heartbeat signal. HR 0. Loc: location unavailable") == 0);

  buildMessage(msg, sizeof(msg), true, true, "CRITICAL", "Asha", "", 70, false, 0, 0, age);
  CHECK(strcmp(msg, "[DEMO] PANIC pressed by Asha. Loc: location unavailable") == 0);

  buildMessage(msg, sizeof(msg), false, true, "CRITICAL", "Asha", "", 70, false, 0, 0, age);
  CHECK(strncmp(msg, "PANIC pressed by Asha.", 22) == 0);

  // worst case: long name, long reason, long coordinates, long age text
  fixAgeText(age, sizeof(age), 119 * 60);
  const char *longName = "Averyveryverylongpatientname";
  const char *longReason = "this reason text is far too long to fit inside one single SMS message at all";
  len = buildMessage(msg, sizeof(msg), true, false, "CRITICAL", longName, longReason, 250, true, -89.123456,
                     -179.123456, age);
  CHECK(len <= 160);
  CHECK(strstr(msg, "Averyveryverylon:") != NULL);
  CHECK(strstr(msg, "https://maps.google.com/?q=-89.12346,-179.12346 (fix 119 min old)") != NULL);
  CHECK(strstr(msg, ". HR 250. ") != NULL);
  len = buildMessage(msg, sizeof(msg), true, true, "CRITICAL", longName, "", 250, true, -89.123456, -179.123456, age);
  CHECK(len <= 160);

  // only plain ASCII goes out
  buildMessage(msg, sizeof(msg), true, false, "WARNING", "Jos\xC3\xA9", "ok", 60, false, 0, 0, age);
  for (size_t i = 0; i < strlen(msg); i++) CHECK(msg[i] >= 32 && msg[i] < 127);

  CHECK(strcmp(stateName(ST_ALERT), "ALERT") == 0);
  CHECK(strcmp(stateName(ST_IDLE), "IDLE") == 0);
}

int main() {
  testMedian();
  testBpm();
  testBpmDropsToZero();
  testSampleIndexClock();
  testRules();
  testButtonPanic();
  testButtonCancelNeverPanics();
  testButtonClearNeverPanics();
  testMessages();
  printf("%d checks, %d failed\n", checked, failed);
  printf(failed == 0 ? "HOST TESTS: PASS\n" : "HOST TESTS: FAIL\n");
  return failed;
}
