import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent / "dashboard"))

import config
import pipeline

DATA_FILE = ML_DIR / "data" / "windows.npz"
# each classifier writes its own files, so one result never overwrites the other
OUT_FILES = {"cnn": ("results.json", "confusion_matrix.png"), "rf": ("results_rf.json", "confusion_matrix_rf.png")}


def save_matrix_picture(matrix, label, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(5, 4.5))
    ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(4), config.CLASS_NAMES)
    ax.set_yticks(range(4), config.CLASS_NAMES)
    ax.set_xlabel("predicted")
    ax.set_ylabel("true")
    ax.set_title("Test records only (windows), " + label, fontsize=9)
    for i in range(4):
        for j in range(4):
            ax.text(j, i, str(matrix[i, j]), ha="center", va="center",
                    color="white" if matrix[i, j] > matrix.max() / 2 else "black")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    # "python ml\evaluate.py" checks the CNN, "python ml\evaluate.py rf" checks the RandomForest fallback
    which = sys.argv[1] if len(sys.argv) > 1 else "cnn"
    model_file = config.RF_PATH if which == "rf" else config.MODEL_PATH
    if which not in OUT_FILES or not model_file.exists() or not DATA_FILE.exists():
        print("Need ml/data/windows.npz and the model file (train.py for cnn, train_rf.py for rf).")
        sys.exit(1)
    # the model is loaded exactly the way the dashboard loads it
    config.CLASSIFIER = which
    classify, label = pipeline.load_classifier()
    result_file, matrix_file = [ML_DIR / name for name in OUT_FILES[which]]
    print("classifier evaluated:", label)
    data = np.load(DATA_FILE)
    test = data["split"] == "test"
    X, y, sources = data["X"][test], data["y"][test], data["sources"][test]
    records = sorted(set(data["records"][test]))
    print("test records: %d, test windows: %d" % (len(records), len(X)))

    pred = np.array([int(np.argmax(classify(w))) for w in X])
    matrix = confusion_matrix(y, pred, labels=[0, 1, 2, 3])
    prec, rec, f1, support = precision_recall_fscore_support(y, pred, labels=[0, 1, 2, 3], zero_division=0)
    macro_f1 = float(f1.mean())

    print("\nConfusion matrix (rows = true class, columns = predicted class):")
    print("%-8s" % "" + "".join("%8s" % n for n in config.CLASS_NAMES))
    for i, name in enumerate(config.CLASS_NAMES):
        print("%-8s" % name + "".join("%8d" % v for v in matrix[i]))
    print("\n%-8s%10s%10s%10s%10s" % ("class", "precision", "recall", "F1", "windows"))
    for i, name in enumerate(config.CLASS_NAMES):
        print("%-8s%10.3f%10.3f%10.3f%10d" % (name, prec[i], rec[i], f1[i], support[i]))
    print("macro-F1: %.3f" % macro_f1)

    print("\nRecall per source database:")
    by_source = {}
    for db in sorted(set(sources)):
        part = {}
        for i, name in enumerate(config.CLASS_NAMES):
            mask = (sources == db) & (y == i)
            if mask.sum() > 0:
                part[name] = [round(float(np.mean(pred[mask] == i)), 3), int(mask.sum())]
        by_source[db] = part
        print("  %-6s %s" % (db, part))

    # time one window at a time, the way the dashboard calls the model
    times = []
    for w in X[:300]:
        start = time.perf_counter()
        classify(w)
        times.append((time.perf_counter() - start) * 1000)
    median_ms = float(np.median(times))
    worst_ms = float(np.max(times))

    targets = {
        "macro-F1 >= 0.80": macro_f1 >= 0.80,
        "VT/VF recall >= 0.90": rec[3] >= 0.90,
        "AFib recall >= 0.85": rec[2] >= 0.85,
        "inference < 50 ms per window": median_ms < 50,
    }
    print("\ninference: median %.2f ms, worst %.2f ms per window" % (median_ms, worst_ms))
    for name, ok in targets.items():
        print("%-32s %s" % (name, "PASS" if ok else "FAIL"))

    save_matrix_picture(matrix, label, matrix_file)
    result_file.write_text(json.dumps({
        "classifier": label, "test_records": records, "test_windows": int(len(X)), "confusion_matrix": matrix.tolist(),
        "classes": config.CLASS_NAMES, "precision": prec.tolist(), "recall": rec.tolist(), "f1": f1.tolist(),
        "macro_f1": macro_f1, "recall_by_source": by_source, "inference_ms_median": median_ms,
        "inference_ms_worst": worst_ms, "targets": {k: bool(v) for k, v in targets.items()},
    }, indent=2))
    print("saved %s and %s" % (result_file.name, matrix_file.name))


if __name__ == "__main__":
    main()
