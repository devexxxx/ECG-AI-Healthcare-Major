import json

import numpy as np

import config
from pipeline import Pipeline
from rules import RuleEngine

FS = config.FS

# the alert each button should end in (None = no alert expected)
EXPECT = {
    "Normal": None, "PVC": "PVC", "AFib": "AFib", "VTach": "VT/VF", "VFib": "VT/VF",
    "Bradycardia": "Bradycardia", "Tachycardia": "Tachycardia", "Flatline": "Flatline", "Noisy": None,
}

next_index = {}


def mv_to_counts(mv):
    # opposite of pipeline.counts_to_mv, limited to the 12-bit ADC range
    counts = (np.asarray(mv, dtype=float) * 1.1 + 1.5) * 4095 / 3.3
    return np.clip(np.round(counts), 0, 4095).astype(np.int16)


def synthetic_ecg(seconds, bpm, noise_mv=0.01, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * FS)) / FS
    mv = np.zeros(len(t))
    # each wave of a beat is a small bump: (height in mV, time from the R peak, width)
    waves = [(0.12, -0.20, 0.025), (-0.10, -0.03, 0.010), (1.00, 0.0, 0.012),
             (-0.20, 0.03, 0.012), (0.30, 0.25, 0.040)]
    beat_time = 0.5
    while beat_time < seconds:
        for height, shift, width in waves:
            mv += height * np.exp(-((t - beat_time - shift) ** 2) / (2 * width ** 2))
        beat_time += 60.0 / bpm * (1 + 0.02 * rng.standard_normal())
    return mv + noise_mv * rng.standard_normal(len(t))


def synthetic_clip(button):
    # stand-in clips so the dashboard can be tried before the real clips are exported
    lead_off_from = None
    onset = 0
    if button == "Normal":
        mv = synthetic_ecg(60, 72)
    elif button == "Bradycardia":
        mv = synthetic_ecg(60, 38)
    elif button == "Tachycardia":
        mv = synthetic_ecg(60, 150)
    elif button == "Flatline":
        mv = synthetic_ecg(60, 72)
        mv[15 * FS:] = 0.002 * np.random.default_rng(2).standard_normal(len(mv) - 15 * FS)
        onset = 15
    elif button == "Noisy":
        mv = synthetic_ecg(60, 72, noise_mv=0.4)
        lead_off_from = 5
        onset = 5
    else:
        return None
    return {"file": "synthetic", "button": button, "record": "synthetic", "onset_s": onset,
            "lead_off_from_s": lead_off_from, "samples": mv_to_counts(mv)}


def clips_for(button):
    index_file = config.SIM_DIR / "clips.json"
    found = []
    if index_file.exists():
        for clip in json.loads(index_file.read_text()):
            if clip["button"] == button and (config.SIM_DIR / clip["file"]).exists():
                found.append(clip)
    return found


def next_clip(button):
    if button not in config.SIM_BUTTONS:
        return None
    found = clips_for(button)
    if not found:
        return synthetic_clip(button)
    # each press of the same button plays the next clip of that button
    k = next_index.get(button, 0)
    next_index[button] = k + 1
    clip = dict(found[k % len(found)])
    clip["samples"] = np.load(config.SIM_DIR / clip["file"])
    return clip


def lead_off_at(clip, seconds):
    start = clip.get("lead_off_from_s")
    return start is not None and seconds >= start


def check_replay(button, clip, out, limit_s=15):
    # decides if one offline replay did what its button promises, returns (ok, short text)
    want = EXPECT[button]
    loud = [(t, a) for t, a in out["alerts"] if a["level"] != "INFO" and a["kind"] != want]
    if want is None:
        if out["alerts"]:
            return False, "unexpected alert " + ",".join(a["kind"] for _, a in out["alerts"])
        if button == "Noisy":
            late = [t for t, _ in out["classes"] if t > clip["onset_s"] + 6]
            return not late, "classified while leads off" if late else "no alert, classifier blocked"
        normal = sum(1 for _, name in out["classes"] if name == "Normal")
        share = normal / max(1, len(out["classes"]))
        return share >= 0.9, "%.0f%% of windows Normal" % (100 * share)

    hits = [(t, a) for t, a in out["alerts"] if a["kind"] == want]
    if not hits:
        got = ",".join(a["kind"] for _, a in out["alerts"]) or "nothing"
        return False, "no %s alert (got %s)" % (want, got)
    when, alert = hits[0]
    # the device acts on the first WARNING/CRITICAL alert, so a different one first is a failure
    if any(t <= when for t, _ in loud):
        return False, "%s alert came first" % loud[0][1]["kind"]
    if alert["level"] != config.ALERTS[want][0]:
        return False, "wrong level " + alert["level"]
    delay = when - clip["onset_s"]
    if delay < 0:
        return False, "%s alert %.0f s before the rhythm change" % (want, -delay)
    if delay > limit_s:
        return False, "%s after %.0f s (limit %d s)" % (want, delay, limit_s)
    return True, "%s %s after %.0f s" % (alert["level"], want, delay)


def replay_offline(clip, classify, source):
    # same pipeline and rules as the live replay, but the clock is the sample count
    now = [0.0]
    pipe = Pipeline(classify, source)
    engine = RuleEngine(clock=lambda: now[0])
    alerts = []
    classes = []
    bad_seconds = 0
    samples = clip["samples"]
    for i in range(0, len(samples) - config.BATCH_SIZE + 1, config.BATCH_SIZE):
        now[0] = (i + config.BATCH_SIZE) / FS
        result = pipe.add(samples[i:i + config.BATCH_SIZE], lead_off_at(clip, i / FS))
        for alert in engine.check(result, sim=True):
            alerts.append((now[0], alert))
        if result["cls"] is not None:
            classes.append((now[0], result["cls"]["name"]))
        if result["second"] and result["quality"] != "ok":
            bad_seconds += 1
    return {"alerts": alerts, "classes": classes, "bad_seconds": bad_seconds}
