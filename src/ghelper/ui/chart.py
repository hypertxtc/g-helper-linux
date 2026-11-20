# SPDX-License-Identifier: GPL-3.0-or-later

"""Single-curve fan chart in the style of G-Helper's Fans and Power window."""

from __future__ import annotations

from typing import Callable

from ..deps import GObject, Gtk

TEMP_MIN, TEMP_MAX = 10, 100
PWM_MAX = 255
RPM_MAX = 5600

CPU_COLOR = (0.227, 0.682, 0.937)  # #3aaeef
GPU_COLOR = (1.0, 0.125, 0.125)  # #ff2020
MID_COLOR = (0.024, 0.706, 0.541)  # #06b48a
_TAU = 6.283185307179586


def interpolate_pwm(temp: float, temps: list[int], pwm: list[int]) -> float | None:
    """Estimate target PWM from an 8-point fan curve via piecewise linear interpolation."""
    if not temps or len(temps) != len(pwm):
        return None
    if temp <= temps[0]:
        return float(pwm[0])
    if temp >= temps[-1]:
        return float(pwm[-1])
    for index in range(len(temps) - 1):
        t0, t1 = temps[index], temps[index + 1]
        if temp <= t1:
            if t1 == t0:
                return float(pwm[index])
            ratio = (temp - t0) / (t1 - t0)
            return float(pwm[index] + ratio * (pwm[index + 1] - pwm[index]))
    return float(pwm[-1])


def format_live_reading(
    temp: float | None, rpm: int | None, pwm: int | None = None
) -> str:
    """Format an optional temperature and fan speed for the chart header."""
    parts: list[str] = []
    if temp is not None:
        parts.append(f"{temp:.0f}\u00b0C")
    if rpm is not None:
        parts.append(f"{int(rpm)} RPM")
    elif pwm is not None:
        parts.append(f"{round(max(0, min(PWM_MAX, int(pwm))) / PWM_MAX * 100)}%")
    return " \u00b7 ".join(parts)


class FanChart(Gtk.DrawingArea):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(
        self,
        title: str,
        color: tuple[float, float, float],
        *,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._color = color
        self._on_changed = on_changed
        self._dark = False
        self._pwm = [20, 43, 56, 66, 86, 104, 122, 175]
        self._temp = [58, 61, 64, 68, 72, 77, 81, 98]
        self._selected: int | None = None
        self._pointer = (0.0, 0.0)
        self._drag: dict | None = None
        self._live_temp: float | None = None
        self._live_rpm: int | None = None
        self._live_pwm: int | None = None

        self.set_content_width(420)
        self.set_content_height(190)
        self.set_draw_func(self._draw)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

    # -- data -----------------------------------------------------------
    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.queue_draw()

    def set_points(self, pwm: list[int], temp: list[int]) -> None:
        if len(pwm) == 8 and len(temp) == 8:
            self._pwm = [int(v) for v in pwm]
            self._temp = [int(v) for v in temp]
            self._selected = None
            self.queue_draw()

    def points(self) -> tuple[list[int], list[int]]:
        return list(self._pwm), list(self._temp)

    def set_live(
        self, temp: float | None, rpm: int | None = None, pwm: int | None = None
    ) -> None:
        """Update the live temperature / fan speed indicator and redraw if changed."""
        new_temp = round(float(temp), 1) if temp is not None else None
        new_rpm = int(rpm) if rpm is not None else None
        new_pwm = int(pwm) if pwm is not None else None
        if (
            new_temp == self._live_temp
            and new_rpm == self._live_rpm
            and new_pwm == self._live_pwm
        ):
            return
        self._live_temp = new_temp
        self._live_rpm = new_rpm
        self._live_pwm = new_pwm
        self.queue_draw()

    # -- geometry -------------------------------------------------------
    def _plot(self) -> tuple[float, float, float, float]:
        width = max(self.get_width(), 2)
        height = max(self.get_height(), 2)
        left, right, top, bottom = 48.0, 14.0, 26.0, 28.0
        return left, top, max(10.0, width - left - right), max(10.0, height - top - bottom)

    def _to_px(self, temp: float, pwm: float) -> tuple[float, float]:
        left, top, width, height = self._plot()
        x = left + (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN) * width
        rpm = pwm / PWM_MAX * RPM_MAX
        y = top + (1.0 - rpm / RPM_MAX) * height
        return x, y

    def _from_px(self, x: float, y: float) -> tuple[float, float]:
        left, top, width, height = self._plot()
        temp = TEMP_MIN + (x - left) / width * (TEMP_MAX - TEMP_MIN)
        rpm = (1.0 - (y - top) / height) * RPM_MAX
        return temp, rpm / RPM_MAX * PWM_MAX

    def _nearest(self, x: float, y: float) -> int | None:
        best = None
        best_distance = 20.0 ** 2
        for index, (temp, pwm) in enumerate(zip(self._temp, self._pwm)):
            px, py = self._to_px(temp, pwm)
            distance = (px - x) ** 2 + (py - y) ** 2
            if distance < best_distance:
                best_distance = distance
                best = index
        return best

    # -- drawing --------------------------------------------------------
    def _draw(self, area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        bg = (0.137, 0.137, 0.137) if self._dark else (1.0, 1.0, 1.0)
        border = (0.227, 0.227, 0.227) if self._dark else (0.878, 0.878, 0.878)
        grid = (0.275, 0.275, 0.275) if self._dark else (0.82, 0.82, 0.82)
        text = (0.78, 0.78, 0.78) if self._dark else (0.32, 0.32, 0.32)

        cr.set_source_rgba(*bg, 1)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.fill()
        cr.set_source_rgba(*border, 1)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()

        left, top, plot_w, plot_h = self._plot()

        # Title and live readout
        cr.set_source_rgba(*text, 0.95)
        cr.set_font_size(10)
        title_extents = cr.text_extents(self._title)
        title_x = left + (plot_w - title_extents.width) / 2
        cr.move_to(title_x, 15)
        cr.show_text(self._title)

        live_text = format_live_reading(self._live_temp, self._live_rpm, self._live_pwm)
        if live_text:
            cr.set_source_rgba(*text, 0.75)
            cr.set_font_size(8.5)
            live_extents = cr.text_extents(live_text)
            live_text_x = left + plot_w - live_extents.width
            if live_text_x > title_x + title_extents.width + 8:
                cr.move_to(live_text_x, 15)
                cr.show_text(live_text)

        # Horizontal grid every 1400 RPM.
        for step in range(5):
            rpm = RPM_MAX * step / 4
            _, y = self._to_px(TEMP_MIN, rpm / RPM_MAX * PWM_MAX)
            cr.set_source_rgba(*grid, 0.9)
            cr.set_line_width(1)
            cr.move_to(left, y)
            cr.line_to(left + plot_w, y)
            cr.stroke()
            cr.set_source_rgba(*text, 0.85)
            cr.set_font_size(8)
            label = f"{int(rpm)}" if step else "OFF"
            extents = cr.text_extents(label)
            cr.move_to(left - extents.width - 5, y + 3)
            cr.show_text(label)

        # Vertical grid every 10 degrees.
        for temp in range(TEMP_MIN, TEMP_MAX + 1, 10):
            x, _ = self._to_px(temp, 0)
            cr.set_source_rgba(*grid, 0.9)
            cr.move_to(x, top)
            cr.line_to(x, top + plot_h)
            cr.stroke()
            cr.set_source_rgba(*text, 0.85)
            cr.set_font_size(8)
            label = f"{temp}"
            extents = cr.text_extents(label)
            cr.move_to(x - extents.width / 2, top + plot_h + 14)
            cr.show_text(label)

        points = [self._to_px(t, p) for t, p in zip(self._temp, self._pwm)]
        if not points:
            return

        cr.set_source_rgba(*self._color, 1.0)
        cr.set_line_width(1.6)
        cr.move_to(*points[0])
        for px, py in points[1:]:
            cr.line_to(px, py)
        cr.stroke()

        for index, (px, py) in enumerate(points):
            selected = self._selected == index
            radius = 5.5 if selected else 4.2
            cr.set_source_rgba(*self._color, 1.0)
            cr.arc(px, py, radius, 0, _TAU)
            cr.fill()
            cr.set_source_rgba(*bg, 1.0)
            cr.set_line_width(1.2)
            cr.arc(px, py, radius, 0, _TAU)
            cr.stroke()

        if self._live_temp is not None:
            clamped_temp = max(TEMP_MIN, min(TEMP_MAX, self._live_temp))
            live_x, _ = self._to_px(clamped_temp, 0)
            cr.set_dash([3.0, 3.0])
            cr.set_source_rgba(*self._color, 0.45)
            cr.set_line_width(1.0)
            cr.move_to(live_x, top)
            cr.line_to(live_x, top + plot_h)
            cr.stroke()
            cr.set_dash([])

            if self._live_rpm is not None:
                live_pwm_val = (
                    max(0, min(RPM_MAX, self._live_rpm)) / RPM_MAX * PWM_MAX
                )
            elif self._live_pwm is not None:
                live_pwm_val = float(max(0, min(PWM_MAX, self._live_pwm)))
            else:
                live_pwm_val = interpolate_pwm(clamped_temp, self._temp, self._pwm)

            if live_pwm_val is not None:
                _, live_y = self._to_px(clamped_temp, live_pwm_val)
                cr.set_source_rgba(*self._color, 0.22)
                cr.arc(live_x, live_y, 7.5, 0, _TAU)
                cr.fill()
                cr.set_source_rgba(*bg, 1.0)
                cr.arc(live_x, live_y, 4.5, 0, _TAU)
                cr.fill()
                cr.set_source_rgba(*self._color, 1.0)
                cr.set_line_width(2.0)
                cr.arc(live_x, live_y, 4.5, 0, _TAU)
                cr.stroke()

        if self._selected is not None and 0 <= self._selected < len(points):
            px, py = points[self._selected]
            rpm = int(self._pwm[self._selected] / PWM_MAX * RPM_MAX)
            label = f"{self._temp[self._selected]}C, {rpm} RPM"
            cr.set_font_size(9)
            extents = cr.text_extents(label)
            box_x = min(max(px - extents.width / 2 - 5, 2), width - extents.width - 14)
            box_y = max(py - 28, 2)
            cr.set_source_rgba(*grid, 0.75)
            cr.rectangle(box_x, box_y, extents.width + 10, 17)
            cr.fill()
            cr.set_source_rgba(*text, 1.0)
            cr.move_to(box_x + 5, box_y + 12)
            cr.show_text(label)

    # -- interaction ----------------------------------------------------
    def _on_motion(self, _controller, x: float, y: float) -> None:
        self._pointer = (x, y)

    def _on_drag_begin(self, _gesture, start_x: float, start_y: float) -> None:
        self._pointer = (start_x, start_y)
        index = self._nearest(start_x, start_y)
        self._selected = index
        self.queue_draw()
        if index is None:
            self._drag = None
            return
        self._drag = {"index": index, "start": (start_x, start_y), "moved": False}

    def _on_drag_update(self, _gesture, offset_x: float, offset_y: float) -> None:
        if not self._drag:
            return
        index = self._drag["index"]
        start_x, start_y = self._drag["start"]
        temp, pwm = self._from_px(start_x + offset_x, start_y + offset_y)

        low = self._temp[index - 1] + 1 if index > 0 else TEMP_MIN
        high = self._temp[index + 1] - 1 if index < len(self._temp) - 1 else TEMP_MAX
        if low <= high:
            new_temp = int(round(max(low, min(high, temp))))
        else:
            new_temp = self._temp[index]
        new_pwm = int(round(max(0, min(PWM_MAX, pwm))))

        if new_temp != self._temp[index] or new_pwm != self._pwm[index]:
            self._temp[index] = new_temp
            self._pwm[index] = new_pwm
            self._drag["moved"] = True
            self.queue_draw()

    def _on_drag_end(self, _gesture, _offset_x: float, _offset_y: float) -> None:
        if self._drag and self._drag.get("moved"):
            self.emit("changed")
            if self._on_changed is not None:
                self._on_changed()
        self._drag = None
