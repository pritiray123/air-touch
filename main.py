"""
Main Entry Point for Touchless Gesture Control Application.
Launches the native floating desktop overlay widget directly on top of OS windows.
"""

import sys
import signal
import argparse
import logging

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from overlay_ui import OverlayWidget

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
)
logger = logging.getLogger("TouchlessScrollMain")


def main():
    parser = argparse.ArgumentParser(description="Touchless Screen Navigation via Webcam Gestures")
    parser.add_argument("--camera", type=int, default=0, help="Webcam device index (default: 0)")
    parser.add_argument("--preview", action="store_true", help="Launch with hand landmark preview expanded")
    args = parser.parse_args()

    # Create Qt Application
    app = QApplication(sys.argv)
    app.setApplicationName("Touchless Gesture Scroll")
    app.setApplicationDisplayName("Touchless Gesture Scroll")

    # Handle Ctrl+C cleanly
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    # Instantiate overlay widget
    overlay = OverlayWidget(camera_index=args.camera)

    if args.preview:
        overlay._toggle_preview()

    # Position in the top-right corner of the primary screen
    screen = QGuiApplication.primaryScreen()
    if screen:
        screen_geo = screen.availableGeometry()
        margin = 32
        x = screen_geo.x() + screen_geo.width() - overlay.width() - margin
        y = screen_geo.y() + margin
        overlay.move(x, y)
    else:
        overlay.move(100, 100)

    overlay.show()
    logger.info("Floating overlay widget launched in corner of desktop.")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

