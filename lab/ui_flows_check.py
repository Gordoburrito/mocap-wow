"""Walk every screen and pop-up of the app in a real browser, getting in and out of each one by
every exit (button, Escape, link), with real mouse presses and key presses, and check that the
engine agrees each time. Runs against an app with simulated hardware:

    ./run.sh --sim --sim-camera --dry-run --no-browser --port 8790      # in one terminal
    .venv/bin/python -m lab.ui_flows_check 8790

Needs Google Chrome. Prints a pass/fail line per flow; docs/user-flows.md is the map.
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import urllib.request

import aiohttp

PORT = sys.argv[1] if len(sys.argv) > 1 else "8790"
APP = f"http://127.0.0.1:{PORT}"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT = 9400 + (int(time.time()) % 500)      # a fresh port each run (a crashed run can't block the next)


def state():
    return json.load(urllib.request.urlopen(APP + "/api/state"))


def debug():
    return json.load(urllib.request.urlopen(APP + "/api/debug"))


async def _wait(cond, seconds=3.0):
    for _ in range(int(seconds / 0.1)):
        if cond():
            return True
        await asyncio.sleep(0.1)
    return cond()


def sim(body):
    req = urllib.request.Request(APP + "/api/sim", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


class Browser:
    def __init__(self, ws):
        self.ws, self.n = ws, 0
        self.errors = []            # JavaScript errors the page threw while we walked it

    async def cmd(self, method, **params):
        return await asyncio.wait_for(self._cmd(method, **params), 20)

    async def _cmd(self, method, **params):
        self.n += 1
        await self.ws.send_str(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = await self.ws.receive()
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise ConnectionError(f"browser connection {msg.type.name.lower()}")
            m = json.loads(msg.data)
            if m.get("method") == "Runtime.exceptionThrown":
                d = m["params"]["exceptionDetails"]
                self.errors.append((d.get("exception") or {}).get("description") or d.get("text"))
            elif m.get("method") == "Runtime.consoleAPICalled" and m["params"].get("type") == "error":
                self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in m["params"]["args"]))
            if m.get("id") == self.n:
                return m.get("result", {})

    async def js(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    async def exists(self, selector):
        return bool(await self.js(f"!!document.querySelector({json.dumps(selector)})"))

    async def click(self, selector, hold=0.12):
        box = await self.js(f"(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return null;"
                            " e.scrollIntoView({block: 'center'});"
                            " const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
        if box is None:
            raise AssertionError(f"no {selector} on screen")
        x, y = box
        await self.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        await self.cmd("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        await asyncio.sleep(hold)
        await self.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)
        await asyncio.sleep(0.6)

    async def key(self, key, code, vk):
        for kind in ("keyDown", "keyUp"):
            await self.cmd("Input.dispatchKeyEvent", type=kind, key=key, code=code, windowsVirtualKeyCode=vk)
        await asyncio.sleep(0.6)

    async def escape(self):
        await self.key("Escape", "Escape", 27)

    async def go(self, hash_):
        await self.js(f"location.hash = {json.dumps(hash_)}")
        await asyncio.sleep(0.8)


async def get_out(b):
    """Close whatever is open, the way a person would: Escape, then back to the Library."""
    for _ in range(4):
        st = state()
        if not (st["capture"] or st["calibration"] or st["drill"]) and not await b.exists(".modal"):
            break
        await b.escape()
    await b.go("#/library")


async def flows(b, report):
    async def check(name, fn):
        try:
            await get_out(b)
            await fn()
            report.append((name, True, ""))
        except Exception as exc:     # keep walking: report everything
            report.append((name, False, f"{type(exc).__name__}: {exc}"[:160]))
        print(f"  {'PASS' if report[-1][1] else 'FAIL'}  {name}{'  — ' + report[-1][2] if report[-1][2] else ''}", flush=True)

    def expect(cond, msg):
        if not cond:
            raise AssertionError(msg)

    # ------------------------------------------------------------- with data in it
    if state()["gestures"]:
        async def every_tile():
            await b.go("#/library")
            for g in state()["gestures"]:
                await b.go("#/library")
                await b.click(f'.tile[data-gid="{g["id"]}"]')
                expect(await b.js("location.hash") == f"#/gesture/{g['id']}", f"tile {g['name']} didn't open")
                name = await b.js("document.querySelector('input[data-k=name]')?.value")
                expect(name == g["name"], f"{g['name']}'s page shows {name!r}")
                if g["kind"] in ("motion", "move", "pose"):
                    expect(await b.exists("select[data-k=scope]"), f"{g['name']}: no 'Judged on'")
                if g["kind"] == "move" and g.get("real"):
                    expect(await b.exists(".panel .frame svg"), f"{g['name']}: recording not drawn")
                await b.click('a[href="#/library"]')
                expect(await b.js("location.hash") == "#/library", f"couldn't get back from {g['name']}")
        await check(f"Every gesture: tile → its page → back ({len(state()['gestures'])} gestures)", every_tile)

        async def filters():
            await b.go("#/library")
            for f in ("melee", "bow", "body", "poses", "movement", "system", "all"):
                await b.click(f'.filters [data-v="{f}"]')
                shown = await b.js("document.querySelectorAll('#tiles .tile').length")
                label = await b.js(f"document.querySelector('.filters [data-v=\"{f}\"] span').textContent")
                expect(str(shown) == label.strip(), f"filter {f}: {shown} tiles but says {label}")
        await check("Library filters show what they count", filters)

        async def scope_change():
            mv = next((g for g in state()["gestures"] if g["kind"] == "move"), None)
            if mv is None:
                return
            await b.go(f"#/gesture/{mv['id']}")
            await b.js("(() => { const s = document.querySelector('select[data-k=scope]'); s.value = 'whole';"
                       " s.dispatchEvent(new Event('change', {bubbles: true})); })()")
            await asyncio.sleep(0.8)
            expect(next(g for g in state()["gestures"] if g["id"] == mv["id"])["scope"] == "whole",
                   "changing 'Judged on' didn't stick")
            await b.js("(() => { const s = document.querySelector('select[data-k=scope]'); s.value = 'auto';"
                       " s.dispatchEvent(new Event('change', {bubbles: true})); })()")
            await asyncio.sleep(0.8)
            expect(next(g for g in state()["gestures"] if g["id"] == mv["id"])["scope"] == "auto",
                   "changing it back didn't stick")
            await b.go("#/library")
        await check("'Judged on' changes and changes back", scope_change)

        async def metrics_data():
            await b.go("#/metrics")
            await asyncio.sleep(1.5)
            expect(await b.js("[...document.querySelectorAll('h2')].some(h => h.textContent.startsWith('Day by day'))"),
                   "no day-by-day section")
            expect(await b.js("document.querySelectorAll('svg rect').length") > 5, "day-by-day chart is empty")
            expect(await b.js("document.body.textContent.includes('Body moves: camera alone')"), "lab results missing")
            await b.go("#/hud")
            expect(await b.js("document.querySelectorAll('.fired li:not(.dim)').length") > 0, "HUD shows nothing fired")
            await b.go("#/library")
        await check("Metrics shows history and lab data; HUD shows what fired", metrics_data)

        async def photos():
            with_photo = [g for g in state()["gestures"] if g.get("photo")]
            if not with_photo:
                return
            await b.go("#/library")
            for g in with_photo:
                expect(await b.exists(f'.tile[data-gid="{g["id"]}"] svg.photo image'), f"{g['name']}: no photo on its tile")
            # Every photo on the page actually loads, as a JPEG.
            bad = await b.js("""(async () => { const out = [];
                for (const im of document.querySelectorAll('svg.photo image')) {
                  const r = await fetch(im.getAttribute('href'));
                  if (!r.ok || r.headers.get('content-type') !== 'image/jpeg') out.push(im.getAttribute('href')); }
                return out; })()""")
            expect(not bad, f"photos that don't load: {bad[:3]}")
            await b.go(f"#/gesture/{with_photo[0]['id']}")
            expect(await b.exists(".big-photo svg.photo image"), "gesture page has no big photo")
            await b.go("#/library")
        await check(f"Photos on tiles and pages load ({len([g for g in state()['gestures'] if g.get('photo')])} gestures)", photos)

    # ------------------------------------------------------------- pages
    async def pages():
        for page, heading in (("engine", "Engine"), ("metrics", "Metrics"), ("library", "Library")):
            await b.click(f'.side .nav a[href="#/{page}"]')
            expect(await b.js("location.hash") == f"#/{page}", f"sidebar didn't open {page}")
            expect(await b.js(f"[...document.querySelectorAll('h1')].some(h => h.textContent.trim() === '{heading}')"),
                   f"{page} heading missing")
    await check("Sidebar: Library, Engine, Metrics", pages)

    async def camera():
        await b.click('.side .nav a[href="#/camera"]')
        expect(await b.js("location.hash") == "#/camera", "Camera didn't open")
        await asyncio.sleep(1.5)
        if state()["camera"]["ok"]:
            expect(await b.js("document.querySelector('.cam-stage img.preview-img')?.classList.contains('live')"),
                   "no live video on the Camera page")
            expect(await b.js("document.querySelector('.cam-stage img.preview-img').naturalWidth") >= 900,
                   "the Camera page's video isn't the big one")
            expect(await b.exists("#cam-over .cam-pill"), "no tracking status over the video")
        # Into capture from here, and back out to the same live view.
        await b.click('[data-act=capture].capture-btn')
        expect(state()["capture"] is not None, "Capture didn't start from the Camera page")
        await b.escape()
        expect(state()["capture"] is None and await b.js("location.hash") == "#/camera", "Escape didn't come back")
        expect(await b.exists("#cam-slot .cam-stage"), "the live view is gone after capture")
        await b.click('.side .nav a[href="#/library"]')
    await check("Camera: live video + skeleton → Capture → Escape back → Library", camera)

    async def hud():
        await b.click('.side .nav a[href="#/hud"]')
        expect(await b.js("location.hash") == "#/hud", "HUD didn't open")
        await b.click('a[href="#/library"]')
        expect(await b.js("location.hash") == "#/library", "no way back from the HUD")
        await b.go("#/hud")
        await b.escape()
        expect(await b.js("location.hash") == "#/library", "Escape didn't leave the HUD")
    await check("Live HUD: in and back out (link, Escape)", hud)

    # ------------------------------------------------------------- capture
    async def capture_exits():
        for opener in ('[data-act=capture].capture-btn', '.side [data-act=capture]'):
            await b.click(opener)
            await asyncio.sleep(0.8)
            expect(state()["capture"] is not None, f"{opener} didn't open capture")
            await b.click(".modal [data-act=capture-cancel]")
            expect(state()["capture"] is None and not await b.exists(".modal"), "Cancel didn't close capture")
        await b.click('[data-act=capture].capture-btn')
        await asyncio.sleep(0.8)
        await b.escape()
        expect(state()["capture"] is None and not await b.exists(".modal"), "Escape didn't close capture")
        for kind_btn, kind, leave in (("capture-pose", "pose", "cancel"), ("capture-move", "move", "escape")):
            await b.click('[data-act=capture].capture-btn')
            await asyncio.sleep(0.8)
            await b.click(f".modal [data-act={kind_btn}]")
            expect((state()["capture"] or {}).get("kind") == kind, f"{kind_btn} didn't switch to {kind}")
            if leave == "cancel":
                await b.click(".modal [data-act=capture-cancel]")
            else:
                await b.escape()
            expect(state()["capture"] is None, f"couldn't leave a {kind} capture")
    await check("Capture: open (button, sidebar) → Cancel / Escape / It's a pose / It's a body move", capture_exits)

    async def body_takes():
        """Countdown capture: kick once in each recording window, like a person would."""
        for _ in range(12):
            c = state()["capture"]
            if c is None or c["phase"] != "perform":
                return
            t0 = time.time()
            while (state()["capture"] or {}).get("take_phase") != "recording" and time.time() - t0 < 10:
                await asyncio.sleep(0.05)
            sim({"body": "kick"})
            while (state()["capture"] or {}).get("take_phase") in ("recording", "checking", "review"):
                await asyncio.sleep(0.05)

    async def bind_phase():
        await b.click('[data-act=capture].capture-btn')
        await asyncio.sleep(0.8)
        await b.click(".modal [data-act=capture-move]")
        await body_takes()
        expect((state()["capture"] or {}).get("phase") == "bind", "didn't reach the key step")
        if state()["camera"]["ok"]:
            shown = await b.js("document.querySelectorAll('.modal .cap-take svg.photo image').length")
            expect(shown >= 3, f"the key step shows {shown} take photos")
        await b.click(".modal [data-act=capture-redo]")
        await asyncio.sleep(0.8)
        c = state()["capture"]
        expect(c is not None and c["phase"] == "perform", "Redo didn't start over")
        await b.escape()
        expect(state()["capture"] is None, "Escape didn't close after Redo")
        await b.click('[data-act=capture].capture-btn')
        await asyncio.sleep(0.8)
        await b.click(".modal [data-act=capture-move]")
        await body_takes()
        await b.key("k", "KeyK", 75)
        expect((state()["capture"] or {}).get("key") is not None, "pressing a key didn't bind it")
        await b.click(".modal [data-act=capture-save]")
        expect(state()["capture"] is None and any(g["kind"] == "move" for g in state()["gestures"]),
               "Save didn't create the move")
    await check("Capture a body move: 5 takes → key step → Redo / Escape; again → press K → Save", bind_phase)

    async def bind_cancel():
        await b.click('[data-act=capture].capture-btn')
        await asyncio.sleep(0.8)
        await b.click(".modal [data-act=capture-move]")
        await body_takes()
        expect((state()["capture"] or {}).get("phase") == "bind", "didn't reach the key step")
        await b.click(".modal [data-act=capture-cancel]")
        expect(state()["capture"] is None, "Cancel on the key step didn't close")
    await check("Capture, key step → Cancel", bind_cancel)

    async def practice_page():
        await b.click('.side .nav a[href="#/practice"]')
        expect(await b.js("location.hash") == "#/practice", "Practice didn't open")
        expect(await _wait(lambda: state()["practice"]), "keys didn't turn off for practice")
        before = len(debug()["sent"] or [])
        if not state()["demo"]:
            g = next((g for g in state()["gestures"] if g["kind"] == "move" and g["ready"]), None)
            expect(g is not None, "nothing to practise")
            sim({"body": "kick"})               # the walk captured a kick on K
        shown = False
        for _ in range(150):                    # the demo player makes a move every few seconds
            if await b.exists(".prac-card.hit, .prac-card.near, .prac-card.miss"):
                shown = True
                break
            await asyncio.sleep(0.1)
        expect(shown, "no result after a move")
        expect(await b.js("document.querySelector('.prac-card .prac-name').textContent.trim().length") > 0, "empty result")
        expect(len(debug()["sent"] or []) == before, "a key was sent while practising")
        await b.click('.side .nav a[href="#/library"]')
        expect(await _wait(lambda: not state()["practice"]), "keys didn't come back after leaving Practice")
    await check("Practice: keys off → do a move → it shows which → leave → keys back", practice_page)

    # ------------------------------------------------------------- HUD toast + gesture page
    async def toast():
        await b.go("#/hud")
        if await b.exists("[data-act=toast-close]"):
            await b.click("[data-act=toast-close]")
            expect(not await b.exists("[data-act=toast-close]"), "toast ✕ didn't close it")
        await b.go("#/library")
    await check("HUD 'Saved' message → ✕", toast)

    async def gesture_page():
        await b.go("#/library")
        await b.click(".tile")
        expect((await b.js("location.hash")).startswith("#/gesture/"), "tile didn't open its page")
        await b.click("[data-act=rebind]")
        expect(await b.exists("[data-act=rebind].listening"), "key button didn't start listening")
        await b.escape()
        expect(not await b.exists("[data-act=rebind].listening"), "Escape didn't stop listening")
        await b.click("[data-act=delete]")
        expect("Click again" in (await b.js("document.querySelector('[data-act=delete]').textContent")),
               "Delete didn't ask to confirm")
        await asyncio.sleep(3.5)
        expect("Delete gesture" in (await b.js("document.querySelector('[data-act=delete]').textContent")),
               "Delete confirmation didn't reset")
        expect(any(g["kind"] == "move" for g in state()["gestures"]), "it deleted without the second click")
        await b.click('a[href="#/library"]')
        expect(await b.js("location.hash") == "#/library", "breadcrumb didn't go back")
    await check("Move page: open from tile → key (Escape) → Delete (times out) → back", gesture_page)

    # ------------------------------------------------------------- engine page
    async def calibration():
        await b.go("#/engine")
        for leave in ("cancel", "escape"):
            await b.click("[data-act=calibrate]")
            await asyncio.sleep(0.5)
            expect(state()["calibration"] is not None and await b.exists(".modal"), "Calibrate didn't open")
            if leave == "cancel":
                await b.click(".modal [data-act=calib-cancel]")
            else:
                await b.escape()
            expect(state()["calibration"] is None and not await b.exists(".modal"), f"calibration {leave} failed")
    await check("Calibrate a weapon → Cancel / Escape", calibration)

    async def neutral():
        await b.go("#/engine")
        for leave in ("cancel", "escape"):
            await b.click("[data-act=neutral]")
            expect(await b.exists("[data-act=neutral-cancel]"), "no Cancel while recording Neutral")
            if leave == "cancel":
                await b.click("[data-act=neutral-cancel]")
            else:
                await b.escape()
            expect(await b.exists("[data-act=neutral]"), f"Neutral recording {leave} failed")
    await check("Record Neutral → Cancel / Escape", neutral)

    # ------------------------------------------------------------- accuracy test
    async def drill():
        await b.go("#/metrics")
        await asyncio.sleep(1.0)
        for leave in ("stop", "escape"):
            await b.click("[data-act=drill-start]")
            await asyncio.sleep(0.5)
            expect(state()["drill"] is not None and await b.exists(".modal"), "accuracy test didn't start")
            if leave == "stop":
                await b.click(".modal [data-act=drill-stop]")
            else:
                await b.escape()
            expect(state()["drill"] is None and not await b.exists(".modal"), f"accuracy test {leave} failed")
    await check("Accuracy test → Stop / Escape", drill)


async def main():
    profile = tempfile.mkdtemp(prefix="mocap-ui-check-")
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={CDP_PORT}", "--window-size=1300,900",
                             f"--user-data-dir={profile}", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = []
    try:
        page = None
        for _ in range(50):
            try:
                page = next(t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json"))
                            if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        async with aiohttp.ClientSession() as s, s.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            b = Browser(ws)
            await b.cmd("Runtime.enable")
            await b.cmd("Page.navigate", url=APP + "/#/library")
            await asyncio.sleep(2.0)
            try:
                await flows(b, report)
                await b.js("1")
            except Exception as exc:
                report.append(("Browser connection", False, f"lost: {exc}"[:200]))
            report.append(("No JavaScript errors while walking", not b.errors,
                           "; ".join(sorted(set(b.errors)))[:200]))
    finally:
        proc.terminate()
    for name, ok, why in report[-2:]:
        if name in ("Browser connection", "No JavaScript errors while walking"):
            print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + why if why else ''}")
    print(f"{sum(ok for _, ok, _ in report)} of {len(report)} flows pass")
    sys.exit(0 if all(ok for _, ok, _ in report) else 1)


if __name__ == "__main__":
    asyncio.run(main())
