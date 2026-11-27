"""에이전트 레지스트리. 새 도구는 모듈을 만들어 MODULES 에 추가한다 (형식은 base.py 참고)."""

from __future__ import annotations

import time

from . import antigravity, claude, codex, cursor
from . import herdr as _herdr
from .base import AgentSession, Limit, Window, until

MODULES = [claude, codex, antigravity, cursor]  # 덱·패널 표시 순서
AGENTS = {m.KEY: m for m in MODULES}

AGENT_LABEL = {m.KEY: m.LABEL for m in MODULES}
AGENT_COLOR = {m.KEY: m.COLOR for m in MODULES}
AGENT_BG = {m.KEY: m.BG for m in MODULES}

LOOKBACK_SEC = 86400  # 이보다 오래된 세션은 무시
RECENT_SEC = 3600     # 확인한 완료 세션을 목록에 남겨두는 시간


def list_sessions(acks: dict[str, float] | None = None, baseline: float = 0.0,
                  limit: int = 50) -> list[AgentSession]:
    """실행 중 + 미확인 완료 + 최근 1시간 내 완료 세션.

    acks: 세션 key → 사용자가 확인한 시각. baseline: 이 시각 이전 완료는 확인한 것으로 본다.
    """
    acks = acks or {}
    now = time.time()
    found: list[AgentSession] = []
    for m in MODULES:
        try:
            found += m.sessions()
        except Exception as e:
            print(f"{time.strftime('%H:%M:%S')} {m.KEY} session error: {e}", flush=True)

    # herdr 안의 세션은 herdr 의 읽음 표시를 따른다 (unread = herdr 화면의 done, read = idle)
    herdr_status = _herdr.statuses(found)
    for s in found:
        if s.state != "done":
            continue
        acked = s.mtime <= acks.get(s.key, 0.0)
        st = herdr_status.get(s.key)
        if st == "unread":
            s.seen = acked
        elif st in ("read", "idle") or acked or s.mtime <= baseline:
            s.seen = True

    out = []
    latest: dict[str, AgentSession] = {}  # 에이전트별 가장 최근 세션
    for s in found:
        if s.agent not in latest or s.mtime > latest[s.agent].mtime:
            latest[s.agent] = s
        if now - s.mtime > LOOKBACK_SEC and not s.is_running:
            continue
        if s.is_running or s.is_waiting or now - s.mtime < RECENT_SEC:
            out.append(s)
    # 모든 에이전트가 덱에 항상 보이도록, 목록에 없는 에이전트는 마지막 세션을 남긴다
    shown = {s.agent for s in out}
    out += [s for a, s in latest.items() if a not in shown]
    out.sort(key=lambda s: (not s.is_running, not s.is_waiting, -s.mtime))
    return out[:limit]
