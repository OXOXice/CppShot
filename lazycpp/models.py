"""运行队列数据模型。"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

STATUS_LABELS = {
    "pending": "待运行",
    "running": "运行中",
    "success": "成功",
    "failed": "失败",
}

STATUS_ICONS = {
    "pending": "○",
    "running": "◌",
    "success": "✓",
    "failed": "✕",
}

DEFAULT_CODE = """#include <iostream>
using namespace std;

int main() {
    cout << "Hello, C++!" << endl;
    return 0;
}
"""


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def safe_filename(name: str, fallback: str = "program") -> str:
    """把用户可编辑的名称变成安全的文件名（保留中文）。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "").strip().strip(".")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:80] or fallback


def unique_name(existing: list[str], base: str) -> str:
    base = (base or "代码").strip() or "代码"
    if base not in existing:
        return base
    n = 2
    while f"{base} ({n})" in existing:
        n += 1
    return f"{base} ({n})"


@dataclass
class CodeItem:
    name: str
    code: str = DEFAULT_CODE
    stdin: str = ""
    source_path: str | None = None
    id: str = field(default_factory=new_id)
    status: str = "pending"
    last_exit_code: int | None = None
    last_image: str | None = None
    last_error: str = ""
    last_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CodeItem":
        allowed = {
            "name",
            "code",
            "stdin",
            "source_path",
            "id",
            "status",
            "last_exit_code",
            "last_image",
            "last_error",
            "last_output",
        }
        values = {k: v for k, v in data.items() if k in allowed}
        values.setdefault("name", "未命名")
        values.setdefault("code", DEFAULT_CODE)
        if not isinstance(values.get("code"), str):
            values["code"] = DEFAULT_CODE
        if not isinstance(values.get("stdin"), str):
            values["stdin"] = ""
        # 上次运行状态不跨会话恢复，避免误导。
        values["status"] = "pending"
        return cls(**values)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def status_icon(self) -> str:
        return STATUS_ICONS.get(self.status, "?")
