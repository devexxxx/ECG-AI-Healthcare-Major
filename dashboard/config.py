from pathlib import Path

# every path is built from this file, so the dashboard starts from any folder
DASH_DIR = Path(__file__).resolve().parent
ROOT_DIR = DASH_DIR.parent
STATIC_DIR = DASH_DIR / "static"
MODEL_PATH = ROOT_DIR / "ml" / "ecg_cnn.pt"
RF_PATH = ROOT_DIR / "ml" / "ecg_rf.joblib"
SIM_DIR = ROOT_DIR / "sim_clips"
EVENTS_DIR = ROOT_DIR / "events"
RECORD_DIR = ROOT_DIR / "recordings"
NOTIFY_CONFIG = DASH_DIR / "notify_config.json"

SERVER_PORT = 8000
DISCOVERY_PORT = 4210
DISCOVERY_EVERY_S = 2

# these must match firmware/ecg_monitor/config.h (tools/check_config.py compares them)
SAMPLE_RATE = 250
BATCH_SIZE = 25
BRADY_MIN_BPM = 20
BRADY_MAX_BPM = 44
TACHY_MIN_BPM = 130
RULE_HOLD_S = 10
FLATLINE_PTP_COUNTS = 40
FLATLINE_HOLD_S = 5
COUNTDOWN_S = 15
RELAY_WAIT_S = 10
SIM_TIMEOUT_S = 5

FS = SAMPLE_RATE
WINDOW_S = 5
WINDOW = WINDOW_S * FS
CONFIRM_WINDOWS = 3
CONFIRM_PROB = 0.80
EVENT_BEFORE_S = 30
EVENT_AFTER_S = 30
TELEGRAM_TIMEOUT_S = 8

CLASS_NAMES = ["Normal", "PVC", "AFib", "VT/VF"]
FALLBACK_LABEL = "rule-based fallback (no trained model)"
RF_LABEL = "rr-forest (GUIDE 8.11 fallback)"
# which trained classifier the dashboard uses: "cnn" or "rf" (the choice is explained in NOTES.md)
CLASSIFIER = "cnn"

# alert level and message reason for each thing that can raise an alert
ALERTS = {
    "PVC": ("INFO", "PVC beats seen"),
    "AFib": ("WARNING", "irregular rhythm"),
    "VT/VF": ("CRITICAL", "dangerous fast rhythm"),
    "Bradycardia": ("CRITICAL", "very slow heart rate"),
    "Tachycardia": ("CRITICAL", "very fast heart rate"),
    "Flatline": ("CRITICAL", "no heartbeat signal"),
    "Panic": ("CRITICAL", "panic button"),
}

SIM_BUTTONS = ["Normal", "PVC", "AFib", "VTach", "VFib", "Bradycardia", "Tachycardia", "Flatline", "Noisy"]
