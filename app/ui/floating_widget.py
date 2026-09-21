from __future__ import annotations

import math

from PyQt6.QtCore import (
    QByteArray,
    QEasingCurve,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import ACCENT_COLOR, PANEL_RAISED

# 悬浮窗尺寸
FLOAT_W = 208
FLOAT_H = 52
# 槽位比悬浮窗每边大出的像素
SLOT_PAD = 6
SLOT_W = FLOAT_W + SLOT_PAD * 2
SLOT_H = FLOAT_H + SLOT_PAD * 2
# 无槽位时的距离吸附兜底（像素）
DOCK_SNAP_DISTANCE = 30

_BASE_FLAGS = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
_TOP_FLAGS = _BASE_FLAGS | Qt.WindowType.WindowStaysOnTopHint

# 各状态符号（纯线条 SVG），颜色用 {color} 占位
STATE_SVG = {
    "clean": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">'
        '<polyline points="20 6 9 17 4 12"/></svg>'
    ),
    "warning": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
        '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
    ),
    "error": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"/>'
        '<line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    ),
    "different": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/>'
        '<circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></svg>'
    ),
    "waiting": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>'
    ),
}

CLEAN_COLOR = "#70D6A5"
TRACK_COLOR = QColor(ACCENT_COLOR)
TRACK_COLOR.setAlpha(60)


def render_state(state: str, color: str, size: int) -> QPixmap:
    svg = STATE_SVG.get(state, STATE_SVG["waiting"]).format(color=color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return pm


class ProgressRing(QWidget):
    """渠道/总状态符号：平时显示状态符号；同步时显示线条进度环，完成后顺势出对号。

    - start_progress：渠道环假填充（至少 1 秒），满后旋转等待真实结果
    - set_progress：总环按 已完成/总数 确定性填充
    - finish(state_key)：环收住，clean 播对号生长，其它直接显示对应符号
    """

    FILL_MS = 1000
    CHECK_MS = 360

    def __init__(self, size: int, parent=None, bounce_h: int = 0):
        super().__init__(parent)
        self._size = size
        # bounce_h>size 时，控件占一条更高的轨道，状态图标在里面上下弹跳
        self._bounce_h = bounce_h if bounce_h and bounce_h > size else 0
        self.setFixedSize(size, self._bounce_h or size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: transparent;")
        self._state_pm: QPixmap | None = None
        # 弹跳：_bounce_frac 0=贴地(压扁) 1=到顶(正常)
        self._bouncing = False
        self._bounce_up = True
        self._bounce_frac = 0.0
        # 弹跳：一条正弦曲线一次循环完成 起->顶->落，顶点平滑掠过不拖沓
        self._bounce_anim = QVariantAnimation(self)
        self._bounce_anim.setDuration(1500)
        self._bounce_anim.setStartValue(0.0)
        self._bounce_anim.setEndValue(1.0)
        self._bounce_anim.setLoopCount(-1)
        self._bounce_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._bounce_anim.valueChanged.connect(self._on_bounce_value)

        # mode: "state" 显示符号 / "ring" 进度环
        self._mode = "state"
        # ring 内阶段：fill 填充 / spin 满后旋转等待 / check 对号生长
        self._phase = "fill"
        self._state_key = "waiting"
        self._state_color = "#8A97AA"
        self._fill = 0.0
        self._spin = 0.0
        self._check = 0.0
        self._spin_after_fill = False
        self._pending: tuple[str, str] | None = None
        self._ring_color = ACCENT_COLOR

        self._fill_anim = QVariantAnimation(self)
        self._fill_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fill_anim.setDuration(self.FILL_MS)
        self._fill_anim.setStartValue(0.0)
        self._fill_anim.setEndValue(1.0)
        self._fill_anim.valueChanged.connect(self._on_fill_value)
        self._fill_anim.finished.connect(self._on_fill_done)

        self._spin_anim = QVariantAnimation(self)
        self._spin_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._spin_anim.setDuration(850)
        self._spin_anim.setStartValue(0.0)
        self._spin_anim.setEndValue(1.0)
        self._spin_anim.setLoopCount(-1)
        self._spin_anim.valueChanged.connect(self._on_spin_value)

        self._check_anim = QVariantAnimation(self)
        self._check_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._check_anim.setDuration(self.CHECK_MS)
        self._check_anim.setStartValue(0.0)
        self._check_anim.setEndValue(1.0)
        self._check_anim.valueChanged.connect(self._on_check_value)
        self._check_anim.finished.connect(self._on_check_done)

        self._prog_anim = QVariantAnimation(self)
        self._prog_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._prog_anim.setDuration(260)
        self._prog_anim.valueChanged.connect(self._on_fill_value)
        self._prog_anim.finished.connect(self._on_prog_done)

    # ------------------------------------------------------------ 对外 API
    def set_state(self, state_key: str, color: str, animate: bool = False) -> None:
        """平时/最终显示某个状态符号。animate=True 且为 clean 时播对号生长。"""
        self._spin_anim.stop()
        self._fill_anim.stop()
        self._prog_anim.stop()
        self._check_anim.stop()
        self._pending = None
        self._state_key = state_key
        self._state_color = color
        self._state_pm = render_state(state_key, color, self._size)
        if animate and state_key == "clean" and self._mode == "ring":
            self._phase = "check"
            self._check = 0.0
            self._check_anim.start()
        else:
            self._mode = "state"
            self._phase = "fill"
        self._refresh_bounce()
        self.update()

    def start_progress(self, color: str = ACCENT_COLOR) -> None:
        """渠道环：假填充（至少 1 秒），满后旋转等待。"""
        self._mode = "ring"
        self._phase = "fill"
        self._fill = 0.0
        self._check = 0.0
        self._spin = 0.0
        self._pending = None
        self._ring_color = color
        self._spin_after_fill = True
        self._check_anim.stop()
        self._spin_anim.stop()
        self._prog_anim.stop()
        self._fill_anim.start()
        self._refresh_bounce()
        self.update()

    def set_progress(self, frac: float, color: str = ACCENT_COLOR) -> None:
        """总环：确定性填充到 frac（0~1）。"""
        frac = max(0.0, min(1.0, frac))
        self._mode = "ring"
        self._phase = "fill"
        self._ring_color = color
        self._spin_after_fill = False
        self._spin_anim.stop()
        self._check_anim.stop()
        self._fill_anim.stop()
        self._prog_anim.stop()
        self._prog_anim.setStartValue(float(self._fill))
        self._prog_anim.setEndValue(float(frac))
        self._prog_anim.start()
        self._refresh_bounce()
        self.update()

    def finish(self, state_key: str, color: str) -> None:
        """真实结果回来：环满后出符号；clean 播对号。"""
        self._state_color = color
        if self._mode != "ring":
            self.set_state(state_key, color, animate=(state_key == "clean"))
            return
        self._pending = (state_key, color)
        filling = (
            self._fill_anim.state() == QVariantAnimation.State.Running
            or self._prog_anim.state() == QVariantAnimation.State.Running
        )
        if self._fill >= 1.0 and not filling:
            self._resolve()
        # 否则等填充动画结束（_on_fill_done / _on_prog_done）再收尾

    def reset_to_state(self, state_key: str, color: str) -> None:
        """同步结束后由真实检查结果覆盖（不播动画）。"""
        self.set_state(state_key, color, animate=False)

    # ------------------------------------------------------------ 动画回调
    def _on_fill_value(self, value) -> None:
        self._fill = float(value)
        self.update()

    def _on_spin_value(self, value) -> None:
        self._spin = float(value)
        if self._phase == "spin":
            self.update()

    def _on_check_value(self, value) -> None:
        self._check = float(value)
        self.update()

    def _on_fill_done(self) -> None:
        self._fill = 1.0
        if self._pending is not None:
            self._resolve()
        elif self._spin_after_fill:
            self._phase = "spin"
            self._spin_anim.start()
        self.update()

    def _on_prog_done(self) -> None:
        # 总环确定性填充到位：_fill 已由最后一帧 valueChanged 设为目标比例，
        # 不再硬写 1.0。若结果已回来则立即收尾出符号。
        if self._pending is not None:
            self._resolve()
        self.update()

    def _on_check_done(self) -> None:
        self._mode = "state"
        self._phase = "fill"
        self._state_key = "clean"
        self._refresh_bounce()
        self.update()

    def _resolve(self) -> None:
        if self._pending is None:
            return
        state_key, color = self._pending
        self._pending = None
        self._spin_anim.stop()
        if state_key == "clean":
            self._state_key = "clean"
            self._state_color = color
            self._phase = "check"
            self._check = 0.0
            self._check_anim.start()
        else:
            self._mode = "state"
            self._phase = "fill"
            self._state_key = state_key
            self._state_color = color
        self._refresh_bounce()
        self.update()

    # ------------------------------------------------------------ 弹跳
    def _start_bounce(self) -> None:
        if self._bounce_h <= 0 or self._bouncing:
            return
        self._bouncing = True
        self._bounce_frac = 0.0
        self._bounce_anim.start()

    def _stop_bounce(self) -> None:
        if not self._bouncing:
            return
        self._bouncing = False
        self._bounce_anim.stop()
        self._bounce_frac = 0.0

    def _refresh_bounce(self) -> None:
        if self._bounce_h <= 0:
            return
        want = self._mode == "state" and self._phase != "check" and self._state_key != "clean"
        if want and not self._bouncing:
            self._start_bounce()
        elif not want and self._bouncing:
            self._stop_bounce()

    def _on_bounce_value(self, value) -> None:
        self._bounce_frac = float(value)
        self.update()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event):  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self._size
        top = (self.height() - w) // 2
        if self._mode == "state":
            pm = self._state_pm if self._state_pm is not None else render_state(
                self._state_key, self._state_color, w)
            if self._bouncing:
                self._paint_bounce(p, pm, w)
            else:
                p.drawPixmap(0, top, pm)
            return

        pen_w = 2.0 if self._size <= 14 else 2.4
        margin = pen_w
        rect = QRect(int(margin), top + int(margin),
                     w - int(margin * 2), w - int(margin * 2))

        if self._phase == "check":
            self._paint_check(p, rect, pen_w)
            return

        # 轨道（淡色整圆，纯线条无填充）
        track_pen = QPen(TRACK_COLOR)
        track_pen.setWidthF(pen_w)
        p.setPen(track_pen)
        p.drawArc(rect, 0, 360 * 16)

        ring_color = QColor(self._ring_color)
        if self._phase == "spin":
            # 满环 + 一段亮色弧绕圈，表示仍在推送
            full_pen = QPen(QColor(self._ring_color))
            full_pen.setWidthF(pen_w)
            p.setPen(full_pen)
            p.drawArc(rect, 0, 360 * 16)
            bright = QColor(255, 255, 255, 210)
            bp = QPen(bright)
            bp.setWidthF(pen_w)
            bp.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(bp)
            start = int(-self._spin * 360 * 16)
            p.drawArc(rect, start, int(90 * 16))
        else:
            pen = QPen(ring_color)
            pen.setWidthF(pen_w)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            # 从顶部（90°）顺时针填充
            span = int(-self._fill * 360 * 16)
            p.drawArc(rect, 90 * 16, span)
        p.end()

    def _paint_check(self, p: QPainter, rect: QRect, pen_w: float) -> None:
        # 整环随对号生长淡出
        ring_alpha = int(220 * (1.0 - self._check))
        if ring_alpha > 0:
            c = QColor(self._ring_color)
            c.setAlpha(ring_alpha)
            pen = QPen(c)
            pen.setWidthF(pen_w)
            p.setPen(pen)
            p.drawArc(rect, 0, 360 * 16)

        # 对号路径（24 坐标系映射进 rect）
        sx = rect.width() / 24.0
        sy = rect.height() / 24.0
        ox = rect.x()
        oy = rect.y()
        path = QPainterPath()
        path.moveTo(ox + 5 * sx, oy + 12.5 * sy)
        path.lineTo(ox + 10 * sx, oy + 17.5 * sy)
        path.lineTo(ox + 19 * sx, oy + 7 * sy)
        total = path.length()
        green = QPen(QColor(CLEAN_COLOR))
        green.setWidthF(pen_w + 0.4)
        green.setCapStyle(Qt.PenCapStyle.RoundCap)
        green.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        green.setDashPattern([total, total + 1.0])
        green.setDashOffset(total * (1.0 - self._check))
        p.setPen(green)
        p.drawPath(path)

    def _paint_bounce(self, p: QPainter, pm: QPixmap, w: int) -> None:
        """非对号稳态：图标在轨道内上下弹跳，空中翻转一圈，落地压扁 + 浅阴影。"""
        H = self.height()
        margin = 5
        u = self._bounce_frac                       # 相位 0->1：起->顶->落
        h = max(0.0, math.sin(math.pi * u))          # 0 贴地 / 1 到顶
        top_y = margin
        bottom_y = H - margin - w
        y = bottom_y - (bottom_y - top_y) * h
        # 仅在贴地附近压扁
        squash = max(0.0, 1.0 - h / 0.4)
        sx = 1.0 + 0.22 * squash
        sy = 1.0 - 0.30 * squash
        # 70% 高度开始翻转：相位跨顶点线性转一圈，到顶 180、落回 70% 正好 360
        u0 = math.asin(0.7) / math.pi
        u1 = 1.0 - u0
        if u < u0:
            t_rot = 0.0
        elif u > u1:
            t_rot = 1.0
        else:
            t_rot = (u - u0) / (u1 - u0)
        rot = 360.0 * t_rot

        # 地面浅阴影：贴地大而略实，到顶小而淡
        ground_y = H - margin
        sh_w = w * (0.92 - 0.42 * h)
        sh_h = 3.0
        sh_alpha = int(72 * (1.0 - 0.55 * h))
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, max(0, sh_alpha)))
        p.drawEllipse(QRectF((w - sh_w) / 2.0, ground_y - sh_h / 2.0 + 1.5, sh_w, sh_h))
        p.restore()

        # 先绕图标中心翻转，再以底边为支点压扁（落地时底边贴地、角度回正）
        p.save()
        cx = w / 2.0
        cy = float(y) + w / 2.0
        base = float(y + w)
        p.translate(cx, cy)
        p.rotate(rot)
        p.translate(-cx, -cy)
        p.translate(cx, base)
        p.scale(sx, sy)
        p.translate(-cx, -base)
        p.drawPixmap(0, int(y), pm)
        p.restore()


class DockSlot(QFrame):
    """主窗口右上角的凹陷槽位：比悬浮窗大一圈，带内阴影，提示停靠位置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SLOT_W, SLOT_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._occupied = True

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("拖回此处吸附")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet("color: #5A7686; font-size: 11px; background: transparent;")
        lay.addWidget(self.hint)
        self.hint.setVisible(False)

    def set_occupied(self, occupied: bool) -> None:
        self._occupied = occupied
        self.hint.setVisible(not occupied)
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt API
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        radius = 13.0
        path = QPainterPath()
        path.addRoundedRect(
            float(rect.x()), float(rect.y()),
            float(rect.width()), float(rect.height()),
            radius, radius,
        )
        # 坑底：卡片在槽内时偏深，拖出后提亮以便看清边缘
        p.fillPath(path, QColor(4, 18, 28, 205) if self._occupied else QColor(18, 56, 78, 240))

        # 内阴影：裁进圆角区域，四边各铺一道暗渐变
        p.save()
        p.setClipPath(path)
        shadow = 130 if self._occupied else 165
        depth = 13.0

        g = QLinearGradient(0, rect.top(), 0, rect.top() + depth)
        g.setColorAt(0, QColor(0, 0, 0, shadow))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(g))

        g = QLinearGradient(0, rect.bottom(), 0, rect.bottom() - depth)
        g.setColorAt(0, QColor(0, 0, 0, shadow))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(g))

        g = QLinearGradient(rect.left(), 0, rect.left() + depth, 0)
        g.setColorAt(0, QColor(0, 0, 0, shadow))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(g))

        g = QLinearGradient(rect.right(), 0, rect.right() - depth, 0)
        g.setColorAt(0, QColor(0, 0, 0, shadow))
        g.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(rect, QBrush(g))
        p.restore()


class FloatingStatusWidget(QWidget):
    """可拖拽脱离 / 自动吸附主窗口槽位的圆角状态悬浮窗。

    始终是独立顶层 Tool 窗：
    - 吸附时 owner=主窗口、不置顶 -> 显示在槽位、随主窗口走、不浮到其他应用；
    - 拖出时无 owner、置顶 -> 独立存活，不受主窗口最小化/托盘影响。
    owner / 置顶只在松手判定后切换，拖拽过程中不重建窗口，保证一次拖成。
    """

    sync_clicked = pyqtSignal()
    project_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(None)
        self.main_window = parent
        self.docked = True
        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._press_pos: QPoint | None = None
        self._project_name = ""
        self._task_text = ""
        self.channel_rings: dict[str, ProgressRing] = {}
        self._channel_order: list[str] = []

        self.setWindowFlags(_BASE_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(FLOAT_W, FLOAT_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("点击切换项目；按住拖动可脱离；拖回槽位自动吸附")

        self._build_ui()
        self._dock_to_main()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 6, 8, 6)
        root.setSpacing(6)

        # 左侧：项目总状态环
        self.total_ring = ProgressRing(16, bounce_h=FLOAT_H - 10)
        self.total_ring.set_state("waiting", "#8A97AA")

        # 中间竖排：项目名（运行时追加任务状态）+ 渠道状态环（按配置动态）
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)
        self.project_label = QLabel("未选择项目")
        self.project_label.setStyleSheet("color: #F4F7FB; font-size: 11px; font-weight: 700; background: transparent;")
        self.project_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.repo_row = QHBoxLayout()
        self.repo_row.setContentsMargins(0, 0, 0, 0)
        self.repo_row.setSpacing(4)
        self.repo_row.addStretch(1)
        text_box.addWidget(self.project_label)
        text_box.addLayout(self.repo_row)

        # 右侧：同步按钮
        self.sync_btn = QPushButton("同步")
        self.sync_btn.setFixedSize(40, 26)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: #04222B; "
            f"border: 1px solid #16E5EE; border-radius: 4px; "
            f"font-size: 11px; font-weight: 700; padding: 0px; }}"
            f"QPushButton:hover {{ background-color: #5AF3F3; border: 1px solid #5AF3F3; }}"
            f"QPushButton:disabled {{ background-color: #16323F; color: #5A6678; border: 1px solid #16323F; }}"
        )
        self.sync_btn.clicked.connect(self.sync_clicked.emit)

        root.addWidget(self.total_ring)
        root.addLayout(text_box, 1)
        root.addWidget(self.sync_btn)

    # ------------------------------------------------------------ 渠道环
    def set_channels(self, keys: list[str]) -> None:
        """按当前项目配置的渠道重建渠道环（有几个排几个，顺序一致）。"""
        if list(keys) == self._channel_order and all(k in self.channel_rings for k in keys):
            return
        for ring in self.channel_rings.values():
            self.repo_row.removeWidget(ring)
            ring.setParent(None)
            ring.deleteLater()
        self.channel_rings.clear()
        self._channel_order = list(keys)
        # stretch 已在末尾，新环插到 stretch 前
        for key in self._channel_order:
            ring = ProgressRing(14)
            ring.set_state("waiting", "#8A97AA")
            self.channel_rings[key] = ring
            self.repo_row.insertWidget(self.repo_row.count() - 1, ring)
        self.update()

    def set_channel_state(self, key: str, state_key: str, color: str,
                          tooltip: str = "", animate: bool = False) -> None:
        ring = self.channel_rings.get(key)
        if ring is None:
            return
        ring.set_state(state_key, color, animate=animate)
        if tooltip:
            ring.setToolTip(tooltip)

    def clear_channels(self) -> None:
        self.set_channels([])

    # ------------------------------------------------------------ 同步动画
    def begin_sync(self, keys: list[str]) -> None:
        self.set_channels(keys)
        self.total_ring.set_progress(0.0)
        for ring in self.channel_rings.values():
            # 未轮到的渠道保持等待符号，轮到时由 channel_started 变环
            ring.set_state("waiting", "#8A97AA")

    def channel_started(self, key: str) -> None:
        ring = self.channel_rings.get(key)
        if ring is not None:
            ring.start_progress()

    def channel_finished(self, key: str, ok: bool) -> None:
        ring = self.channel_rings.get(key)
        if ring is None:
            return
        if ok:
            ring.finish("clean", CLEAN_COLOR)
        else:
            ring.finish("error", "#F27788")

    def set_overall_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.total_ring.set_progress(done / total)

    def end_sync(self, state_key: str, color: str) -> None:
        self.total_ring.finish(state_key, color)

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(), 10, 10)
        painter.fillPath(path, QColor(PANEL_RAISED))
        pen = QPen(QColor(ACCENT_COLOR))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(path)

    # ------------------------------------------------------------------ 拖拽 / 点击
    def mousePressEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            # 拖拽全程不重建窗口，只记录偏移
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_pos = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            if self._press_pos is not None:
                moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
                if moved >= 4:
                    self._dragging = True
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._dragging
            self._drag_offset = None
            self._press_pos = None
            self._dragging = False
            if was_dragging:
                # 松手后才切换归属 / 置顶（此时重建窗口不影响拖拽）
                self._evaluate_dock()
            else:
                self.project_clicked.emit()
            event.accept()

    def mouseDoubleClickEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.main_window is not None:
            self.main_window._show_main_from_floating()
            if not self.docked:
                self._dock_to_main()
            event.accept()

    def contextMenuEvent(self, event):  # noqa: N802 - Qt API
        if self.main_window is None:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #0B293B; border: 1px solid #1E4B5C; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; border-radius: 4px; }"
            "QMenu::item:selected { background: #0B6B7A; color: white; }"
        )
        show_action = menu.addAction("显示主窗口")
        dock_action = menu.addAction("吸附回槽位")
        menu.addSeparator()
        quit_action = menu.addAction("退出软件")
        chosen = menu.exec(event.globalPos())
        if chosen == show_action:
            self.main_window._show_main_from_floating()
        elif chosen == dock_action:
            self._dock_to_main()
        elif chosen == quit_action:
            self.main_window._exit_from_tray()

    # ------------------------------------------------------------------ 吸附
    def _anchor_pos(self) -> QPoint:
        """槽位中心（悬浮窗居中放入后）的全局坐标。"""
        mw = self.main_window
        if mw is not None and getattr(mw, "dock_slot", None) is not None:
            return mw.dock_slot.mapToGlobal(QPoint(SLOT_PAD, SLOT_PAD))
        if mw is None:
            return QPoint(0, 0)
        top_right = mw.frameGeometry().topRight()
        return QPoint(top_right.x() - FLOAT_W - 14, top_right.y + 42)

    def _dock_to_main(self) -> None:
        self.docked = True
        # owner=主窗口：显示在主窗口之上、随其最小化/关闭，但不浮到其他应用
        self.setParent(self.main_window, _BASE_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.move(self._anchor_pos())
        if self.main_window is not None:
            self.main_window._on_floating_dock_changed(True)
        self.show()
        self.update()

    def _detach(self, keep_pos: QPoint) -> None:
        self.docked = False
        # 无 owner + 全局置顶：脱离主窗口独立存活
        self.setParent(None, _TOP_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.move(keep_pos)
        self.show()
        if self.main_window is not None:
            self.main_window._on_floating_dock_changed(False)
        self.update()

    def _evaluate_dock(self) -> None:
        mw = self.main_window
        slot = getattr(mw, "dock_slot", None) if mw is not None else None
        should_dock = False
        if slot is not None:
            sg = slot.mapToGlobal(QPoint(0, 0))
            slot_rect = QRect(sg.x(), sg.y(), slot.width(), slot.height())
            inter = self.frameGeometry().intersected(slot_rect)
            if not inter.isEmpty():
                overlap = inter.width() * inter.height()
                should_dock = overlap >= int(FLOAT_W * FLOAT_H * 0.4)
        else:
            anchor = self._anchor_pos()
            dist = (self.frameGeometry().topLeft() - anchor).manhattanLength()
            should_dock = dist <= DOCK_SNAP_DISTANCE
        if should_dock:
            self._dock_to_main()
        else:
            self._detach(self.frameGeometry().topLeft())

    def follow_main_window(self) -> None:
        if self.docked and self.main_window is not None:
            self.move(self._anchor_pos())

    # ------------------------------------------------------------------ 数据更新
    def set_project_state(self, name: str, state_key: str, color: str, pixmap=None) -> None:
        self._project_name = name or "未选择项目"
        self._refresh_title()
        self.total_ring.set_state(state_key, color)

    def set_task(self, text: str) -> None:
        self._task_text = text or ""
        self._refresh_title()

    def _refresh_title(self) -> None:
        if self._task_text and self._task_text != "空闲":
            self.project_label.setText(f"{self._project_name} · {self._task_text}")
        else:
            self.project_label.setText(self._project_name)

    # 兼容旧调用：主窗口已改用 set_channel_state，保留转发避免遗漏
    def set_sync_enabled(self, enabled: bool) -> None:
        self.sync_btn.setEnabled(enabled)
