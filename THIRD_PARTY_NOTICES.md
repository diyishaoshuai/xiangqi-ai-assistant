# 第三方组件说明

本工具调用 [Pikafish](https://github.com/official-pikafish/Pikafish) 象棋引擎（版本 2026-01-02）。Pikafish 采用 GNU GPL v3 许可证；对应许可证、作者名单和 NNUE 许可说明位于内嵌的 `licenses/pikafish/`。单 EXE 版运行时可通过“帮助”菜单阅读内嵌说明与许可证，无需外部配套文件。

该版本使用的官方发布包与对应源代码可从以下位置取得：

- 发布页：https://github.com/official-pikafish/Pikafish/releases/tag/Pikafish-2026-01-02
- 源代码：https://github.com/official-pikafish/Pikafish/tree/Pikafish-2026-01-02

本工具的截图读写功能使用 Pillow；Pillow 许可证信息随运行库一并嵌入程序。

截图识别使用 [Chinese Chess Recognition](https://github.com/TheOne1006/chinese-chess-recognition) 项目及其 [Hugging Face ONNX 模型](https://huggingface.co/spaces/yolo12138/Chinese_Chess_Recognition)。模型空间声明 MIT 许可证，相关上游 Python 包声明 Apache License 2.0。

ONNX 推理依赖 ONNX Runtime、OpenCV-Python Headless 和 NumPy。对应许可证与第三方组件说明位于 `licenses/vision/`。

本地集成修改（2026-08-31）：`cchess_onnx/base_onnx.py` 限制每个模型会话的 CPU 线程数并关闭空闲自旋，给游戏、界面及急停监听保留响应时间；模型权重未修改。
