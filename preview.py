"""README용 덱 미리보기 이미지(docs/preview.webp)를 만든다. 장치 없이 실제 렌더 코드로 그린다.

예시 데이터만 쓴다 (실제 세션·프로젝트 이름은 넣지 않는다).
실행: .venv\\Scripts\\python.exe preview.py
"""

from __future__ import annotations

import io
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from PIL import Image

from agents.base import AgentSession, Limit, Window
from d200 import ICON, Button, render_icon
from dashboard import NEXT_BUTTON, Dashboard, _session_button
from panel import render_usage_panel_wide

GAP, PAD = 20, 28
OUT = os.path.join(os.path.dirname(__file__), "docs", "preview.webp")


def _sample():
    now = time.time()
    t = datetime.now(timezone.utc)
    S = lambda agent, project, title, model, branch, ctx, state="done", seen=True, ago=0: AgentSession(
        agent, project + title, project, now - ago, state, title, model=model, branch=branch,
        context=ctx, seen=seen)
    sessions = [
        S("claude", "webapp", "로그인 리팩터링", "Opus", "feat/auth", 0.42, "running"),
        S("codex", "api-server", "캐시 버그 수정", "gpt-5", "fix/cache", 0.18, "running"),
        S("claude", "docs", "README 정리", "Sonnet", "main", 0.65, seen=False),
        S("agy", "mobile", "온보딩 화면", "Gemini", "ui/onboard", 0.30, "running"),
        S("codex", "cli-tool", "테스트 추가", "gpt-5", "test/e2e", 0.51, seen=False),
        S("claude", "infra", "배포 스크립트", "Opus", "ops", 0.77, ago=1800),
        S("agy", "landing", "히어로 섹션", "Gemini", "design", 0.12, ago=7200),
    ]
    W = lambda used, mins: Window(used, t + timedelta(minutes=mins))
    usages = {
        "claude": SimpleNamespace(limits=[Limit("5H", W(27, 178), 5), Limit("주간", W(40, 3000), 168)]),
        "codex": SimpleNamespace(limits=[Limit("300분", W(8, 45), 5), Limit("주간", W(22, 5000), 168)]),
        "agy": SimpleNamespace(limits=[Limit("5H", W(61, 130), 5), Limit("주간", W(35, 900), 168)]),
    }
    return sessions, usages


def _frame(sessions, usages, blink_on: bool) -> Image.Image:
    dash = Dashboard.__new__(Dashboard)  # 장치 없이 렌더 함수만 쓴다
    dash.usages, dash.show_used = usages, False

    body: dict[int, Button] = {i: _session_button(s, blink_on) for i, s in enumerate(sessions)}
    for i in range(len(sessions), NEXT_BUTTON):
        body[i] = Button(bg="#0c0d14")
    body[NEXT_BUTTON] = Button(title="→", sub="1/1", bg="#0c0d14", fg="#3a3a4a")
    body.update(dash._limit_buttons())

    step = ICON + GAP
    img = Image.new("RGB", (PAD * 2 + 5 * step - GAP, PAD * 2 + 3 * step - GAP), "#000000")
    for i, btn in body.items():
        col, row = (i % 5, i // 5)
        tile = Image.open(io.BytesIO(render_icon(btn))).convert("RGB")
        img.paste(tile, (PAD + col * step, PAD + row * step))
    # 넓은 패널: 마지막 줄 오른쪽 2칸 자리에 비율 유지해 맞춘다
    panel = Image.open(io.BytesIO(render_usage_panel_wide(dash._panel_rows()))).convert("RGB")
    pw = 2 * ICON + GAP
    panel = panel.resize((pw, round(panel.height * pw / panel.width)), Image.LANCZOS)
    img.paste(panel, (PAD + 3 * step, PAD + 2 * step + (ICON - panel.height) // 2))
    return img


def main() -> None:
    sessions, usages = _sample()
    frames = [_frame(sessions, usages, on) for on in (True, False)]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=700, loop=0,
                   quality=90, method=6)
    print(OUT)


if __name__ == "__main__":
    main()
