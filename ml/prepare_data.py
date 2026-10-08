import json
import sys
from pathlib import Path

import numpy as np
import wfdb
from scipy.signal import resample, resample_poly

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent / "dashboard"))

import config
from pipeline import filter_all, rhythm_features, zscore

DATA_DIR = ML_DIR / "data"
OUT_FILE = DATA_DIR / "windows.npz"
SPLIT_FILE = DATA_DIR / "split.json"

FS = config.FS
WIN = config.WINDOW
SKIP = 3 * FS
CAP = 150
# RR spread (std / mean) above this is not a regular rhythm; checked mitdb normal beats sit at 0.02-0.10
NORMAL_MAX_SPREAD = 0.12
DATABASES = ["mitdb", "afdb", "vfdb", "cudb"]
NORMAL, PVC, AFIB, VTVF = 0, 1, 2, 3
OTHER = -1


def rhythm_class(name):
    # priority VT/VF > AFib, exact match for normal so (NOD and (NOISE are not normal
    if name is None:
        return OTHER
    if name.startswith("(VT") or name.startswith("(VF"):
        return VTVF
    if name == "(AFIB":
        return AFIB
    if name in ("(N", "(NSR"):
        return NORMAL
    return OTHER


def read_marks(folder, rec, scale):
    ann = wfdb.rdann(str(folder / rec), "atr")
    marks = []
    for sample, symbol, aux in zip(ann.sample, ann.symbol, ann.aux_note):
        marks.append((int(round(sample * scale)), symbol, aux.strip("\x00").strip()))
    return marks


def rhythm_spans(marks, total, db):
    # walk through the marks and remember which rhythm is running
    current = "(N" if db == "cudb" else None
    start = 0
    spans = []
    for sample, symbol, aux in marks:
        new = None
        if symbol == "+" and aux.startswith("("):
            new = aux
        elif symbol == "[":
            new = "(VF"
        elif symbol == "]":
            new = "(?"
        if new is not None:
            spans.append((start, sample, current))
            current, start = new, sample
    spans.append((start, total, current))
    return [s for s in spans if s[1] > s[0]]


def load_segments(db, rec):
    # returns pieces of one record: 250 Hz signal in mV, rhythm spans and marks in local sample numbers
    folder = DATA_DIR / db
    head = wfdb.rdheader(str(folder / rec))
    if head.sig_len is None or not head.n_sig:
        return []
    scale = FS / head.fs
    total = int(head.sig_len * scale)
    marks = read_marks(folder, rec, scale)
    spans = rhythm_spans(marks, total, db)

    if db == "afdb":
        segments = []
        for path in sorted((folder / "pieces").glob(rec + "_*.npy")):
            a, b = [int(v) for v in path.stem.split("_")[1:]]
            sig = np.nan_to_num(np.load(path).astype(float))
            part_spans = [(max(s, a) - a, min(e, b) - a, name) for s, e, name in spans if e > a and s < b]
            part_marks = [(s - a, sym, aux) for s, sym, aux in marks if a <= s < b]
            segments.append({"sig": sig, "spans": part_spans, "marks": part_marks, "start": a})
        return segments

    channel = 0
    if db == "mitdb":
        # every mitdb record is read from the same lead, records without MLII are skipped
        if "MLII" not in head.sig_name:
            return []
        channel = head.sig_name.index("MLII")
    sig = wfdb.rdrecord(str(folder / rec), channels=[channel]).p_signal[:, 0]
    sig = np.nan_to_num(sig)
    if head.fs != FS:
        # 360 Hz -> 250 Hz is the exact ratio 25/36
        sig = resample_poly(sig, 25, 36)
    return [{"sig": sig, "spans": spans, "marks": marks, "start": 0}]


def label_windows(seg, db):
    n = len(seg["sig"])
    rhythm = np.full(n, OTHER, dtype=np.int8)
    for start, end, name in seg["spans"]:
        rhythm[start:end] = rhythm_class(name)
        # cudb beats before VF are often already abnormal, so 10 s before each VF are not used as normal
        if db == "cudb" and rhythm_class(name) == VTVF:
            rhythm[max(0, start - 10 * FS):start] = OTHER

    marks = [(s, sym) for s, sym, aux in seg["marks"] if sym not in ("+", "[", "]")]
    mark_pos = np.array([s for s, sym in marks], dtype=int)
    mark_sym = [sym for s, sym in marks]
    has_beats = db in ("mitdb", "cudb")

    found = []
    # slide a 5 s window in 1 s steps, the first 3 s are skipped (filter warm-up)
    for start in range(SKIP, n - WIN + 1, FS):
        codes = rhythm[start:start + WIN]
        low, high = codes.min(), codes.max()
        if low == high and low in (VTVF, AFIB):
            found.append((start, int(low)))
            continue
        # a window that only partly touches VT/VF or AFib is not used at all
        if VTVF in codes or AFIB in codes:
            continue
        # other rhythms (bigeminy and so on) are allowed only for mitdb PVC windows
        if low != NORMAL and db != "mitdb":
            continue
        if not has_beats:
            if low == high == NORMAL:
                found.append((start, NORMAL))
            continue
        a, b = np.searchsorted(mark_pos, [start, start + WIN])
        symbols = mark_sym[a:b]
        if len(symbols) < 3:
            continue
        if all(s == "N" for s in symbols) and low == high == NORMAL:
            found.append((start, NORMAL))
        elif db == "mitdb" and "V" in symbols and all(s in ("N", "V") for s in symbols):
            found.append((start, PVC))
    return found


def regular(window):
    # only mitdb has checked beat labels; in the other databases "normal" rhythm still hides
    # ectopic beats and pauses, so a Normal window there must also have evenly spaced beats
    r = rhythm_features(zscore(window))
    return r[6] == 0 and r[1] < NORMAL_MAX_SPREAD


def cap_evenly(starts):
    # keep at most CAP windows per record and class, spread over the whole record
    if len(starts) <= CAP:
        return starts
    keep = np.linspace(0, len(starts) - 1, CAP).round().astype(int)
    return [starts[i] for i in keep]


def collect():
    windows, labels, records, sources, stretched = [], [], [], [], []
    dropped = {}
    rng = np.random.default_rng(5)
    for db in DATABASES:
        folder = DATA_DIR / db
        names = sorted(p.stem for p in folder.glob("*.hea"))
        used = 0
        for rec in names:
            try:
                segments = load_segments(db, rec)
            except Exception as err:
                print(f"  {db}/{rec}: skipped ({err})")
                continue
            by_class = {NORMAL: [], PVC: [], AFIB: [], VTVF: []}
            for k, seg in enumerate(segments):
                seg["filt"] = filter_all(seg["sig"])
                for start, cls in label_windows(seg, db):
                    if cls == NORMAL and db != "mitdb" and not regular(seg["filt"][start:start + WIN]):
                        dropped[db] = dropped.get(db, 0) + 1
                        continue
                    by_class[cls].append((k, start))
            if sum(len(v) for v in by_class.values()) > 0:
                used += 1
            for cls, starts in by_class.items():
                for k, start in cap_evenly(starts):
                    windows.append(zscore(segments[k]["filt"][start:start + WIN]).astype(np.float32))
                    labels.append(cls)
                    records.append(db + "/" + rec)
                    sources.append(db)
                    stretched.append(False)
            # checked normal rhythm replayed slower or faster (0.45x to 2.2x), so the model also
            # learns that a regular rhythm at 35-160 BPM is still Normal
            if db == "mitdb":
                normal_starts = set(by_class[NORMAL])
                for k, start in cap_evenly(by_class[NORMAL]):
                    speed = rng.uniform(0.45, 2.2)
                    if speed > 1 and not ((k, start + WIN) in normal_starts and (k, start + WIN + FS) in normal_starts):
                        continue
                    piece = segments[k]["filt"][start:start + int(WIN * speed)]
                    windows.append(zscore(resample(piece, WIN)).astype(np.float32))
                    labels.append(NORMAL)
                    records.append(db + "/" + rec)
                    sources.append(db)
                    stretched.append(True)
        print(f"{db}: {used} of {len(names)} records gave windows, "
              f"{dropped.get(db, 0)} irregular 'normal' window positions left out")
    return np.array(windows), np.array(labels), np.array(records), np.array(sources), np.array(stretched)


def split_records(labels, records, sources):
    # each record goes to exactly one split, grouped by its rarest class and its database
    rng = np.random.default_rng(7)
    groups = {}
    for rec in sorted(set(records)):
        mine = labels[records == rec]
        rare = next(c for c in (VTVF, AFIB, PVC, NORMAL) if c in mine)
        groups.setdefault((rare, rec.split("/")[0]), []).append(rec)
    split = {}
    for key in sorted(groups):
        names = groups[key]
        rng.shuffle(names)
        n_hold = int(round(0.15 * len(names)))
        if len(names) >= 4:
            n_hold = max(1, n_hold)
        for i, rec in enumerate(names):
            split[rec] = "test" if i < n_hold else "val" if i < 2 * n_hold else "train"
    return split


def check_split(labels, records, split_of):
    parts = {name: set(r for r in set(records) if split_of[r] == name) for name in ("train", "val", "test")}
    assert not (parts["train"] & parts["val"]), "record in train and val"
    assert not (parts["train"] & parts["test"]), "record in train and test"
    assert not (parts["val"] & parts["test"]), "record in val and test"
    assert len(parts["train"]) + len(parts["val"]) + len(parts["test"]) == len(set(records))
    counts = {}
    for name in ("val", "test"):
        for cls in (AFIB, VTVF):
            counts[(name, cls)] = len(set(records[(labels == cls) & np.isin(records, list(parts[name]))]))
    assert counts[("test", AFIB)] >= 2 and counts[("test", VTVF)] >= 2, "test needs 2 AFib and 2 VT/VF records"
    assert counts[("val", AFIB)] >= 1 and counts[("val", VTVF)] >= 1, "val needs 1 AFib and 1 VT/VF record"
    print("SPLIT CHECK: PASS (no record in two splits; records train/val/test = %d/%d/%d; "
          "test AFib records %d, test VT/VF records %d, val AFib %d, val VT/VF %d)" % (
              len(parts["train"]), len(parts["val"]), len(parts["test"]), counts[("test", AFIB)],
              counts[("test", VTVF)], counts[("val", AFIB)], counts[("val", VTVF)]))
    return parts


def anti_shortcut(labels, sources):
    problems = []
    notes = []
    for cls, name in enumerate(config.CLASS_NAMES):
        dbs = sorted(set(sources[labels == cls]))
        if len(dbs) >= 2:
            continue
        # only mitdb has checked beat labels, so PVC cannot come from a second database
        if name == "PVC" and dbs == ["mitdb"]:
            notes.append("PVC comes only from mitdb (the other databases have no checked beat labels)")
        else:
            problems.append(f"class {name} comes from {dbs}")
    for db in DATABASES:
        classes = sorted(set(labels[sources == db]))
        if db in sources and len(classes) < 2:
            problems.append(f"source {db} gives only class {classes}")
    for note in notes:
        print("NOTE:", note)
    if problems:
        print("ANTI-SHORTCUT: FAIL")
        for p in problems:
            print("  " + p)
    else:
        print("ANTI-SHORTCUT: PASS")
    return not problems


def main():
    X, y, records, sources, stretched = collect()
    if len(X) == 0:
        print("No windows found. Run ml/download_data.py first.")
        sys.exit(1)

    print("\nWindows per class and source:")
    print("%-8s" % "" + "".join("%9s" % db for db in DATABASES) + "%9s" % "total")
    for cls, name in enumerate(config.CLASS_NAMES):
        row = [int(np.sum((y == cls) & (sources == db))) for db in DATABASES]
        print("%-8s" % name + "".join("%9d" % v for v in row) + "%9d" % sum(row))
    shortcut_ok = anti_shortcut(y, sources)

    split_of = split_records(y, records, sources)
    parts = check_split(y, records, split_of)
    split = np.array([split_of[r] for r in records])
    # the speed-changed copies are training material only, validation and test keep real windows
    keep = ~stretched | (split == "train")
    print("speed-changed Normal windows added to training:", int(np.sum(stretched & keep)))
    X, y, records, sources, split = X[keep], y[keep], records[keep], sources[keep], split[keep]
    print("\nWindows per class and split:")
    for cls, name in enumerate(config.CLASS_NAMES):
        print("%-8s" % name + "".join("%9d" % int(np.sum((y == cls) & (split == s))) for s in ("train", "val", "test")))

    # the rhythm numbers of every window are stored next to it for training
    R = np.array([rhythm_features(w) for w in X], dtype=np.float32)
    np.savez_compressed(OUT_FILE, X=X, R=R, y=y, records=records, sources=sources, split=split)
    SPLIT_FILE.write_text(json.dumps({name: sorted(recs) for name, recs in parts.items()}, indent=2))
    print(f"\nSaved {len(X)} windows to {OUT_FILE}")
    if not shortcut_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
