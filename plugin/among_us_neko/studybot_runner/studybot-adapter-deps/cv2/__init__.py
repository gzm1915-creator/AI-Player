"""Minimal OpenCV compatibility namespace for StudyBot task imports.

Only non-OCR StudyBot tasks are currently supported. OpenCV-specific operations
raise a clear error instead of silently behaving incorrectly.
"""

LINE_4 = 4
COLOR_BGR2RGB = 4
COLOR_BGR2GRAY = 6
THRESH_BINARY = 0
THRESH_OTSU = 8
MORPH_RECT = 0
RETR_EXTERNAL = 0
CHAIN_APPROX_SIMPLE = 2
BORDER_DEFAULT = 4
ROTATE_90_CLOCKWISE = 0


def _unsupported(*_args, **_kwargs):
    raise NotImplementedError(
        "This OpenCV operation is only required by OCR minigames"
    )


cvtColor = threshold = dilate = getStructuringElement = _unsupported
findContours = boundingRect = drawContours = GaussianBlur = _unsupported
imwrite = minAreaRect = boxPoints = getPerspectiveTransform = _unsupported
warpPerspective = rotate = _unsupported

from . import cv2 as cv2  # noqa: E402,F401
