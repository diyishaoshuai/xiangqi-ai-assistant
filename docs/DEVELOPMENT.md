# 开发与二次开发指南

本文面向希望在本项目上继续开发、移植识别器、增加棋盘主题或定制界面的开发者。

## 1. 环境准备

推荐 Windows 10/11 x64、Python 3.12。Python 3.11+ 应可运行。创建独立环境：

```powershell
git clone https://github.com/diyishaoshuai/xiangqi-ai-assistant.git
cd xiangqi-ai-assistant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

源码启动需要 Pikafish 目录，其中至少包含：

```text
Pikafish/
├─ Windows/pikafish-sse41-popcnt.exe
├─ pikafish.nnue
├─ Copying.txt
├─ AUTHORS
└─ NNUE-License.md
```

设置路径并启动：

```powershell
$env:PIKAFISH_DIR = "C:\path\to\Pikafish"
python app.py
```

用户数据写入 `%LOCALAPPDATA%\XiangqiAI`，不会写入源码目录。

## 2. 测试

运行全部源码测试：

```powershell
python -m unittest discover -s . -p "test_*.py"
```

常用定向测试：

```powershell
python -m unittest test_grid_geometry test_candidate_grid
python -m unittest test_app_automation test_autoplay_state test_tracking
python -m unittest test_move_policy test_search_pipeline
python -m unittest test_ui test_winrate_recovery
```

测试自动接管时应 mock 屏幕、前台窗口、用户输入和点击函数。不要让自动化测试操作真实游戏。

## 3. 构建与验证

```powershell
$env:PIKAFISH_DIR = "C:\path\to\Pikafish"
pyinstaller --noconfirm --clean XiangqiAI.spec
python build_support/verify_singlefile.py --exe dist/XiangqiAI.exe
```

验证程序会在 `build/singlefile-verification/` 中创建隔离环境，检查内嵌引擎、模型、日志、
进程退出、自动化模拟和单文件完整性。`build/`、`dist/` 与 EXE 均被 `.gitignore` 排除。

发布前还应：

1. 同步 `diagnostics.py`、`build_support/version_info.txt` 和 README 中的版本。
2. 运行全量源码测试。
3. 构建并完成全部单文件断言。
4. 计算 `Get-FileHash dist\XiangqiAI.exe -Algorithm SHA256`。
5. 只把源码提交到 Git；EXE 和校验文件使用 GitHub Release 附件发布。

## 4. 扩展棋盘识别

通用兼容应优先改进证据与几何，而不是按平台写固定坐标：

- `recognition.py`：ONNX 候选搜索、90 格分类、概率与几何输出。
- `grid_geometry.py`：9×10 网格拟合、棋子圆心锚点和点击前几何审计。
- `tracking.py`：连续帧、光流和锁定区域更新。
- `screen_cache.py`：已确认画面的严格缓存匹配。

新增主题时至少覆盖：正常视角、旋转视角、缩放、透视、选中光效、移动动画、遮挡、窗口移动
和错误候选区域。平台配置只应描述动画/遮罩特征；不得用硬编码屏幕坐标替代视觉定位。

## 5. 修改自动接管

主要闭环位于 `app.py`，持久状态在 `autoplay_state.py`，事务和视觉候选在
`automation.py`。修改时必须保持：

- 点击前使用最新截图重新验证局面和几何。
- 棋盘与引擎使用的局面不一致时不得点击。
- 终点点击发送后绝不重发同一事务。
- F1、窗口切换或用户操作可以在点击前中止。
- 异常使用状态栏和结构化日志，不弹阻塞接管的模态错误窗口。

## 6. 修改引擎决策

- `engine.py`：Pikafish/UCI 进程和完整迭代结果。
- `search_pipeline.py`：基础搜索、取消、预测和结果复用。
- `move_policy.py`：风险检测与 3/5 秒复核。
- `core.py`：棋盘、FEN、合法着法与走法转换。

自动接管固定 `MultiPV=1`。静态规则只能触发深搜，不应直接覆盖深搜首选。任何新策略都需要
覆盖取消、半截 UCI 深度、不一致 PV、合法性以及必败局面的测试。

## 7. 数据、隐私与问题报告

日志和诊断数据只存本机。提交 Issue 前请人工检查并裁剪敏感内容；仓库不接受完整桌面截图、
账号信息或未经授权的第三方素材。推荐提供版本号、时间、复现步骤、状态迁移日志和仅棋盘区域的
匿名图片。

## 8. 许可证与再分发

自有源码使用 MIT License，可以修改和二次分发，但必须保留版权和许可文本。Pikafish、NNUE、
ONNX 模型、DirectML、OpenCV 等仍使用各自许可证。重新打包或发布衍生版本前，请检查
`THIRD_PARTY_NOTICES.md`、`vision_licenses/` 和上游引擎附带文件；不要把 MIT 说明误写成覆盖第三方组件。
