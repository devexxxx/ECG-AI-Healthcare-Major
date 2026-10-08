import numpy as np
import torch
from torch import nn

from pipeline import rhythm_features


class ECGNet(nn.Module):
    def __init__(self):
        super().__init__()
        # three conv blocks with 16, 32 and 64 filters, each one shrinks the signal 4 times
        self.features = nn.Sequential(
            nn.Conv1d(1, 16, kernel_size=7, padding=3), nn.BatchNorm1d(16), nn.ReLU(), nn.MaxPool1d(4),
            nn.Conv1d(16, 32, kernel_size=7, padding=3), nn.BatchNorm1d(32), nn.ReLU(), nn.MaxPool1d(4),
            nn.Conv1d(32, 64, kernel_size=7, padding=3), nn.BatchNorm1d(64), nn.ReLU(), nn.MaxPool1d(4),
        )
        # the conv part sees beat shape (under 1 s at a time), this small branch adds the beat timing
        self.rhythm = nn.Sequential(nn.Linear(7, 16), nn.ReLU())
        self.drop = nn.Dropout(0.3)
        self.out = nn.Linear(64 + 16, 4)

    def forward(self, x, r):
        # global average pool: one number per filter for the whole 5 s window
        shape = self.features(x).mean(dim=2)
        return self.out(self.drop(torch.cat([shape, self.rhythm(r)], dim=1)))


def load_model(path):
    # one thread is enough for this small net and keeps the server responsive
    torch.set_num_threads(1)
    net = ECGNet()
    net.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    net.eval()
    return net


def predict(net, window):
    # window is one z-scored 5 s window, the result is 4 class probabilities
    x = torch.from_numpy(np.asarray(window, dtype=np.float32)).view(1, 1, -1)
    r = torch.from_numpy(rhythm_features(window)).view(1, -1)
    with torch.no_grad():
        probs = torch.softmax(net(x, r), dim=1)
    return probs[0].numpy()
