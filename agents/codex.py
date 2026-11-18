"""Codex (데스크톱 / CLI).

- 세션: ~/.codex/state_5.sqlite(threads) + thread_history_1.sqlite(thread_turns) 의 턴 상태
- 읽음: state_5.sqlite thread_read_receipts.first_unread_turn (NULL = 앱에서 읽음)
- 한도: ChatGPT 백엔드 실시간 조회 (실패 시 로그의 token_count 이벤트)
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psutil

from .base import (HOME, AgentSession, Limit, Window, http_json, iso, parse_cached, parse_ts,
                   project_name, recent_files, tail_lines, window)

KEY = "codex"
LABEL = "Codex"
SHORT = "CODEX"
COLOR = "#10a37f"
BG = "#081a0f"
APP_TITLE = "Codex"

LOG_GLOB = f"{HOME}/.codex/sessions/**/*.jsonl"
STATE_DB = f"{HOME}/.codex/state_5.sqlite"            # threads: cwd, updated_at, thread_source
HISTORY_DB = f"{HOME}/.codex/thread_history_1.sqlite"  # thread_turns: status (inProgress/completed/...)
LOOKBACK_SEC = 86400  # 이보다 오래 갱신 없는 스레드는 세션으로 보지 않는다
STALE_SEC = 1800      # 실행 중인데 이 시간 동안 스레드 갱신이 없으면 완료로 간주


# ---- 세션 ----
# jsonl 은 Codex 가 연 채로 쓰기 때문에 Windows 에서 mtime 이 갱신되지 않는다.
# 그래서 앱이 관리하는 sqlite 의 스레드·턴 상태를 읽는다.


def _db(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


_ctx_cache: dict[str, tuple[int, float | None]] = {}


def _context(path: str) -> float | None:
    """롤아웃 마지막 token_count 의 입력 토큰 / model_context_window."""
    path = path.removeprefix("\\\\?\\")
    try:
        size = os.path.getsize(path)
        hit = _ctx_cache.get(path)
        if hit and hit[0] == size:
            return hit[1]
        result = None
        for line in reversed(tail_lines(path, size)):
            if '"token_count"' not in line:
                continue
            try:
                info = (json.loads(line).get("payload") or {}).get("info") or {}
            except ValueError:
                continue
            used = (info.get("last_token_usage") or {}).get("input_tokens")
            limit = info.get("model_context_window")
            if used and limit:
                result = min(1.0, used / limit)
                break
        _ctx_cache[path] = (size, result)
        return result
    except OSError:
        return None


def _alive() -> bool:
    try:
        return any((p.info.get("name") or "").lower() == "codex.exe"
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def sessions() -> list[AgentSession]:
    alive = _alive()
    now = time.time()
    out = []
    try:
        st = _db(STATE_DB)
        hi = _db(HISTORY_DB)
        try:
            threads = st.execute(
                "SELECT id, cwd, updated_at, rollout_path, model, git_branch, name FROM threads WHERE archived = 0 AND updated_at > ?"
                " AND COALESCE(thread_source, '') != 'subagent'",
                (int(now - LOOKBACK_SEC),)).fetchall()
            # 앱의 읽음 표시: first_unread_turn 이 NULL 이면 앱에서 다 읽은 스레드
            read = {r[0] for r in st.execute(
                "SELECT thread_id FROM thread_read_receipts WHERE first_unread_turn IS NULL")}
            for tid, cwd, updated, rollout, model, branch, name in threads:
                turn = hi.execute(
                    "SELECT status, started_at, completed_at FROM thread_turns"
                    " WHERE thread_id = ? ORDER BY rollout_ordinal DESC LIMIT 1", (tid,)).fetchone()
                if not turn:
                    continue  # 턴이 한 번도 없음
                status, started, completed = turn
                if status == "inProgress" and alive and now - updated <= STALE_SEC:
                    state, ts = "running", float(started or updated)
                else:
                    state, ts = "done", float(completed or updated)
                out.append(AgentSession(
                    KEY, tid, project_name(cwd) if cwd else tid[:8], ts,
                    state=state, focus_url=f"codex://threads/{tid}",
                    title=name or "", model=(model or "").replace("gpt", "GPT"),
                    branch=branch or "", context=_context(rollout) if rollout else None,
                    seen=state == "done" and tid in read,
                ))
        finally:
            st.close()
            hi.close()
    except sqlite3.Error:
        pass
    return out


# ---- 한도 / 사용량 ----

@dataclass
class CodexUsage:
    today_tokens: int = 0
    week_tokens: int = 0
    primary: Window = field(default_factory=Window)
    secondary: Window = field(default_factory=Window)
    primary_window_min: int | None = None
    credits: str | None = None
    updated: datetime | None = None

    @property
    def limits(self) -> list[Limit]:
        m = self.primary_window_min
        return [Limit(f"{m or 300}분", self.primary, (m or 300) / 60,
                      short="5H" if m in (None, 300) else f"{m}분"),
                Limit("주간", self.secondary, 168)]


def _parse_log(path: str) -> list[tuple]:
    out = []
    with open(path, errors="ignore") as fp:
        for line in fp:
            if '"token_count"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get("payload") or {}
            if p.get("type") != "token_count":
                continue
            info = p.get("info") or {}
            last = (info.get("last_token_usage") or {}).get("total_tokens", 0) or 0
            out.append((parse_ts(d["timestamp"]), last, p.get("rate_limits")))
    return out


def from_logs(now: datetime | None = None) -> CodexUsage:
    now = now or datetime.now(timezone.utc)
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    c = CodexUsage()
    latest_rl: tuple[datetime, dict] | None = None
    for f in recent_files(LOG_GLOB):
        for ts, tokens, rl in parse_cached(f, _parse_log):
            if ts >= week_start:
                c.week_tokens += tokens
            if ts >= local_midnight:
                c.today_tokens += tokens
            if rl and (latest_rl is None or ts > latest_rl[0]):
                latest_rl = (ts, rl)
    if latest_rl:
        ts, rl = latest_rl
        c.updated = ts
        c.primary = window(rl.get("primary"), "used_percent", "resets_at")
        c.secondary = window(rl.get("secondary"), "used_percent", "resets_at")
        c.primary_window_min = (rl.get("primary") or {}).get("window_minutes")
        credits = rl.get("credits") or {}
        if credits.get("unlimited"):
            c.credits = "무제한"
        elif credits.get("balance") is not None:
            c.credits = str(credits["balance"])
    return c


def live() -> CodexUsage | None:
    """ChatGPT 백엔드에서 Codex 한도를 실시간 조회."""
    try:
        tokens = json.load(open(f"{HOME}/.codex/auth.json"))["tokens"]
        d = http_json(
            "https://chatgpt.com/backend-api/wham/usage",
            {
                "Authorization": f"Bearer {tokens['access_token']}",
                "ChatGPT-Account-Id": tokens.get("account_id", ""),
                "User-Agent": "codex-cli",
                "Accept": "application/json",
            },
            timeout=8,
        )
    except Exception:
        return None

    def win(w: dict | None) -> tuple[Window, int | None]:
        if not w:
            return Window(), None
        reset = w.get("reset_at")
        return (
            Window(float(w["used_percent"]), datetime.fromtimestamp(reset, timezone.utc) if reset else None),
            (w.get("limit_window_seconds") or 0) // 60 or None,
        )

    rl = d.get("rate_limit") or {}
    c = CodexUsage()
    c.primary, c.primary_window_min = win(rl.get("primary_window"))
    c.secondary, _ = win(rl.get("secondary_window"))
    c.updated = datetime.now(timezone.utc)
    credits = d.get("credits") or {}
    c.credits = "무제한" if credits.get("unlimited") else credits.get("balance")
    return c


def usage() -> CodexUsage:
    return live() or from_logs()
