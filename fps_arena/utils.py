"""Small math/render utility helpers."""

import math


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def normalize_angle(angle: float) -> float:
    while angle < 0:
        angle += math.tau
    while angle >= math.tau:
        angle -= math.tau
    return angle


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def rgb(r: int, g: int, b: int) -> str:
    r = int(clamp(r, 0, 255))
    g = int(clamp(g, 0, 255))
    b = int(clamp(b, 0, 255))
    return f"#{r:02x}{g:02x}{b:02x}"
