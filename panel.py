"""오른쪽 넓은 칸(458x196px) — Claude / Codex / AGY 사용량 패널."""

from __future__ import annotations

import io
import os
import platform
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

W, H = 458, 196

if platform.system() == "Windows":
    _WIN = os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "Fonts")
    _REG = os.path.join(_WIN, "malgun.ttf")
    _BOLD = os.path.join(_WIN, "malgunbd.ttf")
else:
    _REG = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
    _BOLD = _REG


def _f(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_BOLD if bold else _REG, size)


def _bar(d: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int,
         ratio: float | None, color: str) -> None:
    d.rounded_rectangle((x0, y0, x1, y1), 5, fill="#1e2235")
    if ratio and ratio > 0:
        w = int((x1 - x0) * min(1.0, ratio))
        if w > 0:
            d.rounded_rectangle((x0, y0, x0 + w, y1), 5, fill=color)


def _until(dt: datetime | None) -> str:
    if not dt:
        return ""
    sec = (dt - datetime.now(timezone.utc)).total_seconds()
    if sec <= 0:
        return "리셋됨"
    h, rem = divmod(int(sec), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


def _until_ko(dt: datetime | None) -> str:
    """'1시간 23분 뒤 초기화' 형식."""
    if not dt:
        return ""
    sec = (dt - datetime.now(timezone.utc)).total_seconds()
    if sec <= 0:
        return "초기화됨"
    h, rem = divmod(int(sec), 3600)
    m = rem // 60
    return f"{h}시간 {m}분 뒤 초기화" if h else f"{m}분 뒤 초기화"


def render_usage_panel_wide(rows: list[tuple]) -> bytes:
    """사용량 대시보드 넓은 패널 (458x196) — 에이전트당 한 줄, 가장 짧은 창(5H)만 크게.

    rows: (이름, 색, limits) 목록. limits 는 agents.base.Limit 목록 (가장 짧은 창만 그린다).
    """
    img = Image.new("RGB", (W, H), "#0a0b10")
    d = ImageDraw.Draw(img)

    RH = H // max(3, len(rows))  # 행 높이 (3행이면 ~65px)
    NW = 78  # 이름 영역 너비

    for row_i, (name, color, limits) in enumerate(rows):
        y0 = row_i * RH
        cy = y0 + RH // 2
        # 구분선
        if row_i:
            d.line([(8, y0), (W - 8, y0)], fill="#1e2235", width=1)
        # 이름
        d.text((12, cy), name, font=_f(17, bold=True), fill=color, anchor="lm")

        # 가장 짧은 창(5H)만 크게: 남은 % / 바 / 초기화까지 남은 시간 (주간은 하단 타일에 있다)
        known = [l for l in limits if l.window.used_percent is not None]
        lim = min(known, key=lambda l: l.hours) if known else None
        if lim is None:
            alt = next((l.alt for l in limits if l.alt), "") or "-"
            d.text((NW + 6, cy), alt, font=_f(20, bold=True), fill="#565f89", anchor="lm")
            continue
        left = 100 - lim.window.used_percent
        d.text((NW + 4, cy), f"{left:.0f}%", font=_f(34, bold=True), fill="#ffffff", anchor="lm")
        # 창 이름은 길이로 통일 (Codex '300분' → '5H')
        tag = f"{lim.hours:g}H" if lim.hours < 24 else lim.label
        d.text((190, cy - 12), f"{tag} 남음", font=_f(14, bold=True), fill="#a9b1d6", anchor="lm")
        _bar(d, 190, cy + 3, 300, cy + 17, left / 100, color)
        reset = _until_ko(lim.window.resets_at)
        d.text((W - 12, cy - 9), reset.replace(" 뒤 초기화", "") or "-",
               font=_f(22, bold=True), fill="#c0caf5", anchor="rm")
        if reset != "초기화됨":
            d.text((W - 12, cy + 15), f"뒤 {tag} 초기화", font=_f(12), fill="#565f89", anchor="rm")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
