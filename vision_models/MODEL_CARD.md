# Chinese Chess Recognition models

The bundled `board_pose.onnx` and `board_classifier.onnx` files come from:

- https://huggingface.co/spaces/yolo12138/Chinese_Chess_Recognition
- https://github.com/TheOne1006/chinese-chess-recognition

The pose model locates the four outer intersections of a Xiangqi board. The
classifier predicts 90 intersections across 16 classes: empty, other/occluded,
and all fourteen red/black piece types.

The Hugging Face Space declares the MIT license. The upstream Python package
declares Apache-2.0 in `pyproject.toml`.
