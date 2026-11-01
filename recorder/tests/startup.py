import ctypes
import json
import os
import pathlib
import subprocess
import sys
import time

app = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
root.mkdir(parents=True, exist_ok=True, mode=0o700)
log = root / 'startup.log'
environment = {**os.environ, 'TICO_RECORDER_DATA_DIR': str(root / 'demo-library')}
with log.open('w') as output:
    process = subprocess.Popen([str(app / 'Contents/MacOS/tico-recorder'), '--demo'], env=environment, stdout=output, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Native app exited during startup: ' + log.read_text()[-2000:])
            time.sleep(.2)
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        owner_key = ctypes.c_void_p.in_dll(cg, 'kCGWindowOwnerPID').value
        layer_key = ctypes.c_void_p.in_dll(cg, 'kCGWindowLayer').value
        windows = cg.CGWindowListCopyWindowInfo(0, 0)
        own_windows = 0
        for index in range(cf.CFArrayGetCount(windows)):
            window = cf.CFArrayGetValueAtIndex(windows, index)
            owner = ctypes.c_longlong()
            layer = ctypes.c_longlong()
            cf.CFNumberGetValue(cf.CFDictionaryGetValue(window, owner_key), 4, ctypes.byref(owner))
            cf.CFNumberGetValue(cf.CFDictionaryGetValue(window, layer_key), 4, ctypes.byref(layer))
            if owner.value == process.pid and layer.value == 0:
                own_windows += 1
        cf.CFRelease(windows)
        assert own_windows >= 1, 'The native process did not create a normal application window'
        memory = subprocess.check_output(['ps', '-o', 'rss=', '-p', str(process.pid)], text=True).strip()
        receipt = {'native_window_count': own_windows, 'demo_rss_kib': int(memory),
                   'binary_bytes': (app / 'Contents/MacOS/tico-recorder').stat().st_size,
                   'synthetic_demo_only': True, 'capture_started': False}
        (root / 'startup-receipt.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt))
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
