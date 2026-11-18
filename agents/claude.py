"""Claude Code (데스크톱 / CLI).

- 세션: ~/.claude/sessions/<pid>.json 의 status (busy / idle)
- 읽음: %APPDATA%/Claude/claude-code-sessions/*/*/<hostSessionId>.json 의 lastFocusedAt >= lastTurnEdgeAt
- 한도: Anthropic OAuth 사용량 API (실패 시 statusline 스냅샷 → ccusage 추정)
- 토큰: ~/.claude/projects/**/*.jsonl 의 assistant 메시지 usage
"""

from __future__ import annotations

import glob
import json
import os
import platform
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psutil

from .base import (HOME, AgentSession, Limit, Window, http_json, parse_cached, parse_ts,
                   project_name, recent_files, tail_lines, window)

KEY = "claude"
LABEL = "Claude"
SHORT = "CLAUDE"
COLOR = "#d97757"
BG = "#1e1208"
APP_TITLE = "Claude"

_NPX = ["npx.cmd" if platform.system() == "Windows" else "npx"]
SESSION_GLOB = f"{HOME}/.claude/sessions/*.json"
LOG_GLOB = f"{HOME}/.claude/projects/**/*.jsonl"
STATUS_SNAPSHOT = f"{HOME}/.cache/d200/claude_status.json"
CRED = f"{HOME}/.claude/.credentials.json"
# 데스크톱 앱의 세션 메타: lastFocusedAt(앱에서 연 시각), lastTurnEdgeAt(턴 끝난 시각), ms 단위
APP_SESSIONS = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions")
BLOCK = timedelta(hours=5)

_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code 공개 OAuth client
_LIVE_TTL = 120  # 사용량 API 는 자주 부르면 429
# True 면 만료된 토큰을 refresh token 으로 갱신하고 .credentials.json 에 덮어쓴다
TOKEN_REFRESH = True
_live_cache: tuple[float, tuple[Window, Window] | None] = (0.0, None)


# ---- 세션 ----

_path_cache: dict[str, str] = {}
_ctx_cache: dict[str, tuple[int, tuple[str, str, float | None]]] = {}


def _model_name(mid: str) -> str:
    """claude-opus-5-5 → Opus 5.5"""
    parts = [p for p in mid.replace("claude-", "").split("-") if not (p.isdigit() and len(p) == 8)]
    return f"{parts[0].title()} {'.'.join(parts[1:])}".strip() if parts else ""


def _context(sid: str) -> tuple[str, str, float | None]:
    """(모델, 브랜치, 컨텍스트 사용률) — 트랜스크립트 마지막 assistant 메시지 기준."""
    path = _path_cache.get(sid)
    if not path:
        hits = glob.glob(f"{HOME}/.claude/projects/*/{sid}.jsonl")
        if not hits:
            return "", "", None
        path = _path_cache[sid] = hits[0]
    try:
        size = os.path.getsize(path)
        hit = _ctx_cache.get(path)
        if hit and hit[0] == size:
            return hit[1]
        result: tuple[str, str, float | None] = ("", "", None)
        for line in reversed(tail_lines(path, size)):
            if '"assistant"' not in line or '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") or {}
            u = m.get("usage") or {}
            used = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                    + u.get("cache_read_input_tokens", 0))
            if not used:
                continue
            mid = m.get("model") or ""
            limit = 1_000_000 if "[1m]" in mid or used > 200_000 else 200_000
            result = (_model_name(mid), d.get("gitBranch") or "", min(1.0, used / limit))
            break
        _ctx_cache[path] = (size, result)
        return result
    except OSError:
        return "", "", None


def _app_viewed(host: str) -> bool:
    """데스크톱 앱에서 마지막 턴이 끝난 뒤 그 세션을 열어봤는지 (lastFocusedAt >= lastTurnEdgeAt)."""
    if not host.startswith("local_"):
        return False
    for f in glob.glob(f"{APP_SESSIONS}/*/*/{host}.json"):
        try:
            d = json.load(open(f, encoding="utf-8"))
            return (d.get("lastFocusedAt") or 0) >= (d.get("lastTurnEdgeAt") or 0) > 0
        except (OSError, ValueError):
            pass
    return False


def sessions() -> list[AgentSession]:
    out = []
    for f in glob.glob(SESSION_GLOB):
        try:
            d = json.load(open(f, encoding="utf-8"))
            if not d.get("cwd") or not psutil.pid_exists(int(d["pid"])):
                continue
            started = (d.get("startedAt") or 0) / 1000
            ts = (d.get("statusUpdatedAt") or d.get("updatedAt") or d.get("startedAt") or 0) / 1000
            running = d.get("status") == "busy"
            host = d.get("hostSessionId") or ""
            sid = d.get("sessionId") or str(d["pid"])
            model, branch, ctx = _context(sid)
            out.append(AgentSession(
                KEY, sid, project_name(d["cwd"]), ts,
                model=model, branch=branch, context=ctx,
                state="running" if running else "done",
                title=d.get("name") or "",
                focus_url=f"claude://code/continue?session={host}" if host.startswith("local_") else None,
                # 띄우기만 하고 아직 작업한 적 없는 세션은 알릴 게 없다
                seen=not running and (ts - started < 5 or _app_viewed(host)),
            ))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out


# ---- 한도 / 사용량 ----

@dataclass
class ClaudeUsage:
    today_tokens: int = 0
    today_output: int = 0
    today_cache_read: int = 0
    today_messages: int = 0
    today_sessions: int = 0
    week_tokens: int = 0
    block_tokens: int = 0
    block_end: datetime | None = None
    by_model: dict[str, int] = field(default_factory=dict)
    five_hour: Window = field(default_factory=Window)
    seven_day: Window = field(default_factory=Window)
    seven_day_cost: float | None = None

    @property
    def limits(self) -> list[Limit]:
        cost = f"${self.seven_day_cost:.1f}" if self.seven_day_cost is not None else ""
        return [Limit("5H", self.five_hour, 5),
                Limit("주간", self.seven_day, 168, alt=cost)]

    @property
    def note(self) -> str:
        return f"오늘 {self.today_messages}건" if self.today_messages else ""


def _token() -> str:
    """~/.claude/.credentials.json 의 access token. 만료됐으면 Claude Code 와 같은 방식으로 갱신해 저장."""
    cred = json.load(open(CRED, encoding="utf-8"))
    o = cred["claudeAiOauth"]
    if o.get("expiresAt", 0) / 1000 < time.time() + 60:
        if not TOKEN_REFRESH:
            raise RuntimeError("토큰 만료 (TOKEN_REFRESH 꺼짐)")
        d = http_json(
            "https://platform.claude.com/v1/oauth/token",
            {"Content-Type": "application/json"},
            {"grant_type": "refresh_token", "refresh_token": o["refreshToken"],
             "client_id": _CLIENT_ID},
        )
        o["accessToken"] = d["access_token"]
        o["refreshToken"] = d.get("refresh_token") or o["refreshToken"]
        o["expiresAt"] = int((time.time() + d.get("expires_in", 3600)) * 1000)
        tmp = CRED + ".agentmon.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cred, f)
        os.replace(tmp, CRED)
    return o["accessToken"]


def live() -> tuple[Window, Window] | None:
    """Anthropic OAuth 사용량 API 에서 (5시간, 주간) 한도를 조회. 실패 시 마지막 성공값."""
    global _live_cache
    at, last = _live_cache
    if time.time() - at < _LIVE_TTL:
        return last
    try:
        d = http_json(
            "https://api.anthropic.com/api/oauth/usage",
            {"Authorization": f"Bearer {_token()}", "anthropic-beta": "oauth-2025-04-20"},
        )
        last = (window(d.get("five_hour"), "utilization", "resets_at"),
                window(d.get("seven_day"), "utilization", "resets_at"))
    except Exception as e:
        print(f"{datetime.now():%H:%M:%S} claude usage error: {e}", flush=True)
    _live_cache = (time.time(), last)
    return last


def _parse_log(path: str) -> list[tuple]:
    out = []
    with open(path, errors="ignore") as fp:
        for line in fp:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message")
            if not isinstance(m, dict) or "usage" not in m or not d.get("timestamp"):
                continue
            u = m["usage"]
            key = f"{m.get('id')}:{d.get('requestId')}"
            out.append((
                key,
                parse_ts(d["timestamp"]),
                m.get("model") or "?",
                u.get("input_tokens", 0) or 0,
                u.get("cache_creation_input_tokens", 0) or 0,
                u.get("cache_read_input_tokens", 0) or 0,
                u.get("output_tokens", 0) or 0,
                d.get("sessionId"),
            ))
    return out


def _ccusage_fill(u: ClaudeUsage) -> None:
    """ccusage CLI로 Claude 5H / 7-day 사용률을 채운다."""
    import subprocess

    # ── 5H: ccusage claude blocks --json ──────────────────────────────
    if u.five_hour.used_percent is None:
        try:
            r = subprocess.run(
                _NPX + ["--yes", "ccusage", "claude", "blocks", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                d = json.loads(r.stdout)
                blocks = d.get("blocks") or (d if isinstance(d, list) else [])
                active = next((b for b in blocks if b.get("isActive")), None)
                if active:
                    proj = active.get("projection") or {}
                    remaining_min = proj.get("remainingMinutes")
                    if remaining_min is not None:
                        used_pct = max(0.0, min(100.0, (1 - remaining_min / 300) * 100))
                        start = active.get("startTime")
                        reset_dt = parse_ts(start) + timedelta(hours=5) if start else None
                        u.five_hour = Window(used_pct, reset_dt)
        except Exception:
            pass

    # ── 7-day 비용: ccusage claude weekly --json (현재 주 totalCost) ────
    if u.seven_day.used_percent is None and u.seven_day_cost is None:
        try:
            r = subprocess.run(
                _NPX + ["--yes", "ccusage", "claude", "weekly", "--json"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout.strip():
                d = json.loads(r.stdout)
                weekly = d.get("weekly", [])
                if weekly:
                    u.seven_day_cost = weekly[-1].get("totalCost")
        except Exception:
            pass


def usage(now: datetime | None = None) -> ClaudeUsage:
    now = now or datetime.now(timezone.utc)
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    seen: set[str] = set()
    recs = []
    for f in recent_files(LOG_GLOB):
        for r in parse_cached(f, _parse_log):
            if r[0] in seen:
                continue
            seen.add(r[0])
            recs.append(r)
    recs.sort(key=lambda r: r[1])

    u = ClaudeUsage()
    sids: set[str] = set()
    block_start: datetime | None = None
    block_tokens = 0
    last_ts: datetime | None = None
    for _, ts, model, inp, cc, cr, outp, sid in recs:
        billable = inp + cc + outp
        if ts >= week_start:
            u.week_tokens += billable
        if ts >= local_midnight:
            u.today_tokens += billable
            u.today_output += outp
            u.today_cache_read += cr
            u.today_messages += 1
            u.by_model[model] = u.by_model.get(model, 0) + billable
            if sid:
                sids.add(sid)
        if block_start is None or ts - block_start >= BLOCK or (last_ts and ts - last_ts >= BLOCK):
            block_start = ts.replace(minute=0, second=0, microsecond=0)
            block_tokens = 0
        block_tokens += billable
        last_ts = ts
    u.today_sessions = len(sids)
    if block_start and now - block_start < BLOCK:
        u.block_tokens = block_tokens
        u.block_end = block_start + BLOCK

    try:
        snap = json.load(open(STATUS_SNAPSHOT))
        rl = snap.get("rate_limits") or {}
        u.five_hour = window(rl.get("five_hour"), "used_percentage", "resets_at")
        u.seven_day = window(rl.get("seven_day"), "used_percentage", "resets_at")
    except (OSError, ValueError):
        pass

    lv = live()
    if lv:
        u.five_hour, u.seven_day = lv

    # 실시간 조회도 스냅샷도 없을 때만 ccusage 로 추정 (5H 는 경과 시간 기준이라 부정확)
    if u.five_hour.used_percent is None or u.seven_day.used_percent is None:
        _ccusage_fill(u)

    return u
