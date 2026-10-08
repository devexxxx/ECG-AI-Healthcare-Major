import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import config
import sim
from pipeline import load_classifier

RUNS = 3


def main():
    classify, source = load_classifier()
    print("classifier:", source)
    failed = 0
    synthetic = 0
    for button in config.SIM_BUTTONS:
        for run in range(RUNS):
            # each run takes the next clip of the button, like pressing the button again
            clip = sim.next_clip(button)
            if clip is None:
                print("%-12s run %d  FAIL  no clip (run ml/export_sim_clips.py)" % (button, run + 1))
                failed += 1
                continue
            if clip["record"] == "synthetic":
                synthetic += 1
            out = sim.replay_offline(clip, classify, source)
            ok, text = sim.check_replay(button, clip, out)
            failed += 0 if ok else 1
            print("%-12s run %d  %s  %-22s %s" % (button, run + 1, "PASS" if ok else "FAIL", clip["file"], text))

    if synthetic:
        print("NOTE: %d runs used synthetic stand-in clips, not held-out PhysioNet records" % synthetic)
    trained = source in ("cnn", config.RF_LABEL)
    if not trained:
        print("NOTE: no trained model, classes came from the rule-based fallback")
    real = failed == 0 and synthetic == 0 and trained
    print("SIMULATION CHECK: %s" % ("PASS" if failed == 0 else "FAIL (%d runs)" % failed))
    print("Gates G3.2/G3.3 count as passed only with a trained model and real clips: %s" % ("yes" if real else "no"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
