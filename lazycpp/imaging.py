"""把真实的运行结果渲染成高保真 CMD 截图。"""

from __future__ import annotations

import getpass
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import CodeItem, safe_filename

# 输出里可能带 ANSI 颜色，截图中不需要。
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _font_candidates(preferred: str) -> list[Path]:
    if sys.platform == "win32":
        fonts = Path(r"C:\Windows\Fonts")
        mapping = {
            "mono": ["consola.ttf", "CascadiaMono.ttf", "simsun.ttc"],
            "mono_bold": ["consolab.ttf", "CascadiaMono-Bold.ttf", "simsun.ttc"],
            "cjk": ["msyh.ttc", "simhei.ttf", "simsun.ttc"],
        }
        return [fonts / name for name in mapping.get(preferred, ["consola.ttf"])]
    if sys.platform == "darwin":
        candidates = {
            "mono": ["/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf",
                     "/System/Library/Fonts/Monaco.ttf"],
            "mono_bold": ["/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf"],
            "cjk": ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
                    "/System/Library/Fonts/STHeiti Medium.ttc"],
        }
    else:
        candidates = {
            "mono": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                     "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                     "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf"],
            "mono_bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                          "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"],
            "cjk": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                    "/usr/share/fonts/truetype/arphic/uming.ttc",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
        }
    return [Path(name) for name in candidates.get(preferred, candidates["mono"])]


def _load_font(preferred: str, size: int) -> ImageFont.FreeTypeFont:
    for path in _font_candidates(preferred):
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _clean_output(text: str) -> str:
    text = _ANSI_RE.sub("", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text)
    # 统一用空格显示 tab，避免截图中宽度不稳定。
    text = text.expandtabs(8)
    # 合并超过 4 个连续空行，截图更紧凑，但不删普通空行。
    text = re.sub(r"\n{5,}", "\n\n\n", text)
    return text


def _wrap_cells(line: str, max_cells: int) -> list[str]:
    """按控制台字符格宽度换行（中文占 2 格）。"""
    if line == "":
        return [""]
    rows: list[list[str]] = []
    current: list[str] = []
    cells = 0
    for ch in line:
        width = 2 if _is_wide(ch) else 1
        if cells + width > max_cells and current:
            rows.append(current)
            current = []
            cells = 0
        current.append(ch)
        cells += width
    if current:
        rows.append(current)
    return ["".join(row) for row in rows] or [""]


def _is_wide(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
        or 0x20000 <= code <= 0x3FFFD
    )


class ConsolePainter:
    def __init__(self, scale: int = 2) -> None:
        self.scale = scale
        self.font_size = 19 * scale
        self.line_height = int(27 * scale)
        self.pad_x = 22 * scale
        self.pad_y = 18 * scale
        self.mono = _load_font("mono", self.font_size)
        self.mono_bold = _load_font("mono_bold", self.font_size)
        self.cjk = _load_font("cjk", self.font_size)
        try:
            self.title_font = _load_font("cjk", 12 * scale)
        except Exception:
            self.title_font = ImageFont.load_default()

    @property
    def cell_width(self) -> int:
        try:
            return max(1, int(round(self.mono.getlength("M"))))
        except Exception:
            return int(self.font_size * 0.6)

    @property
    def line_cells(self) -> int:
        return 92

    def text_width(self, text: str) -> int:
        return sum(2 if _is_wide(ch) else 1 for ch in text) * self.cell_width

    def draw_line(self, draw: ImageDraw.ImageDraw, x: int, baseline: int, text: str,
                  fill: tuple[int, int, int] = (204, 204, 204), bold: bool = False) -> None:
        cursor = x
        mono = self.mono_bold if bold else self.mono
        for ch in text:
            if _is_wide(ch):
                glyph = self.cjk
                advance = self.cell_width * 2
                draw.text((cursor + (advance - self.cjk.getlength(ch)) / 2, baseline),
                          ch, font=glyph, fill=fill, anchor="ls")
            else:
                if ch == " ":
                    cursor += self.cell_width
                    continue
                draw.text((cursor, baseline), ch, font=mono, fill=fill, anchor="ls")
                advance = self.cell_width
            cursor += advance


def _shell_context(item: CodeItem) -> tuple[str, str, str]:
    """返回 (提示符路径, 执行命令, 窗口标题)。"""
    user = getpass.getuser() or "student"
    if sys.platform == "win32":
        if item.source_path:
            base = str(Path(item.source_path).parent)
        else:
            base = rf"C:\Users\{user}\CppShot"
        return base, f"{item.name}.exe", f"C:\\Windows\\system32\\cmd.exe - {item.name}.exe"
    if item.source_path:
        base = str(Path(item.source_path).parent)
    else:
        base = str(Path.home() / "CppShot")
    host = socket.gethostname().split(".")[0] or "localhost"
    prompt = f"{user}@{host}:{base}$"
    return prompt, f"./{item.name}", f"Terminal - {item.name}"


def render_console_image(item: CodeItem, output: str, compiler_label: str = "",
                         save_path: Path | None = None) -> Image.Image:
    painter = ConsolePainter(scale=2)
    output = _clean_output(output)
    if len(output) > 12000:
        output = output[-12000:]
        output = "……（输出过长，仅保留最后部分）\n" + output

    prompt, command, title = _shell_context(item)
    first_line = f"{prompt}>{command}" if sys.platform == "win32" else f"{prompt} {command}"
    raw_lines = [
        first_line,
        *output.split("\n"),
        "",
        prompt,
    ]
    wrapped: list[tuple[str, bool]] = []
    for idx, line in enumerate(raw_lines):
        for sub in _wrap_cells(line, painter.line_cells):
            # 第一行是命令，用更亮的前景色；最后一行 prompt 也是。
            highlight = idx == 0 or (idx >= len(raw_lines) - 2 and idx < len(raw_lines))
            wrapped.append((sub, highlight))
    if len(wrapped) > 260:
        wrapped = wrapped[:250] + [("……（输出过长，截图已截断）", False)] + wrapped[-8:]

    width = int(painter.cell_width * painter.line_cells + painter.pad_x * 2)
    width = max(900, min(width, 2400))
    title_h = int(34 * painter.scale)
    body_h = painter.pad_y * 2 + painter.line_height * max(1, len(wrapped))
    body_h = max(body_h, int(170 * painter.scale))
    height = title_h + body_h

    image = Image.new("RGB", (width, height), (12, 12, 12))
    draw = ImageDraw.Draw(image)
    # 标题栏
    draw.rectangle([0, 0, width, title_h], fill=(32, 32, 32))
    draw.line([0, title_h, width, title_h], fill=(62, 62, 62), width=1)
    # 终端小图标
    icon_x = int(14 * painter.scale)
    icon_y = int(9 * painter.scale)
    icon_size = int(16 * painter.scale)
    draw.rounded_rectangle(
        [icon_x, icon_y, icon_x + icon_size, icon_y + icon_size - 2],
        radius=2, outline=(200, 200, 200), width=1,
    )
    draw.line([icon_x + 4, icon_y + 6, icon_x + 7, icon_y + 9, icon_x + 4, icon_y + 12],
              fill=(200, 200, 200), width=2)
    try:
        draw.text((int(40 * painter.scale), title_h // 2), title,
                  font=painter.title_font, fill=(225, 225, 225), anchor="lm")
    except Exception:
        pass
    # 右上角窗口按钮
    bx = width - int(98 * painter.scale)
    by = title_h // 2
    white = (215, 215, 215)
    draw.line([bx - 8, by, bx + 4, by], fill=white, width=2)
    draw.rectangle([bx + 24, by - 6, bx + 36, by + 6], outline=white, width=1)
    draw.line([bx + 58, by - 6, bx + 70, by + 6], fill=white, width=2)
    draw.line([bx + 70, by - 6, bx + 58, by + 6], fill=white, width=2)

    # 控制台内容
    x = painter.pad_x
    baseline = title_h + painter.pad_y + painter.font_size
    body_color = (204, 204, 204)
    for text, highlight in wrapped:
        color = (242, 242, 242) if highlight else body_color
        painter.draw_line(draw, x, baseline, text, fill=color)
        baseline += painter.line_height
    # 最后一行的光标
    last_text = wrapped[-1][0] if wrapped else ""
    cursor_x = x + painter.text_width(last_text) + int(2 * painter.scale)
    cursor_y = baseline - painter.line_height
    draw.rectangle(
        [cursor_x, cursor_y - painter.font_size + 4, cursor_x + painter.cell_width - 2, cursor_y],
        fill=(205, 205, 205),
    )
    # 外边框
    draw.rectangle([0, 0, width - 1, height - 1], outline=(75, 75, 75), width=1)

    # 底部极淡的水印，不影响阅读，也方便辨认批量文件。
    if compiler_label:
        try:
            small = _load_font("cjk", 10 * painter.scale)
            draw.text((width - painter.pad_x, height - int(7 * painter.scale)),
                      f"CppShot · {compiler_label}", font=small,
                      fill=(92, 98, 104), anchor="rs")
        except Exception:
            pass
    return image


def save_screenshot(item: CodeItem, output: str, save_dir: str | Path,
                    compiler_label: str = "", index: int | None = None) -> Path:
    folder = Path(save_dir)
    folder.mkdir(parents=True, exist_ok=True)
    image = render_console_image(item, output, compiler_label)
    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_filename(item.name, "program")
    prefix = f"{index:02d}_" if index is not None else ""
    path = folder / f"{prefix}{base}_{date}.png"
    suffix = 2
    while path.exists():
        path = folder / f"{prefix}{base}_{date}_{suffix}.png"
        suffix += 1
    image.save(path, format="PNG", optimize=True)
    return path


def open_path(path: str | Path) -> None:
    """在系统文件管理器中打开文件或文件夹。"""
    target = Path(path)
    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
        else:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        if target.is_file():
            subprocess.Popen(["open", "-R", str(target)])
        else:
            target.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["open", str(target)])
    else:
        if target.is_file():
            target = target.parent
        target.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(target)])
