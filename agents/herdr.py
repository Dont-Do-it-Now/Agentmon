"""Herdr 연결.

herdr-app이 실행 중이고 세션이 herdr 터미널 안에서 돌고 있으면
- 누를 때 그 터미널로 화면을 옮기고 (dashboard.py의 _focus → activate)
- 읽음 여부를 herdr 터미널 상태로 판정한다 (list_sessions → statuses).
herdr에 없는 세션이면 아무것도 하지 않는다 → 기존 앱 딥링크 / 앱 읽음 표시.

- 터미널 목록: hc ls (status, turn), hc get (foreground pid, title, labels)
- 읽음: status 가 done 이고 labels["herdr.seen"] < turn 이면 안 봄 (herdr 화면의 done),
  herdr.seen >= turn 이면 봄 (herdr 화면의 idle)
- 이동: herdr-app do agent.focus --terminal <주소>
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import psutil

from .base import AgentSession

APP_TITLE = "Herdr"
_LOCAL = os.environ.get("LOCALAPPDATA", "")
_APP = os.path.join(_LOCAL, "Programs", "herdr-app", "herdr-app.exe")
_HC = os.path.join(_LOCAL, "Programs", "herdr-core", "hc.exe")
_NO_WINDOW = 0x08000000

# agentmon KEY → herdr의 agent 이름
_AGENT_TYPE = {"claude": "claude-code", "codex": "codex"}

_LS_TTL = 3    # 터미널 목록·상태 캐시 (세션 갱신 주기 2초)
_GET_TTL = 10  # 터미널 상세(pid·제목) 캐시
_ls_cache: tuple[float, list[dict]] = (0.0, [])
_get_cache: dict[str, tuple[float, dict]] = {}


def alive() -> bool:
    try:
        return any((p.info.get("name") or "").lower() == "herdr-app.exe"
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def _hc(*args: str) -> dict:
    return json.loads(subprocess.check_output(
        [_HC, *args, "--json"], timeout=5, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW))


def _norm(path: str) -> str:
    path = path.removeprefix("\\\\?\\")
    return os.path.normcase(os.path.normpath(path)) if path else ""


def _terminals(fresh: bool = False) -> list[dict]:
    """live 에이전트 터미널: address, agent, cwd, status, pid, title."""
    global _ls_cache
    now = time.time()
    if not fresh and now - _ls_cache[0] < _LS_TTL:
        return _ls_cache[1]
    out = []
    for t in _hc("ls").get("terminals", []):
        if t.get("state") != "live" or t.get("agent") not in _AGENT_TYPE.values():
            continue
        hit = _get_cache.get(t["address"])
        # 완료 터미널도 캐시한다. 매번 hc get을 호출하면 세션 갱신 루프가 막힌다.
        # 포커스 이동(fresh=True) 시에는 즉시 다시 읽는다.
        if fresh or not hit or now - hit[0] > _GET_TTL:
            hit = _get_cache[t["address"]] = (now, _hc("get", t["address"]))
        d = hit[1]
        seen = int((d.get("labels") or {}).get("herdr.seen", -1))
        if t.get("status") == "done":
            status = "unread" if seen < (t.get("turn") or 0) else "read"
        else:
            status = t.get("status") or ""
        out.append({**t, "status": status, "pid": (d.get("foreground") or {}).get("pid"),
                    "title": d.get("title") or ""})
    _ls_cache = (now, out)
    return out


def _match(s: AgentSession, terms: list[dict], strict: bool) -> dict | None:
    """pid → 터미널 제목(Codex는 스레드 이름이 들어감) → 작업 폴더 순.
    strict 면 같은 폴더 터미널이 여럿일 때 폴더로는 고르지 않는다."""
    agent = _AGENT_TYPE.get(s.agent)
    cands = [t for t in terms if t["agent"] == agent]
    if s.pid:
        for t in cands:
            if t["pid"] == s.pid:
                return t
    if s.title:
        for t in cands:
            if s.title in t["title"]:
                return t
    if s.cwd:
        same = [t for t in cands if _norm(t.get("cwd", "")) == _norm(s.cwd)]
        if same and (len(same) == 1 or not strict):
            return same[0]
    return None


def statuses(sessions: list[AgentSession]) -> dict[str, str]:
    """세션 key → herdr 터미널 status. herdr 밖 세션은 빠진다.
    한 터미널에는 가장 최근 세션 하나만 붙인다 (같은 폴더의 예전 세션이 되살아나지 않게)."""
    if not alive() or not os.path.isfile(_HC):
        return {}
    try:
        terms = _terminals()
    except Exception as e:
        print(f"herdr status error: {e}", flush=True)
        return {}
    out: dict[str, str] = {}
    used: set[str] = set()
    for s in sorted(sessions, key=lambda s: -s.mtime):
        t = _match(s, terms, strict=True)
        if t and t["address"] not in used:
            used.add(t["address"])
            out[s.key] = t.get("status") or ""
    return out


def activate(s: AgentSession) -> bool:
    """herdr 안의 세션이면 그 터미널로 이동하고 True."""
    if not alive() or not os.path.isfile(_HC):
        return False
    try:
        t = _match(s, _terminals(fresh=True), strict=False)
        if not t:
            return False
        subprocess.run([_APP, "do", "agent.focus", "--terminal", t["address"]],
                       timeout=5, capture_output=True, creationflags=_NO_WINDOW)
    except Exception as e:
        print(f"herdr focus error: {e}", flush=True)
        return False
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-Command",
         "Add-Type -AssemblyName Microsoft.VisualBasic; "
         f"[Microsoft.VisualBasic.Interaction]::AppActivate('{APP_TITLE}')"],
        creationflags=_NO_WINDOW,
    )
    return True
