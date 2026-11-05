"""应用配置与会话持久化。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import CodeItem, DEFAULT_CODE
from .security import protect_text, unprotect_text


APP_DIR_NAME = "CppShot"


def app_data_dir() -> Path:
    override = os.environ.get("CPPSHOT_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        path = Path(base) / APP_DIR_NAME if base else Path.home() / f".{APP_DIR_NAME.lower()}"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    else:
        base = os.environ.get("XDG_DATA_HOME")
        path = Path(base) / APP_DIR_NAME if base else Path.home() / ".local" / "share" / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_screenshot_dir() -> Path:
    override = os.environ.get("CPPSHOT_SCREENSHOT_DIR")
    if override:
        return Path(override)
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.home()
    return desktop / "C++运行截图"


@dataclass
class AppConfig:
    save_dir: str = field(default_factory=lambda: str(default_screenshot_dir()))
    compiler_id: str = "auto"
    timeout_sec: int = 15
    open_folder_after: bool = False
    api_base: str = "https://api.deepseek.com/v1"
    api_model: str = "deepseek-chat"
    api_key_enc: str = ""
    last_screenshot: str = ""
    window_geometry: str = ""

    @property
    def api_key(self) -> str:
        return unprotect_text(self.api_key_enc)

    @api_key.setter
    def api_key(self, value: str) -> None:
        self.api_key_enc = protect_text(value.strip())

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("api_key_enc", None)
        return data


class Store:
    """配置文件 + 自动保存的代码队列。"""

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or app_data_dir()
        self.base.mkdir(parents=True, exist_ok=True)
        self.config_path = self.base / "config.json"
        self.session_path = self.base / "session.json"

    # ---------- config ----------
    def load_config(self) -> AppConfig:
        config = AppConfig()
        if self.config_path.exists():
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
                for key, value in raw.items():
                    if hasattr(config, key) and key != "api_key":
                        setattr(config, key, value)
            except Exception:
                pass
        try:
            config.timeout_sec = max(1, min(300, int(config.timeout_sec)))
        except Exception:
            config.timeout_sec = 15
        if not config.api_base:
            config.api_base = "https://api.deepseek.com/v1"
        if not config.api_model:
            config.api_model = "deepseek-chat"
        if not config.save_dir:
            config.save_dir = str(default_screenshot_dir())
        return config

    def save_config(self, config: AppConfig) -> None:
        data = asdict(config)
        self._atomic_json(self.config_path, data)

    # ---------- session ----------
    def load_session(self) -> tuple[list[CodeItem], int]:
        items: list[CodeItem] = []
        index = 0
        if not self.session_path.exists():
            return items, index
        try:
            raw = json.loads(self.session_path.read_text(encoding="utf-8"))
            for entry in raw.get("items", []):
                if isinstance(entry, dict):
                    try:
                        items.append(CodeItem.from_dict(entry))
                    except Exception:
                        continue
            index = max(0, min(int(raw.get("selected", 0)), max(0, len(items) - 1)))
        except Exception:
            return [], 0
        return items, index

    def save_session(self, items: list[CodeItem], selected: int) -> None:
        payload = {
            "version": 1,
            "selected": selected,
            "items": [item.to_dict() for item in items],
        }
        self._atomic_json(self.session_path, payload)

    @staticmethod
    def _atomic_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass


def initial_items() -> list[CodeItem]:
    return [
        CodeItem(name="示例：Hello C++", code=DEFAULT_CODE),
    ]
