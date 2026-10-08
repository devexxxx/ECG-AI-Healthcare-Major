import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import events
import notify
import pipeline
import rules
import sim

FS = config.FS


def feed(pipe, counts, lead_off=False):
    # push a whole clip through the pipeline in 25-sample batches, like the device does
    results = []
    for i in range(0, len(counts) - 24, 25):
        results.append(pipe.add(counts[i:i + 25], lead_off))
    return results


def normal_probs(window):
    return np.array([0.9, 0.04, 0.03, 0.03])


@pytest.fixture
def event_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVENTS_DIR", tmp_path / "events")
    events.pending.clear()
    events.history.clear()
    return tmp_path / "events"


@pytest.fixture
def notify_file(tmp_path, monkeypatch):
    path = tmp_path / "notify_config.json"
    monkeypatch.setattr(config, "NOTIFY_CONFIG", path)
    return path


def test_filter_removes_dc_and_mains():
    t = np.arange(10 * FS) / FS
    hum = np.sin(2 * np.pi * 50 * t)
    out = pipeline.filter_all(1.0 + hum)
    assert np.abs(out[3 * FS:]).max() < 0.05


def test_bpm_matches_synthetic_rate():
    for bpm in (38, 72, 150):
        pipe = pipeline.Pipeline(normal_probs, "test")
        results = feed(pipe, sim.mv_to_counts(sim.synthetic_ecg(20, bpm)))
        assert abs(results[-1]["bpm"] - bpm) <= 3


def test_first_window_comes_after_five_seconds():
    pipe = pipeline.Pipeline(normal_probs, "test")
    results = feed(pipe, sim.mv_to_counts(sim.synthetic_ecg(8, 72)))
    first = next(i for i, r in enumerate(results) if r["cls"] is not None)
    assert (first + 1) * 25 == 5 * FS
    assert results[first]["cls"] == {"name": "Normal", "conf": 0.9, "source": "test"}
    assert sum(r["cls"] is not None for r in results) == 4


def test_leads_off_stops_classification():
    pipe = pipeline.Pipeline(normal_probs, "test")
    results = feed(pipe, sim.mv_to_counts(sim.synthetic_ecg(10, 72)), lead_off=True)
    assert all(r["cls"] is None for r in results)
    assert results[-1]["quality"] == "leads off"
    assert results[-1]["bpm"] == 0


def test_flat_and_saturated_signal():
    rng = np.random.default_rng(0)
    pipe = pipeline.Pipeline(normal_probs, "test")
    flat = feed(pipe, 1860 + rng.integers(-3, 4, 6 * FS))
    assert flat[-1]["quality"] == "flat" and flat[-1]["flat"] and flat[-1]["cls"] is None
    pipe.reset()
    stuck = feed(pipe, np.full(6 * FS, 4095))
    assert stuck[-1]["quality"] == "saturated" and stuck[-1]["cls"] is None


def test_fallback_is_labelled(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODEL_PATH", tmp_path / "missing.pt")
    classify, name = pipeline.load_classifier()
    assert name == config.FALLBACK_LABEL
    assert classify is pipeline.fallback_classify
    probs = classify(pipeline.zscore(pipeline.filter_all(sim.synthetic_ecg(5, 72))))
    assert config.CLASS_NAMES[int(np.argmax(probs))] == "Normal"


def test_rr_features_of_regular_and_irregular_beats():
    steady = pipeline.rr_features(pipeline.zscore(sim.synthetic_ecg(5, 60, noise_mv=0)))
    mean_rr, sdnn, rmssd, pnn50, early = steady
    assert abs(mean_rr - 1.0) < 0.05 and sdnn < 0.06 and rmssd < 0.08 and early == 0
    # the same beats with every third one pulled 0.3 s earlier
    t = np.arange(5 * FS) / FS
    uneven = np.zeros(len(t))
    for beat in (0.5, 1.3, 1.9, 2.7, 3.5, 4.1, 4.9):
        uneven += np.exp(-((t - beat) ** 2) / (2 * 0.012 ** 2))
    mean_rr, sdnn, rmssd, pnn50, early = pipeline.rr_features(pipeline.zscore(uneven))
    assert sdnn > 0.08 and rmssd > 0.1 and pnn50 > 0.5 and early >= 1
    assert list(pipeline.rr_features(np.zeros(5 * FS))) == [0, 0, 0, 0, 0]


def test_forest_fallback_is_used_and_labelled(monkeypatch, tmp_path):
    joblib = pytest.importorskip("joblib")
    from sklearn.ensemble import RandomForestClassifier
    # a tiny forest that only knows: slow steady beats = Normal, fast = VT/VF
    rows = np.array([[1.0, 0.01, 0.01, 0, 0], [0.25, 0.01, 0.01, 0, 0]] * 5)
    forest = RandomForestClassifier(n_estimators=5, random_state=0).fit(rows, [0, 3] * 5)
    forest.classes_ = np.array([0, 3])
    monkeypatch.setattr(config, "RF_PATH", tmp_path / "ecg_rf.joblib")
    monkeypatch.setattr(config, "CLASSIFIER", "rf")
    # selected but not trained yet: the rule fallback is used and says so
    assert pipeline.load_classifier()[1] == config.FALLBACK_LABEL
    joblib.dump(forest, config.RF_PATH)
    classify, name = pipeline.load_classifier()
    assert name == config.RF_LABEL and name != "cnn"
    probs = classify(pipeline.zscore(sim.synthetic_ecg(5, 60, noise_mv=0)))
    assert int(np.argmax(probs)) == 0


def window_result(name, conf, bpm=80):
    return {"cls": {"name": name, "conf": conf, "source": "test"}, "second": True, "bpm": bpm,
            "quality": "ok", "flat": False}


def second_result(bpm, quality="ok"):
    return {"cls": None, "second": True, "bpm": bpm, "quality": quality, "flat": quality == "flat"}


def test_ml_alert_needs_three_confident_windows():
    engine = rules.RuleEngine(clock=lambda: 0)
    assert engine.check(window_result("AFib", 0.9), sim=False) == []
    assert engine.check(window_result("AFib", 0.9), sim=False) == []
    alerts = engine.check(window_result("AFib", 0.9), sim=False)
    assert [(a["kind"], a["level"]) for a in alerts] == [("AFib", "WARNING")]
    # the same episode does not alert again
    assert engine.check(window_result("AFib", 0.95), sim=False) == []


def test_streak_resets_on_other_result_and_low_confidence():
    engine = rules.RuleEngine(clock=lambda: 0)
    order = [("VT/VF", 0.9), ("VT/VF", 0.9), ("Normal", 0.9), ("VT/VF", 0.9), ("VT/VF", 0.79), ("VT/VF", 0.9),
             ("VT/VF", 0.9), ("AFib", 0.9), ("VT/VF", 0.9), ("VT/VF", 0.9)]
    for name, conf in order:
        assert engine.check(window_result(name, conf), sim=False) == []
    alerts = engine.check(window_result("VT/VF", 0.9), sim=False)
    assert [(a["kind"], a["level"]) for a in alerts] == [("VT/VF", "CRITICAL")]


def test_pvc_is_info_and_episode_can_repeat_after_clearing():
    engine = rules.RuleEngine(clock=lambda: 0)
    got = [engine.check(window_result("PVC", 0.9), sim=False) for _ in range(3)]
    assert got[2][0]["level"] == "INFO"
    for _ in range(rules.CLEAR_WINDOWS):
        assert engine.check(window_result("Normal", 0.9), sim=False) == []
    got = [engine.check(window_result("PVC", 0.9), sim=False) for _ in range(3)]
    assert got[2][0]["kind"] == "PVC"


def test_rate_rules_run_only_in_simulation():
    now = [0.0]
    live_engine = rules.RuleEngine(clock=lambda: now[0])
    sim_engine = rules.RuleEngine(clock=lambda: now[0])
    kinds = []
    for second in range(30):
        now[0] = second
        assert live_engine.check(second_result(38), sim=False) == []
        kinds += [(second, a["kind"]) for a in sim_engine.check(second_result(38), sim=True)]
    assert kinds == [(10, "Bradycardia")]
    # the live engine raises no alert (the device owns it) but reports the same rule status
    assert live_engine.rule_status() == sim_engine.rule_status()
    assert live_engine.rule_status() == {"name": "Bradycardia", "level": "CRITICAL", "held": 10, "need": 10,
                                         "confirmed": True}


def test_tachy_flatline_timing_and_reset():
    now = [0.0]
    engine = rules.RuleEngine(clock=lambda: now[0])
    seen = []
    plan = [second_result(150)] * 6 + [second_result(90)] + [second_result(150)] * 11 + [second_result(0, "flat")] * 6
    for second, result in enumerate(plan):
        now[0] = second
        seen += [(second, a["kind"]) for a in engine.check(result, sim=True)]
    assert seen == [(17, "Tachycardia"), (23, "Flatline")]
    # leads-off is not a flatline
    engine.reset()
    for second in range(20):
        now[0] = second
        assert engine.check(second_result(0, "leads off"), sim=True) == []


def test_event_has_30s_before_and_after(event_dir):
    events.add_samples(list(range(40 * FS)))
    event_id = events.start_event({"kind": "Test", "level": "CRITICAL", "reason": "x", "bpm": 70, "source": "live"})
    assert events.list_events() == []
    for second in range(30):
        saved = events.add_samples([second] * FS)
    assert saved
    lines = (event_dir / event_id / "ecg.csv").read_text().split()
    assert lines[0] == "adc" and len(lines) - 1 == 15000
    assert lines[1] == str(10 * FS)
    meta = json.loads((event_dir / event_id / "meta.json").read_text())
    assert meta["samples"] == 15000 and meta["short"] is False and meta["kind"] == "Test"
    assert [e["id"] for e in events.list_events()] == [event_id]


def test_every_pending_event_gets_samples_and_flush_marks_short(event_dir):
    events.add_samples([1] * (30 * FS))
    first = events.start_event({"kind": "A"})
    events.add_samples([2] * (20 * FS))
    second = events.start_event({"kind": "B"})
    assert first != second
    events.add_samples([3] * (10 * FS))
    assert json.loads((event_dir / first / "meta.json").read_text())["samples"] == 15000
    assert events.flush() == 1
    meta = json.loads((event_dir / second / "meta.json").read_text())
    assert meta["short"] is True and meta["samples"] == 30 * FS + 10 * FS
    assert len(events.history) == 0 and events.pending == []


def test_event_id_is_checked(event_dir):
    for bad in ("../secret", "..\\..\\x", "20261008_101010_0", "abc", "20261008_101010_00/.."):
        assert events.event_folder(bad) is None
        assert events.make_pdf(bad) is None
    assert events.event_folder("20261008_101010_00") is None


def test_pdf_is_created(event_dir):
    events.add_samples([int(v) for v in sim.mv_to_counts(sim.synthetic_ecg(30, 72))])
    event_id = events.start_event({"kind": "Test", "level": "INFO", "reason": "x", "bpm": 72, "source": "sim"})
    events.flush()
    out = events.make_pdf(event_id)
    assert out.exists() and out.read_bytes()[:4] == b"%PDF"


def test_notify_dry_run_and_demo_rule(notify_file, capsys):
    notify_file.write_text(json.dumps({"bot_token": "PUT_BOT_TOKEN_HERE", "chat_ids": ["111", "222"],
                                       "demo_only_first": True, "dry_run": True}))
    result = notify.send_telegram("ECG EMERGENCY test")
    assert result == {"ok": True, "sent": 1, "info": "dry run"}
    assert "[DEMO] ECG EMERGENCY test" in capsys.readouterr().out


def test_notify_reports_missing_config(notify_file):
    assert notify.send_telegram("x")["ok"] is False
    notify_file.write_text(json.dumps({"bot_token": "PUT_BOT_TOKEN_HERE", "chat_ids": ["111"]}))
    assert notify.send_telegram("x") == {"ok": False, "sent": 0, "info": "bot_token not set"}
    notify_file.write_text(json.dumps({"bot_token": "1:abc", "chat_ids": ["PUT_YOUR_OWN_CHAT_ID_FIRST"]}))
    assert notify.send_telegram("x")["info"] == "no chat_ids set"


def test_notify_posts_to_every_chat_when_not_demo(notify_file, monkeypatch):
    calls = []

    def fake_post(token, chat, text):
        calls.append((chat, text))
        return chat != "222", "delivered" if chat != "222" else "chat not found"

    monkeypatch.setattr(notify, "post_message", fake_post)
    notify_file.write_text(json.dumps({"bot_token": "1:abc", "chat_ids": ["111", "222"], "demo_only_first": False}))
    result = notify.send_telegram("hello")
    assert result["ok"] is True and result["sent"] == 1
    # the failing chat is tried twice (one retry), and no [DEMO] is added when demo mode is off
    assert calls == [("111", "hello"), ("222", "hello"), ("222", "hello")]


def test_synthetic_clips_give_expected_alerts():
    wanted = {"Normal": [], "Bradycardia": ["Bradycardia"], "Tachycardia": ["Tachycardia"], "Flatline": ["Flatline"],
              "Noisy": []}
    for button, kinds in wanted.items():
        clip = sim.synthetic_clip(button)
        out = sim.replay_offline(clip, normal_probs, "test")
        assert [a["kind"] for _, a in out["alerts"]] == kinds, button
        for when, _ in out["alerts"]:
            assert when - clip["onset_s"] <= 15, button
    assert sim.synthetic_clip("AFib") is None


@pytest.fixture
def client(tmp_path, monkeypatch, notify_file):
    # start the server from another folder to prove the paths do not depend on it
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "EVENTS_DIR", tmp_path / "events")
    monkeypatch.setattr(config, "RECORD_DIR", tmp_path / "recordings")
    notify_file.write_text(json.dumps({"bot_token": "PUT_BOT_TOKEN_HERE", "chat_ids": ["111"], "dry_run": True}))
    from fastapi.testclient import TestClient
    import server
    events.pending.clear()
    events.history.clear()
    for key in ("samples", "gaps", "lost", "bad_json"):
        server.state[key] = 0
    # every test starts from the LIVE source with nothing running; replays run 25 times faster
    server.link.update(speed=25.0, standin=None, sim_task=None)
    server.state.update(source="live", alert=None, sim=None, notify="", cls=None, status={})
    server.engine.reset()
    server.live_pipe.reset()
    server.sim_pipe.reset()
    with TestClient(server.app) as test_client:
        yield test_client
        test_client.post("/api/source?mode=live")


def next_cmd(dev, name, **fields):
    # read what the dashboard sends to the device until the wanted command comes
    for _ in range(3000):
        msg = json.loads(dev.receive_text())
        if msg.get("c") == name and all(msg.get(k) == v for k, v in fields.items()):
            return msg
    raise AssertionError("command %s never came" % name)


def wait_for(client, check, seconds=20):
    # ask the dashboard for its state until the check is true
    end = time.time() + seconds
    while time.time() < end:
        now = client.get("/api/state").json()
        if check(now):
            return now
        time.sleep(0.05)
    raise AssertionError("state never reached: %s" % client.get("/api/state").json())


def use_classifier(monkeypatch, probs):
    import server
    for pipe in (server.live_pipe, server.sim_pipe):
        monkeypatch.setattr(pipe, "classify", lambda window: np.array(probs))


def count_telegrams(monkeypatch):
    sent = []
    real = notify.send_telegram
    monkeypatch.setattr(notify, "send_telegram", lambda text: sent.append(text) or real(text))
    return sent


def send_ecg(dev, counts):
    for k in range(len(counts) // 25):
        part = [int(v) for v in counts[k * 25:k * 25 + 25]]
        dev.send_text(json.dumps({"t": "ecg", "n": k * 25, "lo": 0, "s": part}))


def ecg_msg(n, value=1800, lo=0):
    return json.dumps({"t": "ecg", "n": n, "lo": lo, "s": [value] * 25})


def test_page_has_no_outside_links(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "http://" not in page.text and "https://" not in page.text.replace("'https:'", "")
    assert os.getcwd() != str(config.DASH_DIR)


def test_device_stream_counts_gaps_and_survives_bad_json(client):
    with client.websocket_connect("/ws/device") as dev:
        dev.send_text(ecg_msg(0))
        dev.send_text(ecg_msg(25))
        dev.send_text("{this is not json")
        dev.send_text(json.dumps({"t": "ecg"}))
        dev.send_text(ecg_msg(100))
        dev.send_text(json.dumps({"t": "status", "bpm": 71, "leads": 1, "state": "IDLE"}))
        dev.send_text(json.dumps({"t": "notify", "id": 7, "level": "CRITICAL", "text": "[DEMO] PANIC pressed by Test."}))
        answer = json.loads(dev.receive_text())
        assert answer == {"c": "notify_result", "id": 7, "ok": True, "sent": 1, "info": "dry run"}
        state = client.get("/api/state").json()
    assert state["samples"] == 75 and state["gaps"] == 1 and state["lost"] == 50
    assert state["bad_json"] == 2 and state["status"]["bpm"] == 71


def test_test_alert_commands_device_and_local_events_are_stored(client):
    with client.websocket_connect("/ws/device") as dev:
        dev.send_text(ecg_msg(0))
        assert client.post("/api/alert/test").json() == {"ok": True, "device": True}
        cmd = json.loads(dev.receive_text())
        assert cmd == {"c": "alert", "level": "CRITICAL", "reason": "test alert", "demo": True}
        # the echo of a commanded alert must not create a second event
        dev.send_text(json.dumps({"t": "event", "what": "countdown", "info": "test alert", "src": "dash"}))
        dev.send_text(json.dumps({"t": "event", "what": "panic", "info": "", "src": "local"}))
        assert client.post("/api/alert/cancel").json() == {"ok": True}
        assert json.loads(dev.receive_text()) == {"c": "cancel"}
        assert client.get("/api/state").json()["alert"]["reason"] == "panic button"
    assert sorted(ev["meta"]["kind"] for ev in events.pending) == ["Panic", "Test"]


def test_sim_start_plays_one_clip_and_ignores_device(client):
    import server
    with client.websocket_connect("/ws/device") as dev:
        before = dict(sim.next_index)
        reply = client.post("/api/sim/start?name=Normal").json()
        assert reply["ok"] and reply["seconds"] == 60
        after = dict(sim.next_index)
        assert after.get("Normal", 0) - before.get("Normal", 0) <= 1
        assert json.loads(dev.receive_text()) == {"c": "sim", "on": True}
        assert next_cmd(dev, "bpm")["c"] == "bpm"
        dev.send_text(ecg_msg(0))
        now = client.get("/api/state").json()
        assert now["sim"].startswith("Normal") and now["source"] == "sim"
        assert server.live_pipe.total == 0
        assert client.post("/api/sim/stop").json() == {"ok": True}
        # stopping a clip keeps the SIMULATION source; only choosing LIVE releases the device
        assert client.get("/api/state").json()["source"] == "sim"
        assert client.post("/api/source?mode=live").json() == {"ok": True, "source": "live"}
        assert next_cmd(dev, "sim", on=False) == {"c": "sim", "on": False}
    assert client.post("/api/source?mode=other").status_code == 400
    assert client.post("/api/sim/start?name=Nonsense").status_code == 404
    assert client.get("/api/events/..%2F..%2Fx/csv").status_code == 404
    assert client.post("/api/notify/test").json()["info"] == "dry run"


def test_same_rule_state_for_live_and_simulated_tachycardia(client, monkeypatch):
    use_classifier(monkeypatch, [0.9, 0.04, 0.03, 0.03])
    with client.websocket_connect("/ws/device") as dev:
        send_ecg(dev, sim.mv_to_counts(sim.synthetic_ecg(20, 150)))
        live = wait_for(client, lambda s: s["rule"] is not None and s["rule"]["confirmed"])
        # live: the rule is shown, but the alert belongs to the device, so the dashboard starts nothing
        assert live["source"] == "live" and live["alert"] is None and events.pending == []
        assert live["condition"] == {"name": "Tachycardia", "level": "CRITICAL", "origin": "rule"}

        assert client.post("/api/sim/start?name=Tachycardia").json()["ok"]
        cmd = next_cmd(dev, "alert")
        simulated = wait_for(client, lambda s: s["rule"] is not None and s["rule"]["confirmed"])
        assert simulated["source"] == "sim"
        assert simulated["rule"] == live["rule"] and simulated["condition"] == live["condition"]
        # the ML box may still say Normal: the condition comes from the rule, and says so
        assert simulated["cls"]["name"] == "Normal" and simulated["condition"]["origin"] == "rule"
        assert cmd == {"c": "alert", "level": "CRITICAL", "reason": "very fast heart rate (simulated ECG)", "demo": True}


@pytest.mark.parametrize("button,rule,reason", [("Tachycardia", "Tachycardia", "very fast heart rate"),
                                                ("Bradycardia", "Bradycardia", "very slow heart rate")])
def test_pc_only_simulation_alerts_and_sends_telegram(client, monkeypatch, button, rule, reason):
    use_classifier(monkeypatch, [0.9, 0.04, 0.03, 0.03])
    monkeypatch.setattr(config, "COUNTDOWN_S", 0.4)
    sent = count_telegrams(monkeypatch)
    assert client.post("/api/sim/start?name=" + button).json()["ok"]
    counting = wait_for(client, lambda s: s["alert"] is not None)
    assert counting["alert"]["by"] == "dashboard" and counting["alert"]["origin"] == "rule"
    assert counting["rule"]["name"] == rule and counting["device"] is False
    done = wait_for(client, lambda s: s["alert"] is not None and s["alert"]["phase"].startswith("sent"))
    assert done["alert"]["phase"] == "sent via dashboard" and "dry run" in done["notify"]
    # one message, through the normal relay function, honest about being a simulation without a device
    assert len(sent) == 1 and sent[0].startswith("[DEMO] ECG EMERGENCY (dashboard only, no device): " + reason)
    assert "(simulated ECG)" in sent[0] and len(sent[0]) <= 160

    wait_for(client, lambda s: s["sim"] is None)
    saved = [e for e in client.get("/api/events").json() if e["kind"] == rule]
    assert len(saved) == 1
    assert saved[0]["source"] == "sim" and saved[0]["origin"] == "rule" and saved[0]["device_connected"] is False
    assert saved[0]["clip"].startswith(button) and saved[0]["reason"].endswith("(simulated ECG)")
    assert client.get("/api/events/%s/pdf" % saved[0]["id"]).content[:4] == b"%PDF"
    assert client.post("/api/alert/cancel").json() == {"ok": True}
    assert client.get("/api/state").json()["alert"] is None


def test_pc_only_countdown_can_be_cancelled(client, monkeypatch):
    monkeypatch.setattr(config, "COUNTDOWN_S", 5)
    sent = count_telegrams(monkeypatch)
    assert client.post("/api/alert/test").json() == {"ok": True, "device": False}
    assert wait_for(client, lambda s: s["alert"] is not None)["alert"]["phase"] == "countdown"
    assert client.post("/api/alert/cancel").json() == {"ok": True}
    wait_for(client, lambda s: s["alert"] is None)
    assert sent == []


def test_hardware_simulation_uses_the_device_ladder_once(client, monkeypatch):
    import server
    use_classifier(monkeypatch, [0.9, 0.04, 0.03, 0.03])
    monkeypatch.setattr(config, "COUNTDOWN_S", 0.2)
    sent = count_telegrams(monkeypatch)
    with client.websocket_connect("/ws/device") as dev:
        assert client.post("/api/sim/start?name=Tachycardia").json()["ok"]
        assert next_cmd(dev, "sim") == {"c": "sim", "on": True}
        cmd = next_cmd(dev, "alert")
        assert cmd["level"] == "CRITICAL" and cmd["reason"] == "very fast heart rate (simulated ECG)"
        # the device owns the countdown and the ladder: the dashboard must not start its own
        assert server.link["standin"] is None and sent == []
        waiting = [ev for ev in events.pending if ev["meta"]["kind"] == "Tachycardia"]
        assert len(waiting) == 1 and waiting[0]["meta"]["source"] == "sim" and waiting[0]["meta"]["device_connected"]

        # the device answers like the real firmware: countdown echo, relay request, result
        dev.send_text(json.dumps({"t": "event", "what": "countdown", "info": cmd["reason"], "src": "dash"}))
        wait_for(client, lambda s: s["alert"] is not None and s["alert"]["phase"] == "countdown")
        assert client.get("/api/state").json()["alert"]["by"] == "device"
        dev.send_text(json.dumps({"t": "notify", "id": 3, "level": "CRITICAL", "text": "[DEMO] ECG EMERGENCY T: x"}))
        assert next_cmd(dev, "notify_result") == {"c": "notify_result", "id": 3, "ok": True, "sent": 1, "info": "dry run"}
        dev.send_text(json.dumps({"t": "event", "what": "notify_ok", "info": "sent via dashboard", "src": "dash",
                                  "via": "dashboard"}))
        wait_for(client, lambda s: s["alert"] is not None and s["alert"]["phase"] == "sent via dashboard")
        # still one event and one Telegram message for this alert
        assert len([ev for ev in events.pending if ev["meta"]["kind"] == "Tachycardia"]) <= 1
        assert len(sent) == 1
        dev.send_text(json.dumps({"t": "status", "state": "COOLDOWN", "bpm": 0, "sim": 1}))
        wait_for(client, lambda s: s["alert"] is None)


def test_ml_alert_takes_the_same_path_live_and_simulated(client, monkeypatch):
    use_classifier(monkeypatch, [0.02, 0.02, 0.02, 0.94])
    with client.websocket_connect("/ws/device") as dev:
        send_ecg(dev, sim.mv_to_counts(sim.synthetic_ecg(12, 72)))
        live_cmd = next_cmd(dev, "alert")
        live = wait_for(client, lambda s: s["ml_alert"] == "VT/VF")
        live_meta = [dict(ev["meta"]) for ev in events.pending if ev["meta"]["kind"] == "VT/VF"][0]

        assert client.post("/api/sim/start?name=Normal").json()["ok"]
        sim_cmd = next_cmd(dev, "alert")
        simulated = wait_for(client, lambda s: s["ml_alert"] == "VT/VF" and s["source"] == "sim")
        sim_meta = [ev["meta"] for ev in events.pending if ev["meta"]["kind"] == "VT/VF"][0]

    assert live_cmd == {"c": "alert", "level": "CRITICAL", "reason": "dangerous fast rhythm", "demo": True}
    assert sim_cmd == dict(live_cmd, reason="dangerous fast rhythm (simulated ECG)")
    assert live["condition"] == simulated["condition"] == {"name": "VT/VF", "level": "CRITICAL", "origin": "ml"}
    # same event fields, only the source (and the clip name) tell the two apart
    assert set(live_meta) == set(sim_meta)
    assert (live_meta["source"], sim_meta["source"]) == ("live", "sim")
    assert live_meta["origin"] == sim_meta["origin"] == "ml"
    assert live_meta["clip"] is None and sim_meta["clip"].startswith("Normal")
