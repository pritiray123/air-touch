"""
Automated Verification & Screenshot Testing Suite for Precision Pinch-to-Scroll Engine.
Executes end-to-end automated verification for:
  1. Pinch Contact vs. Slight-Gap Test (Engage ratio=0.18, Gap release ratio=0.30 with zero scroll)
  2. Multi-Stroke Upward Scroll Test (Cumulative scroll with zero reverse kickback)
  3. Fist-Pause Isolation Test (Closed fist 1.2s hold triggers PAUSED, zero-cooldown release)
  4. Screenshot Captures: test_pinch_engaged.png and test_pinch_gap_release.png
"""

import os
import sys
import time
import math
import logging
from typing import List

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QLabel,
    QScrollArea,
    QFrame,
)
from PyQt6.QtGui import QGuiApplication

from overlay_ui import OverlayWidget
from gesture_engine import (
    LandmarkClassifier,
    GestureState,
    GestureStateMachine,
    SmoothScrollController,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestAutomation")


class MockLandmarkPoint:
    """Mock landmark representation matching MediaPipe landmark format."""
    def __init__(self, x: float, y: float, z: float = 0.0):
        self.x = x
        self.y = y
        self.z = z


def make_pinch_hand(
    pinch_ratio: float = 0.18,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    is_fist: bool = False,
    is_open_palm: bool = False,
) -> List[MockLandmarkPoint]:
    """
    Generates synthetic 21-point hand landmark model with realistic finger geometry,
    wrist-relative coordinates, precise Index-Thumb pinch ratio, and posture variations.
    """
    w = MockLandmarkPoint(0.50 + offset_x, 0.70 + offset_y)
    mcp9 = MockLandmarkPoint(0.50 + offset_x, 0.45 + offset_y)
    palm_size = 0.25

    if is_fist:
        # Closed fist: all 4 non-thumb fingers tightly curled
        lms = [w]
        # Thumb 1-4
        lms.extend([
            MockLandmarkPoint(0.48 + offset_x, 0.65 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.60 + offset_y),
            MockLandmarkPoint(0.42 + offset_x, 0.56 + offset_y),
            MockLandmarkPoint(0.44 + offset_x, 0.55 + offset_y),
        ])
        # Index 5-8 (curled)
        lms.extend([
            MockLandmarkPoint(0.45 + offset_x, 0.52 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.44 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.48 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.56 + offset_y),
        ])
        # Middle 9-12 (curled)
        lms.extend([
            mcp9,
            MockLandmarkPoint(0.50 + offset_x, 0.38 + offset_y),
            MockLandmarkPoint(0.50 + offset_x, 0.44 + offset_y),
            MockLandmarkPoint(0.50 + offset_x, 0.55 + offset_y),
        ])
        # Ring 13-16 (curled)
        lms.extend([
            MockLandmarkPoint(0.55 + offset_x, 0.46 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.39 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.45 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.56 + offset_y),
        ])
        # Pinky 17-20 (curled)
        lms.extend([
            MockLandmarkPoint(0.60 + offset_x, 0.48 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.42 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.47 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.57 + offset_y),
        ])
        return lms

    elif is_open_palm:
        # Open palm: all 5 fingers fully extended and spread apart
        lms = [w]
        lms.extend([
            MockLandmarkPoint(0.42 + offset_x, 0.65 + offset_y),
            MockLandmarkPoint(0.35 + offset_x, 0.58 + offset_y),
            MockLandmarkPoint(0.28 + offset_x, 0.50 + offset_y),
            MockLandmarkPoint(0.22 + offset_x, 0.44 + offset_y),
        ])
        lms.extend([
            MockLandmarkPoint(0.45 + offset_x, 0.48 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.38 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.28 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.18 + offset_y),
        ])
        lms.extend([
            mcp9,
            MockLandmarkPoint(0.50 + offset_x, 0.36 + offset_y),
            MockLandmarkPoint(0.50 + offset_x, 0.26 + offset_y),
            MockLandmarkPoint(0.50 + offset_x, 0.16 + offset_y),
        ])
        lms.extend([
            MockLandmarkPoint(0.55 + offset_x, 0.48 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.38 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.28 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.18 + offset_y),
        ])
        lms.extend([
            MockLandmarkPoint(0.60 + offset_x, 0.50 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.42 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.34 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.24 + offset_y),
        ])
        return lms

    else:
        # Precision pinch posture: Thumb Tip & Index Tip distance = pinch_ratio * palm_size,
        # with Middle and Ring fingers partially relaxed/extended (guard active).
        anchor_x = 0.45 + offset_x
        anchor_y = 0.35 + offset_y
        dist_pt = pinch_ratio * palm_size

        t_tip = MockLandmarkPoint(anchor_x - dist_pt / 2.0, anchor_y)
        i_tip = MockLandmarkPoint(anchor_x + dist_pt / 2.0, anchor_y)

        lms = [w]
        # Thumb 1-4
        lms.extend([
            MockLandmarkPoint(0.48 + offset_x, 0.62 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.54 + offset_y),
            MockLandmarkPoint(0.43 + offset_x, 0.44 + offset_y),
            t_tip,
        ])
        # Index 5-8
        lms.extend([
            MockLandmarkPoint(0.45 + offset_x, 0.50 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.44 + offset_y),
            MockLandmarkPoint(0.45 + offset_x, 0.39 + offset_y),
            i_tip,
        ])
        # Middle 9-12 (extended / relaxed)
        lms.extend([
            mcp9,
            MockLandmarkPoint(0.50 + offset_x, 0.36 + offset_y),
            MockLandmarkPoint(0.50 + offset_x, 0.28 + offset_y),
            MockLandmarkPoint(0.50 + offset_x, 0.20 + offset_y),
        ])
        # Ring 13-16 (relaxed)
        lms.extend([
            MockLandmarkPoint(0.55 + offset_x, 0.46 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.38 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.30 + offset_y),
            MockLandmarkPoint(0.55 + offset_x, 0.22 + offset_y),
        ])
        # Pinky 17-20 (relaxed)
        lms.extend([
            MockLandmarkPoint(0.60 + offset_x, 0.48 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.41 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.34 + offset_y),
            MockLandmarkPoint(0.60 + offset_x, 0.26 + offset_y),
        ])
        return lms


class MockDocumentViewer(QWidget):
    """
    Test window simulating an active desktop window (PDF/browser/code editor)
    with live metrics card and smooth 2D scroll tracking.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mock Desktop Window - Document Viewer")
        self.resize(540, 520)
        self.setObjectName("mockViewer")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Header bar
        header = QLabel("DESKTOP TARGET APPLICATION (Pinch-to-Scroll Test Target)")
        header.setStyleSheet("color: #E5E7EB; font-weight: bold; font-size: 12px; background: #242826; padding: 6px 10px; border-radius: 4px;")
        layout.addWidget(header)

        # Live Test Metrics Card
        self.metrics_card = QFrame()
        self.metrics_card.setObjectName("metricsCard")
        metrics_layout = QVBoxLayout(self.metrics_card)
        metrics_layout.setContentsMargins(10, 8, 10, 8)
        metrics_layout.setSpacing(4)

        self.test_phase_label = QLabel("STAGE: Initializing...")
        self.test_phase_label.setStyleSheet("color: #4ADE80; font-weight: bold; font-size: 11px;")
        metrics_layout.addWidget(self.test_phase_label)

        self.test_detail_label = QLabel("Awaiting verification loop...")
        self.test_detail_label.setStyleSheet("color: #9CA3AF; font-size: 10px; font-family: monospace;")
        metrics_layout.addWidget(self.test_detail_label)

        self.scroll_stat_label = QLabel("Scrollbar: 0 px | Displacement: 0 px | State: Initializing")
        self.scroll_stat_label.setStyleSheet("color: #E5E7EB; font-size: 10px; font-family: monospace;")
        metrics_layout.addWidget(self.scroll_stat_label)

        layout.addWidget(self.metrics_card)

        # Scrollable document area
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("testScrollArea")
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)

        for i in range(1, 150):
            row_label = QLabel(
                f"Line {i:03d} | Touchless Screen Scrolling Test Content | Precision Pinch-to-Scroll Viewport Offset..."
            )
            row_label.setStyleSheet(
                "color: #9CA3AF; font-family: monospace; font-size: 11px; padding: 3px 6px; border-bottom: 1px solid #1F2421;"
            )
            self.content_layout.addWidget(row_label)

        self.content_widget.setLayout(self.content_layout)
        self.scroll_area.setWidget(self.content_widget)
        layout.addWidget(self.scroll_area)

        self.setStyleSheet("""
        #mockViewer {
            background-color: #141615;
            border: 1px solid #2C322E;
            border-radius: 4px;
        }
        #metricsCard {
            background-color: #1A1D1B;
            border: 1px solid #2C322E;
            border-radius: 4px;
        }
        #testScrollArea {
            background-color: #1A1D1B;
            border: 1px solid #2C322E;
            border-radius: 4px;
        }
        QScrollBar:vertical {
            background: #141615;
            width: 8px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #4ADE80;
            border-radius: 4px;
            min-height: 24px;
        }
        """)

    def update_metrics(self, phase: str, detail: str, stat: str):
        self.test_phase_label.setText(phase)
        self.test_detail_label.setText(detail)
        self.scroll_stat_label.setText(stat)


def capture_screenshot(target_path: str, overlay: QWidget, doc_viewer: QWidget, screen, app):
    """Flushes Qt repaint events and compositor pipeline, then captures high-resolution desktop screenshot."""
    doc_viewer.update()
    overlay.update()
    doc_viewer.repaint()
    overlay.repaint()
    for _ in range(25):
        app.processEvents()
        time.sleep(0.03)

    if screen:
        pix = screen.grabWindow(0)
        pix.save(target_path)
    else:
        pix = overlay.grab()
        pix.save(target_path)
    logger.info(f"Captured screenshot: {target_path} ({os.path.getsize(target_path)} bytes)")
    assert os.path.exists(target_path), f"Screenshot was not created: {target_path}"
    assert os.path.getsize(target_path) > 1000, f"Screenshot file is empty: {target_path}"


def run_pinch_verification_suite():
    logger.info("=================================================================")
    logger.info("STARTING PRECISION PINCH-TO-SCROLL AUTOMATED VERIFICATION SUITE")
    logger.info("=================================================================")

    app = QApplication.instance() or QApplication(sys.argv)
    screen = QGuiApplication.primaryScreen()

    # Instantiate Target Document Window & Floating Overlay Widget
    doc_viewer = MockDocumentViewer()
    doc_viewer.move(50, 60)
    doc_viewer.show()

    overlay = OverlayWidget(camera_index=0)
    overlay.move(620, 60)
    overlay.show()

    # Allow initial layout stabilization
    t_start = time.time()
    while time.time() - t_start < 1.0:
        app.processEvents()
        time.sleep(0.05)

    scroll_bar = doc_viewer.scroll_area.verticalScrollBar()
    scroll_bar.setValue(600)
    app.processEvents()
    initial_scroll_pos = scroll_bar.value()

    # -----------------------------------------------------------------
    # TEST 1: Pinch Contact vs. Slight-Gap & Omnidirectional Movement
    # -----------------------------------------------------------------
    logger.info("\n--- [TEST 1] Pinch Contact vs. Slight-Gap Test ---")
    test_sm = GestureStateMachine(sensitivity_gain=3800.0)
    dispatched_ticks = []

    def mock_dispatcher(dx, dy):
        if dx != 0 or dy != 0:
            dispatched_ticks.append((dx, dy))
            if dy != 0:
                new_v = scroll_bar.value() - int(dy)
                scroll_bar.setValue(max(0, min(scroll_bar.maximum(), new_v)))

    test_sm.scroller.scroll_dispatcher = mock_dispatcher

    # 1.1 Contact phase: pinch_ratio = 0.18 (< 0.22 threshold -> PINCHED)
    sim_t = 1000.0
    # Frame 1 & 2: Settle buffer check
    st1, dx1, dy1, _ = test_sm.update(make_pinch_hand(pinch_ratio=0.18, offset_y=0.0), current_time=sim_t)
    sim_t += 0.033
    st2, dx2, dy2, _ = test_sm.update(make_pinch_hand(pinch_ratio=0.18, offset_y=0.0), current_time=sim_t)

    logger.info(f"Settle Frame 1: state={st1.value}, dx={dx1}, dy={dy1}")
    logger.info(f"Settle Frame 2: state={st2.value}, dx={dx2}, dy={dy2}")
    assert st1 == GestureState.PINCHED and st2 == GestureState.PINCHED
    assert dx1 == 0.0 and dy1 == 0.0, "Settle buffer must absorb closing motion with zero scroll"
    assert dx2 == 0.0 and dy2 == 0.0, "Settle buffer must absorb closing motion with zero scroll"

    # Frame 3+: Upward and omnidirectional drag after settle buffer
    upward_stroke_deltas = []
    for k in range(1, 6):
        sim_t += 0.033
        curr_y = -0.036 * k
        curr_x = 0.015 * k
        st, dx, dy, _ = test_sm.update(
            make_pinch_hand(pinch_ratio=0.18, offset_x=curr_x, offset_y=curr_y),
            current_time=sim_t,
        )
        test_sm.scroller.tick_60hz()
        upward_stroke_deltas.append((dx, dy))
        app.processEvents()

    # Finish swipe with momentum decay
    test_sm.scroller.end_swipe()
    for _ in range(50):
        test_sm.scroller.tick_60hz()
        app.processEvents()

    active_swipe_y = sum(d[1] for d in dispatched_ticks)
    active_swipe_x = sum(d[0] for d in dispatched_ticks)
    post_pinch_scroll_val = scroll_bar.value()

    logger.info(f"Active Pinch Total Dispatched: Y={active_swipe_y} px, X={active_swipe_x} px")
    logger.info(f"Scrollbar moved from {initial_scroll_pos} to {post_pinch_scroll_val}")
    assert active_swipe_y > 200, f"Expected substantial scroll displacement, got {active_swipe_y}"
    assert active_swipe_x > 0, f"Expected positive horizontal scroll displacement, got {active_swipe_x}"
    assert post_pinch_scroll_val < initial_scroll_pos, "Scrollbar should have moved UP"

    # Update overlay UI to show PINCHED state and capture screenshot 1
    overlay._on_state_update("PINCHED", "#4ADE80")
    doc_viewer.update_metrics(
        phase="TEST 1: Pinch Contact & Omnidirectional Scroll — PASSED",
        detail="Pinch contact (ratio=0.18) triggered smooth 2D scroll after 2-frame settle buffer.",
        stat=f"Dispatched: Y=+{active_swipe_y} px, X=+{active_swipe_x} px | Scrollbar: {initial_scroll_pos} -> {post_pinch_scroll_val}",
    )

    # Capture Screenshot 1: test_pinch_engaged.png
    test_1_path = os.path.abspath("test_pinch_engaged.png")
    capture_screenshot(test_1_path, overlay, doc_viewer, screen, app)
    logger.info("✓ Test 1.1 Passed: Pinch contact and omnidirectional drag verified.")

    # 1.2 Slight-Gap Release phase: pinch_ratio = 0.30 (> 0.27 release threshold -> OPEN)
    gap_dispatched_ticks = []
    test_sm.scroller.scroll_dispatcher = lambda dx, dy: gap_dispatched_ticks.append((dx, dy))

    st_gap, dx_g, dy_g, _ = test_sm.update(make_pinch_hand(pinch_ratio=0.30), current_time=sim_t + 0.1)
    logger.info(f"Slight Gap Frame: state={st_gap.value}, is_pinched={test_sm.is_pinched}")
    assert st_gap == GestureState.OPEN, "State must immediately release to OPEN on slight gap"
    assert not test_sm.is_pinched, "is_pinched must immediately become False"

    # Rapid up and down hand movement while gap is open: MUST generate ZERO scroll
    pre_gap_scroll_val = scroll_bar.value()
    for k in range(1, 12):
        sim_t += 0.033
        offset_y = math.sin(k * 0.8) * 0.15
        offset_x = math.cos(k * 0.8) * 0.15
        st_move, dx_m, dy_m, _ = test_sm.update(
            make_pinch_hand(pinch_ratio=0.30, offset_x=offset_x, offset_y=offset_y),
            current_time=sim_t,
        )
        test_sm.scroller.tick_60hz()
        assert dx_m == 0.0 and dy_m == 0.0, f"Movement during slight gap must yield 0 delta, got ({dx_m}, {dy_m})"

    post_gap_scroll_val = scroll_bar.value()
    assert len(gap_dispatched_ticks) == 0, f"Expected 0 dispatched ticks during gap movement, got {len(gap_dispatched_ticks)}"
    assert post_gap_scroll_val == pre_gap_scroll_val, "Scroll position must not move during slight gap repositioning"
    logger.info("✓ Test 1.2 Passed: Slight-gap instant release and zero scroll input verified.")

    # Update overlay UI to show OPEN state and capture screenshot 2
    overlay._on_state_update("OPEN", "#9CA3AF")
    doc_viewer.update_metrics(
        phase="TEST 1 (Gap Release): Instant Clutch Release — PASSED",
        detail="Slight gap (ratio=0.30) immediately disengaged scroll; rapid hand movement produced zero scroll.",
        stat=f"Active Movement: Zero Input (0 ticks) | Reposition Freely: Verified | Scrollbar: {post_gap_scroll_val} (Fixed)",
    )

    # Capture Screenshot 2: test_pinch_gap_release.png
    test_2_path = os.path.abspath("test_pinch_gap_release.png")
    capture_screenshot(test_2_path, overlay, doc_viewer, screen, app)
    logger.info("✓ Test 1.2 Screenshot captured: test_pinch_gap_release.png")

    # -----------------------------------------------------------------
    # TEST 2: Multi-Stroke Upward Scroll Test (Zero Reverse Kickback)
    # -----------------------------------------------------------------
    logger.info("\n--- [TEST 2] Multi-Stroke Upward Scroll Test ---")
    multi_stroke_dispatched = []
    test_sm.scroller.scroll_dispatcher = lambda dx, dy: multi_stroke_dispatched.append((dx, dy))

    # Cycle 1: Pinch-down & swipe up
    sim_t += 0.2
    for _ in range(2):
        test_sm.update(make_pinch_hand(pinch_ratio=0.18, offset_y=0.0), current_time=sim_t)
        sim_t += 0.033
    for k in range(1, 5):
        sim_t += 0.033
        test_sm.update(make_pinch_hand(pinch_ratio=0.18, offset_y=-0.04 * k), current_time=sim_t)
        test_sm.scroller.tick_60hz()
    test_sm.scroller.end_swipe()
    for _ in range(40):
        test_sm.scroller.tick_60hz()

    # Cycle 2: Gap open & move hand down in air (reset stroke)
    sim_t += 0.1
    for k in range(1, 6):
        sim_t += 0.033
        test_sm.update(make_pinch_hand(pinch_ratio=0.30, offset_y=-0.16 + 0.04 * k), current_time=sim_t)
        test_sm.scroller.tick_60hz()

    # Cycle 3: Pinch-down & second swipe up
    sim_t += 0.1
    for _ in range(2):
        test_sm.update(make_pinch_hand(pinch_ratio=0.18, offset_y=0.04), current_time=sim_t)
        sim_t += 0.033
    for k in range(1, 5):
        sim_t += 0.033
        test_sm.update(make_pinch_hand(pinch_ratio=0.18, offset_y=0.04 - 0.04 * k), current_time=sim_t)
        test_sm.scroller.tick_60hz()
    test_sm.scroller.end_swipe()
    for _ in range(40):
        test_sm.scroller.tick_60hz()

    total_multi_y = sum(d[1] for d in multi_stroke_dispatched)
    reverse_kickback = sum(d[1] for d in multi_stroke_dispatched if d[1] < 0)

    logger.info(f"Multi-Stroke Cumulative Scroll: {total_multi_y} px")
    logger.info(f"Multi-Stroke Reverse Kickback: {reverse_kickback} px")
    assert total_multi_y > 400, f"Expected cumulative upward scroll > 400 px, got {total_multi_y}"
    assert reverse_kickback == 0, f"Expected 0 reverse kickback, got {reverse_kickback}"
    logger.info("✓ Test 2 Passed: Multi-stroke scroll verified with zero reverse kickback.")

    # -----------------------------------------------------------------
    # TEST 3: Fist-Pause Isolation & Zero-Cooldown Lag Test
    # -----------------------------------------------------------------
    logger.info("\n--- [TEST 3] Fist-Pause Isolation & Zero-Cooldown Recovery Test ---")
    fist_dispatched = []
    test_sm.scroller.scroll_dispatcher = lambda dx, dy: fist_dispatched.append((dx, dy))

    sim_t = 5000.0
    # 3.1 Closed fist held for 0.5s -> NOT paused yet, but can_pinch is FALSE (no false pinch)
    st_f1, _, _, _ = test_sm.update(make_pinch_hand(is_fist=True), current_time=sim_t)
    st_f2, _, _, _ = test_sm.update(make_pinch_hand(is_fist=True), current_time=sim_t + 0.5)
    logger.info(f"Fist at 0.5s: state={st_f2.value}, is_pinched={test_sm.is_pinched}")
    assert st_f2 != GestureState.PAUSED, "Should not pause before 1.2s threshold"
    assert not test_sm.is_pinched, "Closed fist must never trigger false pinch"

    # 3.2 Closed fist held for 1.3s (>= 1.2s) -> triggers PAUSED
    st_f3, _, _, _ = test_sm.update(make_pinch_hand(is_fist=True), current_time=sim_t + 1.3)
    logger.info(f"Fist at 1.3s: state={st_f3.value}")
    assert st_f3 == GestureState.PAUSED, "Fist held >= 1.2s must enter PAUSED state"
    assert not test_sm.is_pinched

    # 3.3 Releasing fist immediately restores readiness with zero cooldown lag
    st_rel, _, _, _ = test_sm.update(make_pinch_hand(pinch_ratio=0.30), current_time=sim_t + 1.35)
    logger.info(f"Fist released immediately (+50ms): state={st_rel.value}")
    assert st_rel == GestureState.OPEN, "Releasing fist must immediately restore OPEN state with zero cooldown lag"

    # 3.4 Immediate pinch readiness after release
    st_p1, _, _, _ = test_sm.update(make_pinch_hand(pinch_ratio=0.18), current_time=sim_t + 1.40)
    logger.info(f"Immediate pinch after unpause: state={st_p1.value}")
    assert st_p1 == GestureState.PINCHED, "Hand must immediately engage pinch with zero cooldown delay"

    logger.info("✓ Test 3 Passed: Fist-pause isolation and zero cooldown lag verified.")

    # Clean up test windows cleanly
    overlay.close()
    doc_viewer.close()
    app.processEvents()

    logger.info("\n=================================================================")
    logger.info("ALL PINCH-TO-SCROLL VERIFICATION TESTS PASSED SUCCESSFULLY!")
    logger.info(f"Artifacts generated:")
    logger.info(f"  1. {test_1_path}")
    logger.info(f"  2. {test_2_path}")
    logger.info("=================================================================")
    return True


if __name__ == "__main__":
    success = run_pinch_verification_suite()
    sys.exit(0 if success else 1)
