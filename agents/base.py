"""에이전트 모듈 공통 타입·도우미.

에이전트 모듈(agents/<name>.py)이 갖춰야 하는 것:
    KEY, LABEL, SHORT, COLOR, BG, APP_TITLE   표시용 상수
    sessions() -> list[AgentSession]          현재 세션과 상태
    usage() -> 한도 객체 | None               `.limits -> list[Limit]` 를 가진 객체 (꺼져 있으면 None)
    focus(session) (선택)                     딥링크만으로 부족할 때 세션을 여는 추가 동작
새 도구는 파일을 만들고 agents/__init__.py 의 MODULES 에 추가하면 된다.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
LOOKBACK_DAYS = 8


@dataclass
class Window:
    used_percent: float | None = None
    resets_at: datetime | None = None


@dataclass
class Limit:
    """한도 창 하나 (패널의 바 하나)."""
    label: str            # 넓은 패널용 라벨
    window: Window
    hours: float          # 창 길이 (페이스 계산용)
    short: str = ""       # 타일용 짧은 라벨 (없으면 label)
    alt: str = ""         # 사용률을 모를 때 대신 보여줄 값


@dataclass
class AgentSession:
    agent: str
    session_id: str
    project: str
    mtime: float              # 마지막 상태 변경 시각 (완료면 완료 시각)
    state: str = "done"       # "running" | "done"
    title: str = ""
    focus_url: str | None = None  # 누르면 여는 딥링크
    seen: bool = False        # 완료 후 사용자가 확인했는지
    nav: str = ""             # 에이전트 모듈의 focus() 가 쓰는 앱 내부 경로
    model: str = ""           # 표시용 모델 이름
    branch: str = ""          # git 브랜치
    context: float | None = None  # 컨텍스트 사용률 0~1 (모르면 None)

    @property
    def key(self) -> str:
        return f"{self.agent}:{self.session_id}"

    @property
    def is_running(self) -> bool:
        return self.state == "running"

    @property
    def is_waiting(self) -> bool:
        """완료됐지만 아직 확인하지 않음."""
        return self.state == "done" and not self.seen

    @property
    def age_text(self) -> str:
        sec = int(time.time() - self.mtime)
        if sec < 60:
            return "방금"
        if sec < 3600:
            return f"{sec // 60}분 전"
        if sec < 86400:
            return f"{sec // 3600}시간 전"
        return f"{sec // 86400}일 전"


def tail_lines(path: str, size: int, nbytes: int = 262144) -> list[str]:
    """파일 끝 nbytes 를 줄 단위로."""
    with open(path, "rb") as f:
        f.seek(max(0, size - nbytes))
        return f.read().decode("utf-8", errors="ignore").splitlines()


def project_name(path: str) -> str:
    """경로 마지막 폴더명. '(Compose + Kotlin) VPP' → 'VPP'."""
    last = path.replace("\\", "/").rstrip("/").split("/")[-1]
    name = re.sub(r"\([^)]*\)", "", last).strip(" []{}+")
    return (name or last)[:14]


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(s: str | None) -> float:
    """ISO 시각 → epoch. 실패하면 0."""
    if not s:
        return 0.0
    try:
        # 나노초 자릿수는 마이크로초로 자른다
        return parse_ts(re.sub(r"(\.\d{6})\d+", r"\1", s)).timestamp()
    except ValueError:
        return 0.0


def window(raw: dict | None, pct_key: str, reset_key: str) -> Window:
    if not isinstance(raw, dict):
        return Window()
    pct = raw.get(pct_key)
    reset = raw.get(reset_key)
    reset_dt = None
    if isinstance(reset, (int, float)):
        reset_dt = datetime.fromtimestamp(reset, timezone.utc)
    elif isinstance(reset, str):
        try:
            reset_dt = parse_ts(reset)
        except ValueError:
            pass
    return Window(float(pct) if pct is not None else None, reset_dt)


def http_json(url: str, headers: dict, body: dict | None = None, timeout: float = 10) -> dict:
    import ssl
    import urllib.request

    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "agentmon", **headers})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read())


_cache: dict[str, tuple[float, int, list]] = {}


def recent_files(pattern: str) -> list[str]:
    cutoff = time.time() - LOOKBACK_DAYS * 86400
    return [f for f in glob.glob(pattern, recursive=True) if os.path.getmtime(f) >= cutoff]


def parse_cached(path: str, parser) -> list:
    st = os.stat(path)
    hit = _cache.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    recs = parser(path)
    _cache[path] = (st.st_mtime, st.st_size, recs)
    return recs


def until(dt: datetime | None, now: datetime | None = None) -> str:
    if not dt:
        return "-"
    sec = (dt - (now or datetime.now(timezone.utc))).total_seconds()
    if sec <= 0:
        return "리셋됨"
    d, rem = divmod(int(sec), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d{h}h" if d else (f"{h}h{m:02d}m" if h else f"{m}m")
