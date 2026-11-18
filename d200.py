"""Ulanzi D200 직접 제어 드라이버 (공식 앱 불필요).

프로토콜 출처: redphx/strmdck (MIT), jcalado/companion-surface-d200 (USBPcap 분석).
패킷: [7c 7c][cmd:2 BE][length:4 LE][data → 1024 바이트 패딩]
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import random
import string
import threading
import time
import zipfile
from dataclasses import dataclass
from typing import Callable

import hid
from PIL import Image, ImageDraw, ImageFont

VID, PID = 0x2207, 0x0019
PACKET = 1024
HEADER = 8
COLS, ROWS = 5, 3
BUTTON_COUNT = 13  # 5 + 5 + 3, 마지막 줄 3_2~4_2 는 작은 창(458x196)
ICON = 196

CMD_SET_BUTTONS = 0x0001
CMD_SMALL_WINDOW = 0x0006
CMD_BRIGHTNESS = 0x000A
CMD_LABEL_STYLE = 0x000B
CMD_PARTIAL_UPDATE = 0x000D
SMALL_BACKGROUND = 2  # 작은 창 모드: 배경 이미지
IN_BUTTON = 0x0101
IN_DEVICE_INFO = 0x0303

if os.name == "nt":
    _WIN_FONTS = os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "Fonts")
    FONT_PATH = os.path.join(_WIN_FONTS, "malgun.ttf")
else:
    FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
BOLD_PATH = FONT_PATH.replace("malgun.ttf", "malgunbd.ttf")

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

def _load_agent_icon(agent: str, size: int) -> Image.Image | None:
    """assets/<agent>.png 를 로드해 size x size RGBA로 반환. 없으면 None."""
    path = os.path.join(_ASSETS_DIR, f"{agent}.png")
    if not os.path.exists(path):
        return None
    try:
        return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None




@dataclass
class InputEvent:
    index: int
    kind: str  # "button" | "encoder"
    action: str  # "press" | "release" | "left" | "right"
    state: int


@dataclass
class Button:
    """버튼 하나의 표시 내용."""

    title: str = ""
    sub: str = ""
    header: str = ""
    bg: str = "#1e1e2e"
    fg: str = "#ffffff"
    accent: str | None = None
    ratio: float | None = None
    bars: list[tuple] | None = None
    border: str | None = None
    header_fg: str | None = None
    raw_header: bool = False
    image: bytes | None = None
    fill: float | None = None
    fill_color: str = "#f7768e"
    footer: str = ""          # 세션 타일: 하단 상태 문구 (있으면 세션 레이아웃)
    dot: str | None = None    # 세션 타일: 헤더 앞 에이전트 색 점
    muted: str = "#a9b1d6"    # 세션 타일: 보조 글자색
    track: str = "#3a3a4a"    # 세션 타일: 컨텍스트 바 바탕색
    agent: str = ""           # 한도 타일: 에이전트 로고 아이콘 ("claude"|"codex"|"agy")


def _draw_agent_icon(d: ImageDraw.ImageDraw, agent: str, cx: float, cy: float, r: float, color: str) -> None:
    """에이전트별 로고 아이콘을 PIL로 그린다."""
    if agent == "claude":
        # Claude 꽃잎 별표 (6개 타원 회전)
        from PIL import Image as _Img
        overlay = _Img.new("RGBA", (ICON, ICON), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        rw, rh = int(r * 0.32), int(r)
        for i in range(6):
            angle = math.radians(i * 60)
            petal = _Img.new("RGBA", (ICON, ICON), (0, 0, 0, 0))
            pd = ImageDraw.Draw(petal)
            pd.ellipse((cx - rw, cy - rh, cx + rw, cy + rh), fill=color)
            petal = petal.rotate(math.degrees(angle), center=(cx, cy))
            overlay = _Img.alpha_composite(overlay, petal)
        # 중앙 원
        od.ellipse((cx - r * 0.22, cy - r * 0.22, cx + r * 0.22, cy + r * 0.22), fill=color)
        d._image.paste(overlay, mask=overlay.split()[3])  # type: ignore[attr-defined]
    elif agent == "codex":
        # Codex: 중앙 원 + 6각 외곽선 (OpenAI 스타일 근사)
        pts = [(cx + r * math.cos(math.pi * i / 3 - math.pi / 6),
                cy + r * math.sin(math.pi * i / 3 - math.pi / 6)) for i in range(6)]
        d.polygon(pts, outline=color, width=3)
        inner = [(cx + r * 0.55 * math.cos(math.pi * i / 3 - math.pi / 6),
                  cy + r * 0.55 * math.sin(math.pi * i / 3 - math.pi / 6)) for i in range(6)]
        d.polygon(inner, fill=color)
    elif agent == "agy":
        # AGY: Gemini 스타일 — 위아래로 긴 별 (4각)
        pts_outer = [
            (cx, cy - r), (cx + r * 0.28, cy - r * 0.28),
            (cx + r * 0.6, cy), (cx + r * 0.28, cy + r * 0.28),
            (cx, cy + r), (cx - r * 0.28, cy + r * 0.28),
            (cx - r * 0.6, cy), (cx - r * 0.28, cy - r * 0.28),
        ]
        d.polygon(pts_outer, fill=color)


def _fit(d: ImageDraw.ImageDraw, text: str, size: int, max_w: int,
         path: str = FONT_PATH) -> ImageFont.FreeTypeFont:
    while size > 12:
        f = ImageFont.truetype(path, size)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return ImageFont.truetype(path, size)


def _clip(d: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    """폭을 넘으면 말줄임."""
    if d.textlength(text, font=font) <= max_w:
        return text
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _render_session(d: ImageDraw.ImageDraw, btn: Button) -> None:
    """세션 타일: 프로젝트 / 제목 / 모델 / 컨텍스트 바 / 상태."""
    pad, w = 16, ICON - 32
    x = pad
    if btn.dot:
        d.ellipse((pad, 18, pad + 8, 26), fill=btn.dot)
        x += 16
    head = ImageFont.truetype(BOLD_PATH, 18)
    d.text((x, 22), _clip(d, btn.header, head, ICON - pad - x), font=head,
           fill=btn.header_fg or btn.fg, anchor="lm")
    if btn.title:
        f = _fit(d, btn.title, 30, w, BOLD_PATH)
        if f.size < 24:  # 너무 작아지면 24 로 두고 말줄임
            f = ImageFont.truetype(BOLD_PATH, 24)
        d.text((ICON / 2, 64), _clip(d, btn.title, f, w), font=f, fill=btn.fg, anchor="mm")
    if btn.sub:
        f = ImageFont.truetype(FONT_PATH, 17)
        d.text((ICON / 2, 98), _clip(d, btn.sub, f, w), font=f, fill=btn.muted, anchor="mm")
    if btn.ratio is not None:
        r = max(0.0, min(1.0, btn.ratio))
        f = ImageFont.truetype(FONT_PATH, 15)
        d.text((pad, 126), "컨텍스트", font=f, fill=btn.muted, anchor="lm")
        d.text((ICON - pad, 126), f"{r * 100:.0f}%", font=f, fill=btn.fg, anchor="rm")
        d.rounded_rectangle((pad, 138, ICON - pad, 146), 4, fill=btn.track)
        if r > 0:
            d.rounded_rectangle((pad, 138, pad + w * r, 146), 4, fill=btn.accent or btn.fg)
    if btn.footer:
        f = _fit(d, btn.footer, 20, w, BOLD_PATH)
        d.text((ICON / 2, 172), btn.footer, font=f, fill=btn.fg, anchor="mm")


def _render_bars(d: ImageDraw.ImageDraw, btn: Button) -> None:
    tiny = ImageFont.truetype(FONT_PATH, 22)
    lab = ImageFont.truetype(FONT_PATH, 19)
    if btn.agent:
        # 왼쪽 아이콘 + 오른쪽 텍스트
        icon_size, ix, iy = 54, 10, 10
        icon_img = _load_agent_icon(btn.agent, icon_size)
        if icon_img is not None:
            d._image.paste(icon_img, (ix, iy), mask=icon_img.split()[3])  # type: ignore[attr-defined]
        else:
            _draw_agent_icon(d, btn.agent, ix + icon_size // 2, iy + icon_size // 2,
                             icon_size // 2, btn.accent or btn.fg)
        text_x = ix + icon_size + 8
        if btn.header:
            d.text((text_x, iy + icon_size // 2), btn.header,
                   font=_fit(d, btn.header, 22, ICON - text_x - 10), fill=btn.accent or btn.fg, anchor="lm")
    elif btn.header:
        d.text((ICON / 2, 24), btn.header, font=_fit(d, btn.header, 22, ICON - 20), fill=btn.accent or btn.fg, anchor="mm")
    if btn.title:
        big = ImageFont.truetype(FONT_PATH, 46 if len(btn.title) <= 4 else 34)
        title_y = 102 if btn.agent else 70
        d.text((ICON / 2, title_y), btn.title, font=big, fill=btn.fg, anchor="mm")
        y, step = (138 if btn.agent else 112), 26
    else:
        y, step = 54, 36
    label_w = max((d.textlength(b[0], font=lab) for b in btn.bars or []), default=0)
    for bar in btn.bars or []:
        label, ratio, color = bar[:3]
        value = bar[3] if len(bar) > 3 else None
        d.text((12, y + 7), label, font=lab, fill="#a9b1d6", anchor="lm")
        x0, x1 = int(12 + label_w + 8), ICON - (68 if value else 14)
        d.rounded_rectangle((x0, y, x1, y + 14), 7, fill="#3a3a4a")
        if ratio is not None and ratio > 0:
            d.rounded_rectangle((x0, y, x0 + (x1 - x0) * min(1.0, ratio), y + 14), 7, fill=color)
        if value:
            d.text((ICON - 10, y + 7), value, font=lab, fill="#c0caf5", anchor="rm")
        y += step
    if btn.sub:
        d.text((ICON / 2, min(y + 10, ICON - 16)), btn.sub, font=lab, fill="#a9b1d6", anchor="mm")


def render_icon(btn: Button) -> bytes:
    if btn.image is not None:
        return btn.image
    img = Image.new("RGB", (ICON, ICON), btn.bg)
    d = ImageDraw.Draw(img)
    if btn.fill is not None and btn.fill > 0:
        top = int(ICON * (1 - min(1.0, btn.fill)))
        d.rectangle((0, top, ICON, ICON), fill=btn.fill_color)
    if btn.border:
        d.rounded_rectangle((2, 2, ICON - 3, ICON - 3), 18, outline=btn.border, width=5)
    if btn.footer:
        _render_session(d, btn)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    if btn.bars:
        _render_bars(d, btn)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    big = ImageFont.truetype(FONT_PATH, 52 if len(btn.title) <= 5 else 34)
    small = ImageFont.truetype(FONT_PATH, 24)
    if btn.header:
        d.text((ICON / 2, 26), btn.header, font=_fit(d, btn.header, 22, ICON - 20), fill=btn.header_fg or btn.accent or btn.fg, anchor="mm")
    d.text((ICON / 2, 84), btn.title, font=_fit(d, btn.title, big.size, ICON - 16), fill=btn.fg, anchor="mm")
    if btn.sub:
        d.text((ICON / 2, 134), btn.sub, font=_fit(d, btn.sub, small.size, ICON - 14), fill="#a9b1d6" if btn.fg == "#ffffff" else btn.fg, anchor="mm")
    if btn.ratio is not None:
        r = max(0.0, min(1.0, btn.ratio))
        d.rounded_rectangle((16, 160, ICON - 16, 176), 8, fill="#3a3a4a")
        if r > 0:
            d.rounded_rectangle((16, 160, 16 + (ICON - 32) * r, 176), 8, fill=btn.accent or "#7aa2f7")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _frame(cmd: int, length: int, data: bytes) -> bytes:
    if len(data) > PACKET - HEADER:
        raise ValueError("data too large for one frame")
    head = b"\x7c\x7c" + cmd.to_bytes(2, "big") + length.to_bytes(4, "little")
    return (head + data).ljust(PACKET, b"\x00")


def _payload_safe(payload: bytes) -> bool:
    return all(payload[i] not in (0x00, 0x7C) for i in range(PACKET - HEADER, len(payload), PACKET))


def build_zip(buttons: dict[int, Button], small_png: bytes | None = None) -> bytes:
    icons: dict[str, bytes] = {}
    manifest: dict[str, dict] = {}
    for idx, btn in buttons.items():
        col, row = idx % COLS, idx // COLS
        png = render_icon(btn)
        name = f"Images/{hashlib.sha1(png).hexdigest()[:16]}.png"
        icons[name] = png
        manifest[f"{col}_{row}"] = {"State": 0, "ViewParam": [{"Icon": name, "Text": ""}]}
    if small_png:
        name = f"Images/{hashlib.sha1(small_png).hexdigest()[:16]}.png"
        icons[name] = small_png
        manifest["3_2"] = {"State": 0, "SmallViewMode": SMALL_BACKGROUND, "ViewParam": [{"Icon": name, "Text": ""}]}

    dummy = ""
    for _ in range(200):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            if dummy:
                z.writestr("dummy.txt", dummy)
            z.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
            for name, data in icons.items():
                z.writestr(name, data)
        payload = buf.getvalue()
        if _payload_safe(payload):
            return payload
        dummy += "".join(random.choices(string.ascii_letters, k=8))
    raise RuntimeError("could not build firmware-safe zip")


class D200:
    def __init__(self) -> None:
        info = next((d for d in hid.enumerate(VID, PID) if d["interface_number"] == 0), None)
        if not info:
            raise RuntimeError("D200 을 찾지 못했어요 (USB 연결과 Ulanzi Studio 종료를 확인하세요)")
        self.dev = hid.device()
        self.dev.open_path(info["path"])
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.disconnected = threading.Event()
        self._small = "1|0|0|00:00:00|0"
        self.on_event: Callable[[InputEvent], None] | None = None
        self.on_info: Callable[[str], None] | None = None

    def _write(self, packets: list[bytes]) -> None:
        with self._lock:
            for p in packets:
                self.dev.write(b"\x00" + p)

    def send(self, cmd: int, data: bytes) -> None:
        self._write([_frame(cmd, len(data), data)])

    def set_brightness(self, percent: int) -> None:
        self.send(CMD_BRIGHTNESS, str(max(0, min(100, percent))).encode())

    def set_small_window(self, mode: int = 1, cpu: int = 0, mem: int = 0, gpu: int = 0) -> None:
        self._small = f"{mode}|{cpu}|{mem}|{time.strftime('%H:%M:%S')}|{gpu}"
        self.send(CMD_SMALL_WINDOW, self._small.encode())

    def set_buttons(self, buttons: dict[int, Button], partial: bool = False, small_png: bytes | None = None) -> None:
        payload = build_zip(buttons, small_png)
        cmd = CMD_PARTIAL_UPDATE if partial else CMD_SET_BUTTONS
        first = payload[: PACKET - HEADER]
        packets = [_frame(cmd, len(payload), first)]
        for off in range(PACKET - HEADER, len(payload), PACKET):
            packets.append(payload[off : off + PACKET].ljust(PACKET, b"\x00"))
        self._write(packets)

    def _parse(self, r: bytes) -> None:
        if len(r) < HEADER or r[0] != 0x7C or r[1] != 0x7C:
            return
        cmd = int.from_bytes(r[2:4], "big")
        length = int.from_bytes(r[4:8], "little")
        data = r[HEADER : HEADER + length]
        if cmd == IN_BUTTON and len(data) >= 4:
            kind = "encoder" if data[2] == 0x02 else "button"
            action = {0x01: "press", 0x02: "left", 0x03: "right"}.get(data[3], "release")
            if self.on_event:
                self.on_event(InputEvent(data[1], kind, action, data[0]))
        elif cmd == IN_DEVICE_INFO and self.on_info:
            self.on_info(data.split(b"\x00")[0].decode("ascii", "replace"))

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                r = self.dev.read(PACKET, 100)
            except (OSError, ValueError):
                self.disconnected.set()
                self._stop.set()
                return
            if r:
                self._parse(bytes(r))

    def _keepalive_loop(self) -> None:
        while not self._stop.wait(5):
            mode = int(self._small.split("|")[0])
            self.set_small_window(mode)

    def start(self) -> None:
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._keepalive_loop, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        time.sleep(0.2)
        self.dev.close()
