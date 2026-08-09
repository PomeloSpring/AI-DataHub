"""Local node metrics — /proc-based server metrics exposed by every service.

Each microservice mounts this router so the central monitoring API can
collect per-node metrics in distributed deployments. Stdlib only (Linux).
"""
from __future__ import annotations

import os
import shutil
import threading
import time

from fastapi import APIRouter

router = APIRouter()

_cpu_lock = threading.Lock()
_cpu_last: dict | None = None  # {"ts": float, "idle": int, "total": int}


def _read_cpu_times() -> tuple[int, int] | None:
    """Return (idle, total) jiffies from /proc/stat aggregate cpu line."""
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu "):
                    fields = [int(x) for x in line.split()[1:]]
                    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)  # idle + iowait
                    return idle, sum(fields)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _cpu_percent() -> float | None:
    """CPU usage % since the previous call (first call samples ~0.3s)."""
    global _cpu_last
    with _cpu_lock:
        sample = _read_cpu_times()
        if sample is None:
            return None
        idle, total = sample
        now = time.monotonic()
        if _cpu_last is None or now - _cpu_last["ts"] < 0.5:
            # Need a fresh baseline — block briefly to produce a real reading
            time.sleep(0.3)
            sample2 = _read_cpu_times()
            if sample2 is None:
                return None
            idle, total = sample2
            _cpu_last = {"ts": time.monotonic(), "idle": idle, "total": total}
            # Compute against the short baseline window
            d_total = total - sample[1]
            d_idle = idle - sample[0]
            if d_total <= 0:
                return None
            return round(max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total))), 1)

        d_total = total - _cpu_last["total"]
        d_idle = idle - _cpu_last["idle"]
        _cpu_last = {"ts": now, "idle": idle, "total": total}
        if d_total <= 0:
            return None
        return round(max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total))), 1)


def _memory_info() -> dict | None:
    """Memory stats from /proc/meminfo (bytes)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key.strip()] = int(rest.strip().split()[0]) * 1024  # kB -> B
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", info.get("MemFree", 0))
        if not total:
            return None
        used = total - available
        return {
            "total": total,
            "used": used,
            "available": available,
            "percent": round(100.0 * used / total, 1),
        }
    except (OSError, ValueError, IndexError):
        return None


def _disk_info(path: str = "/") -> dict | None:
    try:
        usage = shutil.disk_usage(path)
        return {
            "path": path,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(100.0 * usage.used / usage.total, 1),
        }
    except OSError:
        return None


def _uptime_seconds() -> int | None:
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError):
        return None


def _process_count() -> int | None:
    try:
        return sum(1 for name in os.listdir("/proc") if name.isdigit())
    except OSError:
        return None


def collect_local_metrics() -> dict:
    """Gather this node's performance metrics."""
    try:
        load1, load5, load15 = os.getloadavg()
        load = {"load1": round(load1, 2), "load5": round(load5, 2), "load15": round(load15, 2)}
    except OSError:
        load = None

    return {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hostname": os.uname().nodename,
        "cpu": {
            "percent": _cpu_percent(),
            "cores": os.cpu_count(),
            "load": load,
        },
        "memory": _memory_info(),
        "disk": _disk_info("/"),
        "uptime_seconds": _uptime_seconds(),
        "process_count": _process_count(),
    }


@router.get("/system-metrics")
def local_system_metrics():
    """This node's performance metrics (internal, no auth — for monitoring aggregation)."""
    return collect_local_metrics()
