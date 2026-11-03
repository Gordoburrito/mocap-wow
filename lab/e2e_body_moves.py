"""End to end: body moves from the (simulated) webcam alone, in the running app.
Captures a kick, a duck and a two-handed shove, plus one weapon slash, then plays a sequence.
A shove done while slashing must send only the slash.

    ./run.sh --sim --sim-camera --dry-run --no-browser --port 8790
    .venv/bin/python lab/e2e_body_moves.py 8790
"""
import asyncio, json, sys

import aiohttp

B = "http://127.0.0.1:%s" % (sys.argv[1] if len(sys.argv) > 1 else "8790")
KEYS = {"Kick": ("KeyK", 40), "Duck": ("KeyD", 2), "Shove": ("KeyF", 3), "Slash": ("Digit1", 18)}


async def main():
    async with aiohttp.ClientSession() as s, s.ws_connect(B + "/ws") as ws:
        async def drain():
            while True:
                try:
                    await asyncio.wait_for(ws.receive(), 0.01)
                except asyncio.TimeoutError:
                    return

        async def post(body):
            async with s.post(B + "/api/sim", json=body) as r:
                return await r.json()

        async def state():
            async with s.get(B + "/api/state") as r:
                return await r.json()

        async def op(o):
            await ws.send_str(json.dumps(o))

        async def wait(sec):
            end = asyncio.get_event_loop().time() + sec
            while asyncio.get_event_loop().time() < end:
                await drain()
                await asyncio.sleep(0.05)

        await op({"op": "settings", "fields": {"require_wow_front": False}})
        await wait(1.5)
        async def takes(body):
            """Countdown capture: do the move once in each recording window."""
            for _ in range(12):
                c = (await state())["capture"]
                if c is None or c["phase"] != "perform":
                    return
                while (await state())["capture"]["take_phase"] != "recording":
                    await wait(0.05)
                await post(body)
                while ((await state())["capture"] or {}).get("take_phase") in ("recording", "checking", "review"):
                    await wait(0.05)

        for name, body in (("Kick", "kick"), ("Duck", "duck"), ("Shove", "shove")):
            await op({"op": "capture_start", "trigger": "ui", "kind": "move"})
            await wait(0.3)
            await takes({"body": body})
            st = await state()
            c = st["capture"]
            print(f"  {name}: {c['phase'] if c else 'gone'}, {c['count'] if c else '?'} takes")
            if c and c["phase"] == "perform":
                await op({"op": "capture_finish"})
                await wait(0.5)
            await op({"op": "capture_key", "key": {"code": KEYS[name][0], "mods": []}})
            await op({"op": "capture_update", "fields": {"name": name}})
            await wait(0.3)
            await op({"op": "capture_save"})
            await wait(0.6)
        await op({"op": "capture_start", "trigger": "ui"})
        await wait(0.3)
        await takes({"role": "melee", "gesture": "slash_left"})
        c = (await state())["capture"]
        print("  Slash:", c and (c["phase"], c["kind"], c["count"], c["message"]))
        if c and c["phase"] == "perform":
            await op({"op": "capture_finish"})
            await wait(0.5)
        await op({"op": "capture_key", "key": {"code": KEYS["Slash"][0], "mods": []}})
        await op({"op": "capture_update", "fields": {"name": "Slash"}})
        await wait(0.3)
        await op({"op": "capture_save"})
        await wait(0.6)
        st = await state()
        for g in st["gestures"]:
            print("  gesture", g["name"], g["kind"], g["key_label"], g["count"], "takes", "margin",
                  (g.get("calibration") or {}).get("margin"), "photos", sum(1 for x in g.get("take_photos") or [] if x))
        photos = [sum(1 for x in g.get("take_photos") or [] if x) for g in st["gestures"]]
        print("photos of every take:", "OK" if all(n == g["count"] for n, g in zip(photos, st["gestures"])) else "MISSING", photos)
        await op({"op": "set_armed", "value": True})
        await wait(0.5)
        async with s.get(B + "/api/debug") as r:
            before = len((await r.json())["sent"])
        seq = ["kick", "duck", "shove", "kick", "shove", "duck", "slash+shove", "kick"]
        for m in seq:
            if m == "slash+shove":
                r1 = await post({"role": "melee", "gesture": "slash_left"})
                r2 = await post({"body": "shove"})
                await wait(max(r1["seconds"], r2["seconds"]) + 1.9)
            else:
                r = await post({"body": m})
                await wait(r["seconds"] + 1.9)
        async with s.get(B + "/api/debug") as r:
            sent = [k for k, d, f in (await r.json())["sent"][before:] if d and k != 56]
        want = [{"kick": 40, "duck": 2, "shove": 3, "slash+shove": 18}[m] for m in seq]
        print("sent keycodes:", sent)
        print("expected:     ", want, "MATCH" if sent == want else "MISMATCH")
        async with s.get(B + "/api/metrics") as r:
            m = await r.json()
        print("body moves:", m["session"]["body_moves"], "reasons:", m["session"]["reasons"])

asyncio.run(main())
