import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import wfdb
from wfdb.io.download import get_version

DATA_DIR = Path(__file__).resolve().parent / "data"

# PhysioNet's own copy on Amazon is much faster than physionet.org, which is the second choice
MIRROR = "https://physionet-open.s3.amazonaws.com"

# afdb records are 10 hours long, so only pieces around rhythm changes are fetched
AFDB_BEFORE_S = 120
AFDB_AFTER_S = 240
AFDB_PIECES_PER_RECORD = 3


def fetch(url, first_byte=None, last_byte=None):
    req = urllib.request.Request(url)
    if first_byte is not None:
        req.add_header("Range", "bytes=%d-%d" % (first_byte, last_byte))
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def try_few_times(job, what):
    # connections drop now and then, so each step gets 3 attempts
    for attempt in range(3):
        try:
            return job()
        except Exception as err:
            print(f"  {what}: attempt {attempt + 1} failed ({err})")
            time.sleep(3)
    return None


def get_file(db, version, name, folder):
    out = folder / name
    if out.exists():
        return True
    # write to a .part file first, so a stopped download never leaves a half file behind
    part = folder / (name + ".part")
    data = try_few_times(lambda: fetch(f"{MIRROR}/{db}/{version}/{name}"), f"{db}/{name} (mirror)")
    if data is not None:
        part.write_bytes(data)
        part.replace(out)
        return True
    try_few_times(lambda: wfdb.dl_files(db, str(folder), [name]), f"{db}/{name} (physionet.org)")
    return out.exists()


def get_record(db, version, rec, folder, with_signal):
    ok = get_file(db, version, rec + ".hea", folder) and get_file(db, version, rec + ".atr", folder)
    if ok and with_signal:
        # the header says which signal file belongs to the record
        try:
            for name in set(wfdb.rdheader(str(folder / rec)).file_name):
                ok = get_file(db, version, name, folder) and ok
        except Exception as err:
            print(f"  {db}/{rec}: bad header ({err})")
            ok = False
    return ok


def download_whole_db(db):
    folder = DATA_DIR / db
    folder.mkdir(parents=True, exist_ok=True)
    records = try_few_times(lambda: wfdb.get_record_list(db), db + " record list")
    version = try_few_times(lambda: get_version(db), db + " version")
    if records is None or version is None:
        print(f"{db}: could not read the record list, skipped")
        return [0, 0]
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda rec: get_record(db, version, rec, folder, True), records))
    for rec, ok in zip(records, results):
        if not ok:
            print(f"  {db}/{rec}: not complete, run the script again")
    print(f"{db}: {sum(results)}/{len(records)} records ready")
    return [sum(results), len(records)]


def rhythm_spans(ann, total):
    # turn the rhythm change marks into (start, end, name) spans
    marks = []
    for sample, note in zip(ann.sample, ann.aux_note):
        note = note.strip("\x00").strip()
        if note.startswith("("):
            marks.append((int(sample), note))
    spans = []
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else total
        spans.append((start, end, name))
    return spans


def pick_afdb_pieces(spans, fs, total):
    before = AFDB_BEFORE_S * fs
    after = AFDB_AFTER_S * fs
    # best pieces are AFib onsets with a long normal part before and long AFib after
    onsets = []
    for i in range(1, len(spans)):
        prev, cur = spans[i - 1], spans[i]
        if cur[2] == "(AFIB" and prev[2] == "(N":
            score = min(prev[1] - prev[0], cur[1] - cur[0])
            onsets.append((score, cur[0]))
    onsets.sort(reverse=True)
    pieces = []
    for score, t in onsets[:AFDB_PIECES_PER_RECORD]:
        pieces.append((max(0, t - before), min(total, t + after)))
    if pieces:
        return pieces
    # no usable onset: take the middle of the longest AFib span and the longest normal span
    for name in ("(AFIB", "(N"):
        same = [s for s in spans if s[2] == name]
        if same:
            start, end, _ = max(same, key=lambda s: s[1] - s[0])
            mid = (start + end) // 2
            half = (before + after) // 2
            pieces.append((max(start, mid - half), min(end, mid + half)))
    return pieces


def decode_212(data, gain, baseline):
    # WFDB format 212: two 12-bit samples in 3 bytes, here one from each of the 2 channels
    raw = np.frombuffer(data, dtype=np.uint8).astype(np.int32).reshape(-1, 3)
    first = ((raw[:, 1] & 0x0F) << 8) | raw[:, 0]
    first[first > 2047] -= 4096
    return ((first - baseline) / gain).astype(np.float32)


def get_piece(version, rec, head, start, end):
    # a 2-channel format 212 file has exactly 3 bytes per sample time
    if head.n_sig == 2 and list(head.fmt) == ["212", "212"]:
        url = f"{MIRROR}/afdb/{version}/{head.file_name[0]}"
        data = try_few_times(lambda: fetch(url, 3 * start, 3 * end - 1), f"afdb/{rec} piece {start} (mirror)")
        if data is not None and len(data) == 3 * (end - start):
            return decode_212(data, head.adc_gain[0], head.baseline[0])
    part = try_few_times(
        lambda: wfdb.rdrecord(rec, pn_dir="afdb", sampfrom=start, sampto=end, channels=[0]),
        f"afdb/{rec} piece {start} (physionet.org)",
    )
    if part is None:
        return None
    return part.p_signal[:, 0].astype(np.float32)


def get_afdb_record(version, rec, folder):
    if not get_record("afdb", version, rec, folder, False):
        return False
    try:
        head = wfdb.rdheader(str(folder / rec))
        ann = wfdb.rdann(str(folder / rec), "atr")
    except Exception as err:
        print(f"  afdb/{rec}: cannot read ({err}), skipped")
        return False
    if head.sig_len is None or not head.n_sig:
        print(f"  afdb/{rec}: has no signal file, skipped")
        return False
    pieces = pick_afdb_pieces(rhythm_spans(ann, head.sig_len), int(head.fs), head.sig_len)
    ok = len(pieces) > 0
    for start, end in pieces:
        out = folder / "pieces" / f"{rec}_{start}_{end}.npy"
        if out.exists():
            continue
        piece = get_piece(version, rec, head, start, end)
        if piece is None:
            ok = False
            continue
        np.save(out, piece)
    return ok


def download_afdb_pieces():
    folder = DATA_DIR / "afdb"
    (folder / "pieces").mkdir(parents=True, exist_ok=True)
    records = try_few_times(lambda: wfdb.get_record_list("afdb"), "afdb record list")
    version = try_few_times(lambda: get_version("afdb"), "afdb version")
    if records is None or version is None:
        print("afdb: could not read the record list, skipped")
        return [0, 0]
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda rec: get_afdb_record(version, rec, folder), records))
    print(f"afdb: {sum(results)}/{len(records)} records have their pieces")
    return [sum(results), len(records)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimal", action="store_true", help="only mitdb and cudb")
    args = parser.parse_args()

    summary = {}
    summary["mitdb"] = download_whole_db("mitdb")
    summary["cudb"] = download_whole_db("cudb")
    if not args.minimal:
        summary["vfdb"] = download_whole_db("vfdb")
        summary["afdb"] = download_afdb_pieces()

    (DATA_DIR / "download_summary.json").write_text(json.dumps(summary, indent=2))
    print("DOWNLOAD SUMMARY (records ready / records listed):", summary)


if __name__ == "__main__":
    main()
