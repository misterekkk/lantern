"""Thread quality of service on macOS (`pthread_set_qos_class_self_np`), through ctypes.

Measured on the M4 (DECISIONS.md, "Power and QoS"): one query embedding takes 9 ms at default, user-initiated or
utility QoS and 21 ms at background QoS (a CPU loop is 7× slower there). So the daemon keeps searches at
user-initiated and moves only its own housekeeping (scans, the control loop) to utility; heavy indexing runs in the
worker process with the background policy (`worker.py`). New threads do not inherit the creating thread's class.
"""

import ctypes
import sys
from functools import cache

CLASSES = {"user_interactive": 0x21, "user_initiated": 0x19, "default": 0x15, "utility": 0x11, "background": 0x09}


@cache
def _libc():
    if sys.platform != "darwin":
        return None
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    libc.pthread_set_qos_class_self_np.argtypes = [ctypes.c_uint, ctypes.c_int]
    libc.qos_class_self.restype = ctypes.c_uint
    return libc


def set_thread_qos(name: str) -> bool:
    """Set the calling thread's QoS class; False when unsupported."""
    libc = _libc()
    return bool(libc) and libc.pthread_set_qos_class_self_np(CLASSES[name], 0) == 0


def thread_qos() -> str | None:
    libc = _libc()
    if not libc:
        return None
    value = libc.qos_class_self()
    return next((k for k, v in CLASSES.items() if v == value), hex(value))
