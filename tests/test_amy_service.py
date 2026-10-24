"""Amy as one always-on service: the job definition, status, restart, and refusing a second copy."""
import plistlib
import subprocess
from pathlib import Path

import pytest

import amy_service as S


class FakeRun:
    def __init__(self, replies=None):
        self.calls, self.replies = [], replies or {}

    def __call__(self, args, **kw):
        self.calls.append(args)
        for key, (rc, out) in self.replies.items():
            if key in " ".join(args):
                return subprocess.CompletedProcess(args, rc, out, "")
        return subprocess.CompletedProcess(args, 0, "", "")


PRINT_RUNNING = "com.anthos.amy-telegram = {\n\tstate = running\n\tpid = 4242\n\tprogram = /bin/bash\n}"
PRINT_LOADED_IDLE = "com.anthos.amy-telegram = {\n\tstate = waiting\n}"


def test_the_job_restarts_itself_logs_to_a_file_and_loads_her_env_like_by_hand(tmp_path):
    d = S.plist_dict("x.test", tmp_path, tmp_path / "logs")
    assert d["KeepAlive"] is True and d["RunAtLoad"] is True and d["ThrottleInterval"] >= 10
    assert d["StandardOutPath"] == str(tmp_path / "logs" / "x.test.log") == d["StandardErrorPath"]
    start = d["ProgramArguments"][-1]
    assert f"cd {tmp_path}" in start and ". ./.env.amy" in start and "exec ./venv/bin/python -u telegram_amy.py" in start
    assert d["EnvironmentVariables"]["AMY_SERVICE"] == "1" and d["EnvironmentVariables"]["PYTHONUNBUFFERED"] == "1"
    assert plistlib.loads(plistlib.dumps(d)) == d                    # a valid property list


def test_a_test_command_replaces_the_start_line(tmp_path):
    d = S.plist_dict("x.test", tmp_path, tmp_path, command=["/usr/bin/true"])
    assert d["ProgramArguments"] == ["/usr/bin/true"]


def test_status_reads_launchds_answer():
    assert S.info(runner=FakeRun({"print": (0, PRINT_RUNNING)})) == {"loaded": True, "pid": 4242}
    assert S.info(runner=FakeRun({"print": (0, PRINT_LOADED_IDLE)})) == {"loaded": True, "pid": None}
    assert S.info(runner=FakeRun({"print": (113, "")})) == {"loaded": False, "pid": None}
    assert S.service_running(runner=FakeRun({"print": (0, PRINT_RUNNING)}))
    assert not S.service_running(runner=FakeRun({"print": (0, PRINT_LOADED_IDLE)}))
    assert not S.service_running(runner=FakeRun({"print": (113, "")}))


def test_install_writes_the_agent_and_loads_it_replacing_an_old_one(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    run = FakeRun()
    path = S.install("x.test", tmp_path, tmp_path / "logs", runner=run)
    assert path == tmp_path / "Library/LaunchAgents/x.test.plist" and path.exists()
    assert (tmp_path / "logs").is_dir()
    verbs = [c[1] for c in run.calls]
    assert verbs == ["bootout", "bootstrap"]                         # old copy removed first, then loaded
    bad = FakeRun({"bootstrap": (5, "")})
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        S.install("x.test", tmp_path, tmp_path / "logs", runner=bad)


def test_restart_stop_start_uninstall_use_the_right_launchctl_verbs(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(S, "LOG_DIR", tmp_path / "logs")
    run = FakeRun()
    S.restart("x.test", run)
    assert run.calls[-1][1:3] == ["kickstart", "-k"]
    S.stop("x.test", run)
    assert run.calls[-1][1] == "bootout"
    with pytest.raises(RuntimeError, match="not installed"):
        S.start("x.test", run)
    S.install("x.test", tmp_path, tmp_path / "logs", runner=run)
    S.start("x.test", run)
    assert run.calls[-1][1] == "bootstrap"
    S.uninstall("x.test", run)
    assert not S.agent_path("x.test").exists()


def test_a_big_log_is_rotated_on_restart(tmp_path):
    (tmp_path / "x.test.log").write_text("a" * 100)
    assert not S.rotate_log("x.test", tmp_path, max_bytes=1000)
    assert S.rotate_log("x.test", tmp_path, max_bytes=50)
    assert (tmp_path / "x.test.log.1").exists() and not (tmp_path / "x.test.log").exists()


def test_a_manual_start_is_refused_while_the_service_runs(monkeypatch):
    try:
        import telegram_amy as T
    except FileNotFoundError:
        pytest.skip("needs amy_self.json (present in the live folder)")
    monkeypatch.setenv("TELEGRAM_TOKEN", "x")
    monkeypatch.delenv("AMY_SERVICE", raising=False)
    monkeypatch.setattr(S, "service_running", lambda *a, **k: True)
    monkeypatch.setattr(S, "info", lambda *a, **k: {"loaded": True, "pid": 4242})
    with pytest.raises(SystemExit) as e:
        T.main()
    assert "already running as a service (pid 4242)" in str(e.value) and "amy_service.py restart" in str(e.value)
