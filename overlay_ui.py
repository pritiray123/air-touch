"""
Native Floating Desktop Overlay Widget with Live Gesture Feedback.
Provides borderless, always-on-top desktop overlay with instant
IDLE / CLUTCH / SCROLLING status pill feedback and 60Hz decoupled dispatch.
"""

import sys
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap, QColor, QPainter, QBrush, QGuiApplication
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QSlider,
)

from gesture_engine import GestureWorkerThread, GestureEngine, GestureState


class StatusIndicator(QWidget):
    """Minimal circular status dot indicator."""
    def __init__(self, color_hex: str = "#9CA3AF", size: int = 9, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.color_hex = color_hex

    def set_color(self, color_hex: str):
        self.color_hex = color_hex
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(self.color_hex)))
        painter.drawEllipse(0, 0, self.width(), self.height())


class OverlayWidget(QWidget):
    """
    Lightweight, borderless, always-on-top floating desktop overlay widget.
    Sits directly over desktop applications without opening a browser window.
    """
    def __init__(self, camera_index: int = 0, parent=None):
        super().__init__(parent)
        self.camera_index = camera_index
        self.is_tracking = False
        self.drag_position = QPoint()

        # Engine & worker thread
        self.engine = GestureEngine()
        self.worker: Optional[GestureWorkerThread] = None

        # 60Hz physics timer for momentum glide
        self.physics_timer = QTimer(self)
        self.physics_timer.setInterval(16)  # ~60Hz
        self.physics_timer.timeout.connect(self._on_physics_tick)

        # Window configuration: native topmost borderless floating desktop overlay
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if sys.platform == "darwin":
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._setup_ui()
        self._apply_styles()

    def position_in_top_right(self, margin: int = 24):
        """Explicitly places the widget in the top-right corner of the primary screen."""
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            x = screen_geo.x() + screen_geo.width() - self.width() - margin
            y = screen_geo.y() + margin
            self.move(x, y)
        else:
            self.move(100, 100)

    def show_floating(self):
        """Ensures floating overlay is positioned, shown, raised, and activated."""
        self.position_in_top_right(margin=24)
        self.show()
        self.raise_()
        self.activateWindow()

    def _setup_ui(self):
        # Outer container frame with dark organic styling
        self.container = QFrame(self)
        self.container.setObjectName("container")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(12, 12, 12, 12)
        container_layout.setSpacing(10)

        # 1. Header & Drag Bar
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)

        self.status_dot = StatusIndicator(color_hex="#9CA3AF", size=9)
        header_layout.addWidget(self.status_dot)

        self.title_label = QLabel("TOUCHLESS SCROLL")
        self.title_label.setObjectName("titleLabel")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()

        self.fps_label = QLabel("")
        self.fps_label.setObjectName("fpsLabel")
        header_layout.addWidget(self.fps_label)

        # Mini toggle for settings drawer
        self.toggle_settings_btn = QPushButton("⚙")
        self.toggle_settings_btn.setObjectName("iconBtn")
        self.toggle_settings_btn.setToolTip("Settings & Sensitivity")
        self.toggle_settings_btn.clicked.connect(self._toggle_settings)
        header_layout.addWidget(self.toggle_settings_btn)

        # Mini toggle for camera preview
        self.toggle_preview_btn = QPushButton("👁")
        self.toggle_preview_btn.setObjectName("iconBtn")
        self.toggle_preview_btn.setToolTip("Toggle Hand Preview")
        self.toggle_preview_btn.clicked.connect(self._toggle_preview)
        header_layout.addWidget(self.toggle_preview_btn)

        # Close button
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setToolTip("Quit Application")
        self.close_btn.clicked.connect(self.close)
        header_layout.addWidget(self.close_btn)

        container_layout.addLayout(header_layout)

        # 2. Live Gesture Status Card & Pill Indicator
        self.status_card = QFrame()
        self.status_card.setObjectName("statusCard")
        status_card_layout = QVBoxLayout(self.status_card)
        status_card_layout.setContentsMargins(10, 8, 10, 8)
        status_card_layout.setSpacing(6)

        pill_row = QHBoxLayout()
        pill_label = QLabel("GESTURE STATE:")
        pill_label.setObjectName("fieldLabel")
        pill_row.addWidget(pill_label)

        # Live status pill indicator (OPEN / PINCHED / PAUSED)
        self.status_pill = QLabel("OPEN")
        self.status_pill.setObjectName("statusPill")
        self.status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_pill.setProperty("state", "OPEN")
        pill_row.addWidget(self.status_pill)
        pill_row.addStretch()
        status_card_layout.addLayout(pill_row)

        desc_row = QHBoxLayout()
        desc_title = QLabel("ACTION:")
        desc_title.setObjectName("fieldLabel")
        self.action_desc_label = QLabel("Ready for pinch")
        self.action_desc_label.setObjectName("actionDesc")
        desc_row.addWidget(desc_title)
        desc_row.addWidget(self.action_desc_label)
        desc_row.addStretch()
        status_card_layout.addLayout(desc_row)

        container_layout.addWidget(self.status_card)

        # 3. Mini Hand Landmark Preview (Folded by default)
        self.preview_frame = QFrame()
        self.preview_frame.setObjectName("previewFrame")
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(4, 4, 4, 4)
        preview_layout.setSpacing(0)

        self.preview_label = QLabel("Camera feed idle")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setFixedSize(286, 170)
        self.preview_label.setObjectName("previewLabel")
        preview_layout.addWidget(self.preview_label)

        self.preview_frame.setVisible(False)
        container_layout.addWidget(self.preview_frame)

        # 4. Settings Drawer (Collapsible)
        self.settings_frame = QFrame()
        self.settings_frame.setObjectName("settingsFrame")
        settings_layout = QVBoxLayout(self.settings_frame)
        settings_layout.setContentsMargins(8, 8, 8, 8)
        settings_layout.setSpacing(6)

        gain_row = QHBoxLayout()
        gain_lbl = QLabel("Sensitivity:")
        gain_lbl.setObjectName("fieldLabel")
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setRange(1500, 7000)
        self.gain_slider.setValue(int(self.engine.state_machine.scroller.sensitivity_gain))
        self.gain_slider.valueChanged.connect(self._on_sensitivity_changed)
        gain_row.addWidget(gain_lbl)
        gain_row.addWidget(self.gain_slider)
        settings_layout.addLayout(gain_row)

        self.settings_frame.setVisible(False)
        container_layout.addWidget(self.settings_frame)

        # 5. Main Action Toggle Button ("Start Gesture Control" / "Stop")
        self.toggle_btn = QPushButton("Start Gesture Control")
        self.toggle_btn.setObjectName("toggleBtn")
        self.toggle_btn.clicked.connect(self.toggle_gesture_control)
        container_layout.addWidget(self.toggle_btn)

        # 6. Quick Gesture Guide Pill
        self.guide_label = QLabel(
            "👌 Pinch Index + Thumb: Drag to Scroll (Any Direction)\n"
            "🤏 Slight Gap: Release / Reposition Freely\n"
            "✊ Fist (1.2s) / ✋ Palm: Pause / Resume"
        )
        self.guide_label.setObjectName("guideLabel")
        self.guide_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self.guide_label)

        self.setFixedWidth(318)

    def _apply_styles(self):
        """Applies dark organic design aesthetic and sharp 4px rounded corners."""
        qss = """
        #container {
            background-color: #141615;
            border: 1px solid #2C322E;
            border-radius: 4px;
        }

        #titleLabel {
            color: #E5E7EB;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }

        #fpsLabel {
            color: #9CA3AF;
            font-size: 10px;
            font-family: Menlo, Monaco, "Courier New", monospace;
            padding-right: 2px;
        }

        #iconBtn, #closeBtn {
            background-color: #242826;
            color: #9CA3AF;
            border: 1px solid #2C322E;
            border-radius: 4px;
            font-size: 11px;
            width: 20px;
            height: 20px;
        }
        #iconBtn:hover {
            color: #E5E7EB;
            border-color: #4ADE80;
        }
        #closeBtn:hover {
            color: #EF4444;
            border-color: #EF4444;
        }

        #statusCard, #settingsFrame {
            background-color: #242826;
            border: 1px solid #2C322E;
            border-radius: 4px;
        }

        #fieldLabel {
            color: #9CA3AF;
            font-size: 10px;
            font-weight: 600;
        }

        #actionDesc {
            color: #E5E7EB;
            font-size: 10px;
            font-weight: 600;
        }

        #statusPill {
            font-size: 10px;
            font-weight: 800;
            padding: 2px 8px;
            border-radius: 4px;
        }
        #statusPill[state="OPEN"] {
            background-color: #1A1D1B;
            color: #9CA3AF;
            border: 1px solid #2C322E;
        }
        #statusPill[state="PINCHED"] {
            background-color: #132B1A;
            color: #4ADE80;
            border: 1px solid #4ADE80;
        }
        #statusPill[state="PAUSED"] {
            background-color: #2B2414;
            color: #FBBF24;
            border: 1px solid #FBBF24;
        }
        #statusPill[state="LOADING"] {
            background-color: #2B2414;
            color: #FBBF24;
            border: 1px solid #FBBF24;
        }

        #previewFrame {
            background-color: #141615;
            border: 1px solid #2C322E;
            border-radius: 4px;
        }

        #previewLabel {
            background-color: #0E100F;
            color: #9CA3AF;
            font-size: 10px;
            border-radius: 4px;
        }

        #toggleBtn {
            background-color: #242826;
            color: #4ADE80;
            border: 1px solid #4ADE80;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 700;
            padding: 8px 12px;
        }
        #toggleBtn:hover {
            background-color: #2D3430;
        }
        #toggleBtn:pressed {
            background-color: #1F2421;
        }

        #toggleBtn[active="true"] {
            background-color: #242826;
            color: #EF4444;
            border: 1px solid #EF4444;
        }
        #toggleBtn[active="true"]:hover {
            background-color: #332020;
        }

        QSlider::groove:horizontal {
            background: #141615;
            height: 4px;
            border-radius: 2px;
        }
        QSlider::sub-page:horizontal {
            background: #4ADE80;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #E5E7EB;
            width: 10px;
            margin-top: -3px;
            margin-bottom: -3px;
            border-radius: 2px;
        }

        #guideLabel {
            color: #9CA3AF;
            font-size: 9px;
            line-height: 1.35;
            padding-top: 2px;
        }
        """
        self.setStyleSheet(qss)

    def _toggle_preview(self):
        visible = not self.preview_frame.isVisible()
        self.preview_frame.setVisible(visible)
        self.adjustSize()

    def _toggle_settings(self):
        visible = not self.settings_frame.isVisible()
        self.settings_frame.setVisible(visible)
        self.adjustSize()

    def _on_sensitivity_changed(self):
        new_gain = float(self.gain_slider.value())
        self.engine.state_machine.scroller.sensitivity_gain = new_gain

    def _on_physics_tick(self):
        """Called every 16ms by QTimer to deliver smooth momentum glide."""
        if self.is_tracking:
            self.engine.physics_tick(0.016)

    def toggle_gesture_control(self):
        if self.is_tracking:
            self.stop_tracking()
        else:
            self.start_tracking()

    def start_tracking(self):
        if self.is_tracking:
            return

        self.toggle_btn.setEnabled(False)
        self.toggle_btn.setText("Starting Vision...")
        self._update_pill_ui("LOADING", "#FBBF24")
        self.action_desc_label.setText("Loading Vision...")

        self.worker = GestureWorkerThread(camera_index=self.camera_index, engine=self.engine)
        self.worker.state_signal.connect(self._on_state_update)
        self.worker.fps_signal.connect(self._on_fps_update)
        self.worker.frame_signal.connect(self._on_frame_update)
        self.worker.error_signal.connect(self._on_error)

        self.worker.start()
        self.physics_timer.start()
        self.is_tracking = True

        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setText("Stop Gesture Control")
        self.toggle_btn.setProperty("active", "true")
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)

    def stop_tracking(self):
        if not self.is_tracking:
            return

        self.toggle_btn.setEnabled(False)
        self.toggle_btn.setText("Stopping...")

        self.physics_timer.stop()
        if self.worker is not None:
            try:
                self.worker.state_signal.disconnect()
                self.worker.fps_signal.disconnect()
                self.worker.frame_signal.disconnect()
                self.worker.error_signal.disconnect()
            except Exception:
                pass
            self.worker.stop()
            self.worker = None

        self.is_tracking = False
        self._update_pill_ui("OPEN", "#9CA3AF")
        self.action_desc_label.setText("Tracking Stopped")
        self.fps_label.setText("")
        self.preview_label.setText("Camera feed idle")
        self.preview_label.setPixmap(QPixmap())

        self.toggle_btn.setEnabled(True)
        self.toggle_btn.setText("Start Gesture Control")
        self.toggle_btn.setProperty("active", "false")
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)

    def _update_pill_ui(self, state_str: str, color_hex: str):
        self.status_pill.setText(f"● {state_str}")
        self.status_pill.setProperty("state", state_str)
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)
        self.status_dot.set_color(color_hex)

    @pyqtSlot(str, str)
    def _on_state_update(self, state_str: str, color_hex: str):
        self._update_pill_ui(state_str, color_hex)
        if state_str == "PINCHED":
            self.action_desc_label.setText("Pinch Active (Drag to Scroll)")
        elif state_str == "PAUSED":
            self.action_desc_label.setText("Paused (Fist / Palm Held)")
        elif state_str == "LOADING":
            self.action_desc_label.setText("Loading Vision...")
        elif state_str == "OPEN":
            self.action_desc_label.setText("Pinch Open (Clutch / Moving Freely)")
        else:
            self.action_desc_label.setText("Searching for Hand...")

    @pyqtSlot(float)
    def _on_fps_update(self, fps: float):
        self.fps_label.setText(f"{fps:.0f} FPS")

    @pyqtSlot(object)
    def _on_frame_update(self, rgb_frame):
        if not self.preview_frame.isVisible():
            return
        h, w, ch = rgb_frame.shape
        bytes_per_line = ch * w
        q_img = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img).scaled(
            self.preview_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setPixmap(pixmap)

    @pyqtSlot(str)
    def _on_error(self, err_msg: str):
        self._update_pill_ui("ERROR", "#EF4444")
        self.action_desc_label.setText(err_msg)
        self.stop_tracking()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def closeEvent(self, event):
        self.stop_tracking()
        self.engine.close()
        event.accept()
