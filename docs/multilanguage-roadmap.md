# 多语言扩展可行性分析

结论先说：**能做，而且不会因为支持 C / Python / Java 就把程序变重很多；真正会膨胀的是测试矩阵和维护成本，而不是代码体积。**

建议先把语言适配层设计好，再按“C → Python → Java”的节奏逐步加入，不必一次全上。

## 1. 当前 C++ 专用的部分

| 位置 | 当前依赖 |
| --- | --- |
| `lazycpp/toolchain.py` | 只发现 MSVC / g++，只生成 `main.cpp` 并编译成 exe |
| `lazycpp/widgets.py` | 代码高亮关键字是 C++ 的 |
| `lazycpp/models.py` | 默认代码模板是 C++ |
| `lazycpp/ui.py` | 导入文件后缀、按钮文案、运行输入不变，但文件选择固定 `.cpp` |
| `lazycpp/ai.py` | 提示词里写死了 C++ 和 `#include/main` |
| `lazycpp/documents.py` | 拆分代码的识别规则偏向 C++ |
| 截图 | 与语言无关：只关心真实 stdout/stderr 和退出码 |

核心链路（队列 → 编译 → 运行 → 错误中止 → 截图）是通用的，只有“如何处理源文件”这一段是语言相关。

## 2. 推荐架构

新增一个很薄的 `LanguageAdapter` 协议，把语言差异收进去：

```python
class LanguageAdapter(Protocol):
    id: str
    name: str
    extensions: tuple[str, ...]
    default_code: str
    syntax: str

    def detect_toolchains(self) -> list[Toolchain]: ...
    def prepare(self, item: CodeItem, workdir: Path, toolchain) -> PreparedProgram: ...
    def compile(self, prepared, timeout) -> CompileResult: ...
    def run_command(self, prepared) -> list[str]: ...
```

然后：

- `CodeItem` 增加一个 `language: str = "cpp"` 字段；
- `lang_registry` 注册 `cpp`、`c`、`python`、`java` 适配器；
- UI 加一个语言下拉框，队列项显示语言徽标；
- AI 提示词接收 `language_name` 参数；
- 截图部分完全复用；
- 失败中止、退出码、超时、截图命名完全复用。

适配器只写“差异”，不复制主流程。这样主窗口和批量运行逻辑不会被语言分支污染。

## 3. 各语言工作量与风险

| 语言 | 工作量 | 主要工作 | 风险 |
| --- | --- | --- | --- |
| C | 极低（约 0.5 天） | 复用 gcc/clang/MSVC；改扩展名、模板、高亮、编译参数 | 几乎没有 |
| Python | 低（约 1 天） | 查找解释器；用 `py_compile` 做语法检查；运行 `python -X utf8 file.py`；高亮 | 环境差异较少，但 `input()`、第三方包、venv 要说明 |
| Java | 中（约 2–3 天） | 查找 JDK；从 `public class` 推断文件名；`javac` + `java`；编码参数；启动慢 | 类名/包名不一致、CLASSPATH、中文编码 |
| C# / Go 等 | 中 | 依赖 SDK，各自不同 | 用户环境更不确定 |

这些都不需要把编译器/解释器打进安装包，仍然是“检测本机工具链”。所以程序体积基本不增加（可能只增加几十 KB 代码和图标），Java/Python 用户自己装环境。

## 4. 为什么不应该现在一次全加

1. **测试矩阵会翻倍**：语言 × 操作系统 × 工具链版本 × 失败路径。C++ 已有 VS2010、VS2026、MinGW 的分支，再加 Java/Python 后每个版本都要回归。
2. **产品定位会模糊**：面向大一 C++ 作业的“三步完成截图”是优势。一次加入太多语言，设置项、文档、报错解释都会变复杂。
3. **AI 拆题/生成需要语言识别**：同一份文档里可能混有多语言代码，需要先选目标语言，UI 和提示词要调整。
4. **多文件项目会拉高复杂度**：一旦用户开始要求“整个 Java 项目”“带 CMake 的 C++ 项目”，就不是截图器而是轻量 IDE 了。应明确只支持单文件作业片段。

## 5. 推荐版本节奏

- **v1.0**：C++ 单文件，稳定截图与失败中止（当前版本）。
- **v1.1**：加入 C 语言（复用 C++ 工具链，实现成本最低），验证语言适配层。
- **v1.2**：加入 Python，顺便补齐“非编译型语言”的运行路径。
- **v1.3**：加入 Java，处理类名与编码。
- 若社区确实需要更多语言，再把适配器做成可安装的“语言包”；第一版建议内置、审核后发布，避免动态加载任意 Python 代码带来的供应链风险。

命名方面，短期保留 `CppShot` 有利于定位；若真的做成多语言，可以再改名为 `CodeShot`，并保留 CppShot 作为 C++ 模式。

## 6. 现在是否要动手

不建议在本版本继续加语言。当前已经完成 C++ 核心闭环，先把 GitHub 发布、免安装包、跨平台 CI 跑通，再按上面的顺序加 C。代码层面已经可以把语言相关逻辑集中到 `toolchain.py`，后续重构成本可控。
