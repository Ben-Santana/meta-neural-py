"""Private WKWebView host. Tokens and callback data never enter argv or logs."""

import json
import sys
import threading
import urllib.parse

from .meta_auth import CALLBACK_SCHEMES


def main(*, smoke_test: bool = False):
    def emit(event, **values):
        print(json.dumps({"version": 1, "event": event, **values}), flush=True)

    try:
        import AppKit as A
        import Foundation as F
        import objc
        import WebKit as W
        from PyObjCTools import AppHelper

        # Force PyObjC's lazy WebKit metadata to load before constructing a
        # Python delegate (native callback thunks capture metadata at creation).
        webview_configuration = W.WKWebViewConfiguration
    except ImportError:
        emit(
            "error",
            kind="dependency",
            message="Install pyobjc-framework-WebKit for interactive sign-in",
        )
        return
    emit("hello")
    try:
        command = json.loads(sys.stdin.buffer.readline(131073))
        parsed = urllib.parse.urlsplit(command["url"])
        if (
            command.get("version") != 1
            or command.get("op") != "login"
            or parsed.scheme != "https"
            or parsed.netloc != "auth.meta.com"
        ):
            raise ValueError()
    except Exception:
        emit("error", message="Invalid login helper command")
        return

    class MetaNeuralLoginDelegate(
        F.NSObject,
        protocols=[
            objc.protocolNamed("WKNavigationDelegate"),
            objc.protocolNamed("WKUIDelegate"),
            objc.protocolNamed("WKURLSchemeHandler"),
            objc.protocolNamed("NSWindowDelegate"),
        ],
    ):
        @objc.python_method
        def finish(self, event, **values):
            if not self.finished:
                self.finished = True
                emit(event, **values)
                AppHelper.stopEventLoop()

        def windowShouldClose_(self, sender):
            self.finish("error", kind="cancelled", message="Meta sign-in was cancelled")
            return True

        def webView_startURLSchemeTask_(self, view, task):
            # The custom-scheme API has no block callback to bridge. Capture the
            # exact redirect in-process instead of forwarding it to another app.
            url = str(task.request().URL().absoluteString())
            parts = urllib.parse.urlsplit(url)
            if parts.scheme in CALLBACK_SCHEMES and parts.netloc == "frl_login":
                self.finish("callback", url=url)
            else:
                self.finish("error", message="Unexpected Meta login callback destination")
            task.didFailWithError_(
                F.NSError.errorWithDomain_code_userInfo_(
                    F.NSURLErrorDomain, F.NSURLErrorCancelled, None
                )
            )

        def webView_stopURLSchemeTask_(self, view, task):
            # Requests are completed synchronously; never call a stopped task.
            pass

        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            self, view, config, action, features
        ):
            if action.targetFrame() is None:
                view.loadRequest_(action.request())
            return None

        def webView_didFailProvisionalNavigation_withError_(self, view, navigation, error):
            # Ignore cancelled navigation after a captured custom-scheme callback.
            if not self.finished:
                self.finish("error", message="Meta sign-in navigation failed")

    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(
        A.NSApplicationActivationPolicyProhibited
        if smoke_test
        else A.NSApplicationActivationPolicyRegular
    )
    delegate = MetaNeuralLoginDelegate.alloc().init()
    delegate.finished = False
    config = webview_configuration.alloc().init()
    config.setWebsiteDataStore_(W.WKWebsiteDataStore.nonPersistentDataStore())
    for scheme in CALLBACK_SCHEMES:
        config.setURLSchemeHandler_forURLScheme_(delegate, scheme)
    view = W.WKWebView.alloc().initWithFrame_configuration_(((0, 0), (640, 720)), config)
    view.setNavigationDelegate_(delegate)
    view.setUIDelegate_(delegate)
    window = None
    if smoke_test:
        # Fixed, synthetic redirect: no window, network, login, or account data.
        scheme = command.get("test_scheme", "fb-viewapp")
        if scheme not in CALLBACK_SCHEMES:
            emit("error", message="Unsupported synthetic callback scheme")
            return
        url = scheme + "://frl_login/?token=synthetic&blob=synthetic"
    else:
        window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (640, 720)),
            A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable | A.NSWindowStyleMaskResizable,
            A.NSBackingStoreBuffered,
            False,
        )
        window.setReleasedWhenClosed_(False)
        window.setTitle_("Meta sign-in — meta-neural-py")
        window.setContentView_(view)
        window.setDelegate_(delegate)
        window.center()
        window.makeKeyAndOrderFront_(None)
        app.activateIgnoringOtherApps_(True)
        url = command["url"]
    view.loadRequest_(F.NSURLRequest.requestWithURL_(F.NSURL.URLWithString_(url)))

    def monitor_parent():
        # EOF closes the UI if the async caller cancels or dies.
        sys.stdin.buffer.read(1)
        AppHelper.callAfter(delegate.finish, "error", kind="cancelled", message="Login host closed")

    threading.Thread(target=monitor_parent, daemon=True).start()
    AppHelper.runEventLoop()
    view.stopLoading()
    view.setNavigationDelegate_(None)
    view.setUIDelegate_(None)
    if window is not None:
        window.orderOut_(None)
