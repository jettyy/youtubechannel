"""채널 목록/진행 상태를 관리하고, 설정한 간격마다 다음 영상 링크를 보내는 엔진."""

import json
import os
import random
import threading
import time
import uuid

import sender
import youtube

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
LOG_LIMIT = 300

DEFAULT_SETTINGS = {
    "interval_sec": 60,          # 전송 간격(초)
    "jitter_sec": 0,             # 간격에 더할 랜덤 추가 시간(0~N초)
    "mode": "round_robin",       # round_robin: 채널 번갈아 / sequential: 한 채널 끝까지 후 다음 채널
    "window_title": "Telegram",  # 앞으로 가져올 창 제목(일부만 맞아도 됨)
    "require_window": False,     # 창을 못 찾으면 전송하지 않음
    "click_x": None,             # 메시지 입력칸 좌표(선택)
    "click_y": None,
    "press_enter": True,         # 붙여넣은 뒤 Enter 로 전송
    "restore_clipboard": True,   # 전송 후 원래 클립보드 내용 복구
    "include_shorts": False,     # 쇼츠도 포함
    "include_streams": False,    # 라이브(지난 방송)도 포함
    "recent_months": 0,          # 최근 N개월 안에 올라온 영상만 (0 = 전체)
    "start_delay_sec": 5,        # 시작 버튼 누른 뒤 첫 전송까지 대기
    "message_format": "{url}",   # 보낼 문구. {url} {title} {channel} 사용 가능
    "dry_run": False,            # 테스트 모드: 실제로 입력하지 않고 기록만 남김
}


class Engine:
    def __init__(self, data_file: str = DATA_FILE, fetcher=None, send_func=None):
        self.data_file = data_file
        self.fetcher = fetcher or youtube.fetch_channel_videos
        self.send_func = send_func or sender.send_text
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.thread = None
        self.running = False
        self._gen = 0  # 시작할 때마다 증가. 이전 실행 스레드가 겹쳐 돌지 않게 함
        self.next_send_at = None
        self.status_msg = "대기 중"
        self.settings = dict(DEFAULT_SETTINGS)
        self.channels: list[dict] = []
        self.log: list[dict] = []
        self.rr_pointer = 0   # 다음 차례 채널 (번갈아 모드)
        self.seq_pointer = 0  # 현재 채널 (순서대로 모드)
        self.total_sent = 0
        self._load()

    # ------------------------------------------------------------------ 저장/불러오기
    def _load(self):
        if not os.path.exists(self.data_file):
            return
        try:
            with open(self.data_file, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        self.settings.update(data.get("settings", {}))
        self.channels = data.get("channels", [])
        for ch in self.channels:
            ch.setdefault("all_videos", list(ch.get("videos", [])))
            if ch.get("status") == "loading":
                ch["status"] = "ready" if ch.get("videos") else "error"
        for ch in self.channels:
            ch.setdefault("note", "")
            self._apply_filter(ch)
        self.log = data.get("log", [])[-LOG_LIMIT:]
        self.rr_pointer = data.get("rr_pointer", 0)
        self.seq_pointer = data.get("seq_pointer", 0)
        self.total_sent = data.get("total_sent", 0)

    def save(self):
        with self.lock:
            data = {
                "settings": self.settings,
                "channels": self.channels,
                "log": self.log[-LOG_LIMIT:],
                "rr_pointer": self.rr_pointer,
                "seq_pointer": self.seq_pointer,
                "total_sent": self.total_sent,
            }
            tmp = self.data_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.data_file)

    def add_log(self, kind: str, text: str, channel: str = "", url: str = ""):
        with self.lock:
            self.log.append(
                {"t": time.time(), "kind": kind, "text": text, "channel": channel, "url": url}
            )
            del self.log[:-LOG_LIMIT]

    # ------------------------------------------------------------------ 채널 관리
    def _find(self, cid):
        for ch in self.channels:
            if ch["id"] == cid:
                return ch
        raise KeyError("채널을 찾을 수 없습니다.")

    def add_channels(self, text: str) -> list[str]:
        """여러 줄/공백/쉼표로 구분된 채널 주소를 추가한다. 오류 메시지 목록 반환."""
        errors = []
        added = []
        for raw in text.replace(",", "\n").split():
            try:
                url = youtube.normalize_channel_url(raw)
            except ValueError as e:
                errors.append(f"{raw}: {e}")
                continue
            with self.lock:
                if any(c["url"].lower() == url.lower() for c in self.channels):
                    errors.append(f"{raw}: 이미 등록된 채널입니다.")
                    continue
                ch = {
                    "id": uuid.uuid4().hex[:10],
                    "url": url,
                    "name": url.rsplit("/", 1)[-1],
                    "enabled": True,
                    "videos": [],
                    "all_videos": [],
                    "note": "",
                    "index": 0,
                    "loops": 0,
                    "sent": 0,
                    "status": "loading",
                    "error": "",
                    "fetched_at": None,
                }
                self.channels.append(ch)
                added.append(ch["id"])
        self.save()
        for cid in added:
            self.refresh_channel_async(cid)
        return errors

    def _tabs(self):
        tabs = ["videos"]
        if self.settings.get("include_shorts"):
            tabs.append("shorts")
        if self.settings.get("include_streams"):
            tabs.append("streams")
        return tuple(tabs)

    def refresh_channel(self, cid, reset_index=False) -> bool:
        with self.lock:
            ch = self._find(cid)
            ch["status"] = "loading"
            url = ch["url"]
        try:
            result = self.fetcher(url, self._tabs())
            name, videos = result[0], result[1]
            warning = result[2] if len(result) > 2 else ""
            if not videos:
                raise RuntimeError("영상 목록이 비어 있습니다.")
        except Exception as e:
            with self.lock:
                ch["status"] = "error" if not ch["videos"] else "ready"
                ch["error"] = str(e)[:300]
            self.add_log("error", f"영상 목록 가져오기 실패: {e}", ch.get("name", ""))
            self.save()
            return False
        with self.lock:
            ch["name"] = name or ch["name"]
            ch["all_videos"] = videos
            ch["fetched_at"] = time.time()
            ch["error"] = warning
            ch["status"] = "ready"
            if reset_index:
                ch["index"] = 0
            # 처음(0번)부터 시작할 차례면 새로 올라온 영상부터 보내도록 위치를 유지하지 않음
            self._apply_filter(ch, keep_position=not reset_index and ch["index"] > 0)
        self.add_log("info", f"영상 {len(videos)}개 불러옴" + (f" → {ch['note']}" if ch["note"] else ""), ch["name"])
        if warning:
            self.add_log("error", warning, ch["name"])
        self.save()
        return True

    def _apply_filter(self, ch, keep_position=True):
        """설정한 기간(최근 N개월) 안의 영상만 골라 ch["videos"] 에 넣는다."""
        all_videos = ch.get("all_videos") or []
        months = self.settings.get("recent_months") or 0
        current = None
        if keep_position and ch["videos"] and ch["index"] < len(ch["videos"]):
            current = ch["videos"][ch["index"]]["id"]

        if not months:
            videos, note = list(all_videos), ""
        elif not any(v.get("ts") for v in all_videos):
            videos = list(all_videos)
            note = "업로드 날짜를 알 수 없어 기간 제한 없이 전체 영상 사용 (새로고침 해 보세요)"
        else:
            cutoff = time.time() - months * 30.44 * 86400
            videos, last_ts = [], None
            for v in all_videos:  # 최신순 목록이라 날짜가 없는 영상은 바로 앞 영상 날짜로 판단
                ts = v.get("ts") or last_ts
                last_ts = ts
                if ts is None or ts >= cutoff:
                    videos.append(v)
            note = (
                f"최근 {months}개월: {len(videos)}개 / 전체 {len(all_videos)}개"
                if videos
                else f"최근 {months}개월 안에 올라온 영상이 없어 건너뜀"
            )

        ch["videos"] = videos
        ch["note"] = note
        ids = [v["id"] for v in videos]
        ch["index"] = ids.index(current) if current in ids else (
            ch["index"] if ch["index"] < len(videos) and not current else 0
        )

    def refresh_channel_async(self, cid, reset_index=False):
        threading.Thread(
            target=self.refresh_channel, args=(cid, reset_index), daemon=True
        ).start()

    def update_channel(self, cid, **fields):
        with self.lock:
            ch = self._find(cid)
            if "enabled" in fields:
                ch["enabled"] = bool(fields["enabled"])
            if "index" in fields:
                n = len(ch["videos"])
                ch["index"] = max(0, min(int(fields["index"]), max(n - 1, 0)))
        self.save()

    def remove_channel(self, cid):
        with self.lock:
            self.channels = [c for c in self.channels if c["id"] != cid]
        self.save()

    def move_channel(self, cid, delta: int):
        with self.lock:
            i = next(i for i, c in enumerate(self.channels) if c["id"] == cid)
            j = max(0, min(len(self.channels) - 1, i + delta))
            self.channels.insert(j, self.channels.pop(i))
        self.save()

    # ------------------------------------------------------------------ 설정
    def update_settings(self, new: dict):
        with self.lock:
            before = dict(self.settings)
            for k, v in new.items():
                if k not in DEFAULT_SETTINGS:
                    continue
                default = DEFAULT_SETTINGS[k]
                if k in ("click_x", "click_y"):
                    v = None if v in (None, "") else int(float(v))
                elif isinstance(default, bool):
                    v = bool(v)
                elif isinstance(default, int):
                    v = max(0, int(float(v)))
                else:
                    v = str(v)
                self.settings[k] = v
            if self.settings["interval_sec"] < 1:
                self.settings["interval_sec"] = 1
            if self.settings["mode"] not in ("round_robin", "sequential"):
                self.settings["mode"] = "round_robin"
            if self.settings["recent_months"] != before.get("recent_months"):
                for ch in self.channels:
                    self._apply_filter(ch)
                m = self.settings["recent_months"]
                self.add_log("info", f"영상 기간: {'최근 ' + str(m) + '개월' if m else '전체'}")
            refetch = any(
                self.settings[k] != before.get(k) for k in ("include_shorts", "include_streams")
            )
            if refetch:  # 쇼츠/라이브 포함 여부가 바뀌면 목록을 다시 불러옴
                targets = list(self.channels)
            elif self.settings["recent_months"]:
                # 예전에 불러와서 업로드 날짜가 없는 채널은 날짜를 받으러 다시 불러옴
                targets = [
                    c for c in self.channels
                    if c.get("all_videos") and not any(v.get("ts") for v in c["all_videos"])
                ]
            else:
                targets = []
        self.save()
        for ch in targets:
            self.refresh_channel_async(ch["id"])
        self.wake.set()  # 대기 중이면 새 간격으로 다시 계산

    # ------------------------------------------------------------------ 다음 영상 고르기
    def _active_channels(self):
        return [c for c in self.channels if c.get("enabled") and c.get("videos")]

    def peek_next(self):
        """다음에 보낼 (채널, 영상)을 고른다. 진행 위치는 바꾸지 않는다."""
        with self.lock:
            active = self._active_channels()
            if not active:
                return None, None
            if self.settings["mode"] == "sequential":
                ch = active[self.seq_pointer % len(active)]
            else:
                ch = active[self.rr_pointer % len(active)]
            if ch["index"] >= len(ch["videos"]):
                ch["index"] = 0
            return ch, ch["videos"][ch["index"]]

    def advance(self, ch):
        """전송에 성공한 뒤 진행 위치를 한 칸 옮긴다. 채널 한 바퀴를 다 돌면 True."""
        with self.lock:
            active = self._active_channels()
            n = max(len(active), 1)
            if self.settings["mode"] != "sequential":
                self.rr_pointer = (self.rr_pointer + 1) % n
            ch["index"] += 1
            if ch["index"] < len(ch["videos"]):
                return False
            # 채널의 마지막 영상까지 보냄 → 처음(최신)부터 다시
            ch["index"] = 0
            ch["loops"] = ch.get("loops", 0) + 1
            if self.settings["mode"] == "sequential":
                self.seq_pointer = (self.seq_pointer + 1) % n
            return True

    def format_message(self, ch, video):
        fmt = (self.settings.get("message_format") or "{url}").replace("\\n", "\n")
        try:
            return fmt.format(url=video["url"], title=video["title"], channel=ch["name"])
        except Exception:
            return video["url"]

    def send_one(self) -> bool:
        ch, video = self.peek_next()
        if not ch:
            self.status_msg = "보낼 영상이 없습니다. 채널을 추가하거나 켜 주세요."
            return False
        text = self.format_message(ch, video)
        pos = f"{ch['index'] + 1}/{len(ch['videos'])}"
        try:
            if not self.settings.get("dry_run"):
                self.send_func(text, self.settings)
        except Exception as e:
            if sender.is_failsafe(e):
                self.add_log("error", "마우스가 화면 모서리로 이동해 긴급 정지했습니다.", ch["name"])
                self.stop()
            else:
                # 실패한 영상은 건너뛰지 않고 다음 차례에 다시 시도
                self.add_log("error", f"전송 실패: {e}", ch["name"], video["url"])
            self.save()
            return False
        with self.lock:
            ch["sent"] = ch.get("sent", 0) + 1
            self.total_sent += 1
        prefix = "[테스트모드] " if self.settings.get("dry_run") else ""
        self.add_log("sent", f"{prefix}({pos}) {video['title']}", ch["name"], video["url"])
        if self.advance(ch):
            self.add_log("info", f"{ch['loops']}바퀴 완료 → 최신 영상부터 다시 시작", ch["name"])
            self.refresh_channel_async(ch["id"])  # 그동안 올라온 새 영상 반영
        self.save()
        return True

    def send_test(self, text="텔레그램 자동 전송 테스트"):
        self.send_func(text, self.settings)
        self.add_log("info", "테스트 메시지 전송")

    # ------------------------------------------------------------------ 실행/정지
    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self._gen += 1
            self.wake.clear()
            self.thread = threading.Thread(target=self._run, args=(self._gen,), daemon=True)
            self.thread.start()
        self.add_log("info", "자동 전송 시작")

    def stop(self):
        with self.lock:
            if not self.running:
                return
            self.running = False
            self.next_send_at = None
            self.wake.set()
        self.add_log("info", "자동 전송 정지")
        self.save()

    def skip_wait(self):
        """대기 시간을 건너뛰고 바로 다음 영상을 보낸다."""
        self.next_send_at = time.time()
        self.wake.set()

    def _alive(self, gen):
        return self.running and self._gen == gen

    def _wait_until(self, gen, target):
        self.next_send_at = target
        while self._alive(gen):
            remaining = self.next_send_at - time.time()
            if remaining <= 0:
                return True
            self.wake.wait(min(remaining, 1.0))
            self.wake.clear()
        return False

    def _run(self, gen):
        self.status_msg = "시작 대기 중 (텔레그램 창을 확인하세요)"
        if not self._wait_until(gen, time.time() + self.settings.get("start_delay_sec", 5)):
            return
        while self._alive(gen):
            self.status_msg = "전송 중..."
            self.send_one()
            if not self._alive(gen):
                break
            delay = self.settings["interval_sec"] + random.uniform(
                0, self.settings.get("jitter_sec", 0) or 0
            )
            self.status_msg = "다음 전송까지 대기 중"
            if not self._wait_until(gen, time.time() + delay):
                break

    # ------------------------------------------------------------------ 화면용 상태
    def snapshot(self):
        with self.lock:
            ch, v = self.peek_next()
            nxt = {"channel": ch["name"], "title": v["title"], "url": v["url"]} if ch else None
            return {
                "running": self.running,
                "status": self.status_msg if self.running else "정지됨",
                "next_send_at": self.next_send_at,
                "now": time.time(),
                "next": nxt,
                "total_sent": self.total_sent,
                "settings": self.settings,
                "channels": [
                    {
                        k: c.get(k)
                        for k in ("id", "url", "name", "enabled", "index", "loops",
                                  "sent", "status", "error", "fetched_at", "note")
                    }
                    | {
                        "count": len(c["videos"]),
                        "total": len(c.get("all_videos") or []),
                        "current": (
                            c["videos"][c["index"]]
                            if c["videos"] and c["index"] < len(c["videos"])
                            else None
                        ),
                    }
                    for c in self.channels
                ],
                "log": list(reversed(self.log[-100:])),
            }
