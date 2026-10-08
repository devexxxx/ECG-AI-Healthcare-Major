import json
import sys
from pathlib import Path

# torch has to be loaded before wfdb on Windows, the other order fails with a DLL error
import torch
import numpy as np
from scipy.signal import resample

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent / "dashboard"))

import config
import sim
from pipeline import counts_to_mv, filter_all, find_r_peaks, load_classifier
from prepare_data import AFIB, NORMAL, SPLIT_FILE, VTVF, load_segments, rhythm_class

FS = config.FS
BEFORE = 15 * FS
AFTER = 45 * FS
CLIP = BEFORE + AFTER
KEEP = 3
MAX_TRIES = 40
LATE_LIMIT = 25
BRADY_BPM = 38
TACHY_BPM = 150


def to_counts(mv):
    # centre the clip and scale it so the beats use about a third of the ADC range (no saturation)
    mv = np.asarray(mv, dtype=float)
    mv = mv - np.median(mv)
    mv = mv / (np.percentile(np.abs(mv), 99.9) + 1e-9)
    return sim.mv_to_counts(np.clip(mv, -1.3, 1.3))


def clip_bpm(counts):
    peaks = find_r_peaks(filter_all(counts_to_mv(counts))[3 * FS:])
    if len(peaks) < 3:
        return 0
    return 60.0 * FS / float(np.median(np.diff(peaks)))


def rhythm_array(seg):
    rhythm = np.full(len(seg["sig"]), -1, dtype=np.int8)
    for start, end, name in seg["spans"]:
        rhythm[start:end] = rhythm_class(name)
    return rhythm


def onset_candidates(segments, prefix):
    # places where the wanted rhythm starts, with 15 s before and 45 s after inside the recording
    found = []
    for rec, seg in segments:
        spans = seg["spans"]
        for i in range(1, len(spans)):
            start, end, name = spans[i]
            before = spans[i - 1][2] or ""
            if not (name or "").startswith(prefix) or before.startswith("(VT") or before.startswith("(VF"):
                continue
            if start - BEFORE < 0 or start + AFTER > len(seg["sig"]):
                continue
            # best first: normal rhythm before the change and a long run after it
            score = (rhythm_class(before) == NORMAL, min(end - start, AFTER))
            found.append((score, rec, seg, start))
    found.sort(key=lambda c: c[0], reverse=True)
    return [(rec, seg, start) for _, rec, seg, start in found]


def pvc_candidates(segments):
    found = []
    for rec, seg in segments:
        if not rec.startswith("mitdb/"):
            continue
        rhythm = rhythm_array(seg)
        beats = [(s, sym) for s, sym, aux in seg["marks"] if sym != "+"]
        v_times = np.array([s for s, sym in beats if sym == "V"])
        for t in v_times:
            if t - BEFORE < 0 or t + AFTER > len(seg["sig"]):
                continue
            # the first PVC after at least 15 s without one, and no VT/VF or AFib anywhere in the clip
            if np.any((v_times < t) & (v_times >= t - BEFORE)):
                continue
            part = rhythm[t - BEFORE:t + AFTER]
            if VTVF in part or AFIB in part:
                continue
            inside = [sym for s, sym in beats if t - BEFORE <= s < t + AFTER]
            if any(sym not in ("N", "V") for sym in inside):
                continue
            found.append((inside.count("V"), rec, seg, int(t)))
    found.sort(key=lambda c: c[0], reverse=True)
    return [(rec, seg, start) for _, rec, seg, start in found]


def pvc_dense_candidates(segments):
    # records where PVCs never pause for 15 s: 60 s pieces with at least 8 PVCs, only N and V beats
    found = []
    for rec, seg in segments:
        if not rec.startswith("mitdb/"):
            continue
        rhythm = rhythm_array(seg)
        beats = [(s, sym) for s, sym, aux in seg["marks"] if sym != "+"]
        for start in range(3 * FS, len(seg["sig"]) - CLIP, 10 * FS):
            part = rhythm[start:start + CLIP]
            inside = [sym for s, sym in beats if start <= s < start + CLIP]
            if VTVF in part or AFIB in part or any(sym not in ("N", "V") for sym in inside):
                continue
            if inside.count("V") >= 8:
                found.append((inside.count("V"), rec, seg, start))
    found.sort(key=lambda c: c[0], reverse=True)
    return [(rec, seg, start) for _, rec, seg, start in found]


def normal_candidates(segments, length):
    # stretches where the rhythm is normal and every labelled beat is N
    found = []
    for rec, seg in segments:
        rhythm = rhythm_array(seg)
        bad = np.array([s for s, sym, aux in seg["marks"] if sym not in ("N", "+")], dtype=int)
        for start in range(3 * FS, len(seg["sig"]) - length, 30 * FS):
            if not np.all(rhythm[start:start + length] == NORMAL):
                continue
            if np.any((bad >= start) & (bad < start + length)):
                continue
            found.append((rec, seg, start))
    # one start per record first, and mitdb before the others (its beats were checked by cardiologists)
    seen = {}
    ranked = []
    for rec, seg, start in found:
        seen[rec] = seen.get(rec, 0) + 1
        ranked.append((seen[rec], not rec.startswith("mitdb/"), rec, start, seg))
    ranked.sort(key=lambda c: c[:4])
    return [(rec, seg, start) for _, _, rec, start, seg in ranked]


def stretched(seg, start, target_bpm):
    # replay a normal piece slower or faster so its heart rate becomes the target
    base = seg["sig"][start:start + 180 * FS]
    bpm = clip_bpm(to_counts(base[:60 * FS]))
    if bpm < 40:
        return None
    need = int(CLIP * target_bpm / bpm)
    if need > len(base):
        return None
    return to_counts(resample(base[:need], CLIP))


def build(button, rec, seg, start, rng):
    lead_off_from = None
    onset = 15
    if button in ("AFib", "VTach", "VFib", "PVC"):
        samples = to_counts(seg["sig"][start - BEFORE:start + AFTER])
    elif button == "PVC dense":
        samples = to_counts(seg["sig"][start:start + CLIP])
        button = "PVC"
        onset = 0
    elif button == "Normal":
        samples = to_counts(seg["sig"][start:start + CLIP])
        onset = 0
    elif button == "Bradycardia":
        samples = stretched(seg, start, BRADY_BPM)
        onset = 0
    elif button == "Tachycardia":
        samples = stretched(seg, start, TACHY_BPM)
        onset = 0
    elif button == "Flatline":
        # 15 s of normal rhythm, then only a few counts of noise around the baseline
        samples = to_counts(seg["sig"][start:start + CLIP])
        samples[BEFORE:] = int(np.median(samples)) + rng.integers(-3, 4, CLIP - BEFORE)
    else:
        # Noisy: heavy noise on a normal clip, with the leads-off flag set from second 5
        noisy = counts_to_mv(to_counts(seg["sig"][start:start + CLIP])) + 0.5 * rng.standard_normal(CLIP)
        samples = sim.mv_to_counts(noisy)
        lead_off_from = 5
        onset = 5
    if samples is None:
        return None
    return {"button": button, "record": rec, "record_start_s": round((seg["start"] + start) / FS, 1),
            "onset_s": onset, "lead_off_from_s": lead_off_from, "samples": samples.astype(np.int16)}


def main():
    if not SPLIT_FILE.exists():
        print("Run ml/prepare_data.py first.")
        sys.exit(1)
    test_records = json.loads(SPLIT_FILE.read_text())["test"]
    classify, source = load_classifier()
    print("classifier used to check the clips:", source)
    print("test records:", len(test_records))

    segments = []
    for rec in test_records:
        db, name = rec.split("/")
        for seg in load_segments(db, name):
            segments.append((rec, seg))

    normal_60 = [("Normal", r, s, t) for r, s, t in normal_candidates(segments, CLIP)]
    normal_180 = normal_candidates(segments, 180 * FS)
    pvc = [("PVC", r, s, t) for r, s, t in pvc_candidates(segments)]
    pvc += [("PVC dense", r, s, t) for r, s, t in pvc_dense_candidates(segments)]
    # every candidate is (how to build it, record, piece of the record, start sample)
    candidates = {
        "Normal": normal_60, "PVC": pvc,
        "AFib": [("AFib", r, s, t) for r, s, t in onset_candidates(segments, "(AFIB")],
        "VTach": [("VTach", r, s, t) for r, s, t in onset_candidates(segments, "(VT")],
        "VFib": [("VFib", r, s, t) for r, s, t in onset_candidates(segments, "(VF")],
        "Bradycardia": [("Bradycardia", r, s, t) for r, s, t in normal_180],
        "Tachycardia": [("Tachycardia", r, s, t) for r, s, t in normal_180],
        "Flatline": [("Flatline", r, s, t) for _, r, s, t in normal_60],
        "Noisy": [("Noisy", r, s, t) for _, r, s, t in normal_60],
    }

    config.SIM_DIR.mkdir(exist_ok=True)
    for old in config.SIM_DIR.glob("*.npy"):
        old.unlink()
    rng = np.random.default_rng(3)
    index = []
    short = []
    for button in config.SIM_BUTTONS:
        kept = 0
        tried = set()
        used_records = []
        # pass 1: one clip per record. pass 2: more from the same records.
        # pass 3 (only if nothing was kept): accept an alert up to 25 s late and mark the clip as late
        for allow_repeat, limit in ((False, 15), (True, 15), (True, LATE_LIMIT)):
            if limit == LATE_LIMIT and kept > 0:
                break
            if limit == LATE_LIMIT:
                tried = set()
            for mode, rec, seg, start in candidates[button]:
                if kept >= KEEP or len(tried) >= MAX_TRIES:
                    break
                if (rec, start) in tried or (not allow_repeat and rec in used_records):
                    continue
                assert rec in test_records
                clip = build(mode, rec, seg, start, rng)
                if clip is None:
                    continue
                tried.add((rec, start))
                # only clips that behave as the button promises are kept (clean examples, said in the viva)
                ok, text = sim.check_replay(button, clip, sim.replay_offline(clip, classify, source), limit)
                bpm = round(clip_bpm(clip["samples"]), 1)
                if button == "Bradycardia" and not bpm < config.BRADY_MAX_BPM + 1:
                    ok, text = False, "measured %.1f BPM, not below 45" % bpm
                if button == "Tachycardia" and not bpm > config.TACHY_MIN_BPM:
                    ok, text = False, "measured %.1f BPM, not above 130" % bpm
                print("%-12s %-12s start %7.1f s  bpm %5.1f  %s  %s" % (
                    button, rec, clip["record_start_s"], bpm, "KEEP" if ok else "skip", text))
                if not ok:
                    continue
                kept += 1
                used_records.append(rec)
                clip["file"] = "%s_%d.npy" % (button, kept)
                clip["bpm"] = bpm
                clip["checked_with"] = source
                clip["check"] = text
                clip["late"] = limit == LATE_LIMIT
                np.save(config.SIM_DIR / clip["file"], clip.pop("samples"))
                index.append(clip)
        print("%-12s kept %d of %d tried (%d candidates)\n" % (button, kept, len(tried), len(candidates[button])))
        if kept < 2 or any(c["late"] for c in index if c["button"] == button):
            short.append(button)

    (config.SIM_DIR / "clips.json").write_text(json.dumps(index, indent=2))
    print("saved %d clips to %s" % (len(index), config.SIM_DIR))
    if short:
        print("SIM CLIPS: FAIL, fewer than 2 clips (or only late ones) for: " + ", ".join(short))
        sys.exit(1)
    print("SIM CLIPS: PASS (every button has at least 2 clips from test records)")


if __name__ == "__main__":
    main()
