import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, recall_score

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent / "dashboard"))

import config
from pipeline import rr_features

DATA_FILE = ML_DIR / "data" / "windows.npz"


def main():
    if not DATA_FILE.exists():
        print("Run ml/prepare_data.py first.")
        sys.exit(1)
    # same windows, labels and record split as the CNN (GUIDE 8.11)
    data = np.load(DATA_FILE)
    y, split = data["y"], data["split"]
    print("computing the 5 RR features for %d windows..." % len(y))
    F = np.array([rr_features(w) for w in data["X"]])
    train = split == "train"
    val = split == "val"

    # balanced class weights do the same job as the weighted loss of the CNN
    forest = RandomForestClassifier(n_estimators=200, min_samples_leaf=3, class_weight="balanced",
                                    random_state=1, n_jobs=-1)
    forest.fit(F[train], y[train])

    pred = forest.predict(F[val])
    recalls = recall_score(y[val], pred, average=None, labels=[0, 1, 2, 3], zero_division=0)
    print("classifier:", config.RF_LABEL)
    print("train windows %d, validation windows %d" % (train.sum(), val.sum()))
    print("validation macro-F1 %.3f, recall N/PVC/AFib/VTVF %s" % (
        f1_score(y[val], pred, average="macro"), " ".join("%.2f" % r for r in recalls)))
    names = ["mean RR", "SDNN", "RMSSD", "pNN50", "premature beats"]
    print("feature importance:", {n: round(float(v), 3) for n, v in zip(names, forest.feature_importances_)})

    joblib.dump(forest, config.RF_PATH, compress=3)
    print("saved", config.RF_PATH)


if __name__ == "__main__":
    main()
