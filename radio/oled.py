# app/oled.py
import threading
import time
from typing import Callable, Dict, Optional

from PIL import Image, ImageDraw, ImageFont
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306


class OledDisplay:
    """
    SSD1306 128x64 I²C OLED helper.

    Usage (event-driven, no background loop):
        oled = OledDisplay(address=0x3C)
        oled.show(name="My Station", freq=96.6, stereo=True, signal=12)

    Optional (background loop; not recommended with TEA5767 unless interval is large):
        oled.start(status_fn=read_status, name_fn=station_name_for, interval=3.0)
        ...
        oled.stop()
    """

    def __init__(
        self,
        i2c_port: int = 1,
        address: int = 0x3C,
        width: int = 128,
        height: int = 64,
        rotate: int = 0,
    ):
        # Initialize I²C + device
        self.serial = i2c(port=i2c_port, address=address)
        self.device = ssd1306(self.serial, width=width, height=height, rotate=rotate)
        self.width = width
        self.height = height

        # Fonts (default bitmap). You can swap to TTF if you like:
        # self.font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        # self.font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        self.font_big = ImageFont.load_default()
        self.font_small = ImageFont.load_default()

        # Background loop members (optional)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._status_fn: Optional[Callable[[], Dict]] = None
        self._name_fn: Optional[Callable[[float], str]] = None
        self._interval = 1.0

    # ========== Event-driven (no loop) ==========
    def show(self, name: str, freq: float, stereo: bool, signal: int):
        """
        Draw a single frame: station name, frequency, stereo/mono, signal bar.
        Safe to call whenever you change station.
        """
        try:
            self._render(name=name, freq=freq, stereo=stereo, signal=signal)
        except Exception:
            # Keep caller resilient on transient I²C/display issues.
            pass

    # ========== Optional background loop (only if you really want it) ==========
    def start(
        self,
        status_fn: Callable[[], Dict],
        name_fn: Callable[[float], str],
        interval: float = 1.0,
    ):
        """
        Start periodic updates by polling your status function.
        Not recommended with TEA5767 at short intervals (may cause audible ticks).
        """
        self._status_fn = status_fn
        self._name_fn = name_fn
        self._interval = max(0.2, float(interval))
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="oled-updater", daemon=True)
        self._thread.start()

    def stop(self):
        """Stop periodic updates and blank the screen (optional)."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self.device.hide()  # turn off panel (optional). Use .show() to re-enable.
        except Exception:
            pass

    # ========== Internal helpers ==========
    def _loop(self):
        while not self._stop.is_set():
            try:
                if not self._status_fn or not self._name_fn:
                    time.sleep(self._interval)
                    continue

                st = self._status_fn()  # expects keys: frequency (float), stereo (bool), signal (int 0..15)
                freq = float(st.get("frequency", 0.0))
                name = self._name_fn(freq)
                stereo = bool(st.get("stereo", False))
                signal = int(st.get("signal", 0))

                self._render(name=name, freq=freq, stereo=stereo, signal=signal)
            except Exception:
                # Never crash the thread on I²C/transient issues
                pass
            time.sleep(self._interval)

    def _render(self, name: str, freq: float, stereo: bool, signal: int):
        W, H = self.width, self.height
        img = Image.new("1", (W, H), 0)  # 1-bit (monochrome)
        draw = ImageDraw.Draw(img)

        # Top: Station name (trim to fit)
        station = name or "Unknown"
        if len(station) > 18:
            station = station[:18] + "…"
        draw.text((2, 2), station, font=self.font_big, fill=1)

        # Second line: Frequency
        draw.text((2, 18), f"{freq:0.1f} MHz", font=self.font_big, fill=1)

        # Third line: Stereo/Mono
        draw.text((2, 34), "Stereo" if stereo else "Mono", font=self.font_small, fill=1)

        # Bottom: Signal bar (0..15)
        max_sig = 15
        signal = max(0, min(int(signal), max_sig))
        bar_w = W - 4
        bar_h = 12
        x0, y0 = 2, H - bar_h - 2
        draw.rectangle((x0, y0, x0 + bar_w, y0 + bar_h), outline=1, fill=0)
        fill_w = int(bar_w * signal / max_sig)
        if fill_w > 0:
            draw.rectangle((x0 + 1, y0 + 1, x0 + 1 + fill_w, y0 + bar_h - 1), outline=0, fill=1)

        # Push to panel
        self.device.display(img)
