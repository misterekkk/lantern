"""The state of this Mac for decisions about heavy work: total memory, macOS's memory pressure, free memory and swap
(sysctl, no permissions, no PyObjC). Defaults follow the amount of RAM; whether a heavy model may start right now
follows the live state, so a 24 GB Mac training a model in the background is treated like a small one (phase 17).
"""

import ctypes
import ctypes.util
import os
import time
from dataclasses import dataclass

GB = 1024**3
LIGHT_BELOW = 12 * GB  # 8 GB Macs get the light profile; 16 GB and more everything (measured on 16 GB)
# Peak memory of the optional heavy work (DECISIONS.md: Whisper ~1.8 GB, the SigLIP 2 image tower ~2.2 GB, the
# EmbeddingGemma audio tower: the worker up to 3.3 GB), plus what must stay free for the user.
NEEDS = {"whisper": 1.8 * GB, "siglip_image": 2.2 * GB, "audio": 3.3 * GB}
HEADROOM = 1.5 * GB
PRESSURE_NORMAL, PRESSURE_WARN, PRESSURE_CRITICAL = 1, 2, 4  # kern.memorystatus_vm_pressure_level
SWAP_GROWTH = 512 * 1024**2  # this much more swap within SWAP_WINDOW_S: the Mac is short of memory
SWAP_WINDOW_S = 120

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _sysctl(name: str, ctype):
    value = ctype()
    size = ctypes.c_size_t(ctypes.sizeof(value))
    if _libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        return None
    return value


class _Swap(ctypes.Structure):
    _fields_ = [("total", ctypes.c_uint64), ("avail", ctypes.c_uint64), ("used", ctypes.c_uint64),
                ("pagesize", ctypes.c_uint32), ("encrypted", ctypes.c_bool)]  # fmt: skip


@dataclass(frozen=True)
class Memory:
    total: int  # bytes of RAM
    pressure: int  # 1 normal, 2 warn, 4 critical
    free_percent: int  # macOS's "memory free percentage" (memory_pressure), 0-100
    swap_used: int  # bytes

    @property
    def available(self) -> int:
        return self.total * self.free_percent // 100


def read() -> Memory:
    total = _sysctl("hw.memsize", ctypes.c_uint64)
    pressure = _sysctl("kern.memorystatus_vm_pressure_level", ctypes.c_int)
    level = _sysctl("kern.memorystatus_level", ctypes.c_int)
    swap = _sysctl("vm.swapusage", _Swap)
    fake = os.environ.get("LANTERN_FAKE_RAM_GB")  # development: see the 8 GB experience on a bigger Mac
    return Memory(
        total=int(float(fake) * GB) if fake else total.value if total else 16 * GB,
        pressure=pressure.value if pressure else PRESSURE_NORMAL,
        free_percent=level.value if level else 50,
        swap_used=swap.used if swap else 0,
    )


def profile(total: int | None = None) -> str:
    """"light" (8 GB: EmbeddingGemma and what macOS does itself) or "full" (16 GB and more)."""
    return "light" if (total if total is not None else read().total) < LIGHT_BELOW else "full"


def fits(work: str, memory: Memory | None = None) -> bool:
    """Whether optional heavy work (NEEDS) may start now: no memory pressure and room for it plus headroom."""
    m = memory or read()
    return m.pressure == PRESSURE_NORMAL and m.available >= NEEDS[work] + HEADROOM


class SwapWatch:
    """Swap growing fast (not its size: a Mac can carry gigabytes of old swap for days)."""

    def __init__(self):
        self.samples: list[tuple[float, int]] = []

    def growing(self, memory: Memory, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        self.samples = [(t, s) for t, s in self.samples if now - t <= SWAP_WINDOW_S] + [(now, memory.swap_used)]
        return memory.swap_used - min(s for _, s in self.samples) >= SWAP_GROWTH
