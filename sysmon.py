"""System monitoring for Amy — CPU, RAM, GPU, disk, thermals."""
from __future__ import annotations
import json
import re
import subprocess


def _run(cmd: list, timeout: int = 8) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def snapshot() -> dict:
    import psutil

    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    # Apple Silicon GPU via powermetrics (requires sudo) — fall back to ioreg
    gpu_pct: float | None = None
    try:
        out = _run(["ioreg", "-r", "-d", "1", "-n", "AGXAccelerator"], timeout=5)
        m = re.search(r'"PerformanceStatistics"\s*=\s*\{([^}]+)\}', out)
        if m:
            gm = re.search(r'"Device Utilization %"\s*=\s*(\d+)', m.group(1))
            if gm:
                gpu_pct = float(gm.group(1))
    except Exception:
        pass

    # Thermals
    temp_c: float | None = None
    try:
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        for key in ("cpu_thermal", "coretemp", "acpitz"):
            if key in temps and temps[key]:
                temp_c = temps[key][0].current
                break
    except Exception:
        pass

    # Ollama model loaded
    ollama_model = ""
    try:
        import requests
        r = requests.get("http://localhost:11434/api/ps", timeout=3)
        models = r.json().get("models", [])
        if models:
            ollama_model = models[0].get("name", "")
    except Exception:
        pass

    return {
        "cpu_pct": cpu,
        "ram_used_gb": round(mem.used / 1e9, 1),
        "ram_total_gb": round(mem.total / 1e9, 1),
        "ram_pct": mem.percent,
        "disk_used_gb": round(disk.used / 1e9, 1),
        "disk_total_gb": round(disk.total / 1e9, 1),
        "disk_pct": round(disk.percent, 1),
        "gpu_pct": gpu_pct,
        "temp_c": temp_c,
        "ollama_model": ollama_model,
    }


def summary() -> str:
    s = snapshot()
    lines = [
        f"CPU: {s['cpu_pct']:.0f}%",
        f"RAM: {s['ram_used_gb']}GB / {s['ram_total_gb']}GB ({s['ram_pct']:.0f}%)",
        f"Disk: {s['disk_used_gb']}GB / {s['disk_total_gb']}GB ({s['disk_pct']}%)",
    ]
    if s["gpu_pct"] is not None:
        lines.append(f"GPU: {s['gpu_pct']:.0f}%")
    if s["temp_c"] is not None:
        lines.append(f"Temp: {s['temp_c']:.0f}°C")
    if s["ollama_model"]:
        lines.append(f"Model loaded: {s['ollama_model']}")
    return "\n".join(lines)
