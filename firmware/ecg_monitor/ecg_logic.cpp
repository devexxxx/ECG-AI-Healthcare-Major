#include "ecg_logic.h"

#include <stdio.h>
#include <string.h>

const int MIN_BEAT_COUNTS = 50;
const int REFRACTORY = 63;
const int NO_BEAT_LIMIT = 3 * LOGIC_FS;
const int MSG_MAX = 160;

int medianOf(const int *values, int count) {
  int sorted[RR_KEEP];
  if (count <= 0) return 0;
  if (count > RR_KEEP) count = RR_KEEP;
  // small insertion sort, there are never more than 5 values
  for (int i = 0; i < count; i++) {
    int v = values[i];
    int j = i;
    while (j > 0 && sorted[j - 1] > v) {
      sorted[j] = sorted[j - 1];
      j--;
    }
    sorted[j] = v;
  }
  if (count % 2 == 1) return sorted[count / 2];
  return (sorted[count / 2 - 1] + sorted[count / 2]) / 2;
}

void beatReset(BeatDetector &d) {
  memset(&d, 0, sizeof(d));
}

bool beatAddSample(BeatDetector &d, int adc, uint32_t index) {
  // subtract the 200 ms moving average so slow baseline drift does not count
  if (d.windowCount == AVG_LEN) {
    d.windowSum -= d.window[d.windowPos];
  } else {
    d.windowCount++;
  }
  d.window[d.windowPos] = adc;
  d.windowSum += adc;
  d.windowPos = (d.windowPos + 1) % AVG_LEN;
  float x = adc - (float)d.windowSum / d.windowCount;

  // the recent maximum fades with a time constant of about 3 s
  d.recentMax *= 1.0f - 1.0f / (3 * LOGIC_FS);
  if (x > d.recentMax) d.recentMax = x;

  // forget old intervals when no beat came for 3 s
  if (d.haveBeat && index - d.lastBeat > (uint32_t)NO_BEAT_LIMIT) {
    d.haveBeat = false;
    d.rrCount = 0;
  }

  float threshold = 0.6f * d.recentMax;
  if (threshold < MIN_BEAT_COUNTS) threshold = MIN_BEAT_COUNTS;
  if (x < threshold) return false;
  // 250 ms refractory period: one QRS is counted once
  if (d.haveBeat && index - d.lastBeat < (uint32_t)REFRACTORY) return false;

  if (d.haveBeat) {
    int rr = (int)(index - d.lastBeat);
    // keep only intervals inside 20-250 BPM
    if (rr >= 60 && rr <= 750) {
      if (d.rrCount == RR_KEEP) {
        memmove(d.rr, d.rr + 1, sizeof(int) * (RR_KEEP - 1));
        d.rrCount--;
      }
      d.rr[d.rrCount++] = rr;
    }
  }
  d.lastBeat = index;
  d.haveBeat = true;
  return true;
}

int beatBpm(const BeatDetector &d, uint32_t index) {
  if (!d.haveBeat || d.rrCount < 2) return 0;
  if (index - d.lastBeat > (uint32_t)NO_BEAT_LIMIT) return 0;
  // RR is counted in samples of 4 ms, so 60 s is 15000 samples
  int rr = medianOf(d.rr, d.rrCount);
  int bpm = (60 * LOGIC_FS + rr / 2) / rr;
  if (bpm < 20 || bpm > 250) return 0;
  return bpm;
}

void rulesReset(RuleState &r) {
  r.flatSeconds = 0;
  r.slowSeconds = 0;
  r.fastSeconds = 0;
}

int rulesStep(RuleState &r, const RuleLimits &lim, int bpm, int minAdc, int maxAdc) {
  // called once per second with that second's lowest and highest ADC value
  bool flat = (maxAdc - minAdc) < lim.flatPtp;
  r.flatSeconds = flat ? r.flatSeconds + 1 : 0;

  // a signal stuck near either rail is not trusted for rate alarms
  bool sane = !flat && minAdc > 50 && maxAdc < 4045;
  bool slow = sane && bpm >= lim.bradyMin && bpm <= lim.bradyMax;
  bool fast = sane && bpm > lim.tachyMin;
  r.slowSeconds = slow ? r.slowSeconds + 1 : 0;
  r.fastSeconds = fast ? r.fastSeconds + 1 : 0;

  int result = RULE_NONE;
  if (r.flatSeconds >= lim.flatHold) result = RULE_FLATLINE;
  else if (r.slowSeconds >= lim.rateHold) result = RULE_BRADY;
  else if (r.fastSeconds >= lim.rateHold) result = RULE_TACHY;
  if (result != RULE_NONE) rulesReset(r);
  return result;
}

const char *ruleReason(int rule) {
  if (rule == RULE_FLATLINE) return "no heartbeat signal";
  if (rule == RULE_BRADY) return "very slow heart rate";
  if (rule == RULE_TACHY) return "very fast heart rate";
  return "";
}

void buttonReset(Button &b) {
  memset(&b, 0, sizeof(b));
}

int buttonStep(Button &b, bool pressed, uint32_t nowMs, int state, uint32_t panicHoldMs, uint32_t clearHoldMs) {
  // debounce: the raw level must stay the same for 30 ms before it is believed
  if (pressed != b.lastRaw) {
    b.lastRaw = pressed;
    b.changedAt = nowMs;
  }
  if (pressed != b.stable && nowMs - b.changedAt >= 30) {
    b.stable = pressed;
    if (pressed) {
      b.pressedAt = nowMs;
      // a press during the countdown is a cancel, so this hold can never become a panic
      b.handled = (state == ST_COUNTDOWN);
      return BTN_PRESS;
    }
  }
  if (!b.stable || b.handled) return BTN_NONE;

  uint32_t held = nowMs - b.pressedAt;
  // while an alert is active a long hold clears it, in every other state a hold is a panic
  if (state == ST_ALERT) {
    if (held >= clearHoldMs) {
      b.handled = true;
      return BTN_CLEAR;
    }
  } else if ((state == ST_IDLE || state == ST_COOLDOWN) && held >= panicHoldMs) {
    b.handled = true;
    return BTN_PANIC;
  }
  return BTN_NONE;
}

void fixAgeText(char *out, size_t size, long ageSeconds) {
  if (ageSeconds < 0) snprintf(out, size, "(older fix)");
  else if (ageSeconds < 120) snprintf(out, size, "(fix %ld s old)", ageSeconds);
  else if (ageSeconds < 7200) snprintf(out, size, "(fix %ld min old)", ageSeconds / 60);
  else snprintf(out, size, "(fix %ld h old)", ageSeconds / 3600);
}

static void copyAscii(char *out, const char *in, int maxLen) {
  // SMS and the 160 character limit need plain ASCII, anything else becomes '?'
  int n = 0;
  while (in[n] != '\0' && n < maxLen) {
    char c = in[n];
    out[n] = (c >= 32 && c < 127) ? c : '?';
    n++;
  }
  out[n] = '\0';
}

int buildMessage(char *out, size_t size, bool demo, bool panic, const char *level, const char *name,
                 const char *reason, int bpm, bool haveFix, double lat, double lon, const char *ageText) {
  char shortName[17];
  char place[80];
  char head[64];
  char cause[48];
  copyAscii(shortName, name, 16);
  if (haveFix) snprintf(place, sizeof(place), "Loc: https://maps.google.com/?q=%.5f,%.5f %s", lat, lon, ageText);
  else snprintf(place, sizeof(place), "Loc: location unavailable");

  const char *tag = demo ? "[DEMO] " : "";
  if (panic) {
    snprintf(out, size, "%sPANIC pressed by %s. %s", tag, shortName, place);
    return (int)strlen(out);
  }
  const char *word = strcmp(level, "WARNING") == 0 ? "ECG WARNING" : "ECG EMERGENCY";
  snprintf(head, sizeof(head), "%s%s %s: ", tag, word, shortName);
  char tail[110];
  snprintf(tail, sizeof(tail), ". HR %d. %s", bpm, place);
  // the reason is cut so the whole message never goes over 160 characters
  int room = MSG_MAX - (int)strlen(head) - (int)strlen(tail);
  if (room > (int)sizeof(cause) - 1) room = sizeof(cause) - 1;
  if (room < 0) room = 0;
  copyAscii(cause, reason, room);
  snprintf(out, size, "%s%s%s", head, cause, tail);
  return (int)strlen(out);
}

const char *stateName(int state) {
  switch (state) {
    case ST_IDLE: return "IDLE";
    case ST_COUNTDOWN: return "COUNTDOWN";
    case ST_SENDING: return "SENDING";
    case ST_ALERT: return "ALERT";
    case ST_COOLDOWN: return "COOLDOWN";
  }
  return "?";
}
