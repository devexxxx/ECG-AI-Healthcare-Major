import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import config
import sim

dev = {"state": "IDLE", "countdown_end": 0.0, "notify_id": 0, "result": None, "sim": False}


async def send(ws, msg):
    await ws.send(json.dumps(msg))


async def send_event(ws, what, info, src, via=None):
    msg = {"t": "event", "what": what, "info": info, "src": src}
    if via:
        msg["via"] = via
    await send(ws, msg)


async def ask_relay(ws, text, src):
    # ladder step 1 like the real device: ask the dashboard to send and wait for its answer
    dev["notify_id"] += 1
    dev["result"] = None
    dev["state"] = "SENDING"
    await send(ws, {"t": "notify", "id": dev["notify_id"], "level": "CRITICAL", "text": text})
    for _ in range(config.RELAY_WAIT_S * 10):
        if dev["result"] is not None:
            break
        await asyncio.sleep(0.1)
    ok = bool(dev["result"] and dev["result"].get("ok"))
    print("notify_result:", dev["result"])
    if ok:
        await send_event(ws, "notify_ok", "sent via dashboard", src, "dashboard")
    else:
        await send_event(ws, "notify_fail", "SEND FAIL round 1", src)
    dev["state"] = "ALERT"
    await asyncio.sleep(5)
    dev["state"] = "IDLE"
    return ok


async def read_commands(ws, countdown_s):
    async for text in ws:
        msg = json.loads(text)
        cmd = msg.get("c")
        if cmd == "alert":
            if dev["state"] == "IDLE":
                dev["state"] = "COUNTDOWN"
                dev["reason"] = msg.get("reason", "alert")
                dev["countdown_end"] = asyncio.get_running_loop().time() + countdown_s
                await send_event(ws, "countdown", dev["reason"], "dash")
            else:
                await send_event(ws, "alert_ignored", dev["state"], "dash")
        elif cmd == "cancel" and dev["state"] == "COUNTDOWN":
            dev["state"] = "IDLE"
            await send_event(ws, "cancel", "", "dash")
        elif cmd == "sim":
            dev["sim"] = bool(msg.get("on"))
        elif cmd == "notify_result" and msg.get("id") == dev["notify_id"]:
            dev["result"] = msg
        if cmd not in ("bpm", "sim"):
            print("command:", msg)


async def stream(ws, args):
    samples = sim.mv_to_counts(sim.synthetic_ecg(60, 72))
    batch = config.BATCH_SIZE
    loop = asyncio.get_running_loop()
    start = loop.time()
    jobs = []
    await send_event(ws, "boot", "fake_device", "local")
    for k in range(int(args.seconds * 10)):
        # every batch has a fixed time from the start, like the 4 ms timer on the device
        wait = start + k * 0.1 - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        n = k * batch
        part = [int(samples[(n + i) % len(samples)]) for i in range(batch)]
        if k != int(args.drop_at * 10):
            await send(ws, {"t": "ecg", "n": n, "lo": 0, "s": part})
        if k % 10 == 0:
            await send(ws, {"t": "status", "bpm": 72, "leads": 1, "lat": 0, "lon": 0, "fix_age": -1, "sd": 0,
                            "oled": 0, "csq": 0, "gsm": "off", "state": dev["state"], "sim": int(dev["sim"]), "up": k // 10,
                            "notify": "fake device"})
        if dev["state"] == "COUNTDOWN" and loop.time() >= dev["countdown_end"]:
            text = "[DEMO] ECG EMERGENCY FakeDevice: %s. HR 72. Loc: location unavailable" % dev.get("reason", "")
            jobs.append(asyncio.create_task(ask_relay(ws, text, "dash")))
        if args.panic_at > 0 and k == int(args.panic_at * 10):
            await send_event(ws, "panic", "", "local")
            text = "[DEMO] PANIC pressed by FakeDevice. Loc: location unavailable"
            jobs.append(asyncio.create_task(ask_relay(ws, text, "local")))
    for job in jobs:
        await job


async def main_async(args):
    url = "ws://%s:%d/ws/device" % (args.host, config.SERVER_PORT)
    async with websockets.connect(url) as ws:
        reader = asyncio.create_task(read_commands(ws, args.countdown))
        if args.notify_test:
            ok = await ask_relay(ws, "[DEMO] notify test from fake_device.py", "local")
            print("NOTIFY TEST: PASS" if ok else "NOTIFY TEST: FAIL")
            reader.cancel()
            return ok
        await stream(ws, args)
        reader.cancel()

    with urllib.request.urlopen("http://%s:%d/api/state" % (args.host, config.SERVER_PORT), timeout=5) as resp:
        state = json.loads(resp.read())
    print("dashboard counted: samples %d, in the last 60 s %d, gaps %d, lost %d" % (
        state["samples"], state["samples_60s"], state["gaps"], state["lost"]))
    return True


def main():
    parser = argparse.ArgumentParser(description="Pretends to be the ESP32 so the dashboard can be tried alone")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--countdown", type=float, default=config.COUNTDOWN_S)
    parser.add_argument("--panic-at", type=float, default=0, help="send a panic event after this many seconds")
    parser.add_argument("--drop-at", type=float, default=-1, help="skip one batch at this second (makes a gap)")
    parser.add_argument("--notify-test", action="store_true", help="only send one notify request (gate G3.5)")
    args = parser.parse_args()
    try:
        ok = asyncio.run(main_async(args))
    except OSError:
        print("Dashboard not reachable. Start it first: python dashboard\\server.py")
        ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
