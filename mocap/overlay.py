"""Native overlay: a slim always-on-top panel beside the game that shows the Live HUD, the
on-screen flash and quick capture. It's a non-activating panel, so clicking it never takes
keyboard focus away from WoW, and it can join WoW's fullscreen space.

Run WoW in "Windowed (Fullscreen)" mode for the most reliable overlay.

AppKit owns the main thread here, so SDL (the Joy-Cons) is pumped from a timer on it.
"""

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
                    NSColor, NSPanel, NSScreen, NSStatusWindowLevel,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
                    NSWindowStyleMaskNonactivatingPanel)
from Foundation import NSMakeRect, NSObject, NSTimer, NSURL, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration

WIDTH = 400
MARGIN = 16


class _Pump(NSObject):
    def initWithCallback_(self, callback):
        self = objc.super(_Pump, self).init()
        if self is None:
            return None
        self.callback = callback
        return self

    def tick_(self, timer):
        self.callback()


class Overlay:
    def __init__(self, engine, url, stop, controllers=None, offscreen=False):
        self.engine = engine
        self.url = url
        self.stop = stop
        self.controllers = controllers
        self.offscreen = offscreen
        self.panel = None
        self.web = None
        self.height = None

    def _frame(self, height=None):
        screen = NSScreen.mainScreen().visibleFrame()
        h = min(height or screen.size.height - 2 * MARGIN, screen.size.height - 2 * MARGIN)
        x = screen.origin.x + screen.size.width - WIDTH - MARGIN
        y = screen.origin.y + screen.size.height - h - MARGIN
        if self.offscreen:
            x, y = -10000, -10000
        return NSMakeRect(x, y, WIDTH, h)

    def build(self, standalone=True):
        app = NSApplication.sharedApplication()
        if standalone:
            # Just the panel: no Dock icon, never takes focus from the game.
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self._frame(), style, NSBackingStoreBuffered, False)
        panel.setFloatingPanel_(True)       # (this resets the level, so set the level after)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                     | NSWindowCollectionBehaviorFullScreenAuxiliary
                                     | NSWindowCollectionBehaviorStationary)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        web = WKWebView.alloc().initWithFrame_configuration_(
            panel.contentView().bounds(), WKWebViewConfiguration.alloc().init())
        web.setValue_forKey_(False, "drawsBackground")
        web.setAutoresizingMask_(18)   # width + height sizable
        panel.setContentView_(web)
        web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(self.url + "#/overlay")))
        panel.orderFrontRegardless()
        self.panel, self.web = panel, web
        return app

    def set_visible(self, visible):
        if visible:
            self.panel.orderFrontRegardless()
        else:
            self.panel.orderOut_(None)

    @property
    def visible(self):
        return bool(self.panel is not None and self.panel.isVisible())

    def update_height(self):
        wanted = getattr(self.engine, "overlay_height", None)
        if wanted != self.height:
            self.height = wanted
            self.panel.setFrame_display_(self._frame(wanted), True)

    def _tick(self):
        if self.stop.is_set():
            NSApplication.sharedApplication().stop_(None)
            # stop_ only takes effect after an event; post one so run() returns.
            from AppKit import NSEvent, NSEventTypeApplicationDefined
            from Foundation import NSPoint
            ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                NSEventTypeApplicationDefined, NSPoint(0, 0), 0, 0, 0, None, 0, 0, 0)
            NSApplication.sharedApplication().postEvent_atStart_(ev, True)
            return
        if self.controllers is not None:
            self.controllers.step()
        self.update_height()

    def run(self):
        app = self.build()
        pump = _Pump.alloc().initWithCallback_(self._tick)
        self._pump = pump
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.004, pump, "tick:", None, True)
        app.run()

    def page_title(self):
        """For tests: the loaded page's title (empty until WebKit finishes loading)."""
        return str(self.web.title() or "")
