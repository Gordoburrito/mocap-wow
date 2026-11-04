"""The local server: each window asks for its own video (the hidden HUD panel saying "no video"
mustn't black out the main window), a live view gets it big, and take photos are served safely."""

import asyncio
import json
import socket

import aiohttp

from mocap.engine import Engine
from mocap.keys import KeyOutput
from mocap.server import Server


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server(tmp_path, web):
    eng = Engine(str(tmp_path / "p.json"), KeyOutput(dry_run=True), profiles_dir=str(tmp_path),
                 photos_dir=str(tmp_path / "photos"))
    port = _free_port()
    Server(eng, web, port=port).start()
    return eng, f"http://127.0.0.1:{port}"


async def _until(cond, limit=2.0):
    for _ in range(int(limit / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


def test_each_window_asks_for_its_own_video(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html>")
    eng, base = _server(tmp_path, web)

    async def main():
        async with aiohttp.ClientSession() as s:
            main_ws = await s.ws_connect(base + "/ws")
            await main_ws.send_str(json.dumps({"op": "preview", "on": True, "width": 480}))
            hud = await s.ws_connect(base + "/ws")            # the HUD panel connects second
            await hud.send_str(json.dumps({"op": "preview", "on": False}))
            await asyncio.sleep(0.2)
            assert eng.preview_wanted and eng.preview_width == 480 and eng.preview_fps == 15
            await main_ws.send_str(json.dumps({"op": "preview", "on": True, "width": 960}))
            assert await _until(lambda: eng.preview_width == 960)
            assert eng.preview_fps == 30                      # a big live view gets smooth video
            # Video goes only to windows that show it.
            for _ in range(3):
                eng.emit_binary(b"\xff\xd8frame")
            got = {"main": 0, "hud": 0}
            for name, ws in (("main", main_ws), ("hud", hud)):
                try:
                    while True:
                        m = await asyncio.wait_for(ws.receive(), 0.3)
                        got[name] += m.type == aiohttp.WSMsgType.BINARY
                except asyncio.TimeoutError:
                    pass
            assert got == {"main": 3, "hud": 0}
            await main_ws.close()
            assert await _until(lambda: not eng.preview_wanted)
            await hud.close()
            # Photos: only photo-shaped names, only from the photos folder.
            (tmp_path / "photos").mkdir()
            (tmp_path / "photos" / "0123456789abcdef.jpg").write_bytes(b"\xff\xd8jpeg")
            async with s.get(base + "/photos/0123456789abcdef.jpg") as r:
                assert r.status == 200 and r.headers["Content-Type"] == "image/jpeg"
            for bad in ("..%2Fp.json", "p.json", "0123456789abcdef.png"):
                async with s.get(base + "/photos/" + bad) as r:
                    assert r.status == 404

    asyncio.run(main())


def test_open_settings_opens_the_right_pane(tmp_path):
    eng = Engine(str(tmp_path / "p.json"), KeyOutput(dry_run=True), profiles_dir=str(tmp_path))
    opened = []
    eng.open_url = opened.append
    eng.handle({"op": "open_settings", "pane": "camera"})
    eng.handle({"op": "open_settings", "pane": "accessibility"})
    eng.handle({"op": "open_settings", "pane": "anything else"})
    assert opened == ["x-apple.systempreferences:com.apple.preference.security?Privacy_Camera",
                      "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"]
