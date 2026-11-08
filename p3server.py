"""Pocket 3 agent server -- one process owns the camera, agents ask it to act.

Design notes that matter:

* A UVC camera can only be opened by one process. This server owns it, so every
  other program (and every agent) talks HTTP instead of touching the device.
* The camera exposes NO image controls over UVC: brightness, contrast,
  saturation, hue, gain, exposure, white balance, sharpness, gamma, backlight,
  focus and iris all report "not implemented" from the driver. Only
  pan/tilt/roll/zoom exist. So image adjustments here are applied in software to
  the frames we hand out, and are labelled as such.
* The gimbal has no interpolation: a single position write snaps instantly.
  Smooth motion therefore has to be produced by writing a target 60 times a
  second, which the sweep experiments showed is what keeps it from stuttering.
* Pan read-back lies on this device (writes can be silently dropped), so we keep
  our own commanded position and treat the picture as the only ground truth.

    python p3server.py --port 8770 [--key SECRET]
"""

import io
import json
import re
import secrets
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from argparse import ArgumentParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

if getattr(sys.stdout, 'encoding', '').lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import cv2
import numpy as np
import comtypes
from comtypes import GUID, IUnknown, COMMETHOD, HRESULT, POINTER
from ctypes import c_long

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES = os.path.join(HERE, 'frames')
MODELS = os.path.join(HERE, 'models')
os.makedirs(FRAMES, exist_ok=True)
os.makedirs(MODELS, exist_ok=True)

# The detector may live in models/ or next to this script -- both layouts work.
YUNET = next((p for p in (os.path.join(MODELS, 'yunet_2023mar.onnx'),
                          os.path.join(HERE, 'yunet_2023mar.onnx'))
              if os.path.exists(p)), os.path.join(MODELS, 'yunet_2023mar.onnx'))

# Subtitle fonts. The caption overlay needs a CJK-capable face; we look through
# the usual Windows locations and fall back to whatever PIL can load, so the
# server still runs on a machine without Chinese fonts installed.
_FONT_CANDIDATES = {
    'bold': [os.environ.get('P3_FONT_BOLD', ''),
             'C:/Windows/Fonts/msyhbd.ttc', 'C:/Windows/Fonts/msyh.ttc',
             'C:/Windows/Fonts/simhei.ttf', 'C:/Windows/Fonts/Deng.ttf',
             '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
             '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'],
    'main': [os.environ.get('P3_FONT_MAIN', ''),
             'C:/Windows/Fonts/simhei.ttf', 'C:/Windows/Fonts/msyh.ttc',
             'C:/Windows/Fonts/simsun.ttc', 'C:/Windows/Fonts/Deng.ttf',
             '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
             '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'],
}


def _find_font(kind):
    for p in _FONT_CANDIDATES[kind]:
        if p and os.path.exists(p):
            return p
    return None


FONT_BOLD = _find_font('bold')
FONT_MAIN = _find_font('main')

IID_IAMCameraControl = GUID("{C6E13370-30AC-11D0-A18C-00A0C9118956}")


class IAMCameraControl(IUnknown):
    _iid_ = IID_IAMCameraControl


IAMCameraControl._methods_ = [
    COMMETHOD([], HRESULT, "GetRange", (["in"], c_long, "P"),
              (["out"], POINTER(c_long), "a"), (["out"], POINTER(c_long), "b"),
              (["out"], POINTER(c_long), "c"), (["out"], POINTER(c_long), "d"),
              (["out"], POINTER(c_long), "e")),
    COMMETHOD([], HRESULT, "Set", (["in"], c_long, "P"), (["in"], c_long, "v"),
              (["in"], c_long, "f")),
    COMMETHOD([], HRESULT, "Get", (["in"], c_long, "P"),
              (["out"], POINTER(c_long), "v"), (["out"], POINTER(c_long), "f")),
]

PAN, TILT, ROLL, ZOOM = 0, 1, 2, 3
AXIS = {'pan': PAN, 'tilt': TILT, 'roll': ROLL, 'zoom': ZOOM}

# 60 Hz, no lead, ~12 deg/s: measured smoothest (cv 0.155 vs 1.179 at 30 Hz).
FPS = 60.0
SPEED = 12.0

CAM = None
AGENT = None
PORT = 8770
API_KEY = None
ALLOW_CUSTOM_BASE = False
TEXTURE_DEFAULT = {'brightness': 0.0, 'contrast': 1.0,
                   'saturation': 1.0, 'gamma': 1.0}


class Camera:
    def __init__(self, index=1, width=1280, height=720):
        comtypes.CoInitialize()
        from pygrabber.dshow_graph import SystemDeviceEnum
        from pygrabber.dshow_ids import DeviceCategories as DC

        sde = SystemDeviceEnum()
        names = sde.get_available_filters(DC.VideoInputDevice)
        if 'OsmoPocket3' not in names:
            raise RuntimeError('OsmoPocket3 not found (camera not in webcam mode): %r' % (names,))
        self.device_name = 'OsmoPocket3'
        filt, _ = sde.get_filter_by_index(DC.VideoInputDevice, names.index(self.device_name))
        self.cc = filt.QueryInterface(IAMCameraControl)

        self.width, self.height = width, height
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            raise RuntimeError('DirectShow index %d would not open' % index)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        for _ in range(12):
            self.cap.read()

        self.lock = threading.Lock()
        self.frame = None
        self.jpeg = None
        self.seq = 0
        self.fps = 0.0
        self.running = True
        self.grade = dict(TEXTURE_DEFAULT)

        # subtitle overlay: the only "screen" we can actually write to.
        # The camera body screen is a closed RTOS UI (see
        # dji-pocket3-features.md section on the 2.0" panel).
        self.caption = None
        self.caption_size = 0
        self.caption_until = 0.0
        self.caption_font = None
        self.caption_hist = []

        # tracking / hold state
        self.track_on = False
        self.track_info = {}
        self.hold_on = False
        self.hold_tilt = None
        self.pos = {'pan': self.state()['pan'], 'tilt': self.state()['tilt']}

        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._aux_loop, daemon=True).start()

    # -- capture ---------------------------------------------------------
    def _raw_loop(self):
        while self.running:
            ok, f = self.cap.read()
            if not ok or f is None:
                time.sleep(0.004)
                continue
            yield f

    def _loop(self):
        t0, n = time.perf_counter(), 0
        for f in self._raw_loop():
            with self.lock:
                self.frame = f
            n += 1
            now = time.perf_counter()
            if now - t0 >= 1.0:
                self.fps = n / (now - t0)
                n, t0 = 0, now
            if not self._stepper_active():
                d = self._encode(self._decorated(f))
                with self.lock:
                    if d:
                        self.jpeg = d
                    self.seq += 1

    def _stepper_active(self):
        return self.track_on or self.hold_on

    def _encode(self, f):
        ok, buf = cv2.imencode('.jpg', f, [cv2.IMWRITE_JPEG_QUALITY, 72])
        return buf.tobytes() if ok else None

    def _graded(self, f):
        g = self.grade
        if (g['brightness'] == 0.0 and g['contrast'] == 1.0
                and g['saturation'] == 1.0 and g['gamma'] == 1.0):
            return f
        out = f.astype(np.float32)
        if g['gamma'] != 1.0:
            out = np.power(np.clip(out / 255.0, 0, 1), 1.0 / g['gamma']) * 255.0
        out = out * float(g['contrast']) + float(g['brightness']) * 255.0
        out = np.clip(out, 0, 255).astype(np.uint8)
        if g['saturation'] != 1.0:
            hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[..., 1] = np.clip(hsv[..., 1] * g['saturation'], 0, 255)
            out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        return out

    # -- on-frame subtitle ------------------------------------------------
    def _caption_layer(self, w, h):
        """Render the caption to a BGR layer once and cache it.

        PIL cannot be called 30x/second without eating the frame budget, so the
        rendered RGBA text block is cached and only recomposited per frame.
        """
        if not self.caption or time.time() > self.caption_until:
            if self.caption and time.time() > self.caption_until:
                self.caption = None
            return None
        key = (self.caption, self.caption_size, w, h)
        if self.caption_hist and self.caption_hist[0] == key:
            return self.caption_hist[1]
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None
        size = self.caption_size or max(16, int(h * 0.042))
        font = None
        for p in (FONT_BOLD, FONT_MAIN):
            if os.path.exists(p):
                try:
                    font = ImageFont.truetype(p, size)
                    break
                except OSError:
                    pass
        if font is None:
            font = ImageFont.load_default()
        # wrap to ~86% of the frame width
        maxw = int(w * 0.86)
        lines, cur = [], ''
        for ch in self.caption:
            if ch == '\n':
                lines.append(cur)
                cur = ''
                continue
            if font.getlength(cur + ch) > maxw:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur or not lines:
            lines.append(cur)
        pad, gap = max(6, size // 2), max(2, size // 5)
        lh = size + gap
        boxw = max(int(font.getlength(l)) for l in lines) + pad * 2
        boxh = lh * len(lines) + pad * 2
        img = Image.new('RGBA', (boxw, boxh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, boxw - 1, boxh - 1], radius=max(4, size // 3),
                            fill=(8, 10, 14, 185))
        for i, l in enumerate(lines):
            d.text((pad, pad + i * lh), l, font=font, fill=(238, 244, 250, 255))
        layer = np.array(img)  # RGBA
        self.caption_hist = [key, layer]
        return layer

    def _decorated(self, f):
        """grade -> subtitle. Everything the outside world sees goes through here."""
        out = self._graded(f)
        layer = self._caption_layer(out.shape[1], out.shape[0])
        if layer is None:
            return out
        h, w = out.shape[:2]
        lh, lw = layer.shape[:2]
        y = h - lh - max(10, int(h * 0.05))
        x = max(6, int((w - lw) / 2))
        y = max(6, min(y, h - lh - 4))
        x = max(4, min(x, w - lw - 4))
        roi = out[y:y + lh, x:x + lw]
        alpha = (layer[..., 3:4].astype(np.float32)) / 255.0
        rgb = layer[..., 2::-1].astype(np.float32)  # RGBA -> BGR
        out[y:y + lh, x:x + lw] = (rgb * alpha + roi.astype(np.float32)
                                   * (1.0 - alpha)).astype(np.uint8)
        return out

    def _aux_loop(self):
        """15 Hz loop driving the software face tracker and the tilt hold."""
        det = None
        if os.path.exists(YUNET):
            try:
                det = cv2.FaceDetectorYN.create(YUNET, '', (320, 320), 0.6, 0.3, 500)
            except Exception as e:
                print('yunet load failed: %s' % e, flush=True)
        while self.running:
            try:
                if det is not None and self.track_on:
                    self._track_step(det)
                elif self.hold_on:
                    self._hold_step()
            except Exception as e:
                print('aux error: %s' % e, flush=True)
            time.sleep(0.066)

    def _track_step(self, det):
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        if f is None:
            return
        h, w = f.shape[:2]
        small = cv2.resize(f, (320, int(320 * h / w)))
        sh, sw = small.shape[:2]
        det.setInputSize((sw, sh))
        _, faces = det.detect(small)
        if faces is None or len(faces) == 0:
            self.track_info = {'seen': False}
            return
        best = max(faces, key=lambda r: r[2] * r[3])
        cx = (best[0] + best[2] / 2) / sw - 0.5
        cy = (best[1] + best[3] / 2) / sh - 0.5
        # 0.5 of frame width ~ 60 degrees of pan, so a proportional gain of ~40
        # closes the gap over a few steps without overshooting.
        dpan = cx * 40.0
        dtilt = -cy * 30.0
        if abs(dpan) > 0.4:
            self.pos['pan'] = float(np.clip(self.pos['pan'] + dpan, -35, 215))
            self.cc.Set(PAN, int(round(self.pos['pan'])), 0x0002)
        if abs(dtilt) > 0.4:
            self.pos['tilt'] = float(np.clip(self.pos['tilt'] + dtilt, -90, 90))
            self.cc.Set(TILT, int(round(self.pos['tilt'])), 0x0002)
        self.track_info = {'seen': True, 'score': round(float(best[-1]), 3),
                           'dx': round(cx, 3), 'dy': round(cy, 3),
                           'box': [int(v) for v in best[:4]]}
        with self.lock:
            self.jpeg = self._encode(self._decorated(f))

    def _hold_step(self):
        if self.hold_tilt is None:
            return
        self.cc.Set(TILT, int(round(self.hold_tilt)), 0x0002)
        with self.lock:
            f = None if self.frame is None else self.frame.copy()
        if f is not None:
            with self.lock:
                self.jpeg = self._encode(self._decorated(f))

    # -- PTZ -------------------------------------------------------------
    def ranges(self):
        out = {}
        for k, p in AXIS.items():
            a, b, c, d, e = self.cc.GetRange(p)
            out[k] = {'min': a, 'max': b, 'step': c, 'default': d}
        return out

    def state(self):
        return {k: self.cc.Get(p)[0] for k, p in AXIS.items()}

    def set(self, axis, v):
        r = self.ranges()[axis]
        v = int(round(max(r['min'], min(r['max'], float(v)))))
        self.cc.Set(AXIS[axis], v, 0x0002)
        self.pos[axis] = float(v)
        return v

    def ramp(self, axis, target, speed=None):
        r = self.ranges()[axis]
        target = max(r['min'], min(r['max'], float(target)))
        start = self.pos.get(axis, self.state()[axis])
        delta = target - start
        if abs(delta) < 1:
            self.set(axis, target)
            return {'from': start, 'to': target, 'steps': 1}
        speed = float(speed or SPEED)
        duration = max(0.15, abs(delta) / max(1.0, speed))
        steps = max(1, int(round(duration * FPS)))
        period = duration / steps
        for i in range(1, steps + 1):
            v = start + delta * i / steps
            self.cc.Set(AXIS[axis], int(round(v)), 0x0002)
            self.pos[axis] = v
            time.sleep(period)
        self.set(axis, target)
        return {'from': start, 'to': target, 'steps': steps,
                'seconds': round(duration, 2)}

    def grab(self, clean=True, tries=45):
        for _ in range(tries):
            with self.lock:
                f = None if self.frame is None else self.frame.copy()
            if f is not None:
                if not clean or (f > 16).mean() > 0.95:
                    return self._decorated(f)
            time.sleep(0.05)
        return None if f is None else self._decorated(f)

    def close(self):
        self.running = False
        try:
            self.cap.release()
        except Exception:
            pass


def _safe_path(name, ext=''):
    """Resolve a caller-supplied filename strictly inside FRAMES.

    /api/call is reachable over the network, so an absolute path or a '..'
    component would otherwise be an arbitrary-file-write primitive. Reject
    both, then confirm the resolved path is still under FRAMES.
    """
    if not name:
        return os.path.join(FRAMES, 'out_%d%s' % (int(time.time()), ext))
    name = str(name).replace('\\', '/')
    if os.path.isabs(name) or re.match(r'^[A-Za-z]:', name):
        raise ValueError('path must be relative to the output directory')
    parts = [p for p in name.split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        raise ValueError('path must not contain ".." or be empty')
    root = os.path.abspath(FRAMES)
    p = os.path.abspath(os.path.join(root, *parts))
    if p != root and not p.startswith(root + os.sep):
        raise ValueError('path escapes the output directory')
    return p


# ---------------------------------------------------------------------------
# Voice helpers
# ---------------------------------------------------------------------------
def speak(text, rate=0, voice=None):
    """Speak through the PC speaker with SAPI -- no downloads, no ffmpeg."""
    ps = ('Add-Type -AssemblyName System.Speech;'
          '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;'
          '$s.Rate = %d; ' % int(rate))
    if voice:
        # Validate instead of escaping: anything outside this charset is
        # refused outright, so nothing is ever interpolated as code.
        if not re.fullmatch(r"[A-Za-z0-9 _.,()\[\]-]+", str(voice)):
            raise ValueError('voice name contains unsupported characters')
        ps += "$s.SelectVoice('%s'); " % voice
    ps += "$s.Speak([IO.File]::ReadAllText('%TXT%', [Text.Encoding]::UTF8))"
    fd, path = tempfile.mkstemp(suffix='.txt')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(text)
    ps = ps.replace('%TXT%', path.replace('\\', '\\\\'))
    try:
        subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-Command', ps], timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def mic_index():
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if 'OsmoPocket3' in d['name'] and d['max_input_channels'] > 0:
            return i, d
    return None, None


def record(seconds=4.0, path=None):
    """Record from the camera microphone. 48 kHz only -- the UVC audio function
    rejects 44.1 k and 16 k with PaErrorCode -9997."""
    import sounddevice as sd
    idx, dev = mic_index()
    if idx is None:
        raise RuntimeError('camera microphone not found')
    p = path or os.path.join(FRAMES, 'listen_%d.wav' % int(time.time()))
    data = sd.rec(int(seconds * 48000), samplerate=48000, channels=1,
                  dtype='int16', device=idx)
    sd.wait()
    with wave.open(p, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(data.tobytes())
    return p, float(np.abs(data).max()) / 32768.0


def transcribe(path, model_size='small'):
    os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')
    from faster_whisper import WhisperModel
    m = WhisperModel(model_size, device='cpu', compute_type='int8')
    segs, info = m.transcribe(path, language='zh', vad_filter=True)
    text = ''.join(s.text for s in segs).strip()
    return text, getattr(info, 'language', 'zh'), getattr(info, 'language_probability', None)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
TOOLS = {}


def tool(name, category, params, doc):
    def deco(fn):
        TOOLS[name] = {'name': name, 'category': category, 'params': params,
                       'doc': doc, 'fn': fn}
        return fn
    return deco


@tool('status', 'core', {}, 'Camera state, ranges, fps, and active modes.')
def t_status(cam, a):
    return {'device': cam.device_name, 'size': [cam.width, cam.height],
            'fps': round(cam.fps, 2), 'state': cam.state(), 'ranges': cam.ranges(),
            'tracking': cam.track_on, 'hold': cam.hold_on,
            'grade': cam.grade}


@tool('see', 'vision', {'path': 'str?', 'clean': 'bool?'},
      'Save the current frame as JPEG and report its path, size, sharpness, brightness.')
def t_see(cam, a):
    f = cam.grab(clean=str(a.get('clean', True)).lower() not in ('0', 'false'))
    if f is None:
        return {'error': 'no frame'}
    p = _safe_path(a.get('path'), '.jpg')
    cv2.imwrite(p, f)
    g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    return {'path': p, 'width': int(f.shape[1]), 'height': int(f.shape[0]),
            'bytes': os.path.getsize(p),
            'sharpness': round(float(cv2.Laplacian(g, cv2.CV_64F).var()), 1),
            'brightness': round(float(g.mean()), 1)}


@tool('look', 'motion', {'pan': 'float?', 'tilt': 'float?', 'zoom': 'float?',
                         'roll': 'float?', 'speed': 'float?'},
      'Move smoothly to absolute pan/tilt/zoom/roll; speed in units per second.')
def t_look(cam, a):
    moves = {}
    for axis in ('pan', 'tilt', 'zoom', 'roll'):
        if a.get(axis) is not None:
            moves[axis] = cam.ramp(axis, a[axis], a.get('speed'))
    return {'ok': bool(moves), 'moves': moves, 'state': cam.state()}


@tool('look_rel', 'motion', {'dpan': 'float?', 'dtilt': 'float?', 'dzoom': 'float?',
                             'speed': 'float?'}, 'Move relative to the current position.')
def t_look_rel(cam, a):
    moves = {}
    for axis, key in (('pan', 'dpan'), ('tilt', 'dtilt'), ('zoom', 'dzoom')):
        if a.get(key) is not None:
            moves[axis] = cam.ramp(axis, cam.pos.get(axis, 0) + float(a[key]),
                                   a.get('speed'))
    return {'ok': bool(moves), 'moves': moves, 'state': cam.state()}


@tool('jump', 'motion', {'pan': 'float?', 'tilt': 'float?', 'zoom': 'float?',
                         'roll': 'float?'}, 'Instant absolute move, no interpolation.')
def t_jump(cam, a):
    out = {}
    for axis in ('pan', 'tilt', 'zoom', 'roll'):
        if a.get(axis) is not None:
            out[axis] = cam.set(axis, a[axis])
    return {'ok': bool(out), 'set': out, 'state': cam.state()}


@tool('center', 'motion', {'speed': 'float?'}, 'Return to pan 0 / tilt 0 / roll 0 / zoom 1x.')
def t_center(cam, a):
    out = {}
    for axis, v in (('pan', 0), ('tilt', 0), ('roll', 0), ('zoom', 100)):
        out[axis] = cam.ramp(axis, v, a.get('speed') or 40)
    return {'ok': True, 'moves': out, 'state': cam.state()}


@tool('scan', 'motion', {'from': 'float?', 'to': 'float?', 'step': 'float?',
                         'speed': 'float?'},
      'Sweep pan across a range, saving a frame at each stop.')
def t_scan(cam, a):
    lo = float(a.get('from', -35))
    hi = float(a.get('to', 215))
    step = abs(float(a.get('step', 25))) or 25
    shots = []
    p = lo
    while p <= hi + 1e-6:
        cam.ramp('pan', p, a.get('speed') or 30)
        time.sleep(0.25)
        shots.append(t_see(cam, {'path': 'scan_%03d.jpg' % int(round(p))}))
        p += step
    return {'ok': True, 'shots': shots}


@tool('record', 'vision', {'seconds': 'float?', 'path': 'str?'},
      'Record an MP4 clip at the streamed resolution.')
def t_record(cam, a):
    secs = float(a.get('seconds', 5))
    p = _safe_path(a.get('path'), '.mp4')
    vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'mp4v'), 30,
                         (cam.width, cam.height))
    n, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < secs:
        with cam.lock:
            f = None if cam.frame is None else cam.frame.copy()
        if f is not None:
            vw.write(cam._decorated(f))
            n += 1
        time.sleep(0.02)
    vw.release()
    return {'path': p, 'frames': n,
            'bytes': os.path.getsize(p) if os.path.exists(p) else 0}


@tool('track', 'vision', {'on': 'bool?', 'seconds': 'float?'},
      'Start/stop software face tracking. Native ActiveTrack is not reachable over UVC, '
      'so the gimbal is driven from face detection instead.')
def t_track(cam, a):
    on = str(a.get('on', 1)).lower() not in ('0', 'false', 'off')
    cam.track_on = on
    if not on:
        cam.track_info = {}
        with cam.lock:
            f = None if cam.frame is None else cam.frame.copy()
        if f is not None:
            with cam.lock:
                cam.jpeg = cam._encode(cam._decorated(f))
        return {'tracking': False}
    dur = float(a.get('seconds', 0) or 0)
    if dur:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < dur and cam.track_on:
            time.sleep(0.1)
        cam.track_on = False
    return {'tracking': True, 'info': cam.track_info,
            'model': os.path.basename(YUNET) if os.path.exists(YUNET) else 'missing'}


@tool('hold', 'motion', {'on': 'bool?', 'tilt': 'float?'},
      'Hold the tilt axis at its current (or given) angle, counteracting the '
      'gimbal follow mode by rewriting the value continuously.')
def t_hold(cam, a):
    on = str(a.get('on', 1)).lower() not in ('0', 'false', 'off')
    if not on:
        cam.hold_on = False
        cam.hold_tilt = None
        return {'hold': False}
    cam.hold_tilt = float(a['tilt']) if a.get('tilt') is not None else \
        float(cam.pos.get('tilt', cam.state()['tilt']))
    cam.hold_on = True
    return {'hold': True, 'tilt': cam.hold_tilt}


@tool('grade', 'vision',
      {'brightness': 'float?', 'contrast': 'float?', 'saturation': 'float?',
       'gamma': 'float?', 'reset': 'bool?'},
      'Software picture adjustment (the camera exposes no image controls over UVC). '
      'brightness -1..1, contrast 0..3, saturation 0..3, gamma 0.2..3.')
def t_grade(cam, a):
    if str(a.get('reset', '')).lower() in ('1', 'true') or a.get('reset') is True:
        cam.grade = dict(TEXTURE_DEFAULT)
        return {'grade': cam.grade}
    for k in ('brightness', 'contrast', 'saturation', 'gamma'):
        if a.get(k) is not None:
            cam.grade[k] = float(a[k])
    return {'grade': cam.grade}


@tool('caption', 'display',
      {'text': 'str?', 'seconds': 'float?', 'size': 'int?', 'clear': 'bool?'},
      'Draw a subtitle onto the live video frame (and therefore into any clip you '
      'record). This is the only screen we can write to: the camera body screen is a '
      'closed RTOS UI with no way in. Use it to make the AI visible.')
def t_caption(cam, a):
    if str(a.get('clear', '')).lower() in ('1', 'true') or a.get('clear') is True:
        cam.caption = None
        cam.caption_until = 0.0
        return {'caption': None}
    text = str(a.get('text') or '').strip()
    if not text:
        return {'error': 'empty text', 'caption': cam.caption}
    cam.caption = text
    cam.caption_size = int(a.get('size', 0) or 0)
    cam.caption_until = time.time() + float(a.get('seconds', 6) or 6)
    return {'caption': text, 'seconds': float(a.get('seconds', 6) or 6)}


@tool('say', 'voice', {'text': 'str', 'rate': 'int?'},
      'Speak text out of the PC speaker (Windows SAPI).')
def t_say(cam, a):
    text = (a.get('text') or '').strip()
    if not text:
        return {'error': 'empty text'}
    speak(text, rate=int(a.get('rate', 0) or 0))
    return {'ok': True, 'said': text}


@tool('listen', 'voice', {'seconds': 'float?', 'model': 'str?'},
      'Record from the camera microphone and transcribe with faster-whisper.')
def t_listen(cam, a):
    secs = float(a.get('seconds', 4))
    path, peak = record(secs)
    text, lang, prob = transcribe(path, a.get('model') or 'small')
    return {'path': path, 'seconds': secs, 'peak': round(peak, 3),
            'text': text, 'language': lang,
            'confidence': None if prob is None else round(float(prob), 3)}


def call_tool(name, args):
    if name not in TOOLS:
        return {'error': 'unknown tool %r' % name, 'tools': sorted(TOOLS)}
    return TOOLS[name]['fn'](CAM, args or {})


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Pocket 3</title>
<style>
html,body{margin:0;height:100%;background:#0b0d10;color:#d7dce3;
  font:13px/1.5 "Segoe UI",system-ui,sans-serif}
#wrap{display:flex;flex-direction:column;height:100%}
#bar{display:flex;gap:10px;align-items:center;padding:7px 10px;background:#12161c;
  border-bottom:1px solid #1e242c;flex:0 0 auto;flex-wrap:wrap}
#bar b{color:#7cc4ff;margin-right:4px}
.v{color:#eaeff5;font-variant-numeric:tabular-nums}
.v i{color:#5d6774;font-style:normal;margin-right:3px}
#view{flex:1 1 auto;display:flex;align-items:center;justify-content:center;
  overflow:hidden;background:#000;min-height:0}
#view img{max-width:100%;max-height:100%;display:block}
.btn{background:#1b2430;border:1px solid #2c3947;color:#cfe2f5;border-radius:5px;
  padding:3px 8px;cursor:pointer;font:inherit;line-height:1.2}
.btn:hover{background:#243040}.btn:active{background:#2f3f52}
.btn.on{background:#1d4a2e;border-color:#2dbd5c;color:#b6ffd0}
#chat{flex:0 0 auto;background:#0f1318;border-top:1px solid #1e242c;display:flex;
  flex-direction:column;max-height:38%}
#log{overflow-y:auto;padding:6px 10px;font-size:12.5px;line-height:1.45;
  min-height:54px;max-height:170px}
#log .u{color:#8fd0ff}#log .a{color:#dfe8f2}#log .t{color:#5d6774;font-size:11.5px}
#log .e{color:#ff9a8b}
#row{display:flex;gap:6px;padding:6px 10px;border-top:1px solid #1e242c}
#say{flex:1 1 auto;background:#151b23;border:1px solid #2c3947;color:#e6eef7;
  border-radius:5px;padding:5px 8px;font:inherit;outline:none}
#say:focus{border-color:#3d8fd6}
</style></head><body><div id="wrap">
<div id="bar">
  <b>Pocket 3</b>
  <span class="v"><i>fps</i><span id="fps">-</span></span>
  <span class="v"><i>pan</i><span id="pan">-</span></span>
  <span class="v"><i>tilt</i><span id="tilt">-</span></span>
  <span class="v"><i>zoom</i><span id="zoom">-</span></span>
  <button class="btn" onclick="go('pan',-25)">&#9668;&#9668;</button>
  <button class="btn" onclick="go('pan',-8)">&#9668;</button>
  <button class="btn" onclick="go('pan',8)">&#9658;</button>
  <button class="btn" onclick="go('pan',25)">&#9658;&#9658;</button>
  <button class="btn" onclick="go('tilt',-10)">&#9650;</button>
  <button class="btn" onclick="go('tilt',10)">&#9660;</button>
  <button class="btn" onclick="go('zoom',40)">+</button>
  <button class="btn" onclick="go('zoom',-40)">&minus;</button>
  <button class="btn" id="bt" onclick="tog('track',this)">face</button>
  <button class="btn" id="bh" onclick="tog('hold',this)">tilt&#8203;lock</button>
  <button class="btn" onclick="fetch('/api/call?name=center')">&#8962;</button>
  <span id="st" style="color:#5d6774">connecting</span>
</div>
<div id="view"><img id="im" alt="live"></div>
<div id="chat">
  <div id="log"></div>
  <div id="row">
    <input id="say" placeholder="跟相机说话…  (回车发送)" autocomplete="off">
    <button class="btn" id="send" onclick="send()">&#10148;</button>
    <button class="btn" id="mic" onclick="mic()">mic</button>
  </div>
</div>
</div><script>
const KEY=(new URLSearchParams(location.search).get('key')||sessionStorage.getItem('p3key')||'');if(KEY){sessionStorage.setItem('p3key',KEY);}
function tick(){const n=new Image();
  n.onload=()=>document.getElementById('im').src=n.src;
  n.src='/snap?t='+Date.now()+'&key='+KEY;}
setInterval(tick,75);tick();
function go(a,d){fetch('/api/call?key='+KEY+'&name=look_rel&d'+a+'='+d);}
async function tog(n,el){const on=!el.classList.contains('on');
  el.classList.toggle('on',on);
  await fetch('/api/call?key='+KEY+'&name='+n+'&on='+(on?1:0));}
async function stat(){try{
  const j=await (await fetch('/api/status?key='+KEY,{cache:'no-store'})).json();
  document.getElementById('fps').textContent=(j.fps||0).toFixed(1);
  document.getElementById('pan').textContent=j.state.pan;
  document.getElementById('tilt').textContent=j.state.tilt;
  document.getElementById('zoom').textContent=j.state.zoom;
  document.getElementById('bt').classList.toggle('on',!!j.tracking);
  document.getElementById('bh').classList.toggle('on',!!j.hold);
  document.getElementById('st').textContent='live';
}catch(e){document.getElementById('st').textContent='offline';}}
setInterval(stat,700);stat();

// ---- conversation with the camera's brain ---------------------------------
function push(cls,text){const l=document.getElementById('log');
  const d=document.createElement('div');d.className=cls;d.textContent=text;
  l.appendChild(d);l.scrollTop=l.scrollHeight;return d;}
async function send(){
  const el=document.getElementById('say');const t=el.value.trim();
  if(!t)return; el.value=''; push('u','你：'+t);
  const act=push('t','…');
  try{
    const r=await fetch('/api/agent/chat?key='+KEY,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:t,speak:true,caption:true})});
    const j=await r.json();
    if(!j.ok){act.className='e';act.textContent=j.error||'失败';
      if(j.hint)push('t',j.hint);return;}
    act.remove();
    for(const a of (j.actions||[]))
      push('t','· '+a.tool+' '+JSON.stringify(a.args)+(a.ok?'':' [失败]'));
    push('a','AI：'+j.reply);
  }catch(e){act.className='e';act.textContent='请求失败: '+e;}
}
document.getElementById('say').addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();send();}});
async function mic(){
  const b=document.getElementById('mic');b.disabled=true;b.textContent='…';
  try{
    const r=await fetch('/api/call?key='+KEY+'&name=listen&seconds=4');
    const j=await r.json();
    if(j.text){document.getElementById('say').value=j.text;send();}
    else push('t','（没听到声音）');
  }catch(e){push('e','录音失败: '+e);}
  b.disabled=false;b.textContent='mic';
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _auth_ok(self, q):
        if not API_KEY:
            return True
        return (q.get('key', [None])[0] == API_KEY
                or self.headers.get('X-Api-Key') == API_KEY)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        cam = CAM

        if u.path in ('/', '/index.html'):
            body = PAGE.replace('__KEY__', json.dumps(''))
            return self._send(200, body, 'text/html; charset=utf-8')
        if not self._auth_ok(q):
            return self._send(401, {'error': 'bad or missing api key'})

        if u.path == '/snap':
            if cam is None:
                return self._send(503, b'no camera', 'text/plain')
            for _ in range(400):
                with cam.lock:
                    d = cam.jpeg
                if d:
                    return self._send(200, d, 'image/jpeg')
                time.sleep(0.01)
            return self._send(503, b'no frame', 'text/plain')

        if u.path == '/api/status':
            return self._send(200, call_tool('status', {}))

        if u.path == '/api/tools':
            return self._send(200, {'count': len(TOOLS), 'tools': [
                {'name': t['name'], 'category': t['category'],
                 'params': t['params'], 'doc': t['doc']}
                for t in TOOLS.values()]})

        if u.path == '/api/call':
            name = q.get('name', [None])[0]
            args = {k: v[0] for k, v in q.items() if k not in ('name', 'key')}
            args = {k: unquote(str(v)) for k, v in args.items()}
            for k in ('pan', 'tilt', 'zoom', 'roll', 'dpan', 'dtilt', 'dzoom',
                      'speed', 'seconds', 'from', 'to', 'step', 'brightness',
                      'contrast', 'saturation', 'gamma'):
                if k in args:
                    try:
                        args[k] = float(args[k])
                    except ValueError:
                        pass
            for k in ('rate',):
                if k in args:
                    try:
                        args[k] = int(args[k])
                    except ValueError:
                        pass
            try:
                return self._send(200, call_tool(name, args))
            except Exception as e:
                return self._send(500, {'error': '%s: %s' % (type(e).__name__, e)})

        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b'{}'
        try:
            body = json.loads(raw.decode('utf-8') or '{}')
        except ValueError:
            return self._send(400, {'error': 'bad json'})

        if u.path == '/api/agent/key':
            if not self._auth_ok(q):
                return self._send(401, {'error': 'bad or missing api key'})
            return self._send(200, _configure_agent(body))

        if not self._auth_ok(q):
            return self._send(401, {'error': 'bad or missing api key'})

        if u.path == '/api/agent/chat':
            if AGENT is None:
                return self._send(503, {'ok': False, 'error': 'agent not initialised'})
            text = str(body.get('text') or '').strip()
            if not text:
                return self._send(400, {'ok': False, 'error': 'empty text'})
            try:
                return self._send(200, AGENT.chat(
                    text,
                    speak=bool(body.get('speak', True)),
                    caption=bool(body.get('caption', True))))
            except Exception as e:
                return self._send(500, {'ok': False,
                                        'error': '%s: %s' % (type(e).__name__, e)})

        return self._send(404, {'error': 'not found'})


def _configure_agent(body):
    """Create or reconfigure the LLM agent without restarting the server."""
    global AGENT
    from agent import Agent, PROVIDERS
    provider = str(body.get('provider') or 'deepseek')
    if provider not in PROVIDERS:
        return {'ok': False, 'error': 'unknown provider %r' % provider,
                'providers': sorted(PROVIDERS)}
    try:
        checked_base = _check_base(body.get('base'))
    except ValueError as e:
        return {'ok': False, 'error': str(e), 'providers': sorted(PROVIDERS)}
    AGENT = Agent(provider=provider,
                  key=body.get('key') or None,
                  model=body.get('model') or None,
                  base=checked_base,
                  server='http://127.0.0.1:%d' % PORT,
                  api_key=API_KEY)
    return {'ok': True, 'provider': AGENT.provider, 'model': AGENT.model,
            'ready': AGENT.ready,
            'reachable': _probe_provider(AGENT.base)}


def _check_base(base):
    """A custom base URL receives the LLM api key as a Bearer token.

    Only allow one behind an explicit --allow-custom-base, and only over
    https, so the key cannot be walked off to an arbitrary plaintext host.
    """
    if not base:
        return None
    base = str(base).strip()
    if not ALLOW_CUSTOM_BASE:
        raise ValueError(
            'custom base URL is disabled because it would receive your LLM api '
            'key; restart with --allow-custom-base to permit it')
    if not base.lower().startswith('https://'):
        raise ValueError('custom base URL must use https://')
    return base


def _probe_provider(base):
    import urllib.request
    try:
        with urllib.request.urlopen(base.rstrip('/') + '/models', timeout=8) as r:
            return r.status
    except Exception as e:
        code = getattr(e, 'code', None)
        return code if code else type(e).__name__


def main():
    global CAM, API_KEY, PORT, ALLOW_CUSTOM_BASE
    ap = ArgumentParser()
    ap.add_argument('--port', type=int, default=8770)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--index', type=int, default=1)
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=720)
    ap.add_argument('--key', default=None,
                      help='API key for the HTTP interface. Omit and a random one is '
                           'generated; authentication is never off by default.')
    ap.add_argument('--no-auth', action='store_true',
                      help='Run with no key. Only for a trusted loopback setup.')
    ap.add_argument('--allow-custom-base', action='store_true',
                      help='Allow a custom LLM base URL. It receives your LLM api key.')
    ap.add_argument('--llm-provider', default='deepseek')
    ap.add_argument('--llm-key', default=None)
    ap.add_argument('--llm-model', default=None)
    a = ap.parse_args()
    if a.no_auth:
        API_KEY = None
        print('WARNING: running with NO authentication (--no-auth).', flush=True)
    elif a.key:
        API_KEY = a.key
    else:
        API_KEY = secrets.token_urlsafe(18)
        print('API key (generated): %s' % API_KEY, flush=True)
        print('Pass it as ?key=... or the X-Api-Key header.', flush=True)
    ALLOW_CUSTOM_BASE = a.allow_custom_base
    PORT = a.port

    try:
        CAM = Camera(a.index, a.width, a.height)
        print('camera open: %s %dx%d' % (CAM.device_name, a.width, a.height), flush=True)
        print('ranges: %s' % json.dumps(CAM.ranges()), flush=True)
        print('state : %s' % json.dumps(CAM.state()), flush=True)
    except Exception as e:
        print('CAMERA FAILED: %s' % e, flush=True)
        CAM = None

    if a.llm_key:
        try:
            cfg = _configure_agent({'provider': a.llm_provider,
                                    'key': a.llm_key, 'model': a.llm_model})
            print('agent: %s' % json.dumps(cfg, ensure_ascii=False), flush=True)
        except Exception as e:
            print('agent failed: %s' % e, flush=True)

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print('serving http://%s:%d/  (tools: %d)' % (a.host, a.port, len(TOOLS)), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if CAM:
            CAM.close()


if __name__ == '__main__':
    main()
