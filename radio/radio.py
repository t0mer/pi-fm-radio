# app/radio.py
import os
import fcntl
import time
import threading
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import yaml

# ------------ Low-level I2C ------------
I2C_DEV = "/dev/i2c-1"
I2C_SLAVE = 0x0703
TEA5767_ADDR = 0x60

_i2c_lock = threading.Lock()

def _i2c_open():
    return os.open(I2C_DEV, os.O_RDWR)

def _i2c_set_addr(fd: int, addr: int):
    fcntl.ioctl(fd, I2C_SLAVE, addr)

def _i2c_write(fd: int, data: bytes):
    return os.write(fd, data)

def _i2c_read(fd: int, n: int) -> bytes:
    return os.read(fd, n)

def raw_write5(b: List[int]):
    if len(b) != 5:
        raise ValueError("Need exactly 5 bytes")
    with _i2c_lock:
        fd = _i2c_open()
        try:
            _i2c_set_addr(fd, TEA5767_ADDR)
            _i2c_write(fd, bytes(b))
        finally:
            os.close(fd)

def raw_read5() -> List[int]:
    with _i2c_lock:
        fd = _i2c_open()
        try:
            _i2c_set_addr(fd, TEA5767_ADDR)
            data = _i2c_read(fd, 5)
            return list(data)
        finally:
            os.close(fd)

# ------------ TEA5767 logic ------------
FREQ_MIN = 87.5
FREQ_MAX = 108.0
STEP = 0.1
DE_EMPHASIS = 50  # µs

_state_lock = threading.Lock()
_current_freq: Optional[float] = None
_forced_mono: bool = False
_muted: bool = False  # write-only on chip; we track it here

# ------------ Presets from YAML ------------
# Path resolution:
# 1) env STATIONS_FILE
# 2) /opt/radio/stations.yaml
# 3) <this file>/../stations.yaml
_DEF_ENV = os.environ.get("STATIONS_FILE")
_DEF_OPT = Path("/opt/radio/stations.yaml")
_DEF_LOCAL = Path(__file__).resolve().parent / "stations.yaml"

_PRESETS: Dict[float, str] = {}  # freq -> name
_PRESETS_LOCK = threading.RLock()

def _find_yaml_path() -> Path:
    if _DEF_ENV:
        p = Path(_DEF_ENV)
        if p.exists():
            return p
    if _DEF_OPT.exists():
        return _DEF_OPT
    return _DEF_LOCAL

def _normalize_freq(x) -> float:
    try:
        return round(float(x), 1)
    except Exception:
        return None

def reload_presets() -> Tuple[Dict[float, str], Path]:
    """Load stations.yaml into memory. Returns (mapping, path)."""
    path = _find_yaml_path()
    mapping: Dict[float, str] = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        items = data.get("stations", [])
        for item in items:
            name = str(item.get("name", "Station")).strip()
            freq = _normalize_freq(item.get("freq"))
            if freq is None:
                continue
            mapping[freq] = name
    else:
        # Fallback defaults if file missing
        mapping = {
            96.6: "Preset 1",
            99.8: "Preset 2",
        }
    with _PRESETS_LOCK:
        _PRESETS.clear()
        _PRESETS.update(mapping)
    return mapping, path

# initial load on import
reload_presets()

def get_presets_list() -> List[Tuple[float, str]]:
    with _PRESETS_LOCK:
        return sorted(_PRESETS.items())

def station_name_for(freq: float) -> str:
    with _PRESETS_LOCK:
        if not _PRESETS:
            return "Unknown"
        nearest = min(_PRESETS.keys(), key=lambda x: abs(x - freq))
        return _PRESETS[nearest] if abs(nearest - freq) <= 0.05 else "Unknown"

# ------------ Tuner ops ------------
def freq_to_pll(freq_mhz: float) -> int:
    return int(4 * ((freq_mhz * 1_000_000) + 225_000) / 32_768)

def clamp_freq(f: float) -> float:
    return max(FREQ_MIN, min(FREQ_MAX, round(f, 1)))

def set_frequency(freq_mhz: float, mute: bool = False, stereo: bool = True, de_emphasis_us: int = DE_EMPHASIS):
    pll = freq_to_pll(freq_mhz)
    b0 = (pll >> 8) & 0x3F
    if mute:
        b0 |= 0x80
    b1 = pll & 0xFF
    b2 = 0xB0
    if not stereo:
        b2 &= ~(1 << 7)
    b3 = 0x10 if de_emphasis_us == 50 else 0x00
    b4 = 0x00
    raw_write5([b0, b1, b2, b3, b4])
    time.sleep(0.12)

def _apply_current_pll(modify):
    s = raw_read5()
    pll_high = s[0] & 0x3F
    pll_low = s[1]
    b0 = pll_high
    b1 = pll_low
    b2 = 0xB0
    b3 = 0x10 if DE_EMPHASIS == 50 else 0x00
    b4 = 0x00
    b0, b1, b2, b3, b4 = modify(b0, b1, b2, b3, b4)
    raw_write5([b0, b1, b2, b3, b4])
    time.sleep(0.05)

def mute():
    global _muted
    def mod(b0,b1,b2,b3,b4): return (b0 | 0x80), b1, b2, b3, b4
    _apply_current_pll(mod)
    with _state_lock: _muted = True

def unmute():
    global _muted
    def mod(b0,b1,b2,b3,b4): return (b0 & ~0x80), b1, b2, b3, b4
    _apply_current_pll(mod)
    with _state_lock: _muted = False

def set_mono():
    def mod(b0,b1,b2,b3,b4): return b0, b1, (b2 & ~(1<<7)), b3, b4
    _apply_current_pll(mod)

def set_stereo():
    def mod(b0,b1,b2,b3,b4): return b0, b1, (b2 | (1<<7)), b3, b4
    _apply_current_pll(mod)

def read_status() -> Dict:
    s = raw_read5()
    if_ready = bool(s[0] & 0x80)
    stereo = bool(s[2] & 0x80)
    signal_level = (s[3] >> 4) & 0x0F
    pll = ((s[0] & 0x3F) << 8) | s[1]
    freq_hz = (pll * 32_768 // 4) - 225_000
    freq_mhz = round(freq_hz / 1_000_000.0, 3)
    with _state_lock:
        muted = _muted
    return {
        "if_ready": if_ready,
        "stereo": stereo,
        "signal": signal_level,
        "frequency": freq_mhz,
        "muted": muted,
        "raw": s,
    }

def tune_to(f: float):
    global _current_freq
    f = clamp_freq(f)
    with _state_lock:
        set_frequency(f, mute=False, stereo=(not _forced_mono), de_emphasis_us=DE_EMPHASIS)
        _current_freq = f
    return f

def step(direction: str):
    global _current_freq
    with _state_lock:
        if _current_freq is None:
            st = read_status()
            _current_freq = st["frequency"]
        delta = STEP if direction == "up" else -STEP
        newf = clamp_freq((_current_freq or 99.8) + delta)
        set_frequency(newf, mute=False, stereo=(not _forced_mono), de_emphasis_us=DE_EMPHASIS)
        _current_freq = newf
    return newf

def set_forced_mono(flag: bool):
    global _forced_mono
    _forced_mono = flag
    if flag: set_mono()
    else: set_stereo()
