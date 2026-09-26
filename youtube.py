"""유튜브 채널 주소에서 영상 목록(최신순)을 가져오는 모듈 (yt-dlp 사용)."""

import os
import re
import ssl
import urllib.request
import xml.etree.ElementTree as ET

import yt_dlp

try:
    # 맥(python.org 파이썬) 등 인증서가 없는 환경에서 HTTPS 오류가 나지 않도록
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover
    _SSL_CTX = ssl.create_default_context()

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)

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
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _clean_error(e) -> str:
    msg = str(e).replace("ERROR: ", "").strip()
    if "CERTIFICATE_VERIFY_FAILED" in msg:
        msg += " (인증서 문제: 실행 스크립트를 다시 실행해 certifi 를 설치하세요)"
    return msg[:300]


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    )
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as r:
        return r.read().decode("utf-8", "replace")


def _channel_id(base: str) -> str:
    m = re.search(r"/channel/(UC[\w-]{22})", base)
    if m:
        return m.group(1)
    html = _http_get(base)
    for pat in (
        r'"externalId":"(UC[\w-]{22})"',
        r'<meta itemprop="identifier" content="(UC[\w-]{22})"',
        r'"channelId":"(UC[\w-]{22})"',
        r'channel/(UC[\w-]{22})',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    raise RuntimeError("채널 ID를 찾지 못했습니다.")


def fetch_rss_videos(base: str) -> tuple[str, list[dict]]:
    """예비 방법: 유튜브 RSS 피드로 최신 영상(최대 15개)을 가져온다."""
    cid = _channel_id(base)
    xml = _http_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    ns = {
        "a": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    root = ET.fromstring(xml)
    name = root.findtext("a:title", "", ns)
    videos = []
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", "", ns)
        if vid:
            videos.append(
                {
                    "id": vid,
                    "title": e.findtext("a:title", "", ns),
                    "url": f"https://www.youtube.com/watch?v={vid}",
                }
            )
    return name, videos


def fetch_channel_videos(channel_url: str, tabs=("videos",)):
    """채널의 영상 목록을 최신순으로 가져온다.

    반환값: (채널 이름, [{"id", "title", "url"}, ...], 경고 메시지)
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
            errors.append(f"{tab}: {_clean_error(e)}")
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

    name = re.sub(r"\s*-\s*(Videos|Shorts|Live|동영상)$", "", name)
    if videos:
        return name or base, videos, ""

    # yt-dlp 가 실패하면 RSS 로 최신 영상만이라도 가져온다
    detail = " / ".join(errors) or "영상 목록이 비어 있습니다."
    try:
        rss_name, rss_videos = fetch_rss_videos(base)
    except Exception as e:
        raise RuntimeError(f"영상 목록을 가져오지 못했습니다. {detail} / RSS: {_clean_error(e)}")
    if not rss_videos:
        raise RuntimeError(f"영상 목록을 가져오지 못했습니다. {detail}")
    return (
        name or rss_name or base,
        rss_videos,
        f"전체 목록을 못 불러와 최신 {len(rss_videos)}개만 사용 중 ({detail})",
    )
