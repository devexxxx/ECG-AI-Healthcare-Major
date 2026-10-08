import time

import config

# an ML episode is over after this many windows in a row without that class
CLEAR_WINDOWS = 5


ML_KINDS = ("VT/VF", "AFib", "PVC")
RULE_HOLDS = {"Flatline": config.FLATLINE_HOLD_S, "Bradycardia": config.RULE_HOLD_S, "Tachycardia": config.RULE_HOLD_S}


def make_alert(kind, bpm):
    # origin says who found it: the ML classifier or a physiological rule
    level, reason = config.ALERTS[kind]
    origin = "ml" if kind in ML_KINDS else "rule"
    return {"kind": kind, "level": level, "reason": reason, "bpm": bpm, "origin": origin}


class RuleEngine:
    def __init__(self, clock=time.monotonic):
        # the clock can be replaced in tests so 10 s rules do not need 10 real seconds
        self.clock = clock
        self.reset()

    def reset(self):
        self.streak_class = None
        self.streak = 0
        self.misses = {}
        self.active = set()
        self.since = {"Bradycardia": None, "Tachycardia": None, "Flatline": None}

    def ml_check(self, cls, bpm):
        name = cls["name"]
        sure = name != "Normal" and cls["conf"] >= config.CONFIRM_PROB
        # any other result breaks the streak
        if sure and name == self.streak_class:
            self.streak += 1
        else:
            self.streak_class = name if sure else None
            self.streak = 1 if sure else 0

        # an episode that is already reported ends after enough windows without it
        for kind in ("PVC", "AFib", "VT/VF"):
            if kind not in self.active:
                continue
            self.misses[kind] = 0 if (sure and name == kind) else self.misses.get(kind, 0) + 1
            if self.misses[kind] >= CLEAR_WINDOWS:
                self.active.discard(kind)

        if sure and self.streak >= config.CONFIRM_WINDOWS and name not in self.active:
            self.active.add(name)
            self.misses[name] = 0
            return make_alert(name, bpm)
        return None

    def held(self, kind, condition, hold_s, bpm):
        # the condition must stay true for hold_s seconds, then it alerts once until it clears
        if not condition:
            self.since[kind] = None
            self.active.discard(kind)
            return None
        now = self.clock()
        if self.since[kind] is None:
            self.since[kind] = now
        if now - self.since[kind] >= hold_s and kind not in self.active:
            self.active.add(kind)
            return make_alert(kind, bpm)
        return None

    def check(self, result, sim):
        alerts = []
        if result["cls"] is not None:
            alerts.append(self.ml_check(result["cls"], result["bpm"]))
        # the three rules are always watched so the page can show them; they raise an alert only
        # for simulation, because in live mode the device owns these alerts
        if result["second"]:
            bpm = result["bpm"]
            ok = result["quality"] == "ok"
            slow = ok and config.BRADY_MIN_BPM <= bpm <= config.BRADY_MAX_BPM
            fast = ok and bpm > config.TACHY_MIN_BPM
            flat = result["quality"] == "flat"
            found = [self.held(kind, on, RULE_HOLDS[kind], bpm)
                     for kind, on in (("Flatline", flat), ("Bradycardia", slow), ("Tachycardia", fast))]
            if sim:
                alerts += found
        return [a for a in alerts if a is not None]

    def rule_status(self):
        # the rule condition that is running now: how long it has held and if it is confirmed
        for kind, hold_s in RULE_HOLDS.items():
            if self.since[kind] is not None:
                held = min(int(self.clock() - self.since[kind]), hold_s)
                return {"name": kind, "level": config.ALERTS[kind][0], "held": held, "need": hold_s,
                        "confirmed": kind in self.active}
        return None

    def ml_status(self):
        # the confirmed ML episode, the most serious one if there are several
        for kind in ML_KINDS:
            if kind in self.active:
                return kind
        return None
