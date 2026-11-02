"""Local web server for the setup UI (http://127.0.0.1:8765). Runs on its own thread."""

import asyncio
import json
import threading
from pathlib import Path

from aiohttp import WSMsgType, web

DROPPABLE = {"pose", "motion"}
MAX_BACKLOG = 30


def _json_default(o):
    """numpy scalars/arrays sneaking into a message shouldn't kill the connection."""
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


class Server:
    def __init__(self, engine, web_dir, host="127.0.0.1", port=8765, sim=None, sim_camera=None):
        self.engine = engine
        self.sim = sim
        self.sim_camera = sim_camera
        self.web_dir = Path(web_dir)
        self.host = host
        self.port = port
        self.loop = None
        self.clients = set()
        self._ready = threading.Event()
        self._error = None

    def start(self):
        threading.Thread(target=self._thread, name="server", daemon=True).start()
        self._ready.wait(10)
        if self._error:
            raise self._error
        self.engine.listeners.append(self._from_engine)
        self.engine.binary_listeners.append(self._from_engine)

    def _thread(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        self.loop.run_forever()

    async def _serve(self):
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/ws", self._ws)
        app.router.add_get("/api/state", self._api_state)
        app.router.add_get("/api/debug", self._api_debug)
        app.router.add_post("/api/sim", self._api_sim)
        app.router.add_get("/api/metrics", self._api_metrics)
        app.router.add_static("/static", self.web_dir)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, self.host, self.port).start()

    async def _index(self, request):
        return web.FileResponse(self.web_dir / "index.html",
                                headers={"Cache-Control": "no-store"})

    async def _api_state(self, request):
        return web.json_response(self.engine.snapshot())

    async def _api_debug(self, request):
        keys = self.engine.keys
        return web.json_response({"dry_run": keys.dry_run, "sent": keys.sent if keys.dry_run else None,
                                  "armed": self.engine.armed})

    async def _api_metrics(self, request):
        m = self.engine.metrics
        history = await self.loop.run_in_executor(None, m.history)
        return web.json_response({"session": m.session(), "history": history, "enabled": m.enabled},
                                 dumps=lambda o: json.dumps(o, allow_nan=False, default=_json_default))

    async def _api_sim(self, request):
        """Simulated hardware, for demos and end-to-end tests (only with --sim)."""
        if self.sim is None:
            return web.json_response({"error": "start with --sim"}, status=400)
        body = await request.json()
        if body.get("clash"):
            self.sim.clash()
            return web.json_response({"ok": True})
        if body.get("body") is not None:
            seconds = self.sim_camera.perform_body(body["body"], float(body.get("size", 1.0))) \
                if self.sim_camera is not None else None
            return web.json_response({"ok": seconds is not None, "seconds": seconds})
        if body.get("pose") is not None:
            ok = self.sim_camera is not None and self.sim_camera.show(body["pose"], float(body.get("seconds", 2)))
            return web.json_response({"ok": ok})
        try:
            seconds = self.sim.perform(body["role"], body["gesture"], float(body.get("spread", 1.0)),
                                       body.get("offset") or (0.0, 0.0, 0.0))
        except (KeyError, StopIteration) as exc:
            return web.json_response({"error": f"unknown {exc}"}, status=400)
        return web.json_response({"ok": True, "seconds": seconds})

    async def _ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        q = asyncio.Queue()
        self.clients.add(q)
        sender = asyncio.ensure_future(self._sender(ws, q))
        self.engine.preview_wanted = True
        q.put_nowait(self.engine.snapshot())
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    op = json.loads(msg.data)
                except ValueError:
                    continue
                reply = await self.loop.run_in_executor(None, self.engine.handle, op)
                if reply:
                    q.put_nowait(reply)
        finally:
            self.clients.discard(q)
            sender.cancel()
            if not self.clients:
                self.engine.preview_wanted = False
        return ws

    async def _sender(self, ws, q):
        try:
            while True:
                msg = await q.get()
                if isinstance(msg, (bytes, bytearray)):
                    await ws.send_bytes(msg)
                else:
                    await ws.send_str(json.dumps(msg, allow_nan=False, default=_json_default))
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
            pass
        except (TypeError, ValueError) as exc:     # a bad message: log it, don't go silent
            print(f"  ! couldn't send a message to the UI: {exc}")

    def _from_engine(self, msg):
        if self.loop is not None and self.clients:
            self.loop.call_soon_threadsafe(self._fanout, msg)

    def _fanout(self, msg):
        droppable = isinstance(msg, (bytes, bytearray)) or msg.get("type") in DROPPABLE
        for q in self.clients:
            if droppable and q.qsize() > MAX_BACKLOG:
                continue
            q.put_nowait(msg)
