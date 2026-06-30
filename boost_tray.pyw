"""
boost_tray.pyw — system-wide audio booster, Windows-style volume HUD (anti-aliased)

Engine : VB-Cable loopback — default playback is routed through VB-Cable, captured,
         multiplied by the boost, and played to the speakers. Default device is
         auto-switched to VB-Cable on start and restored on quit.
HUD    : compact glass pill above the native Windows volume box, PIL-rendered, slides up.
         Pressing Volume-Up while already at 100% pops it; keys raise/lower the boost.
Tray   : left-click = show HUD, right-click = Set max boost / Quit.
"""
import os
import sys
import json
import time
import atexit
import threading
import ctypes
import ctypes.wintypes as wintypes
import tkinter as tk

# single instance: if already running, exit (avoids double tray / device fight)
_mx = ctypes.windll.kernel32.CreateMutexW(None, False, 'AudioBoostBeyond100_singleton')
if ctypes.windll.kernel32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
    sys.exit(0)

# DPI-aware BEFORE any GUI, so PIL pixels map 1:1 to device pixels
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)        # per-monitor
except Exception:
    try: ctypes.windll.user32.SetProcessDPIAware()
    except Exception: pass

import sounddevice as sd
import numpy as np
import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk
from pycaw.pycaw import AudioUtilities
from comtypes import (CoInitialize, CoCreateInstance, GUID, COMMETHOD,
                      HRESULT, IUnknown, CLSCTX_ALL)
from ctypes import c_int, c_wchar_p

# ── config ────────────────────────────────────────────────────────
CFG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.boost_config.json')
SAFE_MAX = 300
AUTO_HIDE_MS = 2000
FADE_MS = 2000

ICON_FONT_PATH = r'C:\Windows\Fonts\SegoeIcons.ttf'
ICON_FONT_FAMILY = 'Segoe Fluent Icons'
GLYPH_VOL  = chr(0xE767)
GLYPH_WARN = chr(0xE7BA)

# colors (yellow accent, dark glass like Windows)
KEY_HEX = '#010203'
KEY  = (1, 2, 3, 255)
CARD = (32, 32, 38, 255)
EDGE = (72, 72, 84, 255)
ACC_RGB    = (255, 209, 10)        # yellow
ACC_HEX    = '#ffd10a'
DANGER_RGB = (255, 69, 58)         # red (above safe max)
DANGER_HEX = '#ff453a'
FG_RGB = (255, 255, 255)
SUB_HEX = '#b0b0ba'
TRK_RGB = (84, 84, 96)
ALPHA = 0.94

_cfg = {'max': 500}
try:
    _cfg.update(json.load(open(CFG_FILE)))
except Exception:
    pass

_boost = 1.0                 # gain ratio; always start at 100% (no boost), keys raise it
_max   = _cfg['max']
_lock  = threading.Lock()
KEY_STEP = 5                 # boost % change per volume-key press

def _save_cfg():
    try:
        json.dump({'max': _max}, open(CFG_FILE, 'w'))
    except Exception:
        pass

# ── boost engine: VB-Cable loopback (capture cable, multiply, play to speakers) ─
_CLSID_PC = GUID('{870af99c-171d-4f9e-af0d-e63df40c2bc9}')
_IID_PC   = GUID('{f8679f50-850a-41cf-9c72-430f290290c8}')

class _IPolicyConfig(IUnknown):
    _iid_ = _IID_PC
    _methods_ = [COMMETHOD([], HRESULT, n) for n in
                 ('GetMixFormat', 'GetDeviceFormat', 'ResetDeviceFormat',
                  'SetDeviceFormat', 'GetProcessingPeriod', 'SetProcessingPeriod',
                  'GetShareMode', 'SetShareMode', 'GetPropertyValue',
                  'SetPropertyValue')] + [
        COMMETHOD([], HRESULT, 'SetDefaultEndpoint',
                  (['in'], c_wchar_p, 'id'), (['in'], c_int, 'role')),
        COMMETHOD([], HRESULT, 'SetEndpointVisibility')]

def _set_default(device_id):
    CoInitialize()
    pc = CoCreateInstance(_CLSID_PC, _IPolicyConfig, CLSCTX_ALL)
    for role in (0, 1, 2):
        pc.SetDefaultEndpoint(device_id, role)

def _find_sd(name, kind):
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d['name'].lower():
            if kind == 'in'  and d['max_input_channels']  > 0: return i
            if kind == 'out' and d['max_output_channels'] > 0: return i

_prev_default = [None]
def _restore_default():
    if _prev_default[0]:
        try: _set_default(_prev_default[0])
        except Exception: pass

atexit.register(_restore_default)

def _wait_devices():
    for _ in range(40):
        i = _find_sd('CABLE Output', 'in')
        o = _find_sd('Speakers (Realtek', 'out') or _find_sd('Speakers', 'out')
        if i is not None and o is not None:
            try: c = min(sd.query_devices(i)['max_input_channels'], 2)
            except Exception: c = 2
            return i, o, c
        time.sleep(1.0)
        try: sd._terminate(); sd._initialize()
        except Exception: pass
    return None, None, 2

def _vb_cable_id():
    """find the VB-Cable render endpoint id at runtime (portable across machines)"""
    try:
        for d in AudioUtilities.GetAllDevices():
            if d.FriendlyName and 'CABLE Input' in d.FriendlyName:
                return d.id
    except Exception:
        pass
    return None

_in, _out, _ch = _wait_devices()
try:
    _prev_default[0] = AudioUtilities.GetSpeakers().id
except Exception:
    pass
try:
    _vbid = _vb_cable_id()                 # route all apps into the cable
    if _vbid: _set_default(_vbid)
except Exception as e:
    print('device switch failed:', e)

def _audio_cb(indata, outdata, frames, t, status):
    with _lock:
        b = _boost
    np.clip(indata * b, -1.0, 1.0, out=outdata)

_stream = None
if _in is not None and _out is not None:
    for _ in range(10):
        try:
            _stream = sd.Stream(samplerate=48000, blocksize=512, device=(_in, _out),
                                channels=_ch, dtype='float32', callback=_audio_cb, latency='low')
            _stream.start()
            break
        except Exception:
            time.sleep(1.0)

# ── tk master + live DPI scale (auto-adapts per monitor) ──────────
root = tk.Tk()
root.withdraw()
_S = [root.winfo_fpixels('1i') / 96.0]

def _update_scale():
    """read the DPI of the monitor that hosts the taskbar/OSD, live —
    so the box size auto-matches Windows on any monitor / scaling."""
    try:
        hwnd = ctypes.windll.user32.FindWindowW('Shell_TrayWnd', None)
        if hwnd:
            dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
            if dpi > 0:
                _S[0] = dpi / 96.0
                return
    except Exception:
        pass
    try:
        _S[0] = root.winfo_fpixels('1i') / 96.0
    except Exception:
        pass

def px(v):  # design units -> device pixels (uses current monitor scale)
    return int(round(v * _S[0]))

# ── fonts (PIL) ───────────────────────────────────────────────────
def _ifont(size):
    return ImageFont.truetype(ICON_FONT_PATH, size)

def _pfont(size):
    for p in (r'C:\Windows\Fonts\segoeui.ttf', r'C:\Windows\Fonts\seguisb.ttf'):
        try: return ImageFont.truetype(p, size)
        except Exception: pass
    return ImageFont.load_default()

# ── tray icon (PIL Fluent glyph) ──────────────────────────────────
_tray_glyph = _ifont(34)
def _make_icon(pct):
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 3, 61, 61], radius=16, fill=ACC_RGB)
    bb = d.textbbox((0, 0), GLYPH_VOL, font=_tray_glyph)
    gw, gh = bb[2]-bb[0], bb[3]-bb[1]
    d.text((32-gw/2-bb[0], 32-gh/2-bb[1]), GLYPH_VOL, font=_tray_glyph, fill=(20, 20, 20))
    return img

# ── HUD geometry (design units, matched to native Windows OSD) ────
CARD_W, CARD_H = 196, 50
RAD = 14
ICONX, ICON_PX = 22, 17
SX0, SX1, TRK_H, KNOB_R = 44, 150, 2.5, 5
NUM_RIGHT, NUM_PX = 18, 15
BOTTOM_GAP = 74              # sit ABOVE the native Windows volume box (jumps up from it)
MIDY = CARD_H / 2
NUM_FG = (236, 236, 240)     # number is light (like Windows), bar carries the accent

def _lerp(c0, c1, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(c0[i] + (c1[i]-c0[i])*t)) for i in range(3))

def _render_card(pct, mx):
    ss = 3
    w, h = px(CARD_W), px(CARD_H)
    big = Image.new('RGBA', (w*ss, h*ss), KEY)
    d = ImageDraw.Draw(big)
    # bar color eases yellow -> red as boost rises above the safe threshold
    if pct <= SAFE_MAX or mx <= SAFE_MAX:
        acc = ACC_RGB
    else:
        acc = _lerp(ACC_RGB, DANGER_RGB, (pct - SAFE_MAX) / (mx - SAFE_MAX))
    # flat card, no outline/border (clean, like Windows)
    d.rounded_rectangle([0, 0, w*ss-1, h*ss-1], radius=px(RAD)*ss, fill=CARD)
    # speaker glyph
    d.text((px(ICONX)*ss, px(MIDY)*ss), GLYPH_VOL, font=_ifont(px(ICON_PX)*ss),
           fill=NUM_FG, anchor='mm')
    # slider
    tx0, tx1, ty, th = px(SX0)*ss, px(SX1)*ss, px(MIDY)*ss, px(TRK_H)*ss
    d.rounded_rectangle([tx0, ty-th, tx1, ty+th], radius=th, fill=TRK_RGB)
    frac = (pct-100)/(mx-100) if mx > 100 else 0
    frac = max(0.0, min(1.0, frac))
    kx = tx0 + frac*(tx1-tx0)
    if kx > tx0 + 2*th:                    # filled bar, rounded end, no knob (like Windows)
        d.rounded_rectangle([tx0, ty-th, kx, ty+th], radius=th, fill=acc)
    elif kx > tx0:
        d.ellipse([tx0, ty-th, tx0+2*th, ty+th], fill=acc)
    # number (no % sign, like Windows)
    d.text((w*ss - px(NUM_RIGHT)*ss, px(MIDY)*ss), f'{int(round(pct))}',
           font=_pfont(px(NUM_PX)*ss), fill=NUM_FG, anchor='rm')
    return big.resize((w, h), Image.LANCZOS)

# ── HUD window ────────────────────────────────────────────────────
fly = tk.Toplevel(root)
fly.withdraw()
fly.overrideredirect(True)
fly.attributes('-topmost', True)
fly.attributes('-alpha', ALPHA)
fly.attributes('-transparentcolor', KEY_HEX)
fly.configure(bg=KEY_HEX)
lbl = tk.Label(fly, bg=KEY_HEX, bd=0, highlightthickness=0)
lbl.pack()
_photo = [None]
_disp = [float(int(_boost*100))]      # animated displayed value (eases toward _boost)
_bar_job = [None]

def _refresh():
    img = _render_card(_disp[0], _max)
    ph = ImageTk.PhotoImage(img)
    _photo[0] = ph
    lbl.configure(image=ph)

def _animate_bar():
    target = _boost * 100
    diff = target - _disp[0]
    if abs(diff) < 0.6:
        _disp[0] = target
        _bar_job[0] = None
    else:
        _disp[0] += diff * 0.28           # smooth ease toward target
        _bar_job[0] = fly.after(12, _animate_bar)
    _refresh()

def _animate_to_value():
    if _bar_job[0] is None:
        _animate_bar()

_icon_job = [None]
def _push_tray():
    pct = int(_boost*100)
    tray.icon = _make_icon(pct)
    tray.title = f'Audio Boost — {pct}%'

# show (slide up + fade-in) / hide (slide down) — time-based, smooth
_hide_job = [None]; _anim_job = [None]
_visible = [False]
_pos = [0, 0]
_easeOut = lambda t: 1 - (1 - t) ** 3      # decelerate (Windows-like)
_easeIn  = lambda t: t * t * t

def _stop_anim():
    if _anim_job[0]: fly.after_cancel(_anim_job[0]); _anim_job[0] = None

def _hide():
    _stop_anim()
    if _hide_job[0]: fly.after_cancel(_hide_job[0]); _hide_job[0] = None
    fly.withdraw()
    _visible[0] = False

def _animate(y0, y1, a0, a1, dur, ease, done):
    """frame-rate-independent move + optional fade"""
    start = time.perf_counter()
    def step():
        t = (time.perf_counter() - start) * 1000.0 / dur
        if t >= 1.0:
            fly.geometry(f'+{_pos[0]}+{y1}')
            fly.attributes('-alpha', a1)
            fly.update_idletasks()
            if done: done()
            return
        e = ease(t)
        fly.geometry(f'+{_pos[0]}+{int(round(y0 + (y1 - y0) * e))}')
        if a0 != a1:
            fly.attributes('-alpha', a0 + (a1 - a0) * e)
        fly.update_idletasks()
        _anim_job[0] = fly.after(8, step)
    step()

def _slide_out():
    _stop_anim()
    _animate(_pos[1], root.winfo_screenheight(), ALPHA, ALPHA, 240, _easeIn, _hide)

def _arm():
    _stop_anim()
    if _hide_job[0]: fly.after_cancel(_hide_job[0])
    fly.geometry(f'+{_pos[0]}+{_pos[1]}')
    fly.attributes('-alpha', ALPHA)
    _hide_job[0] = fly.after(AUTO_HIDE_MS, _slide_out)

def _hud_top(ih):
    """place above the native Windows volume OSD, read live from the taskbar
    (adapts to taskbar height, auto-hide state, and DPI on any monitor)"""
    sh = root.winfo_screenheight()
    base = sh
    try:
        hwnd = _u32.FindWindowW('Shell_TrayWnd', None)
        r = wintypes.RECT()
        if hwnd and _u32.GetWindowRect(hwnd, ctypes.byref(r)):
            tb_visible = r.top < sh - 3       # parked off-screen when auto-hidden
            base = r.top if tb_visible else sh
    except Exception:
        pass
    native_top = base - px(12) - ih           # native OSD sits ~px(12) up, ~same height as ours
    return native_top - px(10) - ih           # our box sits px(10) above it

def show_hud():
    _stop_anim()
    if _hide_job[0]: fly.after_cancel(_hide_job[0]); _hide_job[0] = None
    _update_scale()
    _refresh()
    iw, ih = px(CARD_W), px(CARD_H)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    _pos[0], _pos[1] = (sw - iw) // 2, _hud_top(ih)
    if not _visible[0]:
        _visible[0] = True
        y0 = _pos[1] + px(50)
        fly.geometry(f'+{_pos[0]}+{y0}')
        fly.attributes('-alpha', 0.0)
        fly.deiconify(); fly.lift()
        _animate(y0, _pos[1], 0.0, ALPHA, 380, _easeOut, _arm)
    else:
        fly.geometry(f'+{_pos[0]}+{_pos[1]}')
        fly.attributes('-alpha', ALPHA)
        fly.lift(); _arm()

def _bump(delta):
    global _boost
    with _lock:
        nb = max(100, min(_max, int(round(_boost*100)) + delta))
        _boost = nb / 100
    show_hud()
    _animate_to_value()                   # bar glides smoothly to new level
    if _icon_job[0]: fly.after_cancel(_icon_job[0])
    _icon_job[0] = fly.after(150, _push_tray)

def _set_from_x(x):
    global _boost
    frac = (x - px(SX0)) / (px(SX1) - px(SX0))
    pct = round(100 + max(0.0, min(1.0, frac)) * (_max - 100))
    with _lock: _boost = pct / 100
    if _bar_job[0]: fly.after_cancel(_bar_job[0]); _bar_job[0] = None
    _disp[0] = pct                        # drag = direct/instant (follows finger)
    _refresh(); _arm()
    if _icon_job[0]: fly.after_cancel(_icon_job[0])
    _icon_job[0] = fly.after(150, _push_tray)

def _on_drag(e):
    if px(SX0)-px(14) <= e.x <= px(SX1)+px(14):
        _set_from_x(e.x)

lbl.bind('<Button-1>', _on_drag)
lbl.bind('<B1-Motion>', _on_drag)
lbl.bind('<Enter>', lambda e: _arm())
fly.bind('<Escape>', lambda e: _hide())

# ── settings card (max boost) — glass, English, matches the HUD ───
SET_W, SET_H, SET_RAD = 260, 150, 16
SET_SX0, SET_SX1, SET_SY = 26, 234, 100
SUB_RGB = (176, 176, 186)

def _render_settings(mv):
    ss = 3
    w, h = px(SET_W), px(SET_H)
    big = Image.new('RGBA', (w*ss, h*ss), KEY)
    d = ImageDraw.Draw(big)
    over = mv > SAFE_MAX
    acc = _lerp(ACC_RGB, DANGER_RGB, (mv-SAFE_MAX)/max(1, 1000-SAFE_MAX)) if over else ACC_RGB
    d.rounded_rectangle([0, 0, w*ss-1, h*ss-1], radius=px(SET_RAD)*ss, fill=CARD)
    d.text((w*ss//2, px(26)*ss), 'MAXIMUM BOOST', font=_pfont(px(11)*ss), fill=SUB_RGB, anchor='mm')
    d.text((w*ss//2, px(60)*ss), f'{mv}', font=_pfont(px(30)*ss), fill=acc, anchor='mm')
    tx0, tx1, ty, th = px(SET_SX0)*ss, px(SET_SX1)*ss, px(SET_SY)*ss, px(TRK_H)*ss
    d.rounded_rectangle([tx0, ty-th, tx1, ty+th], radius=th, fill=TRK_RGB)
    frac = max(0.0, min(1.0, (mv-200)/800))
    kx = tx0 + frac*(tx1-tx0)
    if kx > tx0 + 2*th:
        d.rounded_rectangle([tx0, ty-th, kx, ty+th], radius=th, fill=acc)
    elif kx > tx0:
        d.ellipse([tx0, ty-th, tx0+2*th, ty+th], fill=acc)
    if over:
        d.text((px(40)*ss, px(130)*ss), GLYPH_WARN, font=_ifont(px(12)*ss), fill=DANGER_RGB, anchor='lm')
        d.text((px(58)*ss, px(130)*ss), 'High volume may damage speakers',
               font=_pfont(px(10)*ss), fill=DANGER_RGB, anchor='lm')
    else:
        d.text((w*ss//2, px(130)*ss), 'drag to set   ·   Esc to close',
               font=_pfont(px(10)*ss), fill=SUB_RGB, anchor='mm')
    return big.resize((w, h), Image.LANCZOS)

_set_photo = [None]
def open_settings():
    _update_scale()
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes('-topmost', True)
    win.attributes('-alpha', ALPHA)
    win.attributes('-transparentcolor', KEY_HEX)
    win.configure(bg=KEY_HEX)
    lab = tk.Label(win, bg=KEY_HEX, bd=0, highlightthickness=0)
    lab.pack()
    iw, ih = px(SET_W), px(SET_H)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    win.geometry(f'+{(sw-iw)//2}+{(sh-ih)//2}')
    st = {'mv': _max}

    def draw():
        ph = ImageTk.PhotoImage(_render_settings(st['mv']))
        _set_photo[0] = ph
        lab.configure(image=ph)

    def apply_x(x):
        global _max, _boost
        frac = (x - px(SET_SX0)) / (px(SET_SX1) - px(SET_SX0))
        mv = int(round((200 + max(0.0, min(1.0, frac)) * 800) / 50.0)) * 50
        st['mv'] = max(200, min(1000, mv))
        _max = st['mv']
        if _boost*100 > _max:
            with _lock: _boost = _max/100
        draw()

    def on_drag(e):
        if px(SET_SX0)-px(16) <= e.x <= px(SET_SX1)+px(16) and abs(e.y - px(SET_SY)) <= px(28):
            apply_x(e.x)

    def close(_=None):
        _save_cfg()
        try: win.destroy()
        except Exception: pass

    lab.bind('<Button-1>', on_drag)
    lab.bind('<B1-Motion>', on_drag)
    win.bind('<Escape>', close)
    win.bind('<FocusOut>', lambda e: win.after(160, lambda: (not win.focus_displayof()) and close()))
    draw()
    win.deiconify(); win.lift(); win.after(60, win.focus_force)

# ── volume-up watcher (low-level keyboard hook) ───────────────────
VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100

class _KBD(ctypes.Structure):
    _fields_ = [('vk', wintypes.DWORD), ('scan', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('extra', ctypes.c_size_t)]

_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                               wintypes.WPARAM, wintypes.LPARAM)
_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32
_k32.GetModuleHandleW.restype = wintypes.HMODULE
_k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_u32.SetWindowsHookExW.restype = ctypes.c_void_p
_u32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HMODULE, wintypes.DWORD]
_u32.CallNextHookEx.restype = wintypes.LPARAM
_u32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_vol_iface = [None]

def _at_max():
    try:
        return _vol_iface[0].GetMasterVolumeLevelScalar() >= 0.99
    except Exception:
        return False

def _hook_cb(nCode, wParam, lParam):
    if nCode >= 0 and wParam == WM_KEYDOWN:
        vk = ctypes.cast(lParam, ctypes.POINTER(_KBD)).contents.vk
        if vk == VK_VOLUME_UP:
            if _at_max():                 # Windows already at 100% -> boost up
                root.after(0, lambda: _bump(+KEY_STEP))
                # don't consume: let the native Windows box stay (we pop up above it)
        elif vk == VK_VOLUME_DOWN:
            if _boost > 1.0001:           # currently boosting -> reduce boost first
                root.after(0, lambda: _bump(-KEY_STEP))
                return 1                  # consume key
    return _u32.CallNextHookEx(None, nCode, wParam, lParam)

_hook_fn = _HOOKPROC(_hook_cb)

def _run_hook():
    CoInitialize()
    try:
        _vol_iface[0] = AudioUtilities.GetSpeakers().EndpointVolume
    except Exception as e:
        print('vol iface failed:', e)
    hmod = _k32.GetModuleHandleW(None)
    if not _u32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_fn, hmod, 0):
        print('hook install failed:', ctypes.get_last_error())
    msg = wintypes.MSG()
    while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0):
        _u32.TranslateMessage(ctypes.byref(msg))
        _u32.DispatchMessageW(ctypes.byref(msg))

threading.Thread(target=_run_hook, daemon=True).start()

# ── tray ──────────────────────────────────────────────────────────
def _quit(icon, item):
    try:
        if _stream: _stream.stop()
    except Exception: pass
    _restore_default()                    # put default device back to speakers
    tray.stop()
    root.after(0, root.destroy)

tray = pystray.Icon('boost', _make_icon(int(_boost*100)), f'Audio Boost — {int(_boost*100)}%',
    pystray.Menu(
        pystray.MenuItem('Show', lambda i, it: root.after(0, show_hud), default=True),
        pystray.MenuItem('Set max boost…', lambda i, it: root.after(0, open_settings)),
        pystray.MenuItem('Quit', _quit)))

threading.Thread(target=tray.run, daemon=True).start()
_push_tray()
root.mainloop()
