"""可选 AI 助手：OpenAI 兼容接口，无第三方依赖。"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import AppConfig
from .documents import extract_single_code, looks_like_cpp
from .models import CodeItem


class AIError(RuntimeError):
    pass


@dataclass
class AIConfig:
    base: str
    key: str
    model: str

    @classmethod
    def from_app_config(cls, config: AppConfig) -> "AIConfig":
        return cls(base=config.api_base.strip(), key=config.api_key.strip(), model=config.api_model.strip())

    @property
    def configured(self) -> bool:
        return bool(self.key and self.base and self.model)


def _endpoint(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    if not base:
        base = "https://api.deepseek.com/v1"
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def chat(config: AIConfig, messages: list[dict[str, str]], *, max_tokens: int = 4096,
         temperature: float = 0.2, timeout: int = 120) -> str:
    if not config.key:
        raise AIError("还没有配置 API Key。请点右侧的「AI 设置」填入 Key。")
    payload = {
        "model": config.model or "deepseek-chat",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    request = urllib.request.Request(
        _endpoint(config.base),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.key}",
            "User-Agent": "CppShot/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        message = body
        try:
            data = json.loads(body)
            message = data.get("error", {}).get("message") or body
        except Exception:
            pass
        if exc.code in (401, 403):
            raise AIError(f"API Key 无效或没有权限（HTTP {exc.code}）。\n{message[:800]}") from exc
        if exc.code == 404:
            raise AIError(f"接口地址或模型名不正确（HTTP 404）。\n请检查 API 地址是否以 /v1 结尾。\n{message[:800]}") from exc
        raise AIError(f"AI 请求失败（HTTP {exc.code}）：\n{message[:1200]}") from exc
    except urllib.error.URLError as exc:
        raise AIError(f"无法连接 AI 服务：{exc.reason}\n请检查网络、代理和 API 地址。") from exc
    except TimeoutError as exc:
        raise AIError("AI 请求超时，请稍后再试。") from exc
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):  # 某些兼容接口返回分段内容
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content or "").strip()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AIError("AI 返回了无法识别的数据格式，请检查接口地址是否为 OpenAI 兼容接口。") from exc


def test_connection(config: AIConfig) -> str:
    reply = chat(
        config,
        [
            {"role": "system", "content": "你是连通性测试助手。"},
            {"role": "user", "content": "只回复：连接成功"},
        ],
        max_tokens=16,
        temperature=0,
        timeout=30,
    )
    return reply or "连接成功"


def ai_diagnose(config: AIConfig, item: CodeItem, last_error: str = "") -> str:
    context = f"""请以 C++ 大一助教的身份检查下面这份代码。

要求：
1. 先用「能否通过编译」「主要问题」「修改建议」三部分回答。
2. 解释要适合初学者，指出具体行号和原因。
3. 如果有错误，请在最后给出一份完整、可直接编译运行的修改版代码（用 ```cpp 代码块）。
4. 不要编造不存在的编译错误。

文件名：{item.name}
运行输入（stdin）：
{item.stdin or '（无）'}

代码：
```cpp
{item.code}
```
"""
    if last_error:
        context += f"\n最近一次编译/运行错误：\n```\n{last_error[:6000]}\n```\n"
    return chat(config, [
        {"role": "system", "content": "你是一位耐心、严谨的 C++ 入门课助教。"},
        {"role": "user", "content": context},
    ], max_tokens=4096)


def ai_generate_code(config: AIConfig, requirement: str) -> str:
    if not requirement.strip():
        raise AIError("题目/需求不能为空。")
    prompt = f"""根据下面的 C++ 作业要求，生成一份完整的 C++ 程序。

要求：
- 只返回一个 ```cpp 代码块，不要解释。
- 代码必须包含完整的 #include、main 函数，能直接编译运行。
- 使用适合中国大一 C++ 课程的写法，避免过于冷门的库。
- 如果题目有输入，请在代码注释里说明输入格式。

作业要求：
{requirement[:30000]}
"""
    reply = chat(config, [
        {"role": "system", "content": "你是严谨的 C++ 课程作业助手，只输出可编译的完整代码。"},
        {"role": "user", "content": prompt},
    ], max_tokens=6000, temperature=0.2)
    code = extract_single_code(reply)
    if not looks_like_cpp(code):
        raise AIError("AI 返回的内容不像一份完整 C++ 代码，请重试或换一个模型。")
    return code


def _parse_json_array(text: str) -> list[dict] | None:
    clean = (text or "").strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    candidates = [clean]
    start_positions = [m.start() for m in re.finditer(r"\[", clean)]
    for start in start_positions[:8]:
        candidates.append(clean[start:])
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
        try:
            data, _end = decoder.raw_decode(candidate)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            continue
    return None


def ai_split_document(config: AIConfig, text: str, source_name: str = "") -> list[dict[str, str]]:
    if not text.strip():
        raise AIError("文档内容为空。")
    prompt = f"""下面是一份 C++ 作业/笔记文档。请把其中包含的每一个独立 C++ 程序依次提取出来。

要求：
- 严格只返回 JSON 数组，不要 Markdown 围栏，不要任何解释。
- 数组每一项是：{{"title": "这段代码的题目或简短标题", "code": "完整C++代码"}}。
- 每段 code 必须是可以独立编译的完整程序（有 include 和 main）；不要漏掉、不要合并。
- 如果文档里只有一段代码，也返回只有一项的数组。
- 如果没有代码，返回空数组 []。

文档名：{source_name or '未命名'}
文档正文：
{text[:60000]}
"""
    reply = chat(config, [
        {"role": "system", "content": "你擅长从中文教学文档中精确提取多段 C++ 源码，并只输出合法 JSON。"},
        {"role": "user", "content": prompt},
    ], max_tokens=8000, temperature=0)
    data = _parse_json_array(reply)
    if data is None:
        # AI 没按 JSON 回时，退化到本地代码块提取。
        from .documents import split_code_locally
        fallback = split_code_locally(reply)
        if fallback:
            return fallback
        raise AIError("AI 返回的内容无法解析成代码列表，请重试，或改用「本地拆分」。")
    results: list[dict[str, str]] = []
    for idx, entry in enumerate(data, 1):
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code", "")).strip()
        if not code:
            continue
        code = extract_single_code(code)
        title = str(entry.get("title", "")).strip() or f"文档代码 {idx}"
        if looks_like_cpp(code):
            results.append({"title": title[:80], "code": code})
    if not results:
        raise AIError("AI 没有从文档中找到完整 C++ 代码。")
    return results
