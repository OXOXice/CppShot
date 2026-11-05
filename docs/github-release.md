# 发布到 GitHub 与安装包说明

## 当前状态

- 本地仓库已准备好提交。
- `.github/workflows/build.yml` 会在 Windows / macOS / Linux 上构建便携包；打 `v*` 标签时会自动创建 GitHub Release。
- Windows 免安装包可以用 `python scripts/build.py` 在本地构建。

## 第一次发布

```bash
cd cppshot
git init
git add .
git commit -m "feat: CppShot v1.0.0"
git branch -M main
git remote add origin https://github.com/OXOXice/CppShot.git
git push -u origin main
```

如果没有远程仓库，先到 GitHub 网页 New repository，创建一个空仓库（不要勾选初始化 README），再把上面的地址替换进去。

登录 `gh` 后也可以直接创建并推送：

```bash
gh auth login
gh repo create CppShot --public --source=. --remote=origin --push
```

## 打版本并自动生成 Release

```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions 会：

1. 运行 `tests/test_core.py`；
2. 在 `windows-latest`、`ubuntu-latest`、`macos-latest` 分别执行 `python scripts/build.py`；
3. 上传三个平台的构建产物；
4. 使用标签名创建 Release，并附上 `release/*` 中的压缩包。

## 安装包形态

| 平台 | 产物 | 是否需要安装 |
| --- | --- | --- |
| Windows | `CppShot-1.0.0-Windows-Portable.zip` | 否，解压即用，需要本机 C++ 编译器 |
| macOS | `CppShot-1.0.0-macOS.zip`（含 `.app`） | 否；未签名，可能需右键打开 |
| Linux | `CppShot-1.0.0-Linux.tar.gz` | 否，便携解压；需要 Tk 与 C++ 编译器 |

严格来说，macOS 的 `.app` 是应用包不是安装器。真正分发给普通用户还需要：

- Apple Developer 账号；
- Developer ID 证书签名；
- notarization（公证）；
- 可选的 `.dmg` 制作。

这些必须在 macOS 上完成，无法在当前 Windows 环境里本地验证。因此目前只提供可运行的未签名应用包，README 中已注明。

Linux 也没有制作 `.deb` / `.rpm` / AppImage。当前产物是 tar.gz；用 AppImage 可以进一步做“双击即用”，但需要额外打包 FUSE 运行时，适合后续迭代。

## 代码签名 / SmartScreen

Windows 免安装包未签名，首次运行可能触发 SmartScreen。发布时可以在 README 说明：

> 未签名版本会提示“Windows 已保护你的电脑”，点击“更多信息 → 仍要运行”。如果你对此有顾虑，请从源码运行。

要消除提示，需要购买代码签名证书并给 exe 签名，CI 中可以通过 secrets 注入签名证书。

## 发布前检查清单

- [ ] 修改 README 中的 GitHub 用户名和克隆地址
- [ ] 确认 `LICENSE` 年份/作者
- [ ] 更新 `CHANGELOG.md`
- [ ] 确认 CI 三个平台都通过
- [ ] 检查 macOS 产物能否在真实 Mac 上打开
- [ ] 检查 Linux 产物在目标发行版能否启动
- [ ] 在 Windows 11 上测试 SmartScreen 和首次启动流程
