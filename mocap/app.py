"""The Mac app. Double-click Mocap.app (or run `python -m mocap.app`): a window with the setup
UI, a menu-bar item to arm/disarm and to show the HUD over WoW, and the same engine as the
command line. Closing the window keeps it running in the menu bar; quit from there or with ⌘Q.

AppKit owns the main thread here, so the Joy-Cons (SDL) are pumped from a timer on it.
"""

import sys
import webbrowser

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                    NSColor, NSMenu, NSMenuItem, NSScreen, NSStatusBar, NSTerminateNow,
                    NSVariableStatusItemLength, NSWindow, NSWindowStyleMaskClosable,
                    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
                    NSWindowStyleMaskTitled)
from Foundation import NSMakeRect, NSObject, NSTimer, NSURL, NSURLRequest
from WebKit import WKWebView, WKWebViewConfiguration

from .__main__ import Running, parser
from .overlay import Overlay

APP_NAME = "Mocap"


def _item(title, action, key="", target=None, mods=None):
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    if target is not None:
        item.setTarget_(target)
    if mods is not None:
        item.setKeyEquivalentModifierMask_(mods)
    return item


class AppDelegate(NSObject):
    def initWithRunning_(self, run):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.run = run
        self.window = None
        self.web = None
        self.status = None
        self.overlay = None
        self._armed_shown = None
        return self

    # ------------------------------------------------------------------ set-up

    def applicationDidFinishLaunching_(self, note):
        app = NSApplication.sharedApplication()
        app.setMainMenu_(self._main_menu())
        self._make_window()
        self._make_status_item()
        self.overlay = Overlay(self.run.engine, self.run.url, self.run.stop, None)
        self.overlay.build(standalone=False)
        if not self.run.args.overlay:
            self.overlay.set_visible(False)
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.004, self, "tick:", None, True)
        app.activateIgnoringOtherApps_(True)
        if self.run.args.snapshot:
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                4.0, self, "snapshot:", None, False)

    def snapshot_(self, timer):
        """--snapshot PATH: save what the window shows (tests can't screen-record)."""
        path = self.run.args.snapshot

        def done(image, error):
            if image is not None:
                from AppKit import NSBitmapImageRep, NSPNGFileType
                rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(path, True)
            print(f"snapshot {'saved' if image is not None else 'failed'}: {path} {error or ''}", flush=True)

        def title(value, error):
            print(f"page: {value}", flush=True)

        self.web.evaluateJavaScript_completionHandler_(
            "document.title + ' | ' + (document.querySelector('nav') ? 'nav ok' : 'no nav') + ' | ' + "
            "(document.querySelector('#error') && document.querySelector('#error').textContent || 'no error')", title)
        self.web.takeSnapshotWithConfiguration_completionHandler_(None, done)

    def _main_menu(self):
        bar = NSMenu.alloc().init()
        app_menu = NSMenu.alloc().initWithTitle_(APP_NAME)
        app_menu.addItem_(_item(f"About {APP_NAME}", "orderFrontStandardAboutPanel:"))
        app_menu.addItem_(NSMenuItem.separatorItem())
        app_menu.addItem_(_item("Accuracy test…", "openMetrics:", "", self))
        app_menu.addItem_(_item("Open the demo (sample data)", "openDemo:", "", self))
        app_menu.addItem_(_item("Open in browser", "openBrowser:", "", self))
        app_menu.addItem_(NSMenuItem.separatorItem())
        app_menu.addItem_(_item(f"Hide {APP_NAME}", "hide:", "h"))
        app_menu.addItem_(_item(f"Quit {APP_NAME}", "terminate:", "q"))
        edit = NSMenu.alloc().initWithTitle_("Edit")      # copy/paste in the page's text fields
        for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), (None, None, None),
                                   ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                                   ("Select All", "selectAll:", "a")):
            edit.addItem_(NSMenuItem.separatorItem() if title is None else _item(title, action, key))
        view = NSMenu.alloc().initWithTitle_("View")
        view.addItem_(_item("Reload", "reload:", "r", self))
        view.addItem_(_item("HUD over the game", "toggleOverlay:", "", self))
        window = NSMenu.alloc().initWithTitle_("Window")
        window.addItem_(_item("Minimize", "performMiniaturize:", "m"))
        window.addItem_(_item("Close", "performClose:", "w"))
        window.addItem_(_item(f"Show {APP_NAME}", "showWindow:", "0", self))
        for menu in (app_menu, edit, view, window):
            top = NSMenuItem.alloc().init()
            top.setSubmenu_(menu)
            bar.addItem_(top)
        NSApplication.sharedApplication().setWindowsMenu_(window)
        return bar

    def _make_window(self):
        screen = NSScreen.mainScreen().visibleFrame()
        w, h = min(1320, screen.size.width - 80), min(860, screen.size.height - 60)
        rect = NSMakeRect(screen.origin.x + (screen.size.width - w) / 2,
                          screen.origin.y + (screen.size.height - h) / 2, w, h)
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                 | NSWindowStyleMaskResizable)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(rect, style, NSBackingStoreBuffered, False)
        win.setTitle_(APP_NAME + (" — demo (sample data)" if self.run.args.demo else ""))
        win.setReleasedWhenClosed_(False)
        win.setMinSize_((760, 520))
        win.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.06, 0.07, 0.09, 1.0))
        web = WKWebView.alloc().initWithFrame_configuration_(win.contentView().bounds(),
                                                             WKWebViewConfiguration.alloc().init())
        web.setAutoresizingMask_(18)
        win.setContentView_(web)
        web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(self.run.url)))
        win.setFrameAutosaveName_("MocapMain")
        win.makeKeyAndOrderFront_(None)
        self.window, self.web = win, web

    def _make_status_item(self):
        item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        menu = NSMenu.alloc().init()
        self.arm_item = _item("Armed", "toggleArmed:", "", self)
        menu.addItem_(self.arm_item)
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(_item(f"Show {APP_NAME}", "showWindow:", "", self))
        self.overlay_item = _item("HUD over the game", "toggleOverlay:", "", self)
        menu.addItem_(self.overlay_item)
        menu.addItem_(_item("Accuracy test…", "openMetrics:", "", self))
        if not self.run.args.demo:
            menu.addItem_(_item("Open the demo (sample data)", "openDemo:", "", self))
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(_item(f"Quit {APP_NAME}", "terminate:", ""))
        item.setMenu_(menu)
        self.status = item
        self._update_status(force=True)

    # ------------------------------------------------------------------ actions

    def showWindow_(self, sender):
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def toggleArmed_(self, sender):
        engine = self.run.engine
        engine.handle({"op": "set_armed", "value": not engine.armed})
        self._update_status(force=True)

    def toggleOverlay_(self, sender):
        self.overlay.set_visible(not self.overlay.visible)
        self._update_status(force=True)

    def openMetrics_(self, sender):
        self.web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(self.run.url + "#/metrics")))
        self.showWindow_(None)

    def openDemo_(self, sender):
        if not self.run.args.demo:
            from .__main__ import launch_demo
            launch_demo()

    def openBrowser_(self, sender):
        webbrowser.open(self.run.url)

    def reload_(self, sender):
        self.web.reload_(None)
        if self.overlay and self.overlay.web is not None:
            self.overlay.web.reload_(None)

    # ------------------------------------------------------------------ loop

    def tick_(self, timer):
        if self.run.stop.is_set():
            NSApplication.sharedApplication().terminate_(None)
            return
        if self.run.controllers is not None:
            self.run.controllers.step()
        if self.overlay is not None and self.overlay.panel is not None:
            self.overlay.update_height()
            if self.run.engine.overlay_hide_requested:
                self.run.engine.overlay_hide_requested = False
                self.overlay.set_visible(False)
                self._update_status(force=True)
        self._update_status()

    def _update_status(self, force=False):
        armed = self.run.engine.armed
        if armed == self._armed_shown and not force:
            return
        self._armed_shown = armed
        self.status.button().setTitle_("⚔ ARMED" if armed else "⚔")
        self.arm_item.setState_(1 if armed else 0)
        if self.overlay is not None and self.overlay.panel is not None:
            self.overlay_item.setState_(1 if self.overlay.visible else 0)

    # ------------------------------------------------------------------ lifecycle

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.showWindow_(None)
        return True

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return False            # keep running in the menu bar

    def applicationShouldTerminate_(self, app):
        self.run.shutdown()
        if self.run.controllers is not None:
            self.run.controllers.close()
        return NSTerminateNow


def main(argv=None):
    args = parser().parse_args(argv if argv is not None else _app_args())
    if args.selftest:
        import json
        from .__main__ import default_data_dir, selftest
        from pathlib import Path
        print(json.dumps(selftest(Path(args.data_dir) if args.data_dir else default_data_dir()), indent=1))
        return
    args.no_browser = True
    run = Running(args).start()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().initWithRunning_(run)
    app.setDelegate_(delegate)
    app.run()


def _app_args():
    # Finder adds -psn_... to apps it launches; ignore anything that isn't ours.
    return [a for a in sys.argv[1:] if not a.startswith("-psn")]


if __name__ == "__main__":
    main()
