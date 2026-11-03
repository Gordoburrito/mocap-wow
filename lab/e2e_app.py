"""End to end against a running app with simulated Joy-Cons and a webcam that sees them.
Captures a slash at three heights (the same to the gyro) and a chop, fights with them, and
checks every key; with `drill`, runs the accuracy test by doing whatever move it asks for.

    ./run.sh --sim --sim-camera --dry-run --no-browser --port 8790      # in one terminal
    .venv/bin/python lab/e2e_app.py 8790                                # capture + fight
    .venv/bin/python lab/e2e_app.py 8790 drill                          # then the accuracy test
"""
import asyncio, json, aiohttp, sys
B = "http://127.0.0.1:%s" % (sys.argv[1] if len(sys.argv) > 1 else "8790")
HIGH, LOW = [0, 0, 0.40], [0, 0, -0.40]
async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(B + "/ws") as ws:
            async def drain():
                while True:
                    try: await asyncio.wait_for(ws.receive(), 0.01)
                    except asyncio.TimeoutError: return
            async def post(body):
                async with s.post(B + "/api/sim", json=body) as r: return await r.json()
            async def state():
                async with s.get(B + "/api/state") as r: return await r.json()
            async def op(o): await ws.send_str(json.dumps(o))
            async def wait(sec):
                end = asyncio.get_event_loop().time() + sec
                while asyncio.get_event_loop().time() < end: await drain(); await asyncio.sleep(0.05)
            await op({"op": "settings", "fields": {"require_wow_front": False}}); await wait(1.0)
            st = await state(); print("devices:", [(d["name"], d["role"], d["rate"], d["side"]) for d in st["devices"]], "camera:", st["camera"])
            moves = [("Slash", "slash_left", None, {"code": "Digit1", "mods": []}),
                     ("High slash", "slash_left", HIGH, {"code": "Digit2", "mods": []}),
                     ("Low slash", "slash_left", LOW, {"code": "Digit3", "mods": []}),
                     ("Chop", "overhead_chop", None, {"code": "Digit4", "mods": []})]
            async def takes(body):
                """Countdown capture: do the move once in each recording window."""
                for i in range(12):
                    c = (await state())["capture"]
                    if c is None or c["phase"] != "perform":
                        return
                    while (await state())["capture"]["take_phase"] != "recording":
                        await wait(0.05)
                    await post(body)
                    while ((await state())["capture"] or {}).get("take_phase") in ("recording", "checking"):
                        await wait(0.05)
                    while ((await state())["capture"] or {}).get("take_phase") == "review":
                        await wait(0.05)
            for name, g, off, key in moves:
                await op({"op": "capture_start", "trigger": "ui"}); await wait(0.3)
                await takes({"role": "melee", "gesture": g, "offset": off})
                st = await state(); c = st["capture"]
                if c and c["phase"] == "perform":
                    await op({"op": "capture_finish"}); await wait(0.4)
                await op({"op": "capture_key", "key": key}); await op({"op": "capture_update", "fields": {"name": name}}); await wait(0.3)
                await op({"op": "capture_save"}); await wait(0.6)
            st = await state()
            for gg in st["gestures"]:
                print("  gesture", gg["name"], gg["key_label"], "takes", gg["count"], "camera takes", gg.get("camera_takes"), "uses camera", gg.get("uses_camera"), "twins", gg.get("twins"),
                      "photos", sum(1 for x in gg.get("take_photos") or [] if x))
            photos = [sum(1 for x in gg.get("take_photos") or [] if x) for gg in st["gestures"]]
            print("photos of every take:", "OK" if all(n == gg["count"] for n, gg in zip(photos, st["gestures"])) else "MISSING", photos)
            print("fusion:", st["fusion"], "devices:", [(d["name"], d["side"], d["hand_found"], d["hand_scores"]) for d in st["devices"]])
            await op({"op": "set_armed", "value": True}); await wait(0.3)
            seq = [("slash_left", None), ("slash_left", HIGH), ("overhead_chop", None), ("slash_left", LOW),
                   ("slash_left", None), ("slash_left", LOW), ("slash_left", HIGH), ("overhead_chop", None),
                   ("slash_left", HIGH), ("slash_left", None)]
            for g, off in seq:
                r = await post({"role": "melee", "gesture": g, "offset": off}); await wait(r["seconds"] + 0.5)
            await wait(0.6)
            async with s.get(B + "/api/debug") as r: dbg = await r.json()
            downs = [k for k, d, f in dbg["sent"] if d and k != 56]
            code = {None: 18, "high": 19, "low": 20, "chop": 21}
            exp = [21 if g == "overhead_chop" else 19 if off == HIGH else 20 if off == LOW else 18 for g, off in seq]
            print("sent keycodes:", downs)
            print("expected:     ", exp, "MATCH" if downs == exp else "MISMATCH")
            async with s.get(B + "/api/metrics") as r: m = await r.json()
            ses = m["session"]
            print("metrics:", {k: ses[k] for k in ("motions", "decisions", "fusion", "latency_ms", "camera_wait_ms", "reasons")})
            st = await state(); print("activity tail:", [(a["gesture"], a["outcome"], a["detail"]) for a in st["activity"][-6:]])
            print("fusion:", st["fusion"], [(d["name"], d["side"], d["bias_dps"], d["auto_align_deg"]) for d in st["devices"]])
            # Accuracy test through the UI ops
            await op({"op": "set_armed", "value": False}); await wait(0.2)
if len(sys.argv) <= 2:
    asyncio.run(main())

async def drill():
    MOVES = {"Slash": ("slash_left", None), "High slash": ("slash_left", HIGH), "Low slash": ("slash_left", LOW),
             "Chop": ("overhead_chop", None)}
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(B + "/ws") as ws:
            async def drain():
                while True:
                    try: await asyncio.wait_for(ws.receive(), 0.01)
                    except asyncio.TimeoutError: return
            async def state():
                async with s.get(B + "/api/state") as r: return await r.json()
            async with s.get(B + "/api/debug") as r: before = len((await r.json())["sent"])
            await ws.send_str(json.dumps({"op": "drill_start", "reps": 2}))
            done_idx, t_end = set(), asyncio.get_event_loop().time() + 120
            while asyncio.get_event_loop().time() < t_end:
                await drain(); st = await state(); d = st["drill"]
                if d is None: break
                if d["phase"] == "go" and d["index"] not in done_idx:
                    done_idx.add(d["index"]); g, off = MOVES[d["name"]]
                    async with s.post(B + "/api/sim", json={"role": "melee", "gesture": g, "offset": off}) as r: await r.json()
                await asyncio.sleep(0.1)
            async with s.get(B + "/api/metrics") as r: m = await r.json()
            drills = [x for day in m["history"] for x in day["drills"]]
            async with s.get(B + "/api/debug") as r: after = len((await r.json())["sent"])
            print("drill prompts done:", len(done_idx), "last drill:", drills[-1] if drills else None, "keys sent during drill:", after - before)

if len(sys.argv) > 2 and sys.argv[2] == "drill":
    asyncio.run(drill())
