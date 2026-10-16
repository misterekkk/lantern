"""Power source, Low Power Mode and thermal state, read from macOS without PyObjC.

- Power source: IOKit `IOPSGetProvidingPowerSourceType` (IOPowerSources), ~0.07 ms per call.
- Low Power Mode and thermal state: `NSProcessInfo` through the Objective-C runtime (Foundation is already loaded in
  every macOS process, so this adds no memory; PyObjC would cost +22 MB in the daemon), microseconds per call.
- `pmset -g` / `pmset -g therm` (~6 ms each) are checked too: whether NSProcessInfo refreshes in a long-running process
  without a run loop could not be verified without toggling Low Power Mode (DECISIONS.md, open questions), and they
  are the fallback when a framework call fails. The daemon reads all of this every POWER_CHECK_S.
"""

import ctypes
import re
import subprocess
from dataclasses import dataclass
from functools import cache

THERMAL_STATES = ("nominal", "fair", "serious", "critical")  # NSProcessInfoThermalState 0–3
THERMAL_PAUSE_FROM = 2  # serious: macOS is already slowing the Mac down


@dataclass(frozen=True)
class PowerState:
    on_battery: bool = False
    low_power: bool = False
    thermal_pressure: bool = False
    thermal: str = "nominal"


def parse_batt(output: str) -> bool:
    """True when running on battery (`pmset -g batt`)."""
    return "'Battery Power'" in output


def parse_low_power(output: str) -> bool:
    """True when Low Power Mode is on (`pmset -g` lists `lowpowermode 1` for the current power source)."""
    return bool(re.search(r"^\s*lowpowermode\s+1\b", output, re.MULTILINE))


def parse_therm(output: str) -> bool:
    """True when the system reports thermal pressure (`pmset -g therm`): a recorded warning level or a CPU limit."""
    if re.search(r"(thermal|performance) warning level set to \d+", output, re.IGNORECASE):
        return True
    limits = [int(v) for v in re.findall(r"CPU_Speed_Limit\s*=\s*(\d+)", output)]
    return any(v < 100 for v in limits)


def _pmset(*args: str) -> str:
    try:
        return subprocess.run(["pmset", "-g", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class _Frameworks:
    def __init__(self):
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        self.copy_info = iokit.IOPSCopyPowerSourcesInfo
        self.copy_info.restype = ctypes.c_void_p
        self.source_type = iokit.IOPSGetProvidingPowerSourceType
        self.source_type.argtypes, self.source_type.restype = [ctypes.c_void_p], ctypes.c_void_p
        self.cstring = cf.CFStringGetCString
        self.cstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        self.cstring.restype = ctypes.c_bool
        self.release = cf.CFRelease
        self.release.argtypes = [ctypes.c_void_p]
        objc.objc_getClass.argtypes, objc.objc_getClass.restype = [ctypes.c_char_p], ctypes.c_void_p
        objc.sel_registerName.argtypes, objc.sel_registerName.restype = [ctypes.c_char_p], ctypes.c_void_p
        send = ("objc_msgSend", objc)
        self.send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(send)
        self.send_bool = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(send)
        self.send_long = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(send)
        self.process_info = self.send_id(objc.objc_getClass(b"NSProcessInfo"), objc.sel_registerName(b"processInfo"))
        self.sel_low_power = objc.sel_registerName(b"isLowPowerModeEnabled")
        self.sel_thermal = objc.sel_registerName(b"thermalState")
        if not self.process_info:
            raise OSError("NSProcessInfo unavailable")

    def power_source(self) -> str:
        info = self.copy_info()
        if not info:
            raise OSError("IOPSCopyPowerSourcesInfo failed")
        try:
            buf = ctypes.create_string_buffer(64)
            ref = self.source_type(info)  # not owned (Get rule)
            if not ref or not self.cstring(ref, buf, len(buf), 0x08000100):  # kCFStringEncodingUTF8
                raise OSError("no providing power source")
            return buf.value.decode()
        finally:
            self.release(info)

    def low_power(self) -> bool:
        return bool(self.send_bool(self.process_info, self.sel_low_power))

    def thermal_state(self) -> int:
        return int(self.send_long(self.process_info, self.sel_thermal))


@cache
def _frameworks() -> _Frameworks | None:
    try:
        return _Frameworks()
    except (OSError, AttributeError):
        return None


def read() -> PowerState:
    fw = _frameworks()
    try:
        on_battery = fw.power_source() == "Battery Power" if fw else parse_batt(_pmset("batt"))
    except OSError:
        on_battery = parse_batt(_pmset("batt"))
    thermal = min(max(fw.thermal_state(), 0), 3) if fw else 0
    low_power = (fw.low_power() if fw else False) or parse_low_power(_pmset())
    pressure = thermal >= THERMAL_PAUSE_FROM or parse_therm(_pmset("therm"))
    return PowerState(on_battery=on_battery, low_power=low_power, thermal_pressure=pressure,
                      thermal=THERMAL_STATES[thermal])  # fmt: skip
