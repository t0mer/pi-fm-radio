# app/main.py
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Body, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from radio import (
    read_status, tune_to, step, station_name_for,
    mute, unmute, set_forced_mono,
    get_presets_list, reload_presets   # <-- NEW
)
# Optional OLED import (server still runs if not available)
try:
    from oled import OledDisplay
    _OLED_AVAILABLE = True
except Exception as e:
    print(f"[oled] optional import failed (continuing without OLED): {e}")
    _OLED_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="TEA5767 Radio")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------- OLED lifecycle (no background thread) ----------
@app.on_event("startup")
def _oled_init():
    """
    Create the OLED device (if available). We won't start any loop.
    We'll update the OLED only after station changes (tune/step).
    """
    if not _OLED_AVAILABLE:
        app.state.oled = None
        return
    try:
        # change address to 0x3D if your OLED is at that address
        app.state.oled = OledDisplay(address=0x3C)
        print("[oled] initialized (event-driven updates)")
        # Initial paint (optional)
        _update_oled_from_chip()
    except Exception as e:
        app.state.oled = None
        print(f"[oled] init failed: {e}")

@app.on_event("shutdown")
def _oled_shutdown():
    # Nothing to stop (no thread), but keep symmetry
    pass


def _update_oled_from_chip():
    """
    Read the current tuner status once and render it to the OLED.
    Called only after station changes (tune/step) or at startup.
    """
    if not getattr(app.state, "oled", None):
        return
    try:
        st = read_status()
        freq = float(st["frequency"])
        name = station_name_for(freq)
        stereo = bool(st["stereo"])
        signal = int(st["signal"])
        # one-shot draw
        app.state.oled.show(name=name, freq=freq, stereo=stereo, signal=signal)
    except Exception as e:
        print(f"[oled] update error: {e}")


# ---------- Web UI ----------
# index route: pass presets from YAML
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    presets_list = get_presets_list()  # List[(freq, name)]
    return templates.TemplateResponse("index.html", {
        "request": request,
        "presets": presets_list,
        "title": "FM Radio (TEA5767)"
    })

# Optional: endpoints to view/reload presets without restarting
@app.get("/api/presets")
def api_presets():
    return {"presets": [{"freq": f, "name": n} for (f, n) in get_presets_list()]}

@app.post("/api/presets/reload")
def api_presets_reload():
    mapping, path = reload_presets()
    return {"ok": True, "count": len(mapping), "file": str(path)}



# ---------- API ----------
@app.get("/api/status")
def api_status():
    st = read_status()
    name = station_name_for(st["frequency"])
    return JSONResponse({
        "frequency": st["frequency"],
        "station_name": name,
        "stereo": st["stereo"],
        "signal": st["signal"],
        "muted": st["muted"],
        "raw": st["raw"],
    })

@app.post("/api/tune")
def api_tune(payload: Dict = Body(...)):
    f = float(payload.get("frequency"))
    f = tune_to(f)
    # Update OLED only on station change
    _update_oled_from_chip()
    return {"ok": True, "frequency": f, "station_name": station_name_for(f)}

@app.post("/api/step")
def api_step(payload: Dict = Body(...)):
    direction = payload.get("direction", "up")
    if direction not in ("up", "down"):
        return JSONResponse({"ok": False, "error": "direction must be 'up' or 'down'"}, status_code=400)
    f = step(direction)
    # Update OLED only on station change
    _update_oled_from_chip()
    return {"ok": True, "frequency": f, "station_name": station_name_for(f)}

@app.post("/api/mute")
def api_mute():
    mute()
    # No OLED update here (per your request)
    return {"ok": True}

@app.post("/api/unmute")
def api_unmute():
    unmute()
    # No OLED update here
    return {"ok": True}

@app.post("/api/mono")
def api_mono(payload: Dict = Body(...)):
    mono = bool(payload.get("mono", False))
    set_forced_mono(mono)
    # Optional: if you DO want OLED to reflect mono/stereo immediately, uncomment:
    # _update_oled_from_chip()
    return {"ok": True, "mono": mono}
