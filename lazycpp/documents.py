"""读取 Word/TXT 文档并提取 C++ 代码。"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_document(path: str | Path) -> str:
    """读取 .docx / .txt / .md 等文本文件为纯文本。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".doc":
        raise ValueError("暂不支持旧版 .doc，请先在 Word 里另存为 .docx。")
    if suffix in {".txt", ".md", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".log", ".json"}:
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        raise ValueError("暂不支持直接读 PDF。请把题目文字复制到输入框，或另存为 Word/TXT。")
    raise ValueError(f"不支持的文件类型：{suffix or '未知'}。请选择 .docx / .txt / .md。")


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("这个 .docx 文件无法读取，可能已经损坏。") from exc
    root = ET.fromstring(xml)
    body = root.find(f"{_W}body")
    if body is None:
        return ""
    blocks: list[str] = []
    for child in body:
        if child.tag == f"{_W}p":
            text = _paragraph_text(child)
            if text.strip():
                blocks.append(text.rstrip())
        elif child.tag == f"{_W}tbl":
            rows: list[str] = []
            for row in child.findall(f".//{_W}tr"):
                cells: list[str] = []
                for cell in row.findall(f"{_W}tc"):
                    cell_parts = [_paragraph_text(p) for p in cell.findall(f"{_W}p")]
                    cell_text = " ".join(p.strip() for p in cell_parts if p.strip())
                    cells.append(cell_text)
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append("\n".join(rows))
    return "\n\n".join(blocks).strip()


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag in {f"{_W}br", f"{_W}cr"}:
            parts.append("\n")
    return "".join(parts)


def clean_doc_text(text: str, limit: int = 80000) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(text) > limit:
        text = text[:limit] + "\n\n……（文档过长，后续内容已截断）"
    return text.strip()


_FENCE_RE = re.compile(
    r"```[ \t]*(?:cpp|c\+\+|cc|cxx|C\+\+)?[ \t]*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_INCLUDE_RE = re.compile(r"^\s*#\s*include\s*[<\"]", re.MULTILINE)


def split_code_locally(text: str) -> list[dict[str, str]]:
    """不调用 AI 的保底拆分：优先 Markdown 代码块，其次按 #include 分段。"""
    text = clean_doc_text(text)
    matches = list(_FENCE_RE.finditer(text))
    blocks: list[dict[str, str]] = []
    if matches:
        for idx, match in enumerate(matches, 1):
            code = match.group(1).strip()
            if not code:
                continue
            before = text[:match.start()].strip().splitlines()
            title = ""
            for line in reversed(before[-4:]):
                stripped = line.strip().strip("#*`- ")
                if stripped:
                    title = stripped[:60]
                    break
            blocks.append({"title": title or f"文档代码 {idx}", "code": code})
        if blocks:
            return blocks

    # 无代码围栏：从每个 #include 开始，切到下一个 #include 之前。
    starts = [m.start() for m in _INCLUDE_RE.finditer(text)]
    if len(starts) <= 1:
        return []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(text)
        segment = text[start:end].strip()
        if not segment:
            continue
        # 去掉下一段标题之类的明显尾部散文（仅去掉最后一段无分号/大括号的短行）。
        lines = segment.splitlines()
        while lines:
            last = lines[-1].strip()
            if last and len(last) < 40 and not re.search(r"[;{}()#]|\w+\s*\(", last):
                lines.pop()
            else:
                break
        segment = "\n".join(lines).strip()
        if re.search(r"\bint\s+main\s*\(", segment):
            blocks.append({"title": f"文档代码 {idx + 1}", "code": segment})
    return blocks


def extract_single_code(text: str) -> str:
    """从 AI 回应或文档中尽量取出一个完整 C++ 程序。"""
    fenced = _FENCE_RE.search(text or "")
    if fenced:
        return fenced.group(1).strip()
    stripped = (text or "").strip()
    start = stripped.find("#include")
    if start >= 0:
        return stripped[start:].strip()
    return stripped


def looks_like_cpp(code: str) -> bool:
    return bool(re.search(r"#\s*include|\bint\s+main\s*\(|\bstd::", code or ""))
