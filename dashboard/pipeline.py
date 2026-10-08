import numpy as np
from scipy import signal

import config

FS = config.FS
KEEP = 8 * FS


def make_filter():
    # band-pass 0.5-40 Hz removes drift and muscle noise, the notch removes 50 Hz mains hum
    band = signal.butter(2, [0.5, 40], btype="bandpass", fs=FS, output="sos")
    b, a = signal.iirnotch(50, 30, fs=FS)
    return np.vstack([band, signal.tf2sos(b, a)])


SOS = make_filter()


def counts_to_mv(counts):
    # rough conversion of 12-bit ADC counts to millivolts at the skin (not calibrated)
    return (np.asarray(counts, dtype=float) * 3.3 / 4095 - 1.5) / 1.1


def filter_start_state(first_value):
    # start the filter as if the input had always been at the first value (smaller start-up jump)
    return signal.sosfilt_zi(SOS) * first_value


def filter_all(x):
    # same causal filter as the live path, used for whole recordings in the ML scripts
    y, _ = signal.sosfilt(SOS, x, zi=filter_start_state(x[0]))
    return y


def zscore(window):
    window = np.asarray(window, dtype=float)
    return (window - window.mean()) / (window.std() + 1e-6)


def find_r_peaks(x):
    x = np.asarray(x, dtype=float)
    if len(x) < FS:
        return np.array([], dtype=int)
    # a QRS is where the signal changes fastest, whichever way the lead points
    slope = np.diff(x)
    energy = np.convolve(slope * slope, np.ones(30) / 30, mode="same")
    # beats are the tall bumps of that energy, at least 250 ms apart
    peaks, _ = signal.find_peaks(energy, height=0.25 * np.percentile(energy, 99), distance=int(0.25 * FS))
    return peaks


def rhythm_features(window):
    # numbers that describe how regular the beats of one 5 s window are
    peaks = find_r_peaks(window)
    if len(peaks) < 4:
        return np.array([0, 0, 0, 0, 0, len(peaks) / 10, 1], dtype=np.float32)
    rr = np.diff(peaks) / FS
    change = np.diff(rr)
    return np.array([
        rr.mean(),
        rr.std() / rr.mean(),
        np.sqrt(np.mean(change ** 2)) / rr.mean(),
        np.mean(np.abs(change) > 0.05),
        np.mean(rr < 0.8 * np.median(rr)),
        len(peaks) / 10,
        0,
    ], dtype=np.float32)


def rr_features(window):
    # the five RR-interval numbers of GUIDE 8.11: mean RR, SDNN, RMSSD, pNN50, premature beats
    peaks = find_r_peaks(window)
    if len(peaks) < 3:
        return np.zeros(5, dtype=np.float32)
    rr = np.diff(peaks) / FS
    change = np.diff(rr)
    return np.array([
        rr.mean(),
        rr.std(),
        np.sqrt(np.mean(change ** 2)),
        np.mean(np.abs(change) > 0.05),
        np.sum(rr < 0.8 * np.median(rr)),
    ], dtype=np.float32)


def bpm_from_peaks(peaks, end_index):
    # no beat in the last 3 s means there is no heart rate to show
    if len(peaks) < 2 or end_index - peaks[-1] > 3 * FS:
        return 0
    rr = np.diff(peaks[-6:]) / FS
    bpm = 60.0 / np.median(rr)
    if bpm < 20 or bpm > 250:
        return 0
    return int(round(bpm))


def fallback_classify(window):
    # simple rhythm rules, used only when there is no trained CNN
    mean_rr, spread, _, _, early, _, too_few = rhythm_features(window)
    probs = np.array([0.85, 0.05, 0.05, 0.05])
    if too_few:
        return probs
    if 60.0 / mean_rr > 180:
        probs = np.array([0.05, 0.05, 0.05, 0.85])
    elif spread > 0.15:
        probs = np.array([0.05, 0.05, 0.85, 0.05])
    elif early > 0:
        probs = np.array([0.05, 0.85, 0.05, 0.05])
    return probs


def load_forest():
    # the RandomForest fallback is a scikit-learn model saved by ml/train_rf.py
    import joblib
    forest = joblib.load(config.RF_PATH)
    forest.set_params(n_jobs=1)
    return lambda window: forest.predict_proba(rr_features(window).reshape(1, -1))[0]


def load_classifier():
    # the returned label always says which classifier is really in use
    if config.CLASSIFIER == "rf" and config.RF_PATH.exists():
        return load_forest(), config.RF_LABEL
    # torch is imported only when a trained model file exists
    if config.CLASSIFIER == "cnn" and config.MODEL_PATH.exists():
        import model
        net = model.load_model(config.MODEL_PATH)
        return (lambda window: model.predict(net, window)), "cnn"
    return fallback_classify, config.FALLBACK_LABEL


class Pipeline:
    def __init__(self, classify, source):
        self.classify = classify
        self.source = source
        self.reset()

    def reset(self):
        self.raw = np.zeros(0)
        self.filt = np.zeros(0)
        self.lead = np.zeros(0, dtype=bool)
        self.state = None
        self.total = 0
        self.since_tick = 0
        self.good_seconds = 0
        self.bpm = 0
        self.quality = "starting"
        self.flat = False
        self.adc = [0, 0, 0]

    def check_quality(self):
        last = self.raw[-FS:]
        self.flat = bool(np.ptp(last) < config.FLATLINE_PTP_COUNTS)
        if self.lead[-FS:].any():
            return "leads off"
        # a signal stuck at either end of the ADC range is not an ECG
        if np.mean((last <= 10) | (last >= 4085)) > 0.2:
            return "saturated"
        if self.flat:
            return "flat"
        return "ok"

    def add(self, samples, lead_off):
        raw = np.asarray(samples, dtype=float)
        mv = counts_to_mv(raw)
        if self.state is None:
            self.state = filter_start_state(mv[0])
        y, self.state = signal.sosfilt(SOS, mv, zi=self.state)

        self.raw = np.concatenate([self.raw, raw])[-KEEP:]
        self.filt = np.concatenate([self.filt, y])[-KEEP:]
        self.lead = np.concatenate([self.lead, np.full(len(raw), bool(lead_off))])[-KEEP:]
        self.total += len(raw)
        self.since_tick += len(raw)

        result = {"y": y, "second": False, "cls": None}
        # everything below runs once per second of signal
        if self.since_tick >= FS:
            self.since_tick -= FS
            result["second"] = True
            # raw ADC numbers for the bench gates: lowest and highest of 5 s, spread of the last 1 s
            last5 = self.raw[-5 * FS:]
            self.adc = [int(last5.min()), int(last5.max()), int(np.ptp(self.raw[-FS:]))]
            self.quality = self.check_quality()
            self.good_seconds = self.good_seconds + 1 if self.quality == "ok" else 0
            self.bpm = 0
            if self.quality == "ok":
                self.bpm = bpm_from_peaks(find_r_peaks(self.filt), len(self.filt))
            # classify only when the whole 5 s window is clean
            if self.good_seconds >= config.WINDOW_S:
                probs = self.classify(zscore(self.filt[-config.WINDOW:]))
                best = int(np.argmax(probs))
                result["cls"] = {
                    "name": config.CLASS_NAMES[best],
                    "conf": round(float(probs[best]), 3),
                    "source": self.source,
                }
        result["bpm"] = self.bpm
        result["quality"] = self.quality
        result["flat"] = self.flat
        result["adc"] = self.adc
        return result
