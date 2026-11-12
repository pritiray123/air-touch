# Air Touch - Touchless Gesture Scroll

A lightweight, local desktop application that enables touchless omnidirectional screen scrolling and navigation using webcam hand gestures, controlled via an always-on-top floating desktop overlay widget.

Built with **OpenCV**, **MediaPipe Hands**, and **PyQt6**.

---

## Download & Run (No Python Required)

Pre-built standalone desktop executables are automatically compiled for Windows and macOS. No Python or virtual environment installation is needed.

Download the latest release from the [Releases](https://github.com/pritiray123/air-touch/releases) page:
- **Windows**: `GestureScroll-Windows.exe`
- **macOS**: `GestureScroll-macOS.zip`

### Windows Setup
1. Download `GestureScroll-Windows.exe`.
2. Double-click the file to launch.
3. *First launch note*: Because the executable is an open-source binary, Windows Defender SmartScreen may display a prompt. Click **More info** &rarr; **Run anyway**.

### macOS Setup
1. Download `GestureScroll-macOS.zip` and double-click to unzip `GestureScroll.app`.
2. Drag `GestureScroll.app` into your **Applications** folder (or run from anywhere).
3. *First launch note*:
   - Right-click (or Control-click) `GestureScroll.app` and choose **Open**, then click **Open** in the dialog (bypasses macOS Gatekeeper for ad-hoc signed open-source builds).
4. *Permissions*:
   - **Camera**: macOS will prompt for camera access on first launch to track hand gestures. Click **OK**.
   - **Accessibility**: If system-level mouse wheel dispatch does not scroll background windows, ensure `GestureScroll` is enabled under **System Settings &rarr; Privacy & Security &rarr; Accessibility**.

---

## Gesture Controls Guide

| Gesture | State Badge | Action |
| :--- | :---: | :--- |
| **Pinch Contact**<br>*(Index Fingertip + Thumb Tip touching)* | `● PINCHED`<br>`#4ADE80` (Green) | **Active Scroll**: Drag hand up, down, left, or right. Motion translates into smooth, accelerated omnidirectional scrolling. |
| **Open Pinch**<br>*(Slight gap between Index & Thumb)* | `● OPEN`<br>`#9CA3AF` (Gray) | **Clutch / Reposition**: Scrolling immediately halts and coasts with trackpad momentum inertia. Reposition your hand freely anywhere in the air without moving the screen. |
| **Closed Fist**<br>*(Hold for 1.2s)* | `● PAUSED`<br>`#FBBF24` (Amber) | **Pause Toggle**: Temporarily locks scroll input so you can rest your hand. Opening your hand immediately restores pinch readiness with zero cooldown lag. |
| **Open Palm**<br>*(5 fingers extended)* | `● PAUSED`<br>`#FBBF24` (Amber) | **Instant Neutral Pause**: Rest pose preventing accidental scroll gestures. |

### Settle Buffer & Motion Physics
- **2-Frame Settle Buffer (~60ms)**: Finger-closing contact twitches are automatically absorbed so the screen does not jump when pinching down.
- **Dynamic Velocity Curve**: $\Delta = \text{sign}(d) \cdot |d|^{1.35} \cdot \text{gain}$ ensures slow finger travel gives precise reading control while fast flicks glide long page distances.
- **60Hz Sub-Pixel Inertia**: High-frequency physics loop decouples smooth trackpad momentum coasting from the 30 FPS camera capture thread.

---

## Native Desktop Overlay

The application floats unobtrusively on top of your desktop windows (browsers, PDFs, IDEs, code editors):
- **Start / Stop Toggle**: Camera stays completely OFF when idle to conserve battery and CPU.
- **Live Status Pill**: Shows real-time posture (`● PINCHED`, `● OPEN`, `● PAUSED`).
- **Sensitivity Slider**: Click the gear icon (`⚙`) to adjust scroll speed and distance dynamically (1500 to 7000 gain).
- **Webcam Preview Drawer**: Click the eye icon (`👁`) to expand a live visual canvas displaying hand landmarks and the pinch anchor.

---

## Run from Source (Developers)

If you prefer running or modifying the Python source code directly:

### 1. Clone the Repository
```bash
git clone https://github.com/pritiray123/air-touch.git
cd air-touch
```

### 2. Set Up Virtual Environment
```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows (Command Prompt)
.venv\Scripts\activate.bat

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Launch Application
```bash
python main.py

# Optional CLI flags:
python main.py --camera 0    # Specify webcam index (default: 0)
python main.py --preview     # Start with webcam landmark canvas expanded
```

---

## Automated Verification Suite

Run the end-to-end automated verification test suite:
```bash
python test_automation.py
```
This tests:
1. **Pinch Contact vs. Slight-Gap Detection**: Validates scale-invariant ratio thresholds (`< 0.22` engage, `> 0.27` release) and zero scroll during gap movement.
2. **Multi-Stroke Upward Scrolling**: Simulates consecutive pinch-scroll and gap-return cycles, asserting cumulative upward displacement with zero reverse kickback.
3. **Closed-Fist Pause Isolation**: Confirms 1.2s fist hold enters `PAUSED` without false-pinching, and releasing immediately recovers pinch readiness.

---

## Automated CI/CD Releases

This repository includes a GitHub Actions workflow (`.github/workflows/release.yml`). Pushing a version tag builds and publishes standalone executables automatically:

```bash
git tag v1.0.0
git push origin v1.0.0
```

GitHub Actions will:
1. Build `GestureScroll-Windows.exe` on `windows-latest`.
2. Build `GestureScroll.app`, inject `NSCameraUsageDescription`, ad-hoc sign, and package `GestureScroll-macOS.zip` on `macos-latest`.
3. Publish both binary assets to the GitHub Release.

---

## License

MIT License. See `LICENSE` for details.

