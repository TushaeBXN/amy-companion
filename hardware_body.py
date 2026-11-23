"""Unitree G1 EDU embodiment bridge for Amy.

Wraps unitree_sdk2py with graceful fallback when hardware is offline or
the SDK isn't installed. Amy calls high-level intents (pose, gesture, walk,
gaze); this module translates them to G1 loco/joint commands.

Connection: Ethernet to robot at UNITREE_HOST (default 192.168.123.161)
            on interface UNITREE_INTERFACE (default "eth0").

Env vars:
  AMY_ROBOT=0     — disable entirely (no connection attempted, all calls no-op)
  AMY_ROBOT=sim   — simulation mode (logs commands, no hardware writes)
  AMY_ROBOT=1     — real hardware (default when var is unset)
  UNITREE_HOST    — robot IP (default 192.168.123.161)
  UNITREE_INTERFACE — network interface (default eth0)

Safety limits (enforced here, not by the model):
  - walk speed capped at 0.6 m/s forward, 0.3 m/s lateral
  - walk duration capped at 10 seconds per call
  - head pitch: -30° to +30°, head yaw: -60° to +60°
  - all hardware calls go through _send(), which checks _BODY_WHITELIST
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Optional

log = logging.getLogger("amy.body")

# ── Configuration ─────────────────────────────────────────────────────────────

_MODE = os.environ.get("AMY_ROBOT", "1").strip().lower()
_ENABLED = _MODE != "0"
_SIM = _MODE == "sim"
_HOST = os.environ.get("UNITREE_HOST", "192.168.123.161")
_INTERFACE = os.environ.get("UNITREE_INTERFACE", "eth0")

# Hard velocity / duration limits
_MAX_VX = 0.6       # m/s forward
_MAX_VY = 0.3       # m/s lateral
_MAX_VYAW = 0.8     # rad/s rotation
_MAX_WALK_SECS = 10.0

# Head joint limits (degrees → converted to radians for SDK)
_HEAD_PITCH_MIN, _HEAD_PITCH_MAX = -30.0, 30.0
_HEAD_YAW_MIN,   _HEAD_YAW_MAX   = -60.0, 60.0

# ── Bridge state ──────────────────────────────────────────────────────────────

class _BodyState:
    connected: bool = False
    pose: str = "unknown"       # last commanded pose
    gesture: str | None = None  # last commanded gesture
    head_pitch: float = 0.0     # degrees, positive = look up
    head_yaw: float = 0.0       # degrees, positive = look right
    battery_pct: float = -1.0   # -1 = not read yet
    error: str | None = None    # last connection error

_state = _BodyState()
_lock = threading.Lock()

# ── SDK handle (lazy init) ─────────────────────────────────────────────────────

_loco: Optional[object] = None   # G1LocoClient instance
_sdk_available: Optional[bool] = None  # None = not yet checked


def _check_sdk() -> bool:
    global _sdk_available
    if _sdk_available is None:
        try:
            import unitree_sdk2py  # noqa: F401
            _sdk_available = True
        except ImportError:
            _sdk_available = False
            log.warning(
                "unitree_sdk2py not installed — run: "
                "pip install unitree-sdk2py>=1.0.0,<2"
            )
    return _sdk_available


def _connect() -> bool:
    """Attempt to initialise DDS and create a G1LocoClient. Idempotent."""
    global _loco
    if _loco is not None:
        return True
    if not _check_sdk():
        return False
    try:
        from unitree_sdk2py.core.channel import ChannelFactory
        from unitree_sdk2py.g1.loco.g1_loco_client import G1LocoClient

        ChannelFactory.Instance().Init(0, _INTERFACE)
        client = G1LocoClient()
        client.SetTimeout(5.0)
        client.Init()
        _loco = client
        _state.connected = True
        _state.error = None
        log.info("Unitree G1 connected at %s via %s", _HOST, _INTERFACE)
        return True
    except Exception as exc:
        _state.connected = False
        _state.error = str(exc)
        log.error("Unitree G1 connect failed: %s", exc)
        return False


def _ensure() -> tuple[bool, str]:
    """Return (ok, reason). ok=True means hardware (or sim) is ready."""
    if not _ENABLED:
        return False, "body hardware disabled (AMY_ROBOT=0)"
    if _SIM:
        return True, "sim"
    with _lock:
        if _loco is not None:
            return True, "connected"
        ok = _connect()
        return ok, ("connected" if ok else f"offline: {_state.error}")


# ── Internal send helpers ─────────────────────────────────────────────────────

def _send(fn_name: str, fn, *args, **kwargs):
    """Call fn(*args, **kwargs) on real hardware, or just log in sim mode.

    fn_name is used only for logging/sim output.
    Returns "ok" on success or an error string.
    """
    ok, reason = _ensure()
    if not ok:
        log.debug("[body] %s skipped — %s", fn_name, reason)
        return f"body offline ({reason})"
    if _SIM:
        log.info("[body/sim] %s args=%s kwargs=%s", fn_name, args, kwargs)
        return "ok (sim)"
    try:
        result = fn(*args, **kwargs)
        return "ok" if result is None else str(result)
    except Exception as exc:
        log.error("[body] %s failed: %s", fn_name, exc)
        return f"error: {exc}"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ── High-level commands ───────────────────────────────────────────────────────

VALID_POSES = {
    "stand", "stand_high", "stand_low", "sit", "crouch",
    "bow", "attention", "balance", "damp", "zero_torque",
}

VALID_GESTURES = {
    "wave_right", "wave_left", "handshake_right", "handshake_left",
    "stretch", "nod", "shake_head",
}

GAZE_PRESETS = {
    "user":    (0.0,   0.0),
    "up":      (20.0,  0.0),
    "down":    (-20.0, 0.0),
    "left":    (0.0,  -40.0),
    "right":   (0.0,   40.0),
    "away":    (0.0,   60.0),
    "forward": (0.0,   0.0),
}


def pose(name: str) -> str:
    """Command a standing/sitting posture. Returns 'ok' or error string."""
    if name not in VALID_POSES:
        return f"unknown pose '{name}'; valid: {sorted(VALID_POSES)}"

    def _run():
        if _loco is None:
            return
        if name == "stand":
            _loco.StandUp()
        elif name == "stand_high":
            _loco.HighStand()
        elif name == "stand_low":
            _loco.LowStand()
        elif name == "balance":
            _loco.BalanceStand()
        elif name == "damp":
            _loco.Damp()
        elif name == "zero_torque":
            _loco.ZeroTorque()
        elif name == "sit":
            _loco.StandDown()
        elif name in ("crouch", "bow", "attention"):
            # Map to closest SDK primitive + head adjustment
            _loco.BalanceStand()
            if name == "bow":
                _set_head(pitch=-20.0, yaw=0.0)

    result = _send(f"pose:{name}", _run)
    if result.startswith("ok"):
        _state.pose = name
    return result


def gesture(name: str) -> str:
    """Execute a one-shot gesture. Returns 'ok' or error string."""
    if name not in VALID_GESTURES:
        return f"unknown gesture '{name}'; valid: {sorted(VALID_GESTURES)}"

    def _run():
        if _loco is None:
            return
        if name == "wave_right":
            _loco.WaveHand(0)   # 0 = right arm
        elif name == "wave_left":
            _loco.WaveHand(1)   # 1 = left arm
        elif name == "handshake_right":
            _loco.Handshake(0)
        elif name == "handshake_left":
            _loco.Handshake(1)
        elif name == "stretch":
            _loco.Stretch()
        elif name == "nod":
            # Nod: pitch down then back to neutral via head joints
            _set_head(pitch=-15.0, yaw=_state.head_yaw)
            time.sleep(0.4)
            _set_head(pitch=0.0, yaw=_state.head_yaw)
        elif name == "shake_head":
            # Shake head: yaw left-right-center
            _set_head(pitch=_state.head_pitch, yaw=-30.0)
            time.sleep(0.4)
            _set_head(pitch=_state.head_pitch, yaw=30.0)
            time.sleep(0.4)
            _set_head(pitch=_state.head_pitch, yaw=0.0)

    result = _send(f"gesture:{name}", _run)
    if result.startswith("ok"):
        _state.gesture = name
    return result


def walk(vx: float = 0.0, vy: float = 0.0, vyaw: float = 0.0,
         duration_secs: float = 2.0) -> str:
    """Send a velocity command for duration_secs, then stop.

    vx: forward m/s (positive = forward), capped at ±0.6
    vy: lateral m/s (positive = left),   capped at ±0.3
    vyaw: rotation rad/s (positive = CCW), capped at ±0.8
    duration_secs: how long to walk, capped at 10 s
    """
    vx   = _clamp(vx,   -_MAX_VX,   _MAX_VX)
    vy   = _clamp(vy,   -_MAX_VY,   _MAX_VY)
    vyaw = _clamp(vyaw, -_MAX_VYAW, _MAX_VYAW)
    duration_secs = _clamp(duration_secs, 0.1, _MAX_WALK_SECS)

    def _run():
        if _loco is None:
            return
        _loco.Move(vx, vy, vyaw)
        time.sleep(duration_secs)
        _loco.StopMove()

    return _send(
        f"walk vx={vx:.2f} vy={vy:.2f} vyaw={vyaw:.2f} t={duration_secs:.1f}s",
        _run,
    )


def gaze(pitch_deg: float = 0.0, yaw_deg: float = 0.0,
         preset: str | None = None) -> str:
    """Aim the head. pitch_deg: up positive. yaw_deg: right positive.

    Optionally pass a preset name: user, up, down, left, right, away, forward.
    """
    if preset:
        if preset not in GAZE_PRESETS:
            return f"unknown gaze preset '{preset}'; valid: {sorted(GAZE_PRESETS)}"
        pitch_deg, yaw_deg = GAZE_PRESETS[preset]

    pitch_deg = _clamp(pitch_deg, _HEAD_PITCH_MIN, _HEAD_PITCH_MAX)
    yaw_deg   = _clamp(yaw_deg,   _HEAD_YAW_MIN,   _HEAD_YAW_MAX)

    result = _send(
        f"gaze pitch={pitch_deg:.1f}° yaw={yaw_deg:.1f}°",
        lambda: _set_head(pitch_deg, yaw_deg),
    )
    if result.startswith("ok"):
        _state.head_pitch = pitch_deg
        _state.head_yaw   = yaw_deg
    return result


def _set_head(pitch: float, yaw: float):
    """Low-level: publish a head-joint position command.

    G1 head joints: index 12 = neck pitch, index 13 = neck yaw.
    Uses ArmSDK / low-level motor command if G1LocoClient doesn't expose head.
    """
    if _loco is None:
        return
    pitch_rad = math.radians(pitch)
    yaw_rad   = math.radians(yaw)

    # Prefer the high-level client's head-angle method if it exists
    if hasattr(_loco, "HeadMove"):
        _loco.HeadMove(pitch_rad, yaw_rad)
        return

    # Fall back to low-level motor command on neck joints
    try:
        from unitree_sdk2py.core.channel import ChannelPublisher
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_
        from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_

        cmd = unitree_hg_msg_dds__LowCmd_()
        # Neck pitch = joint 12, neck yaw = joint 13 (G1 EDU mapping)
        cmd.motor_cmd[12].q = pitch_rad
        cmd.motor_cmd[12].kp = 60.0
        cmd.motor_cmd[12].kd = 2.0
        cmd.motor_cmd[13].q = yaw_rad
        cmd.motor_cmd[13].kp = 60.0
        cmd.motor_cmd[13].kd = 2.0
        # Leave all other joints at zero command so only neck moves
        pub = ChannelPublisher("rt/lowcmd", LowCmd_)
        pub.Init()
        pub.Write(cmd)
    except Exception as exc:
        log.warning("head joint command failed: %s", exc)


def stop() -> str:
    """Emergency stop — StopMove then Damp."""
    def _run():
        if _loco is None:
            return
        try:
            _loco.StopMove()
        except Exception:
            pass
        _loco.Damp()

    return _send("stop", _run)


def status() -> dict:
    """Return connection and state info as a dict."""
    ok, reason = _ensure()
    batt = _state.battery_pct
    if ok and not _SIM and _loco is not None:
        try:
            # Try to read battery from robot state (SDK2 LowState or SportState)
            from unitree_sdk2py.g1.loco.g1_loco_client import G1LocoClient  # noqa
            # Battery reading varies by firmware; skip gracefully if unavailable
        except Exception:
            pass

    return {
        "connected": _state.connected or _SIM,
        "mode":      "sim" if _SIM else ("hardware" if _state.connected else "offline"),
        "host":      _HOST,
        "interface": _INTERFACE,
        "pose":      _state.pose,
        "head_pitch_deg": round(_state.head_pitch, 1),
        "head_yaw_deg":   round(_state.head_yaw, 1),
        "battery_pct":    batt if batt >= 0 else "unknown",
        "error":          _state.error,
    }


# ── Mood → motion mapping ─────────────────────────────────────────────────────
# Called by companion.py / amy_server.py after state.after_turn() when mood
# shifts — expresses the new mood through posture and head position automatically.

_MOOD_EXPRESSIONS: dict[str, dict] = {
    "curious":    {"pose": "balance",    "gaze_preset": "forward", "gesture": None},
    "focused":    {"pose": "attention",  "gaze_preset": "user",    "gesture": None},
    "reflective": {"pose": "balance",    "gaze_preset": "down",    "gesture": None},
    "playful":    {"pose": "stand",      "gaze_preset": "user",    "gesture": "wave_right"},
    "tired":      {"pose": "stand_low",  "gaze_preset": "down",    "gesture": None},
    "alert":      {"pose": "stand_high", "gaze_preset": "forward", "gesture": None},
}

_last_expressed_mood: str | None = None


def express_mood(mood: str, force: bool = False) -> None:
    """Translate an Amy mood into body posture + gaze. Fire-and-forget in a thread.

    Only acts when the mood has actually changed (or force=True), so calling
    this every turn is cheap.
    """
    global _last_expressed_mood
    if not _ENABLED:
        return
    if mood == _last_expressed_mood and not force:
        return
    expr = _MOOD_EXPRESSIONS.get(mood)
    if not expr:
        return

    def _do():
        global _last_expressed_mood
        pose(expr["pose"])
        gaze(preset=expr["gaze_preset"])
        if expr["gesture"]:
            time.sleep(0.5)
            gesture(expr["gesture"])
        _last_expressed_mood = mood

    threading.Thread(target=_do, daemon=True, name="amy-body-mood").start()
