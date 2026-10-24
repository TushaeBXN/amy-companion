"""Run Amy as one always-on macOS service (a launchd agent): starts at login, restarts itself if it dies,
and keeps a log. Nobody starts her by hand any more, so two copies can't kill each other.

    python3 amy_service.py install     write the agent, start it
    python3 amy_service.py restart     after a deploy (the only restart anyone needs)
    python3 amy_service.py status | stop | start | logs | uninstall

Her secrets stay where they are: .env is read by the code, and .env.amy is sourced the way it was by hand."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.anthos.amy-telegram"
HERE = Path(__file__).resolve().parent
LOG_DIR = Path.home() / "Library" / "Logs" / "amy"
MAX_LOG_BYTES = 20 * 1024 * 1024


def agent_path(label: str = LABEL) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def plist_dict(label: str = LABEL, repo: Path = HERE, log_dir: Path = LOG_DIR, command: list = None) -> dict:
    """The launchd job. `command` replaces Amy's start line (used only to test the service machinery)."""
    start = ("cd {repo} && set -a && [ -f .env.amy ] && . ./.env.amy; set +a; "
             "exec ./venv/bin/python -u telegram_amy.py").format(repo=repo)
    args = command or ["/bin/bash", "-lc", start]
    return {
        "Label": label,
        "ProgramArguments": args,
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": True,                       # restart whenever she exits, for any reason
        "ThrottleInterval": 15,                  # but not faster than every 15 s if she crashes at once
        "StandardOutPath": str(log_dir / f"{label}.log"),
        "StandardErrorPath": str(log_dir / f"{label}.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "AMY_SERVICE": "1", "HOME": str(Path.home()),
                                 "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _run(args: list, runner=subprocess.run):
    return runner(args, capture_output=True, text=True)


def info(label: str = LABEL, runner=subprocess.run) -> dict:
    """{'loaded': bool, 'pid': int|None} from launchd."""
    r = _run(["launchctl", "print", f"{_domain()}/{label}"], runner)
    if r.returncode != 0:
        return {"loaded": False, "pid": None}
    pid = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("pid ="):
            try:
                pid = int(line.split("=")[1])
            except ValueError:
                pass
    return {"loaded": True, "pid": pid}


def service_running(label: str = LABEL, runner=subprocess.run) -> bool:
    i = info(label, runner)
    return i["loaded"] and i["pid"] is not None


def rotate_log(label: str = LABEL, log_dir: Path = LOG_DIR, max_bytes: int = MAX_LOG_BYTES) -> bool:
    p = log_dir / f"{label}.log"
    if p.exists() and p.stat().st_size > max_bytes:
        p.replace(log_dir / f"{label}.log.1")
        return True
    return False


def install(label: str = LABEL, repo: Path = HERE, log_dir: Path = LOG_DIR, command: list = None, runner=subprocess.run) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = agent_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist_dict(label, repo, log_dir, command), f)
    _run(["launchctl", "bootout", f"{_domain()}/{label}"], runner)           # replace an older copy, if any
    r = _run(["launchctl", "bootstrap", _domain(), str(path)], runner)
    if r.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {r.stderr.strip() or r.stdout.strip()}")
    return path


def restart(label: str = LABEL, runner=subprocess.run) -> None:
    rotate_log(label)
    r = _run(["launchctl", "kickstart", "-k", f"{_domain()}/{label}"], runner)
    if r.returncode != 0:
        raise RuntimeError(f"launchctl kickstart failed: {r.stderr.strip() or r.stdout.strip()}")


def stop(label: str = LABEL, runner=subprocess.run) -> None:
    _run(["launchctl", "bootout", f"{_domain()}/{label}"], runner)


def start(label: str = LABEL, runner=subprocess.run) -> None:
    path = agent_path(label)
    if not path.exists():
        raise RuntimeError("not installed yet: python3 amy_service.py install")
    r = _run(["launchctl", "bootstrap", _domain(), str(path)], runner)
    if r.returncode not in (0, 5):                                           # 5: already loaded
        raise RuntimeError(f"launchctl bootstrap failed: {r.stderr.strip() or r.stdout.strip()}")


def uninstall(label: str = LABEL, runner=subprocess.run) -> None:
    stop(label, runner)
    agent_path(label).unlink(missing_ok=True)


def main(argv: list) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "install":
        print(f"installed {install()}; log: {LOG_DIR / (LABEL + '.log')}")
    elif cmd == "restart":
        restart()
        print("restarting Amy")
    elif cmd == "stop":
        stop()
        print("stopped (until `start` or next login)")
    elif cmd == "start":
        start()
        print("started")
    elif cmd == "uninstall":
        uninstall()
        print("removed")
    elif cmd == "logs":
        os.execvp("tail", ["tail", "-n", "60", "-f", str(LOG_DIR / f"{LABEL}.log")])
    else:
        i = info()
        print(f"{'running, pid ' + str(i['pid']) if i['pid'] else 'loaded but not running' if i['loaded'] else 'not installed/loaded'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
