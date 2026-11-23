"""Antigravity.

Antigravity 가 ~/.gemini/antigravity/daemon/hub_<port>.json 에 남기는 포트·CSRF 토큰으로
127.0.0.1 의 language server 를 호출한다. 외부 전송 없음.

- 세션: GetAllCascadeTrajectories 의 status / lastUserViewTime
- 한도: GetUserStatus 의 모델별 quotaInfo
- 포커스: antigravity:// 딥링크로 창을 앞으로, 로컬 디버그 포트(CDP)로 대화 이동
"""

from __future__ import annotations

import glob
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import unquote

import psutil

from .base import HOME, AgentSession, Limit, Window, iso, project_name, window

KEY = "agy"
LABEL = "Antigravity"
SHORT = "AGY"
COLOR = "#4285f4"
BG = "#0a0f1e"
APP_TITLE = "Antigravity"

_HUB_GLOB = f"{HOME}/.gemini/antigravity/daemon/hub_*.json"
_ACTIVITY_GLOBS = (
    f"{HOME}/.gemini/antigravity/conversations/*.db",
    f"{HOME}/.gemini/antigravity/brain/*/.system_generated/tasks/*.log",
)
_SERVICE = "exa.language_server_pb.LanguageServerService"
_DEVTOOLS_PORT = f"{HOME}/AppData/Roaming/Antigravity/DevToolsActivePort"


# ---- language server ----

def _hub() -> dict | None:
    for f in sorted(glob.glob(_HUB_GLOB), key=os.path.getmtime, reverse=True):
        try:
            h = json.load(open(f, encoding="utf-8"))
            if psutil.pid_exists(int(h["pid"])):
                return h
        except (OSError, ValueError, KeyError):
            continue
    return None


def ls_call(method: str, body: dict | None = None, timeout: float = 4) -> dict | None:
    """language server RPC 호출. Antigravity 가 꺼져 있거나 실패하면 None."""
    h = _hub()
    if not h:
        return None
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{h['httpPort']}/{_SERVICE}/{method}",
            data=json.dumps(body or {}).encode(),
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
                "X-Codeium-Csrf-Token": h["csrfToken"],
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def activity_mtime() -> float:
    """GUI/CLI 구분 없이 최근 Antigravity 대화·작업 로그의 시각."""
    paths = (f for pattern in _ACTIVITY_GLOBS for f in glob.glob(pattern))
    return max((os.path.getmtime(f) for f in paths), default=0.0)


# ---- 세션 ----

_meta_cache: dict[str, tuple[int, tuple[str, float | None]]] = {}
META_LOOKBACK_SEC = 86400  # 이보다 오래된 대화는 모델·컨텍스트를 조회하지 않는다


def _find(o, key: str):
    """중첩 dict/list 에서 key 의 첫 값."""
    if isinstance(o, dict):
        if key in o:
            return o[key]
        o = o.values()
    if isinstance(o, (list, type({}.values()))):
        for v in o:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def _meta(cid: str, steps: int) -> tuple[str, float | None]:
    """(모델 이름, 컨텍스트 사용률) — 마지막 생성 메타데이터. 응답이 커서 step 수가 바뀔 때만 조회."""
    hit = _meta_cache.get(cid)
    if hit and hit[0] == steps:
        return hit[1]
    d = ls_call("GetCascadeTrajectoryGeneratorMetadata", {"cascadeId": cid}, timeout=10)
    gm = (d or {}).get("generatorMetadata") or []
    result: tuple[str, float | None] = hit[1] if hit else ("", None)
    if gm:
        last = gm[-1]
        cw = _find(last, "contextWindowMetadata") or {}
        used, limit = cw.get("estimatedTokensUsed"), cw.get("maxContextTokens")
        name = _find(last, "modelDisplayName") or ""
        result = (name.split(" (")[0], min(1.0, int(used) / int(limit)) if used and limit else None)
    _meta_cache[cid] = (steps, result)
    return result


def sessions() -> list[AgentSession]:
    d = ls_call("GetAllCascadeTrajectories")
    if not d:
        return []
    out = []
    for cid, v in (d.get("trajectorySummaries") or {}).items():
        try:
            mod = iso(v.get("lastModifiedTime"))
            running = v.get("status") == "CASCADE_RUN_STATUS_RUNNING"
            viewed = iso((v.get("annotations") or {}).get("lastUserViewTime"))
            ws = v.get("workspaces") or []
            uri = ws[0].get("workspaceFolderAbsoluteUri", "") if ws else ""
            title = v.get("summary") or ""
            pid = (v.get("trajectoryMetadata") or {}).get("projectId") or "outside-of-project"
            model, ctx = (_meta(cid, v.get("stepCount") or 0)
                          if time.time() - mod < META_LOOKBACK_SEC else ("", None))
            out.append(AgentSession(
                KEY, cid, project_name(unquote(uri)) if uri else (title[:14] or "AGY"), mod,
                state="running" if running else "done",
                title=title,
                model=model, context=ctx,
                branch=(ws[0].get("branchName") or "") if ws else "",
                # 창 제목이 대화 제목이라 제목으로는 못 찾는다: 딥링크로 창을 앞으로 가져온다
                focus_url="antigravity://focus",
                nav=f"/c/{cid}?project={pid}",
                seen=viewed >= mod,  # Antigravity 에서 이미 열어봄
            ))
        except (AttributeError, TypeError):
            pass
    return out


# ---- 포커스 ----

def _ws_send(ws_url: str, payload: str) -> None:
    """웹소켓으로 텍스트 프레임 하나를 보내고 응답 하나를 기다린다 (의존성 없는 최소 구현)."""
    import base64
    import socket
    import struct
    from urllib.parse import urlparse

    u = urlparse(ws_url)
    with socket.create_connection((u.hostname, u.port), timeout=3) as s:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.netloc}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        if b" 101 " not in s.recv(4096):
            raise OSError("websocket handshake failed")
        data, mask = payload.encode(), os.urandom(4)
        n = len(data)
        head = bytes([0x81, 0x80 | n]) if n < 126 else bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        s.recv(4096)  # 명령이 처리될 때까지 대기


def navigate(path: str) -> bool:
    """Antigravity 창을 앱 내부 경로(/c/<대화 id>)로 이동시킨다.

    Antigravity 가 항상 열어두는 로컬 디버그 포트(CDP)로 라우터 이동을 실행한다.
    """
    try:
        port = open(_DEVTOOLS_PORT).read().split()[0]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
            targets = json.loads(r.read())
        page = next(t for t in targets
                    if t.get("type") == "page" and t.get("url", "").startswith("https://127.0.0.1:"))
        js = (f"history.pushState({{}}, '', {json.dumps(path)});"
              "window.dispatchEvent(new PopStateEvent('popstate', {state: history.state}))")
        _ws_send(page["webSocketDebuggerUrl"],
                 json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": js}}))
        return True
    except Exception:
        return False


def focus(s: AgentSession) -> None:
    if s.nav:
        navigate(s.nav)


# ---- 한도 ----

@dataclass
class AgyUsage:
    """창별 한도. 모델군(Gemini / Claude·GPT) 중 가장 빠듯한 값."""
    five_hour: Window = field(default_factory=Window)
    weekly: Window = field(default_factory=Window)
    login_required: bool = False

    @property
    def limits(self) -> list[Limit]:
        return [Limit("5H", self.five_hour, 5),
                Limit("주간", self.weekly, 168)]


def usage() -> AgyUsage | None:
    """RetrieveUserQuotaSummary 의 모델군별 5h / weekly 버킷. Antigravity 가 꺼져 있으면 None.
    (GetUserStatus 의 quotaInfo 는 5시간 한도만 준다)"""
    try:
        creds = json.load(open(f"{HOME}/.gemini/oauth_creds.json", encoding="utf-8"))
        if not (creds.get("access_token") or creds.get("refresh_token")):
            return AgyUsage(login_required=True)
    except (OSError, ValueError, TypeError):
        return AgyUsage(login_required=True)
    d = ls_call("RetrieveUserQuotaSummary", {"metadata": {"ideName": "antigravity",
                                                          "extensionName": "antigravity"}})
    if not d:
        return None
    g = AgyUsage()
    for grp in (d.get("response") or d).get("groups") or []:
        for b in grp.get("buckets") or []:
            slot = {"5h": "five_hour", "weekly": "weekly"}.get(b.get("window"))
            if not slot or b.get("remainingFraction") is None:
                continue
            w = window({"p": (1 - float(b["remainingFraction"])) * 100, "r": b.get("resetTime")}, "p", "r")
            cur = getattr(g, slot)
            if cur.used_percent is None or w.used_percent > cur.used_percent:
                setattr(g, slot, w)
    return g
