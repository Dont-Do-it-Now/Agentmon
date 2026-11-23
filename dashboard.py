"""Claude Code / Codex / Gemini 에이전트 모니터링 대시보드 (Ulanzi D200).

버튼 배치 (넓은 패널은 항상 에이전트별 한도):
  0~8  : 세션 타일 (페이지당 9개)
  9    : → 다음 세션 페이지 (마지막 페이지에서 누르면 1페이지)
  10~11: 고정 에이전트 2개의 한도 타일 (누르면 남은/사용 전환 + 한도 갱신)
  12   : AGY / Cursor 한도 타일 순환

실행: python dashboard.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from d200 import SMALL_BACKGROUND, Button, D200, InputEvent
from agents import (AGENT_COLOR, AGENT_LABEL, AGENTS, MODULES, AgentSession, Limit,
                    Window, list_sessions, until)
from agents import herdr as _herdr
from panel import render_usage_panel_wide

SESSION_SEC = 2   # 세션 상태 갱신 주기
USAGE_SEC = 60    # 한도 갱신 주기
BRIGHTNESS = 80
SESSIONS_PER_PAGE = 9
NEXT_BUTTON = 9
LIMIT_BUTTONS = (10, 11, 12)
DYNAMIC_LIMIT_BUTTON = 12


# ---- 데이터 수집 ----

def _focus(s: AgentSession) -> None:
    """세션이 속한 앱을 앞으로 가져오고, 가능하면 해당 세션을 연다."""
    if _herdr.activate(s):
        return
    mod = AGENTS[s.agent]
    if s.focus_url:
        try:
            os.startfile(s.focus_url)  # 에이전트 딥링크
        except OSError:
            pass
    if hasattr(mod, "focus"):
        mod.focus(s)  # 에이전트별 추가 동작 (예: Antigravity 대화 이동)
        return
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-Command",
         "Add-Type -AssemblyName Microsoft.VisualBasic; "
         f"[Microsoft.VisualBasic.Interaction]::AppActivate('{mod.APP_TITLE}')"],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )


def _limits(u) -> list:
    """사용률이 있거나, 대체 텍스트(alt)가 있는 한도만."""
    return [l for l in (u.limits if u else []) if l.window.used_percent is not None or l.alt]


def _llm_tile(name: str, color: str, bg: str,
              bars: list[tuple], sub: str = "", agent: str = "", show_used: bool = False,
              main: float | None = None) -> Button:
    # bars 의 비율은 표시 모드(남은/사용)에 맞춰 이미 변환돼 있다.
    # 큰 숫자는 main(가장 긴 창 = 주간), 모르면 가장 빠듯한 창
    valid = [b[1] for b in bars if b[1] is not None]
    if main is None and valid:
        main = max(valid) if show_used else min(valid)
    title = f"{main * 100:.0f}%" if main is not None else ""
    return Button(
        header=f"{name} {'사용' if show_used else '남음'}", title=title,
        bars=bars if bars else None,
        sub=sub,
        bg=bg, fg="#ffffff", accent=color,
        agent=agent,
    )


# ---- 세션 타일 ----

def _mix(hex_color: str, other: str, t: float) -> str:
    """두 색을 t(0~1) 비율로 섞는다."""
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _session_button(s: AgentSession, blink_on: bool = True) -> Button:
    color = AGENT_COLOR[s.agent]  # Claude 주황 / Codex 초록 / AGY 파랑
    dark = _mix(color, "#000000", 0.82)
    if s.is_running or (s.is_waiting and blink_on):
        # 실행 중: 에이전트 색으로 채움. 완료·미확인: 같은 색으로 깜박
        bg, fg, muted, bar, track = color, "#ffffff", _mix(color, "#ffffff", 0.7), "#ffffff", _mix(color, "#000000", 0.4)
        footer = "WORKING" if s.is_running else "DONE"
    elif s.is_waiting:
        bg, fg, muted, bar, track, footer = dark, _mix(color, "#ffffff", 0.6), _mix(color, "#000000", 0.2), color, _mix(color, "#000000", 0.6), "DONE"
    else:
        # 확인한 완료: 어둡게, 경과 시간
        bg, fg, muted, bar, track, footer = "#14151c", "#c0caf5", "#6b7089", color, "#2a2b36", f"READY · {s.age_text}"
    return Button(
        header=s.project,
        title=s.title or s.branch or AGENT_LABEL[s.agent],
        sub=" · ".join(x for x in (s.model, s.branch if s.title else "") if x) or AGENT_LABEL[s.agent],
        ratio=s.context,
        footer=footer,
        dot="#ffffff" if bg == color else color,
        bg=bg, fg=fg, muted=muted, accent=bar, track=track,
    )


# ---- 대시보드 ----

class Dashboard:
    def __init__(self) -> None:
        self.deck = D200()
        self.deck.on_event = self.on_event
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.stop = threading.Event()
        self.fetch_now = threading.Event()
        self.render_lock = threading.Lock()

        self.sessions: list[AgentSession] = []
        self.page = 0
        self.usages: dict = {}        # 에이전트 key → 한도 객체 (없으면 None)
        self.usage_stamp = 0          # 한도 데이터가 바뀔 때마다 증가
        self.show_used = False        # 한도 타일: False=남은 %, True=사용 %
        self.dynamic_key: str | None = None  # 하단 12번: 사용자가 고른 선택 에이전트
        self.acks: dict[str, float] = {}   # 세션 key → 확인한 시각
        self.baseline = time.time()   # 시작 전에 끝난 세션은 확인한 것으로 본다
        self.blink_on = True
        self._states: dict[str, tuple] = {}
        self._sig: tuple | None = None  # 마지막 전체 렌더 내용
        self.pressed_at: dict[int, float] = {}

    @property
    def total_session_pages(self) -> int:
        n = len(self.sessions)
        return max(1, (n + SESSIONS_PER_PAGE - 1) // SESSIONS_PER_PAGE)

    def on_event(self, ev: InputEvent) -> None:
        if ev.kind != "button":
            return
        if ev.action == "press":
            self.pressed_at[ev.index] = time.time()
            return
        if ev.action != "release":
            return
        self.pressed_at.pop(ev.index, None)

        if ev.index < SESSIONS_PER_PAGE:
            i = self.page * SESSIONS_PER_PAGE + ev.index
            if i < len(self.sessions):
                s = self.sessions[i]
                self.acks[s.key] = time.time()
                s.seen = True
                _focus(s)
                self.render()
        elif ev.index == NEXT_BUTTON:
            self.page = (self.page + 1) % self.total_session_pages
            self.render()
        elif ev.index in LIMIT_BUTTONS:
            if ev.index == DYNAMIC_LIMIT_BUTTON:
                self._cycle_optional()
            else:
                self.show_used = not self.show_used
            self.render()
            self.fetch_now.set()

    def _optional_modules(self) -> list:
        return MODULES[2:]

    def _selected_optional(self):
        options = self._optional_modules()
        if not options:
            return None
        if self.dynamic_key:
            return next((m for m in options if m.KEY == self.dynamic_key), options[0])
        recent = [(m, self._activity_mtime(m)) for m in options]
        if any(stamp for _, stamp in recent):
            return max(recent, key=lambda item: item[1])[0]
        return next((m for m in options if self.usages.get(m.KEY) is not None), options[0])

    @staticmethod
    def _activity_mtime(module) -> float:
        try:
            return float(module.activity_mtime())
        except (AttributeError, OSError, TypeError, ValueError):
            return 0.0

    def _cycle_optional(self) -> None:
        options = self._optional_modules()
        if not options:
            return
        current = self._selected_optional()
        pos = next((i for i, m in enumerate(options) if m.KEY == current.KEY), -1)
        self.dynamic_key = options[(pos + 1) % len(options)].KEY

    def fetch_sessions(self) -> None:
        try:
            self.sessions = list_sessions(self.acks, self.baseline, limit=50)
            # 상태 전환을 로그에 남긴다 (감지 문제 추적용)
            states = {s.key: (s.state, s.agent, s.project) for s in self.sessions}
            for k, v in states.items():
                if self._states.get(k, (None,))[0] != v[0]:
                    print(f"{datetime.now():%H:%M:%S} {v[1]} {v[2]}: {v[0]}", flush=True)
                    if v[0] == "running":
                        self.page = 0  # 실행 중 세션은 1페이지 맨 앞에 온다: 다른 페이지를 보고 있으면 놓친다
            self._states = states
            if self.page >= self.total_session_pages:
                self.page = max(0, self.total_session_pages - 1)
        except Exception as e:
            print(f"{datetime.now():%H:%M:%S} session error: {e}", flush=True)

    def _usage_loop(self) -> None:
        """한도 조회는 느리므로 별도 스레드에서 돈다."""
        while not self.stop.is_set():
            try:
                futures = {m.KEY: self.pool.submit(m.usage) for m in MODULES}
                for key, f in futures.items():
                    try:
                        self.usages[key] = f.result()
                    except Exception as e:
                        print(f"{datetime.now():%H:%M:%S} {key} usage error: {e}", flush=True)
                self.usage_stamp += 1
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} usage error: {e}", flush=True)
            self.fetch_now.wait(USAGE_SEC)
            self.fetch_now.clear()

    def _panel_rows(self) -> list[tuple]:
        """넓은 패널용 (이름, 색, 한도 목록) — 에이전트당 한 줄."""
        modules = list(MODULES[:2])
        if (m := self._selected_optional()) is not None:
            modules.append(m)
        rows = []
        for m in modules:
            u = self.usages.get(m.KEY)
            limits = u.limits if u else []
            if m is modules[-1] and getattr(u, "login_required", False):
                limits = [Limit("상태", Window(), 0, alt="↪ 로그인 필요")]
            elif m is modules[-1] and u is None:
                if self._activity_mtime(m):
                    limits = [Limit("상태", Window(), 0, alt="한도 조회")]
                else:
                    limits = [Limit("상태", Window(), 0, alt="▶ 실행 필요")]
            if not limits:
                limits = [Limit("상태", Window(), 0, alt="▶ 실행 필요")]
            rows.append((m.SHORT, m.COLOR, limits))
        return rows

    def _limit_buttons(self) -> dict[int, Button]:
        """하단 2칸 고정 + 1칸 선택 에이전트의 한도."""
        body: dict[int, Button] = {}
        modules = list(MODULES[:2])
        if (m := self._selected_optional()) is not None:
            modules.append(m)
        for idx, m in zip(LIMIT_BUTTONS, modules):
            u = self.usages.get(m.KEY)
            if idx == DYNAMIC_LIMIT_BUTTON and getattr(u, "login_required", False):
                body[idx] = Button(header=m.SHORT, title="↪", sub="로그인 필요",
                                   bg=m.BG, fg="#ffffff", accent=m.COLOR, agent=m.KEY)
                continue
            bars = [
                (l.short or l.label,
                 None if l.window.used_percent is None else
                 (l.window.used_percent if self.show_used else 100 - l.window.used_percent) / 100,
                 m.COLOR,
                 until(l.window.resets_at) if l.window.used_percent is not None else l.alt)
                for l in _limits(u)
            ]
            if bars:
                ls = _limits(u)
                known = [l for l in ls if l.window.used_percent is not None]
                if not known:
                    body[idx] = Button(header=m.SHORT, sub="한도 조회", status_icon="quota",
                                       bg=m.BG, fg="#ffffff", accent=m.COLOR,
                                       agent=m.KEY)
                    continue
                if len(known) != len(ls):
                    ls = known
                    bars = [
                        (l.short or l.label,
                         (l.window.used_percent if self.show_used else 100 - l.window.used_percent) / 100,
                         m.COLOR,
                         until(l.window.resets_at))
                        for l in ls
                    ]
                longest = max(range(len(ls)), key=lambda i: ls[i].hours)
                body[idx] = _llm_tile(m.SHORT, m.COLOR, m.BG, bars, agent=m.KEY,
                                      show_used=self.show_used, main=bars[longest][1])
            elif u is None and idx == DYNAMIC_LIMIT_BUTTON:
                recent = bool(self._activity_mtime(m))
                if recent:
                    body[idx] = Button(header=m.SHORT, sub="한도 조회", status_icon="quota",
                                       bg=m.BG, fg="#ffffff", accent=m.COLOR,
                                       agent=m.KEY)
                else:
                    body[idx] = Button(header=m.SHORT, title="▶", sub="실행 필요",
                                       bg=m.BG, fg="#ffffff", accent=m.COLOR, agent=m.KEY)
            elif u is None:
                body[idx] = Button(header=m.SHORT, title="○", sub="꺼짐",
                                   bg=m.BG, fg="#565f89", accent=m.COLOR, agent=m.KEY)
            else:
                body[idx] = Button(header=m.SHORT, title="?",
                                   sub=getattr(u, "note", "") or "데이터 없음",
                                   bg=m.BG, fg="#565f89", accent=m.COLOR, agent=m.KEY)
        return body

    def _render_session_page(self) -> None:
        offset = self.page * SESSIONS_PER_PAGE
        visible = self.sessions[offset: offset + SESSIONS_PER_PAGE]

        sig = ("sessions", self.page, self.total_session_pages, self.usage_stamp, self.show_used,
               self._selected_optional().KEY if self._selected_optional() else None,
               tuple((s.key, s.state, s.seen, s.project, s.title, s.model,
                      None if s.context is None else round(s.context, 2),
                      "" if s.is_running or s.is_waiting else s.age_text) for s in visible))
        if sig == self._sig:
            # 내용은 그대로: 미확인 완료 타일만 부분 갱신으로 깜박인다
            blink = {i: _session_button(s, self.blink_on)
                     for i, s in enumerate(visible) if s.is_waiting}
            if blink:
                self.deck.set_buttons(blink, partial=True)
            return
        self._sig = sig

        body: dict[int, Button] = {}
        for i in range(SESSIONS_PER_PAGE):
            body[i] = (_session_button(visible[i], self.blink_on)
                       if i < len(visible) else Button(bg="#0c0d14"))

        pages = self.total_session_pages
        body[NEXT_BUTTON] = Button(
            title="→",
            sub=f"{self.page + 1}/{pages}",
            bg="#1a1b26" if pages > 1 else "#0c0d14",
            fg="#c0caf5" if pages > 1 else "#3a3a4a",
        )
        body.update(self._limit_buttons())

        small = render_usage_panel_wide(self._panel_rows())
        self.deck.set_buttons(body, small_png=small)

    def render(self) -> None:
        with self.render_lock:
            try:
                self._render_session_page()
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} render error: {e}", flush=True)

    def run(self) -> None:
        self.deck.start()
        self.deck.set_brightness(BRIGHTNESS)
        self.deck.set_small_window(mode=SMALL_BACKGROUND)
        print(f"{datetime.now():%H:%M:%S} agentmon 시작 (Ctrl+C 종료)", flush=True)

        threading.Thread(target=self._usage_loop, daemon=True).start()
        tick = 0
        while not self.stop.is_set() and not self.deck.disconnected.is_set():
            if tick % SESSION_SEC == 0:
                self.fetch_sessions()
            self.blink_on = not self.blink_on
            self.render()
            tick += 1
            self.stop.wait(1)

        self.deck.close()
        if self.deck.disconnected.is_set():
            print(f"{datetime.now():%H:%M:%S} 장치 연결 끊김", flush=True)
            raise SystemExit(1)


def main() -> None:
    try:
        dash = Dashboard()
    except RuntimeError as e:
        print(f"{datetime.now():%H:%M:%S} {e}", flush=True)
        raise SystemExit(1)

    def _quit(*_):
        dash.stop.set()
        dash.fetch_now.set()

    signal.signal(signal.SIGINT, _quit)
    signal.signal(signal.SIGTERM, _quit)
    dash.run()


if __name__ == "__main__":
    main()
