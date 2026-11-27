"""Small bounded Tk trend view for the panel's existing 5 Hz snapshots."""
from collections import deque
import math
import tkinter as tk
from tkinter import ttk

WINDOW_S = 10.
BREAK_S = .65
ANGLE = '#1d65a6'
TORQUE = ('#1d65a6', '#9a541b', '#11746a')


class TrendView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=(6, 4))
        self.points = deque(maxlen=80)
        self.session = None
        self.boundary = None
        self.drawn_at = 0.
        self.canvas = tk.Canvas(self, background='white', highlightthickness=0, height=157)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda event: setattr(self, 'drawn_at', 0.))
        ttk.Label(self, text='近 10 秒 · 面板快照约 5 Hz · 缺测处断线 · 供趋势观察').pack(anchor='w')

    def clear(self):
        self.points.clear()
        self.session = None
        self.boundary = None
        self.drawn_at = 0.

    def add(self, timestamp, status):
        session = status.get('serial_session_id')
        if session != self.session:
            self.clear()
            self.session = session
        if session is None or (self.points and timestamp <= self.points[-1][0]):
            return
        fresh = (status.get('serial_wire_fresh') and status.get('serial_session_ready')
                 and status.get('serial_peer_session_id') == session)
        angle = status.get('angle_rad') if fresh and status.get('telemetry_fresh') else None
        if angle is not None:
            angle = math.degrees(angle)
        threshold = status.get('serial_free_boundary_rad')
        direction = status.get('serial_forward_sign', -1)
        initial = status.get('serial_initial_angle_rad', 0.)
        if threshold is not None:
            self.boundary = math.degrees(initial + direction * threshold)
        last = status.get('serial_last_sent') or {}
        sent_age = status.get('serial_last_sent_age_ms')
        sent = (last.get('target_mnm') if fresh and sent_age is not None and sent_age < BREAK_S * 1000
                else None)
        requested = status.get('target_torque_nm') if fresh else None
        echoed = status.get('serial_applied_target_nm') if fresh else None
        self.points.append((timestamp, angle, requested, None if sent is None else sent / 1000, echoed))

    def draw(self, now):
        if not self.winfo_ismapped() or now - self.drawn_at < .2:
            return
        self.drawn_at = now
        self.canvas.delete('all')
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 260 or height < 105:
            return
        visible = [point for point in self.points if now - point[0] <= WINDOW_S]
        middle = width / 2
        angle_range = [0., -20., 20.] + [p[1] for p in visible if p[1] is not None]
        if self.boundary is not None:
            angle_range.append(self.boundary)
        torque_max = max([.1] + [v for p in visible for v in p[2:] if v is not None])
        charts = (
            (45, middle - 14, min(angle_range), max(angle_range), '机械角度 (°)',
             ((1, ANGLE, None),), [(0., '零位'), (self.boundary, '自由区边界')]),
            (middle + 51, width - 13, 0., torque_max * 1.12, '目标阻力 (N·m)',
             ((2, TORQUE[0], None), (3, TORQUE[1], (5, 2)), (4, TORQUE[2], (2, 2))), []),
        )
        for left, right, low, high, label, series, reference in charts:
            self.draw_chart(now, visible, left, right, low, high, height, label, series, reference)
        self.canvas.create_text(middle + 12, height - 8, text='PC 计算（实线）   写出（虚线）   MCU 回报（点线）',
                                font=('Microsoft YaHei UI', -11), anchor='w', fill='#334155')

    def draw_chart(self, now, points, left, right, low, high, height, title, lines, refs):
        top, bottom = 20, height - 36
        cv = self.canvas

        def x(timestamp):
            return right - (now - timestamp) / WINDOW_S * (right - left)

        def y(value):
            return bottom - (value - low) / (high - low) * (bottom - top)

        cv.create_text(left, 9, text=title, anchor='w', fill='#334155')
        cv.create_rectangle(left, top, right, bottom, outline='#d4e1ee')
        for level in (low, high):
            cv.create_text(left - 4, y(level), text=f'{level:.2g}', anchor='e', fill='#334155')
        for seconds in (-10, -5, 0):
            sx = x(now + seconds)
            cv.create_line(sx, top, sx, bottom, fill='#e8eef4')
            anchor = 'nw' if seconds == -10 else 'ne' if seconds == 0 else 'n'
            cv.create_text(sx, bottom + 3, text=f'{seconds} s', anchor=anchor,
                           font=('Microsoft YaHei UI', -12), fill='#526477')
        for value, text in refs:
            if value is not None:
                cv.create_line(left, y(value), right, y(value), fill='#7b91a6', dash=(3, 3))
                cv.create_text(right - 3, y(value) - 1, text=text, anchor='se', fill='#526477')
        for index, color, dash in lines:
            previous = None
            for point in points:
                timestamp, value = point[0], point[index]
                if value is None:
                    previous = None
                    continue
                px, py = x(timestamp), y(value)
                if previous is not None and timestamp - previous[0] < BREAK_S:
                    cv.create_line(previous[1], previous[2], px, py, fill=color, width=2, dash=dash)
                else:
                    cv.create_oval(px - 2, py - 2, px + 2, py + 2, outline='', fill=color)
                previous = timestamp, px, py
