# 参与开发

感谢参与 Xiangqi AI Assistant。提交代码前请先阅读
[开发指南](docs/DEVELOPMENT.md)和[架构说明](docs/ARCHITECTURE.md)。

## 基本流程

1. Fork 仓库，从最新 `main` 创建功能分支。
2. 每个分支只解决一个明确问题，避免夹带格式化或无关重构。
3. 修改行为时同步增加或更新测试；视觉故障优先增加可匿名复现的裁剪图或合成夹具。
4. 运行 `python -m unittest discover -s . -p "test_*.py"`。
5. 修改打包或运行时资源时，再构建 EXE 并执行单文件验证。
6. Pull Request 中写明问题、方案、风险、验证结果以及是否改变鼠标点击授权条件。

## 安全约束

- 不读游戏进程内存、不注入进程、不解析游戏网络协议。
- 自动接管只能在视觉局面、轮次、棋盘几何和目标窗口全部通过校验后发送点击。
- 同一落子事务的目标点击最多发送一次；任何修改不得绕过这一性质。
- 识别不确定时安全等待，不以降低全盘校验阈值换取表面速度。
- 测试默认不得控制真实桌面鼠标；使用模拟接口或内置自检。
- 不提交用户日志、诊断截图、学习模板、引擎临时文件或打包产物。

## 提交建议

提交信息使用简短的命令式描述，例如：

```text
fix: allow verified grid fallback for a missing source anchor
test: cover fast opponent reply recovery
docs: describe recognition pipeline extension points
```

对外公开的行为变化应同步更新 README 和 `docs/` 下的发布说明。版本号需要同时更新
`diagnostics.py` 与 `build_support/version_info.txt`。

## 许可证

项目自有源码按 [MIT License](LICENSE) 发布。引擎、模型和运行库不是因此重新许可；
分发前必须同时遵守 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和
`vision_licenses/` 中列出的第三方条款。
