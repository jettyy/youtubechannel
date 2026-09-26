"""유튜브 채널 → 텔레그램 자동 링크 전송 대시보드.

실행:  python app.py   → 브라우저에서 http://127.0.0.1:5000 이 열립니다.
"""

import os
import threading
import time
import webbrowser

from flask import Flask, jsonify, render_template, request

import sender
from engine import Engine

app = Flask(__name__)
engine = Engine()
capture_state = {"until": None, "result": None}


def ok(**extra):
    return jsonify({"ok": True, **extra})


def fail(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/state")
def state():
    snap = engine.snapshot()
    snap["capture"] = capture_state
    return jsonify(snap)


@app.post("/api/channels")
def add_channels():
    text = (request.json or {}).get("urls", "")
    errors = engine.add_channels(text)
    return ok(errors=errors)


@app.patch("/api/channels/<cid>")
def update_channel(cid):
    try:
        engine.update_channel(cid, **(request.json or {}))
    except KeyError as e:
        return fail(str(e), 404)
    return ok()


@app.delete("/api/channels/<cid>")
def delete_channel(cid):
    engine.remove_channel(cid)
    return ok()


@app.post("/api/channels/<cid>/refresh")
def refresh_channel(cid):
    try:
        engine.refresh_channel_async(cid, reset_index=bool((request.json or {}).get("reset")))
    except KeyError as e:
        return fail(str(e), 404)
    return ok()


@app.post("/api/channels/<cid>/move")
def move_channel(cid):
    engine.move_channel(cid, int((request.json or {}).get("delta", 0)))
    return ok()


@app.post("/api/settings")
def save_settings():
    try:
        engine.update_settings(request.json or {})
    except (TypeError, ValueError) as e:
        return fail(f"설정 값이 올바르지 않습니다: {e}")
    return ok()


@app.post("/api/start")
def start():
    engine.start()
    return ok()


@app.post("/api/stop")
def stop():
    engine.stop()
    return ok()


@app.post("/api/skip")
def skip():
    engine.skip_wait()
    return ok()


@app.post("/api/test")
def test_send():
    delay = int((request.json or {}).get("delay", 3))

    def run():
        time.sleep(delay)
        try:
            engine.send_test()
        except Exception as e:
            engine.add_log("error", f"테스트 전송 실패: {e}")

    threading.Thread(target=run, daemon=True).start()
    return ok()


@app.post("/api/capture")
def capture_position():
    """N초 뒤 마우스 위치를 텔레그램 입력칸 좌표로 저장한다."""
    delay = int((request.json or {}).get("delay", 5))
    capture_state.update(until=time.time() + delay, result=None)

    def run():
        time.sleep(delay)
        try:
            x, y = sender.mouse_position()
            engine.update_settings({"click_x": x, "click_y": y})
            capture_state["result"] = f"좌표 저장됨: ({x}, {y})"
        except Exception as e:
            capture_state["result"] = f"좌표를 읽지 못했습니다: {e}"
        capture_state["until"] = None

    threading.Thread(target=run, daemon=True).start()
    return ok()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    url = f"http://127.0.0.1:{port}"
    print(f"\n  대시보드 주소: {url}\n  종료하려면 이 창에서 Ctrl+C\n")
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
