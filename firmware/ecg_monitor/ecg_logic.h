#pragma once
#include <stddef.h>
#include <stdint.h>

// device states, in the order of the state machine in the guide
enum DeviceState { ST_IDLE, ST_COUNTDOWN, ST_SENDING, ST_ALERT, ST_COOLDOWN };

const int LOGIC_FS = 250;
const int AVG_LEN = 50;
const int RR_KEEP = 5;

struct BeatDetector {
  int window[AVG_LEN];
  long windowSum;
  int windowPos;
  int windowCount;
  float recentMax;
  uint32_t lastBeat;
  bool haveBeat;
  int rr[RR_KEEP];
  int rrCount;
};

struct RuleLimits {
  int flatPtp;
  int flatHold;
  int bradyMin;
  int bradyMax;
  int tachyMin;
  int rateHold;
};

struct RuleState {
  int flatSeconds;
  int slowSeconds;
  int fastSeconds;
};

enum RuleResult { RULE_NONE, RULE_FLATLINE, RULE_BRADY, RULE_TACHY };

struct Button {
  bool stable;
  bool lastRaw;
  uint32_t changedAt;
  uint32_t pressedAt;
  bool handled;
};

enum ButtonEvent { BTN_NONE, BTN_PRESS, BTN_PANIC, BTN_CLEAR };

int medianOf(const int *values, int count);

void beatReset(BeatDetector &d);
bool beatAddSample(BeatDetector &d, int adc, uint32_t index);
int beatBpm(const BeatDetector &d, uint32_t index);

void rulesReset(RuleState &r);
int rulesStep(RuleState &r, const RuleLimits &lim, int bpm, int minAdc, int maxAdc);
const char *ruleReason(int rule);

void buttonReset(Button &b);
int buttonStep(Button &b, bool pressed, uint32_t nowMs, int state, uint32_t panicHoldMs, uint32_t clearHoldMs);

void fixAgeText(char *out, size_t size, long ageSeconds);
int buildMessage(char *out, size_t size, bool demo, bool panic, const char *level, const char *name,
                 const char *reason, int bpm, bool haveFix, double lat, double lon, const char *ageText);
const char *stateName(int state);
