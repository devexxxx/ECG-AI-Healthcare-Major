import json
import re
import time
from collections import deque

import numpy as np

import config
import pipeline

BEFORE = config.EVENT_BEFORE_S * config.FS
AFTER = config.EVENT_AFTER_S * config.FS
ID_FORMAT = re.compile(r"^\d{8}_\d{6}_\d{2}$")

# the last 30 s of raw samples, and the events still waiting for their 30 s after
history = deque(maxlen=BEFORE)
pending = []


def new_id():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    waiting = [ev["meta"]["id"] for ev in pending]
    # two events in the same second get different end numbers
    for k in range(100):
        event_id = "%s_%02d" % (stamp, k)
        if event_id not in waiting and not (config.EVENTS_DIR / event_id).exists():
            return event_id
    return stamp + "_99"


def start_event(meta):
    meta = dict(meta)
    meta["id"] = new_id()
    meta["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    pending.append({"meta": meta, "before": list(history), "after": []})
    return meta["id"]


def save(ev):
    samples = ev["before"] + ev["after"]
    meta = ev["meta"]
    meta["fs"] = config.FS
    meta["samples"] = len(samples)
    meta["before_samples"] = len(ev["before"])
    # short means less than the full 30 s + 30 s was available
    meta["short"] = len(samples) < BEFORE + AFTER
    folder = config.EVENTS_DIR / meta["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ecg.csv").write_text("adc\n" + "\n".join(str(int(s)) for s in samples) + "\n")
    (folder / "meta.json").write_text(json.dumps(meta, indent=2))
    pending.remove(ev)


def add_samples(samples):
    history.extend(samples)
    saved = False
    # every waiting event gets its own copy of the new samples
    for ev in pending[:]:
        need = AFTER - len(ev["after"])
        ev["after"].extend(samples[:need])
        if len(ev["after"]) >= AFTER:
            save(ev)
            saved = True
    return saved


def flush():
    # the source changed or a simulation ended: save what there is and start clean
    count = len(pending)
    for ev in pending[:]:
        save(ev)
    history.clear()
    return count


def event_folder(event_id):
    # the id comes from the browser, so it is checked before it touches the disk
    if not ID_FORMAT.match(str(event_id)):
        return None
    folder = config.EVENTS_DIR / event_id
    if not (folder / "meta.json").exists():
        return None
    return folder


def list_events():
    found = []
    if not config.EVENTS_DIR.exists():
        return found
    for folder in sorted(config.EVENTS_DIR.iterdir(), reverse=True):
        if not ID_FORMAT.match(folder.name):
            continue
        try:
            found.append(json.loads((folder / "meta.json").read_text()))
        except (OSError, ValueError):
            continue
    return found


def make_pdf(event_id):
    folder = event_folder(event_id)
    if folder is None:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    meta = json.loads((folder / "meta.json").read_text())
    counts = np.loadtxt(folder / "ecg.csv", skiprows=1, ndmin=1)
    mv = pipeline.filter_all(pipeline.counts_to_mv(counts))
    fs = config.FS
    row_len = 10 * fs
    rows = max(1, int(np.ceil(len(mv) / row_len)))

    fig, axes = plt.subplots(rows, 1, figsize=(11.7, 1.4 * rows + 1.2), squeeze=False)
    for r in range(rows):
        ax = axes[r][0]
        part = mv[r * row_len:(r + 1) * row_len]
        t = np.arange(len(part)) / fs
        ax.plot(t, part, color="black", linewidth=0.6)
        # ECG paper at 25 mm/s: thick line every 0.2 s, thin line every 0.04 s
        ax.set_xticks(np.arange(0, 10.01, 0.2))
        ax.set_xticks(np.arange(0, 10.01, 0.04), minor=True)
        ax.set_yticks(np.arange(-2, 2.01, 0.5))
        ax.grid(which="major", color="#e08080", linewidth=0.5)
        ax.grid(which="minor", color="#f3c6c6", linewidth=0.25)
        ax.set_xlim(0, 10)
        ax.set_ylim(-2, 2)
        ax.set_xticklabels([])
        ax.set_yticklabels([])
        ax.tick_params(length=0, which="both")
        ax.set_ylabel("%d s" % (r * 10), rotation=0, labelpad=14, fontsize=7)
    title = "%s  %s  %s (%s)  HR %s  source: %s\napproximate amplitude, not diagnostic" % (
        meta.get("time", ""), meta.get("level", ""), meta.get("kind", ""), meta.get("reason", ""),
        meta.get("bpm", "?"), meta.get("source", "?"))
    fig.suptitle(title, fontsize=9)
    fig.tight_layout()
    out = folder / "ecg.pdf"
    fig.savefig(out)
    plt.close(fig)
    return out
