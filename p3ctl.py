"""Pocket 3 UVC PTZ controller.

Key finding: the UVC Pan/Tilt/Zoom/Roll controls on the OsmoPocket3 *only take
effect while a video stream is open*, and the MediaCapture control session must
be created BEFORE the capture session. This module wraps a persistent
PowerShell daemon (uvc_daemon.ps1) that owns MediaCapture so that each control
update costs ~10 ms instead of ~1.2 s.
"""

import os
import subprocess
import sys
import threading
import time

import cv2

WS = os.path.dirname(os.path.abspath(__file__))
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
DAEMON = os.path.join(WS, 'uvc_daemon.ps1')

LIMITS = {
    'Pan':  (-35.0, 215.0),
    'Tilt': (-90.0, 90.0),
    'Roll': (-30.0, 30.0),
    'Zoom': (100.0, 400.0),
}

NEUTRAL = {'Pan': 0.0, 'Tilt': 0.0, 'Roll': 0.0, 'Zoom': 100.0}


class UvcDaemon:
    def __init__(self, ready_timeout=90):
        self.proc = subprocess.Popen(
            [PS, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', DAEMON],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace', bufsize=1)
        self._lock = threading.Lock()
        t0 = time.time()
        while time.time() - t0 < ready_timeout:
            line = self.proc.stdout.readline()
            if not line:
                if self.proc.poll() is not None:
                    raise RuntimeError('uvc daemon exited during startup')
                continue
            line = line.strip()
            if line == 'READY':
                self.startup_s = time.time() - t0
                return
            if line.startswith('ERR'):
                raise RuntimeError('uvc daemon: %s' % line)
        raise TimeoutError('uvc daemon did not become ready')

    def _cmd(self, text):
        with self._lock:
            if self.proc.poll() is not None:
                raise RuntimeError('uvc daemon is gone')
            self.proc.stdin.write(text + '\n')
            self.proc.stdin.flush()
            return self.proc.stdout.readline().strip()

    def set(self, name, value):
        lo, hi = LIMITS[name]
        v = max(lo, min(hi, float(value)))
        r = self._cmd('SET %s %g' % (name, v))
        if not r.startswith('OK'):
            raise RuntimeError(r)
        return r

    def get(self, name):
        return self._cmd('GET %s' % name)

    def neutral(self, settle=0.8):
        for n, v in NEUTRAL.items():
            self.set(n, v)
        time.sleep(settle)

    def close(self):
        try:
            self.proc.stdin.write('QUIT\n')
            self.proc.stdin.flush()
            self.proc.wait(timeout=8)
        except Exception:
            self.proc.kill()


def open_camera(index=1, width=1280, height=720):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError('cannot open camera index %d' % index)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    for _ in range(20):
        cap.read()
    return cap


def find_pocket3_index(max_index=6):
    """Return the DirectShow index whose frames come from OsmoPocket3.

    The ACER webcam is usually index 0, but the order is not guaranteed, so we
    probe each index and pick the one reporting the DJI resolution/driver hint.
    """
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if not cap.isOpened():
            continue
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        ok, f = cap.read()
        cap.release()
        if ok and f is not None:
            # Pocket 3 exposes 1080p MJPG; built-in ACER is 1280x720 by default
            yield i, w, h


if __name__ == '__main__':
    print('probing indices:')
    for i, w, h in find_pocket3_index():
        print('  idx=%d %dx%d' % (i, w, h))
    d = UvcDaemon()
    print('daemon ready in %.2fs' % d.startup_s)
    for n in LIMITS:
        print(' ', d.get(n))
    d.close()
