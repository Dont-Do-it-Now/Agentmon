"""Herdr 연결.

herdr-app이 실행 중이고 세션이 herdr 터미널 안에서 돌고 있으면 그 터미널로 화면을 옮긴다.
dashboard.py의 _focus()가 사용한다. herdr에 없는 세션이면 False → 기존 앱 딥링크.

- 터미널 목록: hc ls / hc get (foreground pid, cwd, title)
- 이동: herdr-app do agent.focus --terminal <주소>
"""

from __future__ import annotations

import json
import os
import subprocess

import psutil

from .base import AgentSession

APP_TITLE = "Herdr"
_LOCAL = os.environ.get("LOCALAPPDATA", "")
_APP = os.path.join(_LOCAL, "Programs", "herdr-app", "herdr-app.exe")
_HC = os.path.join(_LOCAL, "Programs", "herdr-core", "hc.exe")
_NO_WINDOW = 0x08000000

# agentmon KEY → herdr의 agent 이름
_AGENT_TYPE = {"claude": "claude-code", "codex": "codex"}


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


def _find_terminal(s: AgentSession) -> str | None:
    """pid → 터미널 제목(Codex는 스레드 이름이 들어감) → 작업 폴더 순으로 찾는다."""
    agent = _AGENT_TYPE.get(s.agent)
    if not agent:
        return None
    details = [_hc("get", t["address"]) for t in _hc("ls").get("terminals", [])
               if t.get("state") == "live" and t.get("agent") == agent]
    if s.pid:
        for d in details:
            if (d.get("foreground") or {}).get("pid") == s.pid:
                return d["address"]
    if s.title:
        for d in details:
            if s.title in (d.get("title") or ""):
                return d["address"]
    if s.cwd:
        for d in details:
            if _norm(d.get("cwd", "")) == _norm(s.cwd):
                return d["address"]
    return None


def activate(s: AgentSession) -> bool:
    """herdr 안의 세션이면 그 터미널로 이동하고 True."""
    if not alive() or not os.path.isfile(_HC):
        return False
    try:
        addr = _find_terminal(s)
        if not addr:
            return False
        subprocess.run([_APP, "do", "agent.focus", "--terminal", addr],
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
