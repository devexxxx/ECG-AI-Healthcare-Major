import argparse
import sys
from pathlib import Path

import numpy as np


def read_segment(path):
    # first line is "fs=250,start_ms=...,epoch=...", then one ADC value per line
    lines = path.read_text().split("\n")
    head = dict(part.split("=") for part in lines[0].strip().split(",") if "=" in part)
    values = [int(v) for v in lines[1:] if v.strip().lstrip("-").isdigit()]
    return {"name": path.name, "fs": int(head.get("fs", 250)), "start_ms": int(head.get("start_ms", 0)),
            "epoch": int(head.get("epoch", 0)), "adc": np.array(values)}


def event_files(folder, number):
    # time order of one event: the older pre segment first, then the newer one, then the two post segments
    names = ["evt_%03d_%s.csv" % (number, part) for part in ("pre2", "pre1", "post1", "post2")]
    return [folder / n for n in names if (folder / n).exists()]


def main():
    parser = argparse.ArgumentParser(description="Plot ECG files from the device SD card")
    parser.add_argument("path", help="SD card folder, or one csv file")
    parser.add_argument("--event", type=int, help="event number, for example 3 for evt_003_*")
    parser.add_argument("--out", help="save the plot as a PNG instead of opening a window")
    args = parser.parse_args()

    path = Path(args.path)
    if path.is_file():
        files = [path]
        title = path.name
    elif args.event is not None:
        files = event_files(path, args.event)
        title = "event %03d" % args.event
        meta = path / ("evt_%03d_meta.txt" % args.event)
        if meta.exists():
            print(meta.read_text().strip())
    else:
        files = sorted(path.glob("ring_*.csv"))
        title = "ring buffer"
        events = sorted({p.name[4:7] for p in path.glob("evt_*_meta.txt")})
        print("events on the card:", ", ".join(events) if events else "none")
    if not files:
        print("no files found")
        sys.exit(1)

    segments = [read_segment(f) for f in files]
    # ring files are put in time order by the start time written in their first line
    if args.event is None and not path.is_file():
        segments.sort(key=lambda s: (s["epoch"], s["start_ms"]))

    total = 0
    for seg in segments:
        seconds = len(seg["adc"]) / seg["fs"]
        total += len(seg["adc"])
        print("%-20s %6d samples  %5.1f s  start_ms=%d epoch=%d" % (seg["name"], len(seg["adc"]), seconds,
                                                                    seg["start_ms"], seg["epoch"]))
    print("total %d samples, %.1f s, order: %s" % (total, total / segments[0]["fs"],
                                                 " -> ".join(s["name"] for s in segments)))

    import matplotlib
    if args.out:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(len(segments), 1, figsize=(12, 2.2 * len(segments)), squeeze=False)
    for ax, seg in zip(axes[:, 0], segments):
        ax.plot(np.arange(len(seg["adc"])) / seg["fs"], seg["adc"], linewidth=0.6, color="black")
        ax.set_ylabel("ADC counts")
        ax.set_title(seg["name"], fontsize=9, loc="left")
        ax.grid(alpha=0.3)
    axes[-1, 0].set_xlabel("seconds from the start of each file")
    fig.suptitle(title + " (raw ADC, not diagnostic)")
    fig.tight_layout()
    if args.out:
        fig.savefig(args.out, dpi=130)
        print("saved", args.out)
    else:
        plt.show()


if __name__ == "__main__":
    main()
