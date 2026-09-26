"""열려 있는 텔레그램 창에 텍스트(링크)를 붙여넣고 전송하는 모듈.

키보드/마우스 자동화(pyautogui)를 사용하므로 컴퓨터가 켜져 있고
화면이 잠기지 않은 상태여야 합니다.
"""

import platform
import subprocess
import time

SYSTEM = platform.system()  # "Windows", "Darwin"(맥), "Linux"


class SendError(Exception):
    pass


def _gui():
    # 화면이 없는 환경에서도 대시보드는 뜨도록 필요할 때만 불러온다.
    try:
        import pyautogui
        import pyperclip
    except Exception as e:  # pragma: no cover - 환경에 따라 다름
        raise SendError(f"자동 입력 모듈을 불러오지 못했습니다: {e}")
    pyautogui.FAILSAFE = True  # 마우스를 화면 모서리로 옮기면 긴급 정지
    pyautogui.PAUSE = 0.05
    return pyautogui, pyperclip


def focus_window(title: str) -> bool:
    """제목에 title 이 들어간 창을 맨 앞으로 가져온다. 성공 여부를 반환."""
    title = (title or "").strip()
    if not title:
        return False

    if SYSTEM == "Windows":
        try:
            import pygetwindow as gw
        except Exception:
            return False
        wins = [w for w in gw.getWindowsWithTitle(title) if w.title]
        if not wins:
            return False
        # 브라우저 탭 등보다 제목이 "Telegram" 으로 시작하는 창을 우선
        wins.sort(key=lambda w: (not w.title.lower().startswith(title.lower()), len(w.title)))
        win = wins[0]
        try:
            if win.isMinimized:
                win.restore()
            win.activate()
        except Exception:
            # 윈도우가 다른 창의 포커스 가져오기를 막는 경우 Alt 키로 우회
            try:
                import pyautogui

                pyautogui.press("alt")
                win.activate()
            except Exception:
                pass
        time.sleep(0.4)
        return True

    if SYSTEM == "Darwin":
        app = "Telegram" if "telegram" in title.lower() else title
        r = subprocess.run(
            ["osascript", "-e", f'tell application "{app}" to activate'],
            capture_output=True,
        )
        time.sleep(0.5)
        return r.returncode == 0

    # Linux (xdotool 이 설치되어 있으면 사용)
    try:
        r = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", title, "windowactivate"],
            capture_output=True,
            timeout=5,
        )
        time.sleep(0.4)
        return r.returncode == 0
    except Exception:
        return False


def send_text(text: str, settings: dict) -> None:
    """텔레그램 창을 앞으로 가져와 text 를 붙여넣고 (옵션) Enter 로 전송한다."""
    pyautogui, pyperclip = _gui()

    focused = focus_window(settings.get("window_title", ""))
    if settings.get("require_window") and not focused:
        raise SendError(
            f"'{settings.get('window_title')}' 창을 찾지 못했습니다. 텔레그램이 켜져 있는지 확인하세요."
        )

    x, y = settings.get("click_x"), settings.get("click_y")
    if x is not None and y is not None:
        pyautogui.click(int(x), int(y))
        time.sleep(0.2)

    try:
        old_clip = pyperclip.paste()
    except Exception:
        old_clip = None

    # 한/영 입력 상태와 상관없이 정확히 입력되도록 클립보드로 붙여넣기
    pyperclip.copy(text)
    time.sleep(0.1)
    pyautogui.hotkey("command" if SYSTEM == "Darwin" else "ctrl", "v")
    time.sleep(0.3)
    if settings.get("press_enter", True):
        pyautogui.press("enter")
        time.sleep(0.2)

    if old_clip is not None and settings.get("restore_clipboard", True):
        try:
            pyperclip.copy(old_clip)
        except Exception:
            pass


def mouse_position() -> tuple[int, int]:
    pyautogui, _ = _gui()
    p = pyautogui.position()
    return int(p[0]), int(p[1])


def is_failsafe(exc: Exception) -> bool:
    return type(exc).__name__ == "FailSafeException"
