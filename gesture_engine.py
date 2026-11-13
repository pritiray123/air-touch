"""
Precision Index-Thumb Pinch-to-Scroll Gesture Engine for Touchless Desktop Navigation.
Features:
  - Scale-invariant Index-Thumb pinch detection normalized by palm size (Wrist 0 to Middle MCP 9).
  - Tight micro-hysteresis thresholds: Engage < 0.22, Release > 0.27 (instant release on slight gap).
  - Guard against closed fist false-pinch (requires Middle 12 or Ring 16 finger relaxed/extended).
  - 2-frame (~60ms) settle guard after pinch contact to eliminate twitch.
  - Omnidirectional 2D scrolling (up, down, left, right) with non-linear velocity curve.
  - High-frequency 60Hz decoupled sub-pixel accumulator with exponential inertia decay (velocity *= 0.85).
  - Zero-lag pause via Closed Fist (1.2s hold) or Open Palm, with instant zero-cooldown recovery.
"""

import time
import math
import logging
from enum import Enum
from typing import Optional, Tuple, List, Callable

from PyQt6.QtCore import QThread, pyqtSignal

# Input controller imports with fallback
try:
    from pynput.mouse import Controller as MouseController
    _pynput_mouse = MouseController()
except Exception as e:
    _pynput_mouse = None
    logging.warning(f"pynput mouse controller fallback: {e}")

_pyautogui = None

def _get_pyautogui():
    global _pyautogui
    if _pyautogui is None:
        try:
            import pyautogui
            pyautogui.PAUSE = 0.0
            pyautogui.FAILSAFE = False
            _pyautogui = pyautogui
        except Exception as e:
            logging.warning(f"pyautogui fallback: {e}")
    return _pyautogui

logger = logging.getLogger("GestureEngine")


class GestureState(Enum):
    OPEN = "OPEN"
    PINCHED = "PINCHED"
    PAUSED = "PAUSED"


class EMASmoother:
    """Exponential Moving Average smoother for natural, low-jitter motion."""
    def __init__(self, alpha: float = 0.45):
        self.alpha = max(0.01, min(1.0, alpha))
        self.value: Optional[float] = None

    def update(self, new_val: float) -> float:
        if self.value is None:
            self.value = new_val
        else:
            self.value = self.alpha * new_val + (1.0 - self.alpha) * self.value
        return self.value

    def reset(self):
        self.value = None


def dist2d(p1, p2) -> float:
    """Euclidean distance between two landmarks."""
    return math.hypot(p1.x - p2.x, p1.y - p2.y)


def calculate_joint_angle(p_base, p_mid, p_tip) -> float:
    """
    Computes angle at the middle joint (p_mid) between vectors:
      u = p_base - p_mid
      v = p_tip - p_mid
    A fully straight finger yields ~180 degrees.
    A folded finger yields ~0 to 60 degrees.
    """
    ux, uy = p_base.x - p_mid.x, p_base.y - p_mid.y
    vx, vy = p_tip.x - p_mid.x, p_tip.y - p_mid.y
    mag_u = math.hypot(ux, uy)
    mag_v = math.hypot(vx, vy)
    if mag_u == 0.0 or mag_v == 0.0:
        return 0.0
    cos_val = max(-1.0, min(1.0, (ux * vx + uy * vy) / (mag_u * mag_v)))
    return math.degrees(math.acos(cos_val))


class LandmarkClassifier:
    """
    Scale-invariant landmark analysis evaluating Index-Thumb pinch distance ratios,
    fist isolation, and pause poses.
    """
    @staticmethod
    def check_finger(wrist, mcp, pip, dip, tip) -> Tuple[bool, bool, float, float]:
        """
        Calculates extension and curled status:
          - EXTENDED: dist(wrist, tip) > dist(wrist, pip) * 1.15 AND PIP angle > 145 deg
          - CURLED: dist(wrist, tip) < dist(wrist, pip) * 1.05 OR PIP angle < 115 deg
        """
        d_wrist_tip = dist2d(wrist, tip)
        d_wrist_pip = dist2d(wrist, pip)
        ratio = d_wrist_tip / max(d_wrist_pip, 1e-6)
        angle = calculate_joint_angle(mcp, pip, tip)

        is_extended = (ratio > 1.15) and (angle > 145.0)
        is_curled = (ratio < 1.05) or (angle < 115.0)
        return is_extended, is_curled, ratio, angle

    @classmethod
    def check_pinch(cls, landmarks) -> Tuple[float, float, float, bool, bool, bool, dict]:
        """
        Computes scale-invariant pinch ratio, anchor midpoint, and posture flags.
        Returns:
          (pinch_ratio, anchor_x, anchor_y, mid_or_ring_relaxed, is_fist, is_open_palm, debug_dict)
        """
        if not landmarks or len(landmarks) < 21:
            return 1.0, 0.5, 0.5, False, False, False, {}

        wrist = landmarks[0]
        middle_mcp = landmarks[9]
        thumb_tip = landmarks[4]
        index_tip = landmarks[8]

        # 1. Palm size baseline: Wrist (0) to Middle MCP (9)
        palm_size = max(dist2d(wrist, middle_mcp), 1e-5)

        # 2. Tip-to-tip distance & scale-invariant pinch ratio
        pinch_dist = dist2d(thumb_tip, index_tip)
        pinch_ratio = pinch_dist / palm_size

        # 3. Anchor tracking midpoint between Thumb Tip and Index Tip
        anchor_x = (thumb_tip.x + index_tip.x) / 2.0
        anchor_y = (thumb_tip.y + index_tip.y) / 2.0

        # 4. Finger posture checks
        idx_ext, idx_curl, _, _ = cls.check_finger(
            wrist, landmarks[5], landmarks[6], landmarks[7], landmarks[8]
        )
        mid_ext, mid_curl, _, _ = cls.check_finger(
            wrist, landmarks[9], landmarks[10], landmarks[11], landmarks[12]
        )
        rng_ext, rng_curl, _, _ = cls.check_finger(
            wrist, landmarks[13], landmarks[14], landmarks[15], landmarks[16]
        )
        pnk_ext, pnk_curl, _, _ = cls.check_finger(
            wrist, landmarks[17], landmarks[18], landmarks[19], landmarks[20]
        )

        # Middle or Ring finger relaxed/extended check:
        # A finger is relaxed/extended if it is not curled
        mid_or_ring_relaxed = (not mid_curl) or (not rng_curl)

        # Closed fist: all 4 non-thumb fingers curled
        is_fist = idx_curl and mid_curl and rng_curl and pnk_curl

        # Open palm: all 5 fingers extended and spread apart
        thumb_dist = dist2d(wrist, thumb_tip)
        thumb_mcp_dist = dist2d(wrist, landmarks[2])
        thumb_ext = thumb_dist > (thumb_mcp_dist * 1.15)
        is_open_palm = idx_ext and mid_ext and rng_ext and pnk_ext and thumb_ext and (pinch_ratio > 0.45)

        debug_info = {
            "palm_size": palm_size,
            "pinch_dist": pinch_dist,
            "pinch_ratio": pinch_ratio,
            "anchor_x": anchor_x,
            "anchor_y": anchor_y,
            "mid_or_ring_relaxed": mid_or_ring_relaxed,
            "is_fist": is_fist,
            "is_open_palm": is_open_palm,
        }

        return pinch_ratio, anchor_x, anchor_y, mid_or_ring_relaxed, is_fist, is_open_palm, debug_info


class SmoothScrollController:
    """
    Omnidirectional 2D sub-pixel scroll accumulator with 60Hz decoupled dispatch
    and exponential inertia decay (velocity *= 0.85).
    """
    def __init__(
        self,
        sensitivity_gain: float = 3800.0,
        velocity_decay: float = 0.85,
        min_velocity: float = 0.5,
        scroll_dispatcher: Optional[Callable[[float, float], None]] = None,
    ):
        self.sensitivity_gain = sensitivity_gain
        self.velocity_decay = velocity_decay
        self.min_velocity = min_velocity
        self.scroll_dispatcher = scroll_dispatcher or self.default_scroll_dispatch

        # Physics state for Y (vertical)
        self.acc_y = 0.0
        self.vel_y = 0.0

        # Physics state for X (horizontal)
        self.acc_x = 0.0
        self.vel_x = 0.0

        self.is_swiping = False
        self.total_scrolled_pixels = 0.0

    @staticmethod
    def default_scroll_dispatch(dx: float, dy: float):
        """Dispatches 2D scroll ticks to the operating system."""
        if dx == 0 and dy == 0:
            return

        dispatched = False
        if _pynput_mouse is not None:
            try:
                # pynput scroll(dx, dy)
                _pynput_mouse.scroll(int(dx), int(dy))
                dispatched = True
            except Exception as e:
                logger.debug(f"pynput dispatch error: {e}")

        if not dispatched:
            pag = _get_pyautogui()
            if pag is not None:
                try:
                    if dy != 0:
                        pag.scroll(int(dy))
                    if dx != 0 and hasattr(pag, "hscroll"):
                        pag.hscroll(int(dx))
                except Exception as e:
                    logger.debug(f"pyautogui dispatch error: {e}")

    def add_pinch_delta(self, delta_x_px: float, delta_y_px: float, dt: float = 0.033):
        """Adds fractional 2D movement deltas into accumulators and charges velocity."""
        effective_dt = max(dt, 0.01)

        if abs(delta_y_px) >= 0.01:
            self.acc_y += delta_y_px
            self.total_scrolled_pixels += abs(delta_y_px)
            self.vel_y = (delta_y_px / effective_dt) * 0.025

        if abs(delta_x_px) >= 0.01:
            self.acc_x += delta_x_px
            self.total_scrolled_pixels += abs(delta_x_px)
            self.vel_x = (delta_x_px / effective_dt) * 0.025

        if abs(delta_x_px) >= 0.01 or abs(delta_y_px) >= 0.01:
            self.is_swiping = True

    def end_swipe(self):
        """Called immediately when pinch releases (slight gap opens)."""
        self.is_swiping = False

    def reset_momentum(self):
        """Immediately halts momentum and clears accumulators."""
        self.vel_x = 0.0
        self.vel_y = 0.0
        self.acc_x = 0.0
        self.acc_y = 0.0
        self.is_swiping = False

    def tick_60hz(self, dt: float = 1.0 / 60.0) -> Tuple[int, int]:
        """
        Executes one 60Hz physics step:
          - Applies exponential velocity decay (velocity *= 0.85).
          - Dispatches accumulated integer scroll units for X and Y.
        Returns (dispatched_dx, dispatched_dy).
        """
        if not self.is_swiping:
            # Inertia decay for Y
            if abs(self.vel_y) >= self.min_velocity:
                self.vel_y *= self.velocity_decay
                inertia_y = self.vel_y * dt * 60.0
                self.acc_y += inertia_y
                self.total_scrolled_pixels += abs(inertia_y)
            else:
                self.vel_y = 0.0

            # Inertia decay for X
            if abs(self.vel_x) >= self.min_velocity:
                self.vel_x *= self.velocity_decay
                inertia_x = self.vel_x * dt * 60.0
                self.acc_x += inertia_x
                self.total_scrolled_pixels += abs(inertia_x)
            else:
                self.vel_x = 0.0

        disp_x = 0
        disp_y = 0

        if abs(self.acc_y) >= 1.0:
            disp_y = math.trunc(self.acc_y)
            self.acc_y -= disp_y

        if abs(self.acc_x) >= 1.0:
            disp_x = math.trunc(self.acc_x)
            self.acc_x -= disp_x

        if disp_x != 0 or disp_y != 0:
            self.scroll_dispatcher(disp_x, disp_y)

        return disp_x, disp_y


class GestureStateMachine:
    """
    State machine managing Precision Pinch detection, micro-hysteresis,
    2-frame settle buffer, omnidirectional velocity translation, and zero-lag pause.
    """
    def __init__(
        self,
        sensitivity_gain: float = 3800.0,
        smoothing_alpha: float = 0.45,
        pinch_engage_threshold: float = 0.22,
        pinch_release_threshold: float = 0.27,
    ):
        self.sensitivity_gain = sensitivity_gain
        self.smoothing_alpha = smoothing_alpha
        self.pinch_engage_threshold = pinch_engage_threshold
        self.pinch_release_threshold = pinch_release_threshold

        # Pinch tracking state
        self.is_pinched = False
        self.current_state = GestureState.OPEN
        self.pinch_frame_count = 0

        # Pause detection (Closed fist 1.2s or Open Palm)
        self.fist_start_time: Optional[float] = None
        self.is_fist_paused = False
        self.is_palm_paused = False

        # EMA coordinate smoothers for anchor midpoint
        self.x_smoother = EMASmoother(alpha=smoothing_alpha)
        self.y_smoother = EMASmoother(alpha=smoothing_alpha)
        self.prev_pinch_x: Optional[float] = None
        self.prev_pinch_y: Optional[float] = None

        # Smooth scroller
        self.scroller = SmoothScrollController(
            sensitivity_gain=sensitivity_gain,
            velocity_decay=0.85,
        )

    def set_sensitivity(self, value: float):
        """Updates sensitivity gain across state machine and scroller in real time."""
        gain = float(value)
        self.sensitivity_gain = gain
        if hasattr(self, "scroller") and self.scroller is not None:
            self.scroller.sensitivity_gain = gain

    def reset_tracking(self):
        """Resets tracking coordinate history."""
        self.x_smoother.reset()
        self.y_smoother.reset()
        self.prev_pinch_x = None
        self.prev_pinch_y = None
        self.pinch_frame_count = 0
        self.is_pinched = False
        self.fist_start_time = None
        self.is_fist_paused = False
        self.is_palm_paused = False
        self.scroller.end_swipe()

    def update(self, landmarks, current_time: Optional[float] = None) -> Tuple[GestureState, float, float, dict]:
        """
        Processes normalized landmarks and returns:
          (current_state, scroll_dx, scroll_dy, debug_info).
        """
        now = time.time() if current_time is None else current_time

        if not landmarks or len(landmarks) < 21:
            self.reset_tracking()
            self.current_state = GestureState.OPEN
            return GestureState.OPEN, 0.0, 0.0, {}

        # 1. Compute geometry & posture flags
        (
            pinch_ratio,
            anchor_x,
            anchor_y,
            mid_or_ring_relaxed,
            is_fist,
            is_open_palm,
            debug_info,
        ) = LandmarkClassifier.check_pinch(landmarks)

        # 2. Pause Handling: Closed Fist held 1.2s OR Open Palm
        if is_fist:
            if self.fist_start_time is None:
                self.fist_start_time = now
            elif now - self.fist_start_time >= 1.2:
                self.is_fist_paused = True
        else:
            # Releasing closed fist clears timer and pause immediately (zero cooldown lag)
            self.fist_start_time = None
            self.is_fist_paused = False

        if is_open_palm:
            self.is_palm_paused = True
        else:
            # Releasing open palm clears pause immediately (zero cooldown lag)
            self.is_palm_paused = False

        # If paused, immediately disengage pinch and momentum
        if self.is_fist_paused or self.is_palm_paused:
            self.is_pinched = False
            self.pinch_frame_count = 0
            self.prev_pinch_x = None
            self.prev_pinch_y = None
            self.x_smoother.reset()
            self.y_smoother.reset()
            self.scroller.end_swipe()
            self.current_state = GestureState.PAUSED
            return GestureState.PAUSED, 0.0, 0.0, debug_info

        # 3. Guard against Closed Fist False-Pinch:
        # Require at least Middle (12) or Ring (16) finger relaxed/extended
        can_pinch = mid_or_ring_relaxed

        # 4. Micro-Hysteresis Pinch Decision:
        if self.is_pinched:
            # RELEASE: slight gap opens (ratio > 0.27) or fist isolation violated
            if pinch_ratio > self.pinch_release_threshold or not can_pinch:
                self.is_pinched = False
        else:
            # ENGAGE: contact closed (ratio < 0.22) and fingers relaxed
            if pinch_ratio < self.pinch_engage_threshold and can_pinch:
                self.is_pinched = True

        # 5. Active Pinch State vs. Open Pinch State
        if self.is_pinched:
            self.current_state = GestureState.PINCHED
            self.pinch_frame_count += 1

            smoothed_x = self.x_smoother.update(anchor_x)
            smoothed_y = self.y_smoother.update(anchor_y)

            # Settle Guard: Discard first 2 frames (~60ms) to absorb finger-closing twitch
            if self.pinch_frame_count <= 2:
                self.prev_pinch_x = smoothed_x
                self.prev_pinch_y = smoothed_y
                return GestureState.PINCHED, 0.0, 0.0, debug_info

            # Settle guard passed: calculate motion deltas
            dx = smoothed_x - self.prev_pinch_x
            dy = smoothed_y - self.prev_pinch_y
            self.prev_pinch_x = smoothed_x
            self.prev_pinch_y = smoothed_y

            # Direction translation:
            # Vertical: hand moving UP (dy < 0) -> scroll page UP (+delta)
            #           hand moving DOWN (dy > 0) -> scroll page DOWN (-delta)
            sign_y = 1.0 if dy < 0 else -1.0
            scroll_delta_y = sign_y * (abs(dy) ** 1.35) * self.sensitivity_gain

            # Horizontal: hand moving RIGHT (dx > 0) -> scroll RIGHT (+delta)
            #             hand moving LEFT (dx < 0) -> scroll LEFT (-delta)
            sign_x = 1.0 if dx > 0 else -1.0
            scroll_delta_x = sign_x * (abs(dx) ** 1.35) * self.sensitivity_gain

            self.scroller.add_pinch_delta(scroll_delta_x, scroll_delta_y)
            return GestureState.PINCHED, scroll_delta_x, scroll_delta_y, debug_info

        else:
            # Open Pinch / Slight Gap:
            # Immediately clear position anchors and let inertia glide smoothly
            self.current_state = GestureState.OPEN
            self.pinch_frame_count = 0
            self.prev_pinch_x = None
            self.prev_pinch_y = None
            self.x_smoother.reset()
            self.y_smoother.reset()
            self.scroller.end_swipe()
            return GestureState.OPEN, 0.0, 0.0, debug_info


class GestureEngine:
    """
    High-level engine tying MediaPipe Hands, state machine, and 60Hz physics.
    """
    def __init__(
        self,
        sensitivity_gain: float = 3800.0,
        smoothing_alpha: float = 0.45,
        scroll_dispatcher: Optional[Callable[[float, float], None]] = None,
    ):
        self.state_machine = GestureStateMachine(
            sensitivity_gain=sensitivity_gain,
            smoothing_alpha=smoothing_alpha,
        )
        if scroll_dispatcher:
            self.state_machine.scroller.scroll_dispatcher = scroll_dispatcher

        # Lazy vision attributes - loaded only on demand inside background worker thread
        self.mp = None
        self.cv2 = None
        self.mp_hands = None
        self.hands = None
        self.mp_draw = None
        self.mp_draw_styles = None
        self._vision_initialized = False

    def ensure_vision_initialized(self):
        """Lazily imports cv2 and mediapipe Hands only when gesture tracking starts."""
        if self._vision_initialized:
            return
        import cv2
        import mediapipe as mp

        self.cv2 = cv2
        self.mp = mp
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            model_complexity=0,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        self.mp_draw = mp.solutions.drawing_utils
        self.mp_draw_styles = mp.solutions.drawing_styles
        self._vision_initialized = True

    @property
    def current_state(self) -> GestureState:
        return self.state_machine.current_state

    @property
    def sensitivity_gain(self) -> float:
        return self.state_machine.sensitivity_gain

    @sensitivity_gain.setter
    def sensitivity_gain(self, value: float):
        self.set_sensitivity(value)

    def set_sensitivity(self, value: float):
        """Thread-safe update of sensitivity gain across state machine and scroller."""
        self.state_machine.set_sensitivity(value)

    def process_frame(self, rgb_frame, current_time: Optional[float] = None) -> Tuple[GestureState, float, float, Optional[list]]:
        """Processes an image frame from camera."""
        self.ensure_vision_initialized()
        results = self.hands.process(rgb_frame)
        landmarks = None
        if results.multi_hand_landmarks:
            landmarks = results.multi_hand_landmarks[0].landmark

        state, dx, dy, _ = self.state_machine.update(landmarks, current_time=current_time)
        return state, dx, dy, results.multi_hand_landmarks

    def process_synthetic_landmarks(self, landmarks_list, current_time: Optional[float] = None) -> Tuple[GestureState, float, float, dict]:
        """Programmatic interface for automated unit tests."""
        state, dx, dy, info = self.state_machine.update(landmarks_list, current_time=current_time)
        return state, dx, dy, info

    def physics_tick(self, dt: float = 1.0 / 60.0) -> Tuple[int, int]:
        """Executes a 60Hz physics and inertia tick."""
        return self.state_machine.scroller.tick_60hz(dt)

    def close(self):
        """Releases MediaPipe resources."""
        if self._vision_initialized and self.hands:
            self.hands.close()
            self.hands = None
            self._vision_initialized = False


class GestureWorkerThread(QThread):
    """
    Dedicated worker thread acquiring webcam frames at 30 FPS and ticking 60Hz physics.
    Completely prevents UI thread blocking.
    """
    state_signal = pyqtSignal(str, str)      # state string (OPEN/PINCHED/PAUSED/LOADING), hex color
    fps_signal = pyqtSignal(float)
    frame_signal = pyqtSignal(object)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        camera_index: int = 0,
        engine: Optional[GestureEngine] = None,
        sensitivity_gain: Optional[float] = None,
    ):
        super().__init__()
        self.camera_index = camera_index
        self.engine = engine or GestureEngine()
        if sensitivity_gain is not None:
            self.engine.set_sensitivity(sensitivity_gain)
        self._is_running = False
        self.cap = None

    def set_sensitivity(self, value: float):
        """Thread-safe sensitivity forwarding to underlying engine."""
        if self.engine is not None:
            self.engine.set_sensitivity(value)

    def run(self):
        self._is_running = True
        logger.info("Initializing vision pipeline in worker thread...")
        self.state_signal.emit("LOADING", "#FBBF24")

        try:
            self.engine.ensure_vision_initialized()
            cv2 = self.engine.cv2
        except Exception as e:
            err_msg = f"Failed to initialize vision modules: {e}"
            logger.exception(err_msg)
            self.error_signal.emit(err_msg)
            self.state_signal.emit("OPEN", "#9CA3AF")
            self._is_running = False
            return

        logger.info(f"Opening camera index {self.camera_index}...")
        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            err_msg = f"Unable to open camera index {self.camera_index}."
            logger.error(err_msg)
            self.error_signal.emit(err_msg)
            self.state_signal.emit("OPEN", "#9CA3AF")
            self._is_running = False
            return

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        # Vision pipeline initialized and ready
        self.state_signal.emit("OPEN", "#9CA3AF")

        frame_count = 0
        fps_timer = time.time()
        fps = 30.0
        last_physics_tick = time.perf_counter()

        try:
            while self._is_running:
                now_perf = time.perf_counter()
                dt_physics = now_perf - last_physics_tick

                # 60Hz physics & inertia tick
                if dt_physics >= (1.0 / 60.0):
                    self.engine.physics_tick(dt_physics)
                    last_physics_tick = now_perf

                ret, frame = self.cap.read()
                if not ret or frame is None:
                    time.sleep(0.005)
                    continue

                # Flip horizontally for natural mirror behavior
                frame = cv2.flip(frame, 1)
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                state, dx, dy, multi_landmarks = self.engine.process_frame(rgb_frame)

                # State color mapping: PINCHED=green, PAUSED=amber, OPEN=gray
                if state == GestureState.PINCHED:
                    color = "#4ADE80"  # Vibrant active green
                elif state == GestureState.PAUSED:
                    color = "#FBBF24"  # Amber pause
                else:
                    color = "#9CA3AF"  # Neutral gray

                self.state_signal.emit(state.value, color)

                if multi_landmarks:
                    for hand_landmarks in multi_landmarks:
                        self.engine.mp_draw.draw_landmarks(
                            rgb_frame,
                            hand_landmarks,
                            self.engine.mp_hands.HAND_CONNECTIONS,
                            self.engine.mp_draw_styles.get_default_hand_landmarks_style(),
                            self.engine.mp_draw_styles.get_default_hand_connections_style(),
                        )
                        # Visual pinch anchor overlay
                        lms = hand_landmarks.landmark
                        h, w, _ = rgb_frame.shape
                        p4 = (int(lms[4].x * w), int(lms[4].y * h))
                        p8 = (int(lms[8].x * w), int(lms[8].y * h))
                        mid_pt = ((p4[0] + p8[0]) // 2, (p4[1] + p8[1]) // 2)

                        if state == GestureState.PINCHED:
                            cv2.line(rgb_frame, p4, p8, (74, 222, 128), 2)
                            cv2.circle(rgb_frame, mid_pt, 6, (74, 222, 128), -1)
                        else:
                            cv2.line(rgb_frame, p4, p8, (156, 163, 175), 1)

                self.frame_signal.emit(rgb_frame)

                # FPS tracking
                frame_count += 1
                now_wall = time.time()
                elapsed = now_wall - fps_timer
                if elapsed >= 0.5:
                    fps = frame_count / elapsed
                    frame_count = 0
                    fps_timer = now_wall
                    self.fps_signal.emit(fps)

        except Exception as e:
            logger.exception(f"Exception inside GestureWorkerThread: {e}")
            self.error_signal.emit(str(e))
        finally:
            if self.cap is not None:
                self.cap.release()
                self.cap = None
            logger.info("Camera released cleanly.")
            self.state_signal.emit("OPEN", "#9CA3AF")

    def stop(self):
        self._is_running = False
        self.wait(1500)
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
            self.cap = None
