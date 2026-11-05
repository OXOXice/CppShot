"""CppShot 核心测试（不依赖 GUI 和网络）。"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lazycpp import __version__  # noqa: E402
from lazycpp.ai import AIConfig, AIError, ai_generate_code, ai_split_document, chat, test_connection  # noqa: E402
from lazycpp.config import AppConfig, Store  # noqa: E402
from lazycpp.documents import (  # noqa: E402
    extract_single_code,
    looks_like_cpp,
    read_document,
    split_code_locally,
)
from lazycpp.imaging import save_screenshot  # noqa: E402
from lazycpp.models import CodeItem, safe_filename, unique_name  # noqa: E402
from lazycpp.security import protect_text, unprotect_text  # noqa: E402


SAMPLE_FENCED = """# 第一题
```cpp
#include <iostream>
int main() { return 0; }
```

# 第二题
```cpp
#include <iostream>
int main() { std::cout << "hi"; return 0; }
```
"""

SAMPLE_PLAIN = """第一题 求和
#include <iostream>
int main() { int a, b; std::cin >> a >> b; return 0; }

第二题 输出
#include <iostream>
int main() { std::cout << "***"; return 0; }
"""


class TestModels(unittest.TestCase):
    def test_unique_name(self) -> None:
        self.assertEqual(unique_name(["a"], "a"), "a (2)")
        self.assertEqual(unique_name(["a", "a (2)"], "a"), "a (3)")

    def test_safe_filename(self) -> None:
        self.assertEqual(safe_filename('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")
        self.assertEqual(safe_filename("  . "), "program")

    def test_item_roundtrip(self) -> None:
        item = CodeItem(name="demo", code="int main(){}", stdin="1 2", status="success")
        restored = CodeItem.from_dict(item.to_dict())
        self.assertEqual(restored.name, "demo")
        self.assertEqual(restored.stdin, "1 2")
        self.assertEqual(restored.status, "pending")  # 状态不跨会话沿用


class TestDocuments(unittest.TestCase):
    def test_split_fenced(self) -> None:
        blocks = split_code_locally(SAMPLE_FENCED)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["title"], "第一题")
        self.assertTrue(looks_like_cpp(blocks[1]["code"]))

    def test_split_plain(self) -> None:
        blocks = split_code_locally(SAMPLE_PLAIN)
        self.assertEqual(len(blocks), 2)
        self.assertTrue(blocks[0]["code"].startswith("#include"))
        self.assertIn("std::cin", blocks[0]["code"])

    def test_extract_single(self) -> None:
        reply = "说明\n```cpp\n#include <iostream>\nint main(){}\n```\n结束"
        self.assertEqual(extract_single_code(reply), "#include <iostream>\nint main(){}")

    def test_read_txt_gbk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.txt"
            path.write_bytes("中文题目".encode("gb18030"))
            self.assertEqual(read_document(path), "中文题目")

    def test_read_docx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.docx"
            document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>第一题</w:t></w:r></w:p>
    <w:p><w:r><w:t>#include &lt;iostream&gt;</w:t></w:r></w:p>
  </w:body>
</w:document>"""
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("word/document.xml", document_xml)
            text = read_document(path)
            self.assertIn("第一题", text)
            self.assertIn("#include <iostream>", text)

    def test_unsupported_legacy_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.doc"
            path.write_bytes(b"x")
            with self.assertRaises(ValueError):
                read_document(path)


class TestSecurityAndConfig(unittest.TestCase):
    def test_protect_roundtrip(self) -> None:
        secret = "sk-test-中文-123"
        protected = protect_text(secret)
        self.assertNotEqual(protected, secret)
        self.assertEqual(unprotect_text(protected), secret)
        self.assertNotIn(secret, protected)

    def test_store_does_not_contain_raw_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            config = AppConfig(save_dir=str(Path(tmp) / "shots"))
            config.api_key = "sk-very-secret"
            store.save_config(config)
            raw = store.config_path.read_text(encoding="utf-8")
            self.assertNotIn("sk-very-secret", raw)
            loaded = store.load_config()
            self.assertEqual(loaded.api_key, "sk-very-secret")

    def test_session_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            items = [CodeItem(name="a", code="int main(){}", stdin="42")]
            store.save_session(items, 0)
            loaded, selected = store.load_session()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].name, "a")
            self.assertEqual(loaded[0].stdin, "42")
            self.assertEqual(selected, 0)


class TestImaging(unittest.TestCase):
    def test_render_screenshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            item = CodeItem(name="中文测试", code="")
            path = save_screenshot(item, "你好，C++!\n第二行\n", tmp, "Test Compiler")
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 5000)
            from PIL import Image
            with Image.open(path) as image:
                self.assertGreater(image.width, 800)
                self.assertGreater(image.height, 300)


class _MockAIHandler(http.server.BaseHTTPRequestHandler):
    reply = "连接成功"
    requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(body)
        payload = {"choices": [{"message": {"content": self.__class__.reply}}]}
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args) -> None:
        pass


class TestAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), _MockAIHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.config = AIConfig(f"http://127.0.0.1:{cls.port}/v1", "sk-test", "mock-model")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        _MockAIHandler.requests.clear()

    def test_chat(self) -> None:
        _MockAIHandler.reply = "连接成功"
        self.assertEqual(chat(self.config, [{"role": "user", "content": "hi"}]), "连接成功")
        self.assertEqual(_MockAIHandler.requests[0]["model"], "mock-model")
        self.assertTrue(_MockAIHandler.requests[0]["messages"])

    def test_test_connection(self) -> None:
        _MockAIHandler.reply = "连接成功"
        self.assertIn("连接", test_connection(self.config))

    def test_generate_code(self) -> None:
        _MockAIHandler.reply = '```cpp\n#include <iostream>\nint main(){ return 0; }\n```'
        code = ai_generate_code(self.config, "输出一个程序")
        self.assertTrue(looks_like_cpp(code))
        self.assertNotIn("```", code)

    def test_split_document(self) -> None:
        _MockAIHandler.reply = json.dumps([
            {"title": "A", "code": "#include <iostream>\nint main(){return 0;}"},
            {"title": "B", "code": "#include <iostream>\nint main(){std::cout<<1;return 0;}"},
        ], ensure_ascii=False)
        blocks = ai_split_document(self.config, "文档内容")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["title"], "A")

    def test_missing_key(self) -> None:
        with self.assertRaises(AIError):
            chat(AIConfig("https://example.com/v1", "", "m"), [{"role": "user", "content": "x"}])


class TestToolchainOptional(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("CPPSHOT_RUN_COMPILER_TEST") == "1",
                         "设置 CPPSHOT_RUN_COMPILER_TEST=1 才运行真实编译测试")
    def test_compile_and_run(self) -> None:
        from lazycpp.toolchain import compile_and_run, detect_compilers, select_compiler
        compilers = detect_compilers()
        compiler = select_compiler(compilers, "auto")
        if compiler is None:
            self.skipTest("没有可用编译器")
        item = CodeItem(name="test", code='#include <iostream>\nint main(){std::cout<<"ok";return 0;}')
        outcome = compile_and_run(item, compiler, 20)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertIn("ok", outcome.output)


if __name__ == "__main__":
    unittest.main()
