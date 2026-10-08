import json
import sys
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import f1_score, recall_score
from torch import nn

ML_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ML_DIR.parent / "dashboard"))

import config
from model import ECGNet

DATA_FILE = ML_DIR / "data" / "windows.npz"
LOG_FILE = ML_DIR / "train_log.json"
EPOCHS = 25
BATCH = 64


def predict_all(net, X, R):
    net.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(X), 512):
            out.append(net(X[i:i + 512], R[i:i + 512]).argmax(dim=1))
    return torch.cat(out).numpy()


def main():
    if not DATA_FILE.exists():
        print("Run ml/prepare_data.py first.")
        sys.exit(1)
    torch.manual_seed(1)
    np.random.seed(1)

    data = np.load(DATA_FILE)
    X = torch.from_numpy(data["X"]).unsqueeze(1)
    R = torch.from_numpy(data["R"])
    y = torch.from_numpy(data["y"].astype(np.int64))
    split = data["split"]
    train = np.where(split == "train")[0]
    val = np.where(split == "val")[0]

    # rare classes get a bigger weight so the loss does not just learn "Normal"
    counts = np.bincount(data["y"][train], minlength=4)
    weights = torch.tensor(len(train) / (4.0 * counts), dtype=torch.float32)
    print("train windows per class:", dict(zip(config.CLASS_NAMES, counts.tolist())))
    print("class weights:", [round(float(w), 2) for w in weights])

    net = ECGNet()
    print("parameters:", sum(p.numel() for p in net.parameters()))
    loss_fn = nn.CrossEntropyLoss(weight=weights)
    opt = torch.optim.Adam(net.parameters(), lr=1e-3)

    best_f1 = -1.0
    log = []
    for epoch in range(1, EPOCHS + 1):
        net.train()
        order = np.random.permutation(train)
        total = 0.0
        for i in range(0, len(order), BATCH):
            idx = order[i:i + BATCH]
            opt.zero_grad()
            loss = loss_fn(net(X[idx], R[idx]), y[idx])
            loss.backward()
            opt.step()
            total += loss.item() * len(idx)

        # the epoch with the best validation macro-F1 is the one that is kept
        pred = predict_all(net, X[val], R[val])
        f1 = f1_score(y[val].numpy(), pred, average="macro")
        recalls = recall_score(y[val].numpy(), pred, average=None, labels=[0, 1, 2, 3], zero_division=0)
        kept = f1 > best_f1
        if kept:
            best_f1 = f1
            torch.save(net.state_dict(), config.MODEL_PATH)
        log.append({"epoch": epoch, "loss": total / len(train), "val_macro_f1": float(f1),
                    "val_recall": [float(r) for r in recalls], "saved": bool(kept)})
        print("epoch %2d  loss %.4f  val macro-F1 %.3f  val recall N/PVC/AFib/VTVF %s%s" % (
            epoch, total / len(train), f1, " ".join("%.2f" % r for r in recalls), "  (saved)" if kept else ""))

    LOG_FILE.write_text(json.dumps(log, indent=2))
    print("best validation macro-F1 %.3f, model saved to %s" % (best_f1, config.MODEL_PATH))


if __name__ == "__main__":
    main()
