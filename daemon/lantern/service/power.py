"""Power and thermal state from `pmset` (no extra dependencies)."""

import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class PowerState:
    on_battery: bool = False
    thermal_pressure: bool = False


def parse_batt(output: str) -> bool:
    """True when running on battery (`pmset -g batt`)."""
    return "'Battery Power'" in output


def parse_therm(output: str) -> bool:
    """True when the system reports thermal pressure (`pmset -g therm`): a recorded warning level or a CPU limit."""
    if re.search(r"(thermal|performance) warning level set to \d+", output, re.IGNORECASE):
        return True
    limits = [int(v) for v in re.findall(r"CPU_Speed_Limit\s*=\s*(\d+)", output)]
    return any(v < 100 for v in limits)


def _pmset(arg: str) -> str:
    try:
        return subprocess.run(["pmset", "-g", arg], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def read() -> PowerState:
    return PowerState(on_battery=parse_batt(_pmset("batt")), thermal_pressure=parse_therm(_pmset("therm")))
