# AGENTS.md

## 项目概述

- RVP(Random Voice Player):Windows 桌面 GUI 应用(customtkinter + pygame-ce),按场景 JSON 驱动音频播放,可联动 mpv 视频与 Intiface/TCode 设备。本仓库是 fork(yihuishou/RVP-CN),现有代码、注释、文档以日语为主。
- 只有 `requirements.txt`(Python 3.12+),无 pyproject/setup.py。`tkinterdnd2`、`pyserial` 是可选依赖,缺失时对应功能自动禁用(导入处 try/except 静默降级),**不要改成硬依赖**。

## 运行与验证

- 安装:`py -m pip install -r requirements.txt`
- 启动(需在仓库根目录):`py rvp_launcher.py` 或 `python -m rvp.main`
- **仓库不含测试,也没有 lint/typecheck/CI 配置**。`tests/`、`test_*.py` 被 `.gitignore` 排除(维护者本地保留)。不要寻找 pytest/ruff 等命令;改动后只能安装依赖、运行 GUI 手工验证。

## 结构(改动时的边界)

- `rvp/main/` — 应用外壳与主窗口,入口 `rvp/main/entry.py` 的 `main()`;`python -m rvp.main` 经 `__main__.py`
- `rvp/player/` — 播放引擎(`scenario_player.py` 等)
- `rvp/scenario/` — 场景 JSON 加载与解析;格式权威文档是根目录 `SCENARIO_FORMAT.md`
- `rvp/editor/` — 场景编辑器 GUI;`rvp/script_edit/` — funscript/CSV 设备脚本编辑
- `rvp/scenario_map.py` — 编辑器与播放页共用的事件/状态图绘制,改图的外观只改这一处
- `rvp/{intiface,tcode,mpv}_client.py` — 外部设备/进程客户端
- `rvp_launcher.py` — 仅为 PyInstaller 准备的入口,转发到 `rvp.main`
- `rvp/main` 和 `rvp/editor` 曾是单个大文件,拆分成包后靠 `__init__.py` 重导出保持 `from rvp.main import X` 兼容;新代码从子模块直接导入

## i18n(`rvp/i18n.py` + `rvp/i18n_zh.py`)

- gettext 风格:**日语原文即翻译键**,`tr("再生")` 查 `EN`/`ZH` 字典。新增 UI 文案时键必须是日语原文;未登记的键回退日语显示。
- 支持 ja/en/zh(`SUPPORTED = ("ja", "en", "zh")`),**默认 zh**(=355)。语言由环境变量 `RVP_LANG` 或 `~/.rvp_config.json` 的 `"language"` 决定,**启动时确定,不支持运行时切换**。
- 回退链:zh 值 → `EN` 值 → 日语键(即 `ZH.get(text, EN.get(text, text))`)。中文翻译全量在**独立文件** `rvp/i18n_zh.py` 的 `ZH` 字典(1201 键,与 `EN` 键集必须一致);改中文文案只改该文件的值,键勿动。
- 语言选择 UI 用 `LANG_NAMES`(自称表记:日本語/English/简体中文),在 `rvp/main/app_header.py`;切后提示语用 `tr_in(new_lang, ...)` 按新语言显示。
- 全量校验:`py tools/check_i18n_zh.py`(`tools/` 被 gitignore)。
- 现有逻辑依赖"显示字符串参与比较"(如 `choice == tr("N分で終了")`),所以不要引入运行时切换语言,也不要随手改动已有 `tr()` 键。
- `RVP_CONFIG_PATH` 环境变量可覆盖配置文件路径(隔离用,避免读写真实用户配置)。

## 构建 exe(详见 `BUILD_EXE.md`)

- `build_exe.bat` 必须保持 **CP932(Shift-JIS)+ CRLF**;`.gitattributes` 只固定了换行,编码需人工保证,用 UTF-8 重存会导致双击后静默不执行。
- PyInstaller 只用 onedir(禁 onefile,pygame-ce LGPL 合规),且只能在 Windows 实机构建。跑 `build_exe.bat` 或 `BUILD_EXE.md` 第 2 节的完整命令。
- 分发前必须把 `LICENSE` / `THIRD-PARTY-LICENSES.txt` 拷入 `dist\RVP\`(应用内许可证画面从该处读取,缺失会报"文件未找到")。
- 版本号唯一来源:`rvp/__init__.py` 的 `__version__`(SemVer),须与 git tag、Release zip 名一致。

## 代码约定与仓库规则

- 注释/docstring 现状为日语,普遍带 `=NNN:` 内部变更计数标签(如 `=352`);该计数与公开版本号 `__version__` 是两回事,不要混淆或批量改写。
- 测试钩子模式:实现代码不直接读模块全局,而是经 `_hooks._pkg().NAME` 在调用时从包属性取值(如 `TkinterDnD`、`messagebox`),以便测试替换;改动 main/editor 的导入时保持这一写法。
- `.gitignore` 排除的本地产物勿提交,也勿删这些规则:`tests/`、`tools/`、`samples/`、`icon_src/`、`build/`、`dist/`、`*.spec`、`*_DEV.md`、`notes/`。
