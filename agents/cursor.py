"""Cursor.

Cursor가 남기는 ~/.cursor/projects/**/agent-transcripts 의 JSONL을 읽는다.
- 세션: 최근 transcript 파일의 마지막 사용자 요청과 수정 시각
- 한도: Cursor 로컬 파일에 확인 가능한 한도 값이 없어 앱 실행 상태만 표시
- 포커스: Cursor 창 활성화는 dashboard의 APP_TITLE 경로를 사용
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass

import psutil

from .base import HOME, AgentSession, Limit, Window, recent_files, tail_lines

KEY = "cursor"
LABEL = "Cursor"
SHORT = "CUR"
COLOR = "#8b9cff"
BG = "#101326"
APP_TITLE = "Cursor"

_TRANSCRIPT_GLOB = f"{HOME}/.cursor/projects/**/agent-transcripts/**/*.jsonl"
_STALE_SEC = 90


def _alive() -> bool:
    try:
        return any((p.info.get("name") or "").lower() == "cursor.exe"
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def activity_mtime() -> float:
    """GUI/CLI 구분 없이 최근 Cursor transcript의 시각."""
    paths = (f for f in glob.glob(_TRANSCRIPT_GLOB, recursive=True)
             if os.sep + "subagents" + os.sep not in f)
    return max((os.path.getmtime(f) for f in paths), default=0.0)


def _project(path: str) -> str:
    name = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(path))))
    return name.replace("-", " ")[-14:] or "Cursor"


def _title(path: str) -> str:
    try:
        for line in reversed(tail_lines(path, os.path.getsize(path), 131072)):
            d = json.loads(line)
            if d.get("role") != "user":
                continue
            for item in d.get("message", {}).get("content", []):
                text = item.get("text", "") if isinstance(item, dict) else ""
                if text and not text.startswith("<timestamp>"):
                    return text.splitlines()[0][:48]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return ""


def sessions() -> list[AgentSession]:
    now = time.time()
    out = []
    for path in recent_files(_TRANSCRIPT_GLOB):
        if os.sep + "subagents" + os.sep in path:
            continue
        try:
            mtime = os.path.getmtime(path)
            session_id = os.path.basename(os.path.dirname(path))
            running = _alive() and now - mtime <= _STALE_SEC
            out.append(AgentSession(
                KEY, session_id, _project(path), mtime,
                state="running" if running else "done",
                title=_title(path),
                focus_url="cursor://",
            ))
        except OSError:
            continue
    return out


@dataclass
class CursorUsage:
    """Cursor 계정 상태. 앱 내부 한도 원본을 기다리는 동안 조회 상태를 표시한다."""
    login_required: bool = False

    @property
    def limits(self) -> list[Limit]:
        alt = "로그인 필요" if self.login_required else "한도 조회"
        return [Limit("5H", Window(), 5, alt=alt),
                Limit("주간", Window(), 168, alt=alt)]


def _logged_in() -> bool:
    """Cursor CLI의 인증 상태만 읽고 토큰 값은 보관·출력하지 않는다."""
    command = shutil.which("cursor-agent")
    if command:
        try:
            result = subprocess.run(
                [command, "status", "--format", "json"],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000,
            )
            status = json.loads(result.stdout)
            if isinstance(status, dict) and "isAuthenticated" in status:
                return bool(status["isAuthenticated"])
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    try:
        config = json.load(open(f"{HOME}/.cursor/cli-config.json", encoding="utf-8"))
        return bool(config.get("authInfo"))
    except (OSError, ValueError, TypeError):
        return False


def usage() -> CursorUsage | None:
    if not _logged_in():
        return CursorUsage(login_required=True)
    return CursorUsage() if _alive() or activity_mtime() else None
