import sys
from pathlib import Path

import numpy as np

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent / "dashboard"))

import config
from pipeline import Pipeline, load_classifier


def check_file(path, classify, source):
    # the recording goes through the same pipeline as the live signal, 25 samples at a time
    counts = np.loadtxt(path, skiprows=1, ndmin=1)
    pipe = Pipeline(classify, source)
    names = []
    bad_seconds = 0
    for i in range(0, len(counts) - config.BATCH_SIZE + 1, config.BATCH_SIZE):
        result = pipe.add(counts[i:i + config.BATCH_SIZE], False)
        if result["cls"] is not None:
            names.append(result["cls"]["name"])
        if result["second"] and result["quality"] != "ok":
            bad_seconds += 1
    return len(counts) / config.FS, names, bad_seconds


def main():
    files = [Path(p) for p in sys.argv[1:]] or sorted(config.RECORD_DIR.glob("rec_*.csv"))
    if not files:
        print("No recordings. Use 'Start recording' on the dashboard while the device is on your body.")
        sys.exit(1)
    classify, source = load_classifier()
    print("classifier:", source)

    total_seconds = 0
    all_names = []
    for path in files:
        seconds, names, bad = check_file(path, classify, source)
        total_seconds += seconds
        all_names += names
        counts = {n: names.count(n) for n in config.CLASS_NAMES}
        share = 100.0 * counts["Normal"] / max(1, len(names))
        print("%s: %.0f s, %d windows, %.1f%% Normal, %s, %d s with bad signal" % (
            path.name, seconds, len(names), share, counts, bad))

    share = 100.0 * all_names.count("Normal") / max(1, len(all_names))
    print("TOTAL: %d files, %.0f s, %d windows, %.1f%% Normal" % (len(files), total_seconds, len(all_names), share))
    enough = total_seconds >= 180 and len(files) >= 2
    if not enough:
        print("Need at least 3 minutes in 2-3 sittings before this counts for gate G3.1.")
    trained = source in ("cnn", config.RF_LABEL)
    if not trained:
        print("No trained model: this result is from the rule-based fallback and does not count for G3.1.")
    passed = share >= 90 and enough and trained
    print("OWN SIGNAL CHECK: " + ("PASS" if passed else "FAIL / NOT COMPLETE"))
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
