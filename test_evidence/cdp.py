# -*- coding: utf-8 -*-
"""Minimal CDP client using websocket-client for Edge headless acceptance."""
import base64
import json
import time

import requests
import websocket


class CDP:
    def __init__(self, debug_port=9333):
        self.base = f"http://127.0.0.1:{debug_port}"
        deadline = time.time() + 30
        targets = None
        while time.time() < deadline:
            try:
                targets = requests.get(self.base + "/json", timeout=2).json()
                if targets:
                    break
            except Exception:
                time.sleep(0.5)
        if not targets:
            raise RuntimeError("no CDP target")
        page = next(t for t in targets if t.get("type") == "page")
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=120)
        self._id = 0
        self.send("Page.enable")
        self.send("Runtime.enable")

    def send(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr, timeout_ms=30000):
        r = self.send("Runtime.evaluate", {
            "expression": expr,
            "awaitPromise": True,
            "returnByValue": True,
            "userGesture": True,
            "timeout": timeout_ms,
        })
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:1000])
        return r.get("result", {}).get("value")

    def wait_js(self, expr, timeout=90, interval=0.4):
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                last = self.eval(expr)
                if last:
                    return last
            except Exception as e:
                last = str(e)
            time.sleep(interval)
        raise TimeoutError(f"wait_js timeout: {expr} last={last}")

    def navigate(self, url, wait=1.0):
        self.send("Page.navigate", {"url": url})
        time.sleep(wait)

    def screenshot(self, path, full_page=False):
        params = {"format": "png"}
        if full_page:
            m = self.send("Page.getLayoutMetrics")
            size = m.get("cssContentSize") or m.get("contentSize")
            params["captureBeyondViewport"] = True
            params["clip"] = {
                "x": 0, "y": 0,
                "width": size["width"],
                "height": min(size["height"], 4000),
                "scale": 1,
            }
        data = self.send("Page.captureScreenshot", params)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
