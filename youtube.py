"""유튜브 채널 주소에서 영상 목록(최신순)을 가져오는 모듈 (yt-dlp 사용)."""

import re

import yt_dlp

TABS = ("videos", "shorts", "streams")


def normalize_channel_url(raw: str) -> str:
    """사용자가 입력한 채널 주소를 채널 기본 주소 형태로 정리한다.

    지원 예시:
      https://www.youtube.com/@채널핸들
      @채널핸들
      https://www.youtube.com/channel/UCxxxx
      https://www.youtube.com/c/이름, https://www.youtube.com/user/이름
      https://www.youtube.com/@채널핸들/videos  (탭 주소도 OK)
    """
    url = raw.strip()
    if not url:
        raise ValueError("채널 주소가 비어 있습니다.")
    if url.startswith("@"):
        url = "https://www.youtube.com/" + url
    if not re.match(r"^https?://", url):
        url = "https://" + url
    url = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    url = re.sub(r"^https?://(m\.|www\.)?youtube\.com", "https://www.youtube.com", url)

    m = re.match(
        r"^(https://www\.youtube\.com/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+))(?:/.*)?$",
        url,
    )
    if not m:
        raise ValueError(
            "유튜브 채널 주소가 아닙니다. 예: https://www.youtube.com/@채널이름"
        )
    return m.group(1)


def _extract(url: str) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def fetch_channel_videos(channel_url: str, tabs=("videos",)) -> tuple[str, list[dict]]:
    """채널의 영상 목록을 최신순으로 가져온다.

    반환값: (채널 이름, [{"id", "title", "url"}, ...])
    """
    base = normalize_channel_url(channel_url)
    name = ""
    videos: list[dict] = []
    seen: set[str] = set()
    errors = []

    for tab in tabs:
        try:
            info = _extract(f"{base}/{tab}")
        except Exception as e:  # 탭이 없는 채널(예: 쇼츠 없음)은 건너뜀
            errors.append(f"{tab}: {e}")
            continue
        name = name or info.get("channel") or info.get("uploader") or info.get("title") or ""
        for entry in info.get("entries") or []:
            if not entry:
                continue
            vid = entry.get("id")
            if not vid or vid in seen or len(vid) != 11:
                continue
            seen.add(vid)
            url = (
                f"https://www.youtube.com/shorts/{vid}"
                if tab == "shorts"
                else f"https://www.youtube.com/watch?v={vid}"
            )
            videos.append({"id": vid, "title": entry.get("title") or "", "url": url})

    if not videos and errors:
        raise RuntimeError("영상 목록을 가져오지 못했습니다. " + " / ".join(errors))
    name = re.sub(r"\s*-\s*(Videos|Shorts|Live|동영상)$", "", name)
    return name or base, videos
