# ECG Monitor with Live AI Anomaly Detection

Wearable single-lead ECG (AD8232 + ESP32) that streams to a local web dashboard. The dashboard filters the signal, shows BPM and classifies 5 s windows with a small 1D-CNN (Normal, PVC, AFib, VT/VF). The device has a panic button, GPS, an SD rolling buffer and a notification ladder (Telegram through the dashboard, Telegram directly from the ESP32, SMS as an optional fallback).

**Demo prototype, not a medical device. Single lead, not diagnostic.**
SMS over GSM is implemented but unverified (no SIM was available). See `docs/NOTES.md` section 6 for all limitations.

- What to do next, step by step: **`END_TO_END_COMPLETION_GUIDE.md`**
- Specification: `docs/GUIDE.md`. Decisions, test results, viva notes: `docs/NOTES.md`. Status: `docs/PROGRESS.md`.

## Folders

```
README.md  END_TO_END_COMPLETION_GUIDE.md
docs/        GUIDE.md NOTES.md PROGRESS.md
firmware/
  ecg_monitor/   real firmware (Arduino tabs) + ecg_logic.h/.cpp (pure C++) + config.example.h
  p0_tests/      one test sketch per hardware gate (t1 ... t9)
  host_tests/    PC test of ecg_logic (no ESP32 needed)
dashboard/     server.py pipeline.py model.py sim.py events.py rules.py notify.py config.py
               static/index.html  test_dashboard.py  requirements*.txt  notify_config.example.json
ml/            download_data.py prepare_data.py train.py evaluate.py export_sim_clips.py own_signal_check.py
               ecg_cnn.pt results.json confusion_matrix.png train_log.json   (data/ is downloaded, not shared)
sim_clips/     replay clips cut from held-out test records + clips.json
tools/         fake_device.py sd_viewer.py verify_simulations.py telegram_setup.py check_config.py
events/ recordings/   created by the dashboard while it runs
```

## Setup

Python 3.10 or newer:
```
pip install -r dashboard\requirements.txt
pip install -r dashboard\requirements-train.txt
```
The second line (torch, wfdb, scikit-learn) is needed for training and for running the CNN. Without `ml\ecg_cnn.pt` the dashboard still runs and uses a rule-based fallback that is always labelled as such.

Secrets (never commit or share the copies):
```
copy firmware\ecg_monitor\config.example.h firmware\ecg_monitor\config.h
copy dashboard\notify_config.example.json dashboard\notify_config.json
python tools\telegram_setup.py --send
python tools\check_config.py
```

## Run the dashboard

```
python dashboard\server.py
```
Open `http://localhost:8000` (or `http://<laptop address>:8000` from the phone). It works from any working directory and needs no internet for the page itself; only Telegram delivery needs internet. Use a phone hotspot (2.4 GHz, mobile data on), not college WiFi. On Windows allow Python on Private networks (TCP 8000, UDP 4210).

The page has a LIVE / SIMULATION source switch. A simulation replays a recorded clip through the same processing, rules, alert and event code as live ECG (docs/GUIDE.md section 5.0). With the ESP32 connected the device reacts for real; without it the dashboard runs the countdown and the Telegram step itself.

Without the device:
```
python tools\fake_device.py --seconds 70
python tools\fake_device.py --notify-test
```

## Firmware

- Arduino IDE, **esp32 by Espressif Systems 3.3.12**, board **ESP32 Dev Module**, Partition Scheme **Huge APP (3MB No OTA/1MB SPIFFS)**. The firmware refuses to compile on core 2.x.
- Libraries: Adafruit SSD1306, Adafruit GFX Library, TinyGPSPlus, ArduinoJson 7.x, WebSockets (Markus Sattler).
- Open `firmware\ecg_monitor\ecg_monitor.ino` and upload, or:
```
arduino-cli compile --fqbn esp32:esp32:esp32:PartitionScheme=huge_app firmware\ecg_monitor
```
- Never wear electrodes while USB is connected. Upload, unplug USB, then run from the power bank.

Pins: ECG GPIO34, LO+ 32, LO- 33, OLED SDA/SCL 21/22, SD CS/SCK/MISO/MOSI 5/18/19/23, GPS RX 16, SIM800L RX/TX/RST 25/26/27, green LED 13, red LED 14, buzzer 4, button 15.

## Tests

```
cd dashboard
python -m pytest -q
cd ..
python tools\check_config.py
python tools\verify_simulations.py
python -m ziglang c++ -std=c++11 -I firmware\ecg_monitor firmware\host_tests\test_logic.cpp firmware\ecg_monitor\ecg_logic.cpp -o test_logic.exe
.\test_logic.exe
```
(`g++` works in place of `python -m ziglang c++`.)

## ML pipeline

```
python ml\download_data.py        (PhysioNet mitdb, cudb, vfdb and pieces of afdb; --minimal for mitdb + cudb)
python ml\prepare_data.py         (250 Hz, same filter as live, 5 s windows, split by record, anti-shortcut check)
python ml\train.py                (1D-CNN, best validation epoch saved to ml\ecg_cnn.pt)
python ml\evaluate.py             (CNN on test records only: confusion matrix, precision/recall, inference time)
python ml\train_rf.py             (GUIDE 8.11 fallback: RandomForest on 5 RR features, same windows and split)
python ml\evaluate.py rf          (same test for the fallback, saved to results_rf.json, CNN results are kept)
python ml\export_sim_clips.py     (replay clips from test records, Brady 38 / Tachy 150 BPM measured)
python ml\own_signal_check.py     (your own recordings from the dashboard "Start recording" button)
```
The dashboard uses the classifier named by `CLASSIFIER` in `dashboard\config.py` (`"cnn"` by default, `"rf"` for the fallback) and always shows which one is in use. Results of the last run are in `ml\results.json` (CNN), `ml\results_rf.json` (fallback) and `docs/NOTES.md` section 3. They are reported as measured, including the targets that were not met.

## SD card viewer

```
python tools\sd_viewer.py E:\ --event 1 --out event1.png
python tools\sd_viewer.py E:\
```
