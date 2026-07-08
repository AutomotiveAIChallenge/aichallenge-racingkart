#!/usr/bin/env python3
"""Real-time route safety monitor visualizer (OpenCV).

Subscribes to vehicle position and route deviation status,
and displays them on an OpenCV window with the lanelet map.

Design: dark theme, fade-out trail, glow vehicle indicator, top HUD bar.
Performance: static map pre-rendered once; per-frame cost is minimal
(circle draws + rectangle + putText only, no alpha blending or copies).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rclpy
import rclpy.node
import threading
from collections import deque

import cv2
import numpy as np

from nav_msgs.msg import Odometry
from std_msgs.msg import Bool

from route_safety_monitor import RouteDeviationSafetyMonitor

# ---------------------------------------------------------------------------
# colour palette (BGR)
# ---------------------------------------------------------------------------
_BG = (20, 20, 25)
_GRID = (35, 35, 40)
_GRID_MAJOR = (48, 48, 54)

_POLY_FILL = (75, 55, 32)
_POLY_EDGE = (120, 95, 58)

_OK = (180, 230, 80)
_OK_GLOW = (90, 150, 40)
_NG = (80, 80, 240)
_NG_GLOW = (45, 45, 160)

_TRAIL_OK_BASE = np.array([100, 170, 50], dtype=np.float32)
_TRAIL_NG_BASE = np.array([50, 50, 180], dtype=np.float32)

_HUD_BG = (28, 28, 32)
_HUD_BORDER = (50, 50, 56)
_HUD_OK = (55, 130, 55)
_HUD_NG = (45, 45, 180)
_TEXT = (210, 210, 215)
_TEXT_DIM = (120, 120, 130)
_WHITE = (255, 255, 255)

_HUD_H = 52


# ---------------------------------------------------------------------------
# ROS node
# ---------------------------------------------------------------------------
class RouteVisualizerNode(rclpy.node.Node):
    TRAIL_LEN = 600

    def __init__(self):
        super().__init__("route_safety_visualizer")
        self._monitor = RouteDeviationSafetyMonitor(logger=self.get_logger())
        self._trail = deque(maxlen=self.TRAIL_LEN)
        self._pos = None
        self._deviated = False
        self._recv_count = 0

        self.create_subscription(
            Odometry, "/localization/kinematic_state", self._odom_cb, 1
        )
        self.create_subscription(
            Bool, "/vehicle/emergency/is_route_deviation", self._dev_cb, 1
        )

    def _odom_cb(self, msg):
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        self._pos = (x, y)
        self._trail.append((x, y, self._deviated))
        self._recv_count += 1

    def _dev_cb(self, msg):
        self._deviated = msg.data


# ---------------------------------------------------------------------------
# map renderer (pre-bake everything static)
# ---------------------------------------------------------------------------
class MapRenderer:
    def __init__(self, monitor, width=1280, height=900, margin=20.0):
        self.w = width
        self.h = height
        polys = monitor._lane_polygons
        all_x = [x for xs, _ in polys for x in xs]
        all_y = [y for _, ys in polys for y in ys]

        self.x_min = min(all_x) - margin
        self.x_max = max(all_x) + margin
        self.y_min = min(all_y) - margin
        self.y_max = max(all_y) + margin

        sx = width / (self.x_max - self.x_min)
        sy = height / (self.y_max - self.y_min)
        self.scale = min(sx, sy)
        self.ox = (width - (self.x_max - self.x_min) * self.scale) / 2
        self.oy = (height - (self.y_max - self.y_min) * self.scale) / 2

        # pre-render: background + grid + polygons + HUD bar (all static)
        bg = np.full((height, width, 3), _BG, dtype=np.uint8)
        self._draw_grid(bg)
        for xs, ys in polys:
            pts = np.array(
                [self.to_px(x, y) for x, y in zip(xs, ys)], dtype=np.int32
            )
            cv2.fillPoly(bg, [pts], _POLY_FILL)
            cv2.polylines(bg, [pts], True, _POLY_EDGE, 1, cv2.LINE_AA)
        # HUD background bar (static)
        cv2.rectangle(bg, (0, 0), (width, _HUD_H), _HUD_BG, -1)
        cv2.line(bg, (0, _HUD_H), (width, _HUD_H), _HUD_BORDER, 1)
        cv2.putText(
            bg, "ROUTE SAFETY MONITOR", (14, 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, _TEXT_DIM, 1, cv2.LINE_AA,
        )
        self._bg = bg

    def _draw_grid(self, img):
        for step, col in ((10.0, _GRID), (50.0, _GRID_MAJOR)):
            x = self.x_min - (self.x_min % step)
            while x <= self.x_max:
                px, _ = self.to_px(x, self.y_min)
                cv2.line(img, (px, 0), (px, self.h), col, 1)
                x += step
            y = self.y_min - (self.y_min % step)
            while y <= self.y_max:
                _, py = self.to_px(self.x_min, y)
                cv2.line(img, (0, py), (self.w, py), col, 1)
                y += step

    def to_px(self, x, y):
        px = int((x - self.x_min) * self.scale + self.ox)
        py = int(self.h - ((y - self.y_min) * self.scale + self.oy))
        return px, py

    def new_frame(self):
        return self._bg.copy()


# ---------------------------------------------------------------------------
# per-frame drawing (kept minimal)
# ---------------------------------------------------------------------------
def _draw_trail(frame, to_px, trail):
    n = len(trail)
    if n == 0:
        return
    inv_n = 1.0 / n
    for i, (x, y, dev) in enumerate(trail):
        t = i * inv_n  # 0 = oldest, 1 = newest
        alpha = 0.12 + 0.88 * t
        base = _TRAIL_NG_BASE if dev else _TRAIL_OK_BASE
        col = (int(base[0] * alpha), int(base[1] * alpha), int(base[2] * alpha))
        r = 1 if t < 0.5 else 2
        px, py = to_px(x, y)
        cv2.circle(frame, (px, py), r, col, -1)


def _draw_vehicle(frame, px, py, deviated):
    glow = _NG_GLOW if deviated else _OK_GLOW
    col = _NG if deviated else _OK
    cv2.circle(frame, (px, py), 15, glow, 1, cv2.LINE_AA)
    cv2.circle(frame, (px, py), 9, col, -1, cv2.LINE_AA)
    cv2.circle(frame, (px, py), 9, _WHITE, 2, cv2.LINE_AA)
    cv2.circle(frame, (px - 2, py - 2), 3, _WHITE, -1, cv2.LINE_AA)


def _draw_hud_dynamic(frame, w, pos, deviated, fps, recv_count):
    """Draw only the dynamic parts of the HUD (text that changes per frame)."""
    if pos is not None:
        # status badge
        label = " DEVIATED " if deviated else " IN ROUTE "
        badge_col = _HUD_NG if deviated else _HUD_OK
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        bx, by = 14, 28
        cv2.rectangle(frame, (bx, by), (bx + tw, by + th + 8), badge_col, -1)
        cv2.putText(
            frame, label, (bx, by + th + 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, _WHITE, 2, cv2.LINE_AA,
        )
        # coordinates
        coord = f"X {pos[0]:.1f}   Y {pos[1]:.1f}"
        cv2.putText(
            frame, coord, (bx + tw + 18, by + th + 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.50, _TEXT, 1, cv2.LINE_AA,
        )
    else:
        dots = "." * (int(time.time() * 2) % 4)
        cv2.putText(
            frame, f"Waiting for odometry{dots}", (14, 42),
            cv2.FONT_HERSHEY_SIMPLEX, 0.50, _TEXT_DIM, 1, cv2.LINE_AA,
        )
    # FPS / msg count (right side)
    info = f"{fps:.0f} FPS | {recv_count} msgs"
    (iw, _), _ = cv2.getTextSize(info, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
    cv2.putText(
        frame, info, (w - iw - 14, 20),
        cv2.FONT_HERSHEY_SIMPLEX, 0.38, _TEXT_DIM, 1, cv2.LINE_AA,
    )


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------
def main():
    rclpy.init()
    node = RouteVisualizerNode()
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()

    renderer = MapRenderer(node._monitor)
    win = "Route Safety Monitor"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)

    fps = 0.0
    t_prev = time.monotonic()
    frame_count = 0

    try:
        while True:
            frame = renderer.new_frame()

            _draw_trail(frame, renderer.to_px, list(node._trail))

            pos = node._pos
            if pos is not None:
                px, py = renderer.to_px(pos[0], pos[1])
                _draw_vehicle(frame, px, py, node._deviated)

            _draw_hud_dynamic(frame, renderer.w, pos, node._deviated, fps, node._recv_count)

            cv2.imshow(win, frame)
            if cv2.waitKey(100) in (27, ord("q")):
                break

            frame_count += 1
            now = time.monotonic()
            if now - t_prev >= 1.0:
                fps = frame_count / (now - t_prev)
                frame_count = 0
                t_prev = now
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
