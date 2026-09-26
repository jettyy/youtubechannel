"""유튜브 채널 → 텔레그램 자동 링크 전송 대시보드.

실행:  python app.py   → 브라우저에서 http://127.0.0.1:8765 가 열립니다.
"""

import json
import os
import socket
import sys
import threading
import urllib.request
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


# 맥은 5000번 포트를 "AirPlay 수신 모드"가 쓰고 있어서 다른 포트를 사용
DEFAULT_PORT = 8765
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_our_app(port: int) -> bool:
    try:
        with _local.open(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return "total_sent" in json.loads(r.read())
    except Exception:
        return False


def port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def open_browser_when_ready(port: int):
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        if is_our_app(port):
            webbrowser.open(url)
            return
        time.sleep(0.2)
    print(f"  [경고] 대시보드가 응답하지 않습니다. 브라우저에서 {url} 을 직접 열어 보세요.")


if __name__ == "__main__":
    no_browser = os.environ.get("NO_BROWSER") == "1"
    ports = [int(os.environ["PORT"])] if os.environ.get("PORT") else range(DEFAULT_PORT, DEFAULT_PORT + 20)
    port = None
    for p in ports:
        if is_our_app(p):
            # 이미 켜져 있으면 두 개가 동시에 텔레그램에 입력하지 않도록 새로 켜지 않음
            print(f"\n  이미 실행 중입니다 → http://127.0.0.1:{p}\n")
            if not no_browser:
                webbrowser.open(f"http://127.0.0.1:{p}")
            sys.exit(0)
        if port_free(p):
            port = p
            break
    if port is None:
        print(f"\n  [오류] 사용할 수 있는 포트가 없습니다: {list(ports)}\n")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"\n  대시보드 주소: {url}\n  이 창을 닫으면 프로그램이 꺼집니다. (종료: Ctrl+C)\n")
    if not no_browser:
        threading.Thread(target=open_browser_when_ready, args=(port,), daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
