import asyncio
import ipaddress
import json
import socket
import time
from collections import deque
from contextlib import asynccontextmanager, suppress

import psutil
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

import config
import events
import notify
import sim
from pipeline import Pipeline, load_classifier
from rules import RuleEngine

classify, classifier_name = load_classifier()
live_pipe = Pipeline(classify, classifier_name)
sim_pipe = Pipeline(classify, classifier_name)

ui_clients = set()
jobs = set()
recent = deque()
link = {"device": None, "sim_task": None, "standin": None, "record": None, "record_name": "", "next_n": None,
        "speed": 1.0}
# source is the only thing that differs between a real and a simulated run: "live" or "sim"
state = {
    "device": False, "status": {}, "source": "live", "sim": None, "alert": None, "cls": None, "bpm": 0,
    "rule": None, "ml_alert": None, "condition": {"name": "No signal", "level": "NONE", "origin": "none"},
    "quality": "no data", "adc": [0, 0, 0], "samples": 0, "samples_60s": 0, "gaps": 0, "lost": 0, "bad_json": 0,
    "notify": "", "recording": False, "classifier": classifier_name,
}


def signal_time():
    # the rules count seconds of signal, not seconds on the wall clock
    pipe = sim_pipe if state["source"] == "sim" else live_pipe
    return pipe.total / config.FS


engine = RuleEngine(clock=signal_time)


async def send_ui(msg):
    text = json.dumps(msg)
    for ws in list(ui_clients):
        try:
            await ws.send_text(text)
        except Exception:
            ui_clients.discard(ws)


async def send_device(msg):
    ws = link["device"]
    if ws is None:
        return False
    try:
        await ws.send_text(json.dumps(msg))
        return True
    except Exception:
        return False


async def log(text):
    print(time.strftime("%H:%M:%S"), text)
    await send_ui({"t": "log", "time": time.strftime("%H:%M:%S"), "text": text})


def start_job(coro):
    # keep a reference so the task is not thrown away while it runs
    task = asyncio.create_task(coro)
    jobs.add(task)
    task.add_done_callback(jobs.discard)
    return task


def event_meta(kind, level, reason, bpm, origin):
    status = state["status"]
    return {
        "kind": kind, "level": level, "reason": reason, "bpm": bpm, "source": state["source"], "origin": origin,
        "clip": state["sim"], "cls": state["cls"], "classifier": classifier_name,
        "device_connected": link["device"] is not None,
        "lat": status.get("lat"), "lon": status.get("lon"), "fix_age": status.get("fix_age"),
    }


def current_condition():
    # one line for the top of the page: what is going on and who found it
    alert, rule, ml = state["alert"], state["rule"], state["ml_alert"]
    if alert is not None and alert["origin"] == "panic":
        return {"name": "Panic button", "level": "CRITICAL", "origin": "panic"}
    if rule is not None and rule["confirmed"]:
        return {"name": rule["name"], "level": rule["level"], "origin": "rule"}
    if ml is not None:
        return {"name": ml, "level": config.ALERTS[ml][0], "origin": "ml"}
    if alert is not None:
        return {"name": alert["reason"], "level": alert["level"], "origin": alert["origin"]}
    if rule is not None:
        return {"name": rule["name"] + " suspected", "level": "INFO", "origin": "rule"}
    if state["quality"] in ("leads off", "saturated", "flat"):
        return {"name": "Check electrodes (" + state["quality"] + ")", "level": "INFO", "origin": "signal"}
    if state["quality"] == "ok" and state["cls"] is not None:
        if state["cls"]["name"] == "Normal":
            return {"name": "Normal rhythm", "level": "OK", "origin": "ml"}
        return {"name": state["cls"]["name"] + " (not confirmed)", "level": "INFO", "origin": "ml"}
    return {"name": "No signal", "level": "NONE", "origin": "none"}


def refresh_status():
    state["rule"] = engine.rule_status()
    state["ml_alert"] = engine.ml_status()
    state["condition"] = current_condition()


def stop_standin():
    task = link["standin"]
    if task is not None and not task.done():
        task.cancel()
    link["standin"] = None


async def standin_alert(alert, reason):
    # no ESP32 connected: the dashboard does the countdown and ladder step 1 itself.
    # With a device connected this never runs, the device owns the countdown and the ladder.
    state["alert"] = {"level": alert["level"], "reason": reason, "origin": alert["origin"], "by": "dashboard",
                      "phase": "countdown", "until": time.time() + config.COUNTDOWN_S}
    try:
        await asyncio.sleep(config.COUNTDOWN_S)
    except asyncio.CancelledError:
        state["alert"] = None
        return
    shown = state["alert"]
    shown["phase"] = "sending"
    shown["until"] = 0
    word = "ECG WARNING" if alert["level"] == "WARNING" else "ECG EMERGENCY"
    text = "[DEMO] %s (dashboard only, no device): %s. HR %d. Loc: location unavailable" % (
        word, reason, alert["bpm"])
    result = await asyncio.to_thread(notify.send_telegram, text)
    shown["phase"] = "sent via dashboard" if result["ok"] else "SEND FAIL (" + result["info"] + ")"
    state["notify"] = "dashboard: " + result["info"]
    await log("Telegram from the dashboard (no device connected): " + result["info"])


async def raise_alert(alert, source):
    # the same function for live and simulated ECG; a simulated alert says so in its reason
    reason = alert["reason"] + (" (simulated ECG)" if source == "sim" else "")
    events.start_event(event_meta(alert["kind"], alert["level"], reason, alert["bpm"], alert["origin"]))
    await log("%s alert: %s (%s), event recording started" % (alert["level"], alert["kind"], reason))
    # INFO is only logged, WARNING and CRITICAL start the alert flow
    if alert["level"] == "INFO":
        return
    if link["device"] is not None:
        await send_device({"c": "alert", "level": alert["level"], "reason": reason, "demo": True})
        state["alert"] = {"level": alert["level"], "reason": reason, "origin": alert["origin"], "by": "device",
                          "phase": "waiting for device", "until": 0}
    elif state["alert"] is None:
        await log("no ESP32 connected: the dashboard runs the countdown and sends the message itself")
        link["standin"] = start_job(standin_alert(alert, reason))
    else:
        await log("alert not started: another alert is still shown (press Cancel / clear)")


async def handle_samples(samples, lead_off, source):
    pipe = sim_pipe if source == "sim" else live_pipe
    result = pipe.add(samples, lead_off)
    if events.add_samples(samples):
        await send_ui({"t": "events"})
    if link["record"] is not None and source == "live":
        link["record"].write("\n".join(str(s) for s in samples) + "\n")

    state["bpm"] = result["bpm"]
    state["quality"] = result["quality"]
    state["adc"] = result["adc"]
    if result["cls"] is not None:
        state["cls"] = result["cls"]
    elif result["second"] and result["quality"] != "ok":
        state["cls"] = None
    await send_ui({"t": "ecg", "y": [round(float(v), 4) for v in result["y"]]})
    for alert in engine.check(result, sim=(source == "sim")):
        await raise_alert(alert, source)
    if result["second"]:
        refresh_status()


def count_samples(n, size):
    # n is the real index of the first sample, so a jump means samples were lost
    expected = link["next_n"]
    if expected is not None and n != expected:
        state["gaps"] += 1
        state["lost"] += max(0, n - expected)
    link["next_n"] = n + size
    state["samples"] += size
    recent.append((time.monotonic(), size))


async def relay_notify(msg):
    # Telegram is slow, so it runs in a worker thread and the ECG keeps flowing
    result = await asyncio.to_thread(notify.send_telegram, str(msg["text"]))
    await send_device({"c": "notify_result", "id": msg["id"], "ok": result["ok"],
                       "sent": result["sent"], "info": result["info"]})
    state["notify"] = "dashboard relay: " + result["info"]
    await log("Telegram relay: " + result["info"])


def device_alert(reason, origin, phase):
    return {"level": "CRITICAL", "reason": reason, "origin": origin, "by": "device", "phase": phase, "until": 0}


async def on_device_event(msg):
    what = str(msg.get("what", ""))
    info = str(msg.get("info", ""))
    local = msg.get("src") == "local"
    bpm = state["status"].get("bpm", 0)
    await log("device event: %s %s" % (what, info))

    if what == "boot":
        link["next_n"] = None
        live_pipe.reset()
    elif what == "panic":
        stop_standin()
        state["alert"] = device_alert("panic button", "panic", "sending")
        events.start_event(event_meta("Panic", "CRITICAL", "panic button", bpm, "panic"))
    elif what == "countdown":
        if state["alert"] is None or local:
            state["alert"] = device_alert(info, "rule", "")
        state["alert"]["phase"] = "countdown"
        state["alert"]["until"] = time.time() + config.COUNTDOWN_S
        # alerts the dashboard commanded already have their event, local ones do not
        if local:
            events.start_event(event_meta("Device rule", "CRITICAL", info, bpm, "rule"))
    elif what == "cancel":
        state["alert"] = None
    elif what in ("notify_ok", "notify_fail") and state["alert"] is not None:
        sent = "sent via " + str(msg.get("via", "?"))
        state["alert"]["phase"] = sent if what == "notify_ok" else "SEND FAIL (" + info + ")"
        state["alert"]["until"] = 0
        state["notify"] = state["alert"]["phase"]
    elif what == "alert_ignored" and state["alert"] is not None:
        state["alert"]["phase"] = "ignored by device (" + info + ")"
    refresh_status()


async def on_device_message(msg):
    kind = msg.get("t")
    if kind == "ecg":
        samples = [int(v) for v in msg["s"]][:10 * config.BATCH_SIZE]
        count_samples(int(msg["n"]), len(samples))
        # with the simulation source selected the device ECG is counted but not analysed
        if state["source"] == "live" and samples:
            await handle_samples(samples, bool(msg.get("lo")), "live")
    elif kind == "status":
        state["status"] = {k: v for k, v in msg.items() if k != "t"}
        alert = state["alert"]
        # the device left the alert (finished, cancelled or cleared by the button)
        over = msg.get("state") in ("IDLE", "COOLDOWN")
        if over and alert is not None and alert["by"] == "device" and alert["phase"] != "waiting for device":
            state["alert"] = None
    elif kind == "event":
        await on_device_event(msg)
    elif kind == "notify":
        start_job(relay_notify(msg))


def clear_signal_state():
    engine.reset()
    state["cls"] = None
    state["bpm"] = 0
    state["quality"] = "no data"
    refresh_status()


async def run_sim(clip, button):
    samples = clip["samples"]
    batch = config.BATCH_SIZE
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        for i in range(0, len(samples) - batch + 1, batch):
            # each batch has a fixed time from the start, so the replay cannot drift
            wait = start + i / config.FS / link["speed"] - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            if i % config.FS == 0:
                await send_device({"c": "sim", "on": True})
                await send_device({"c": "bpm", "v": sim_pipe.bpm})
            part = [int(v) for v in samples[i:i + batch]]
            await handle_samples(part, sim.lead_off_at(clip, i / config.FS), "sim")
    finally:
        link["sim_task"] = None
        state["sim"] = None
        flushed = events.flush()
        sim_pipe.reset()
        clear_signal_state()
        await send_ui({"t": "events"})
        await log("simulation %s ended, %d event(s) saved short" % (button, flushed))


async def stop_sim_task():
    task = link["sim_task"]
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def set_source(mode):
    # switching the source starts both pipelines and the rules from clean
    await stop_sim_task()
    stop_standin()
    state["source"] = mode
    state["alert"] = None
    events.flush()
    live_pipe.reset()
    sim_pipe.reset()
    clear_signal_state()
    await send_device({"c": "sim", "on": mode == "sim"})
    await log("source is now " + ("SIMULATION" if mode == "sim" else "LIVE"))


def broadcast_targets():
    # one directed broadcast per network card, so Windows cannot pick the wrong card
    targets = {("0.0.0.0", "255.255.255.255")}
    for addrs in psutil.net_if_addrs().values():
        for a in addrs:
            if a.family == socket.AF_INET and a.netmask and not a.address.startswith("127."):
                net = ipaddress.ip_network(a.address + "/" + a.netmask, strict=False)
                targets.add((a.address, str(net.broadcast_address)))
                targets.add((a.address, "255.255.255.255"))
    return targets


def lan_addresses():
    return sorted({own for own, _ in broadcast_targets() if own != "0.0.0.0"})


async def discovery_loop():
    text = ("ECG_SERVER %d" % config.SERVER_PORT).encode()
    while True:
        for own, target in broadcast_targets():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.bind((own, 0))
                    sock.sendto(text, (target, config.DISCOVERY_PORT))
            except OSError:
                continue
        await asyncio.sleep(config.DISCOVERY_EVERY_S)


async def state_loop():
    while True:
        # count the samples that arrived in the last 60 s
        now = time.monotonic()
        while recent and now - recent[0][0] > 60:
            recent.popleft()
        state["samples_60s"] = sum(size for _, size in recent)
        state["device"] = link["device"] is not None
        state["recording"] = link["record"] is not None
        # between two clips the device is kept in simulation mode, so its own rules stay quiet
        if state["source"] == "sim" and link["sim_task"] is None:
            await send_device({"c": "sim", "on": True})
            await send_device({"c": "bpm", "v": 0})
        refresh_status()
        await send_ui({"t": "state", "now": time.time(), **state})
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    print("Classifier:", classifier_name)
    print("Dashboard addresses (use one as SERVER_IP if discovery fails):", lan_addresses())
    tasks = [asyncio.create_task(discovery_loop()), asyncio.create_task(state_loop())]
    yield
    for task in tasks:
        task.cancel()
    if link["record"] is not None:
        link["record"].close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state():
    return state


@app.websocket("/ws/ui")
async def ui_socket(ws: WebSocket):
    await ws.accept()
    ui_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ui_clients.discard(ws)


@app.websocket("/ws/device")
async def device_socket(ws: WebSocket):
    await ws.accept()
    link["device"] = ws
    link["next_n"] = None
    live_pipe.reset()
    await log("device connected")
    try:
        while True:
            text = await ws.receive_text()
            # a broken message from the device is counted and skipped, never fatal
            try:
                msg = json.loads(text)
                await on_device_message(msg)
            except (ValueError, KeyError, TypeError, AttributeError):
                state["bad_json"] += 1
    except WebSocketDisconnect:
        pass
    finally:
        if link["device"] is ws:
            link["device"] = None
            state["status"] = {}
            if state["alert"] is not None and state["alert"]["by"] == "device":
                state["alert"] = None
            if state["source"] == "live":
                live_pipe.reset()
                clear_signal_state()
            await log("device disconnected")


@app.post("/api/source")
async def choose_source(mode: str):
    if mode not in ("live", "sim"):
        return JSONResponse({"ok": False, "info": "mode must be live or sim"}, 400)
    await set_source(mode)
    return {"ok": True, "source": mode}


@app.post("/api/sim/start")
async def start_sim(name: str):
    clip = sim.next_clip(name)
    if clip is None:
        return JSONResponse({"ok": False, "info": "no clip for " + name + ", run ml/export_sim_clips.py"}, 404)
    await set_source("sim")
    state["sim"] = name + " (" + str(clip["record"]) + ")"
    link["sim_task"] = asyncio.create_task(run_sim(clip, name))
    await log("simulation %s started from %s" % (name, clip["file"]))
    return {"ok": True, "clip": clip["file"], "seconds": len(clip["samples"]) // config.FS}


@app.post("/api/sim/stop")
async def stop_sim():
    await stop_sim_task()
    return {"ok": True}


@app.post("/api/alert/test")
async def test_alert():
    alert = {"kind": "Test", "level": "CRITICAL", "reason": "test alert", "bpm": state["bpm"], "origin": "test"}
    await raise_alert(alert, state["source"])
    return {"ok": True, "device": link["device"] is not None}


@app.post("/api/alert/cancel")
async def cancel_alert():
    # with a device this cancels its countdown; without one it cancels or clears the dashboard's own alert
    if link["device"] is not None:
        return {"ok": await send_device({"c": "cancel"})}
    stop_standin()
    state["alert"] = None
    return {"ok": True}


@app.post("/api/notify/test")
async def notify_test():
    result = await asyncio.to_thread(notify.send_telegram, "[DEMO] Telegram test from ECG dashboard")
    state["notify"] = "test: " + result["info"]
    await log("Telegram test: " + result["info"])
    return result


@app.post("/api/record/start")
async def record_start():
    if link["record"] is None:
        config.RECORD_DIR.mkdir(exist_ok=True)
        link["record_name"] = time.strftime("rec_%Y%m%d_%H%M%S.csv")
        link["record"] = open(config.RECORD_DIR / link["record_name"], "w")
        link["record"].write("fs=%d\n" % config.FS)
    return {"ok": True, "file": link["record_name"]}


@app.post("/api/record/stop")
async def record_stop():
    if link["record"] is not None:
        link["record"].close()
        link["record"] = None
    return {"ok": True, "file": link["record_name"]}


@app.get("/api/events")
async def get_events():
    return events.list_events()


@app.get("/api/events/{event_id}/csv")
async def event_csv(event_id: str):
    folder = events.event_folder(event_id)
    if folder is None:
        return JSONResponse({"ok": False, "info": "no such event"}, 404)
    return FileResponse(folder / "ecg.csv", filename=event_id + "_ecg.csv")


@app.get("/api/events/{event_id}/pdf")
async def event_pdf(event_id: str):
    out = await asyncio.to_thread(events.make_pdf, event_id)
    if out is None:
        return JSONResponse({"ok": False, "info": "no such event"}, 404)
    return FileResponse(out, filename=event_id + "_ecg.pdf")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.SERVER_PORT)
