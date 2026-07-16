"""Device telemetry used to decide whether local model calls are safe."""

from __future__ import annotations

from dataclasses import dataclass

import config

try:
    import psutil
except ImportError:  # pragma: no cover - keeps the app importable before deps install.
    psutil = None


@dataclass(frozen=True)
class DeviceSnapshot:
    """CPU, memory, battery, and local-LLM safety state."""

    cpu_percent: float | None
    memory_percent: float | None
    battery_percent: float | None
    power_plugged: bool | None
    safe_for_llm: bool
    reason: str

    def summary_text(self) -> str:
        """Return a short text summary safe for status UI and model prompts."""
        cpu = format_percent(self.cpu_percent)
        memory = format_percent(self.memory_percent)
        battery = "unknown"
        if self.battery_percent is not None:
            battery = format_percent(self.battery_percent)
            if self.power_plugged is True:
                battery = f"{battery}, charging"
            elif self.power_plugged is False:
                battery = f"{battery}, on battery"
        status = "safe" if self.safe_for_llm else "busy"
        return f"Device: CPU {cpu}, RAM {memory}, battery {battery}; {status} - {self.reason}"


class DeviceMonitor:
    """Collect psutil telemetry and apply configured LLM load thresholds."""

    def __init__(self) -> None:
        if psutil is not None:
            psutil.cpu_percent(interval=None)

    def snapshot(self) -> DeviceSnapshot:
        """Return one device-load sample."""
        if psutil is None:
            return DeviceSnapshot(
                cpu_percent=None,
                memory_percent=None,
                battery_percent=None,
                power_plugged=None,
                safe_for_llm=False,
                reason="device telemetry is unavailable; install requirements to enable safe model calls",
            )

        cpu_percent = float(psutil.cpu_percent(interval=None))
        memory_percent = float(psutil.virtual_memory().percent)
        battery_percent = None
        power_plugged = None
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, RuntimeError):
            battery = None
        if battery is not None:
            battery_percent = float(battery.percent)
            if battery.power_plugged is not None:
                power_plugged = bool(battery.power_plugged)

        reasons = []
        if cpu_percent >= config.SMART_NUDGE_MAX_CPU_PERCENT:
            reasons.append(f"CPU is {cpu_percent:.0f}%")
        if memory_percent >= config.SMART_NUDGE_MAX_MEMORY_PERCENT:
            reasons.append(f"RAM is {memory_percent:.0f}%")

        safe = not reasons
        reason = "device load is OK" if safe else ", ".join(reasons)
        return DeviceSnapshot(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            battery_percent=battery_percent,
            power_plugged=power_plugged,
            safe_for_llm=safe,
            reason=reason,
        )


def format_percent(value: float | None) -> str:
    """Format optional percentage values for human-facing summaries."""
    if value is None:
        return "unknown"
    return f"{value:.0f}%"
