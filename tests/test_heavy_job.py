"""Exclusive image mode: while FLUX runs, nothing may load the chat model."""
import json
import os
import threading
import time

import pytest

import heavy_job
import imagegen
import worker

_REAL_SLEEP = time.sleep   # captured before any test patches time.sleep


@pytest.fixture(autouse=True)
def flag(tmp_path, monkeypatch):
    monkeypatch.setattr(heavy_job, "FLAG", tmp_path / ".heavy_job")
    monkeypatch.setattr(heavy_job, "_inflight", 0)
    monkeypatch.setattr(heavy_job, "_last_end", 0.0)
    monkeypatch.setattr(heavy_job, "QUIET_S", 0)      # tests that need a quiet period set their own
    return heavy_job.FLAG


def test_calls_run_normally_when_no_job():
    assert heavy_job.call(lambda x: x * 2, 21) == 42


def test_gate_refuses_new_model_calls_and_reopens(flag):
    with heavy_job.exclusive("image generation"):
        assert heavy_job.active() == "image generation"
        with pytest.raises(heavy_job.JobBusy):
            heavy_job.call(lambda: "must not run")
    assert heavy_job.active() is None and not flag.exists()
    assert heavy_job.call(lambda: "ok") == "ok"


def test_gate_reopens_even_if_the_job_blows_up(flag):
    with pytest.raises(ValueError):
        with heavy_job.exclusive("image generation"):
            raise ValueError("mflux crashed")
    assert not flag.exists()


def test_exclusive_waits_for_in_flight_calls_to_finish():
    events = []

    def slow_call():
        with heavy_job.model_call():
            events.append("call started")
            time.sleep(0.6)
            events.append("call finished")

    t = threading.Thread(target=slow_call)
    t.start()
    time.sleep(0.2)
    with heavy_job.exclusive("image generation", quiet_s=0, wait_s=5):
        events.append("job started")
    t.join()
    assert events == ["call started", "call finished", "job started"]


def test_exclusive_gives_up_if_calls_never_drain(flag):
    release = threading.Event()

    def stuck():
        with heavy_job.model_call():
            release.wait(5)

    t = threading.Thread(target=stuck)
    t.start()
    time.sleep(0.2)
    with pytest.raises(heavy_job.JobBusy, match="no quiet moment"):
        with heavy_job.exclusive("image generation", quiet_s=0, wait_s=0.5):
            pytest.fail("must not start")
    assert not flag.exists()
    release.set()
    t.join()


def test_only_one_job_at_a_time():
    with heavy_job.exclusive("image generation"):
        with pytest.raises(heavy_job.JobBusy):
            with heavy_job.exclusive("something else"):
                pass


def test_a_flag_left_by_a_dead_process_is_ignored(flag):
    flag.write_text(json.dumps({"job": "image generation", "pid": 2 ** 22 + 12345, "started": 0}))
    assert heavy_job.active() is None and not flag.exists()


def test_another_process_flag_is_honoured(flag):
    flag.write_text(json.dumps({"job": "image generation", "pid": os.getppid(), "started": 0}))
    with pytest.raises(heavy_job.JobBusy):
        heavy_job.call(lambda: "must not run")


# ── two phases: her own reply must survive the image request ────────────────

def _hold_pending(job="image generation"):
    """Put the gate into its 'pending' phase without waiting (what exclusive() does first)."""
    heavy_job._write(job, "pending")


def test_pending_refuses_background_but_lets_a_chat_turn_finish(flag):
    _hold_pending()
    with pytest.raises(heavy_job.JobBusy):
        heavy_job.call(lambda: "worker call must be refused")
    assert heavy_job.call(lambda: "her reply", chat=True) == "her reply"
    assert heavy_job.active() is None and heavy_job.held() == "image generation"


def test_active_refuses_chat_too(flag):
    heavy_job._write("image generation", "active")
    with pytest.raises(heavy_job.JobBusy):
        heavy_job.call(lambda: "x", chat=True)


def test_the_turn_that_asked_for_the_image_finishes_before_the_machine_is_taken():
    events = []
    heavy_job._last_end = time.time()    # the model call that asked for the image has just ended

    def her_reply():            # the chat call that composes her answer after the tool returns
        time.sleep(0.2)         # her reply starts inside the quiet window, as it does in real use
        with heavy_job.model_call(chat=True):
            events.append("reply composed")
            time.sleep(0.4)

    t = threading.Thread(target=her_reply)
    t.start()
    with heavy_job.exclusive("image generation", quiet_s=0.5, wait_s=10):
        events.append("machine taken")
    t.join()
    assert events == ["reply composed", "machine taken"]


def test_no_quiet_moment_means_no_job(flag):
    stop = threading.Event()

    def keep_chatting():
        while not stop.is_set():
            with heavy_job.model_call(chat=True):
                time.sleep(0.05)

    t = threading.Thread(target=keep_chatting)
    t.start()
    try:
        with pytest.raises(heavy_job.JobBusy, match="no quiet moment"):
            with heavy_job.exclusive("image generation", quiet_s=0.5, wait_s=1):
                pytest.fail("must not start while she is still talking")
    finally:
        stop.set()
        t.join()
    assert not flag.exists()


def test_worker_priority_refused_but_chat_priority_passes_while_pending(monkeypatch):
    monkeypatch.setattr(worker, "_ollama_chat", lambda prio, **kw: f"{prio} ok")
    _hold_pending()
    with pytest.raises(heavy_job.JobBusy):
        worker.ollama_chat(_priority="worker", model="m", messages=[])
    assert worker.ollama_chat(_priority="chat", model="m", messages=[]) == "chat ok"


def test_scheduler_starts_nothing_while_pending(flag):
    _hold_pending()
    assert worker._pick() is None


# ── the real funnel and the scheduler ────────────────────────────────────────

def test_worker_chat_funnel_refuses_and_never_reaches_ollama(monkeypatch):
    monkeypatch.setattr(worker.ollama, "chat", lambda **k: pytest.fail("Ollama must not be called"))
    with heavy_job.exclusive("image generation"):
        for prio in ("chat", "worker"):
            with pytest.raises(heavy_job.JobBusy):
                worker.ollama_chat(_priority=prio, model="m", messages=[])


def test_scheduler_starts_no_tasks_during_a_job():
    with heavy_job.exclusive("image generation"):
        assert worker._pick() is None


# ── imagegen: unload must be confirmed, and FLUX runs only inside the gate ────

def test_unload_waits_until_the_model_stays_gone(monkeypatch):
    # the first reading is the "is Ollama up?" probe; the next two are still loaded, then it goes
    states = iter([["gemma12-test:latest"]] * 3 + [[]] * 12)
    unloads = []
    monkeypatch.setattr(imagegen, "_loaded_models", lambda: next(states))
    import ollama
    monkeypatch.setattr(ollama, "generate", lambda **k: unloads.append(k["model"]))
    monkeypatch.setattr(imagegen.time, "sleep", lambda s: _REAL_SLEEP(0))
    imagegen._unload_chat_models(wait_s=60)
    assert unloads == ["gemma12-test:latest", "gemma12-test:latest"]


def test_unload_that_never_sticks_refuses_to_start(monkeypatch):
    import ollama
    monkeypatch.setattr(imagegen, "_loaded_models", lambda: ["gemma12-test:latest"])
    monkeypatch.setattr(ollama, "generate", lambda **k: None)
    monkeypatch.setattr(imagegen.time, "sleep", lambda s: _REAL_SLEEP(0.01))
    with pytest.raises(heavy_job.JobBusy, match="couldn't unload"):
        imagegen._unload_chat_models(wait_s=0.1)


def test_flux_runs_only_inside_the_gate_and_the_gate_closes_after(monkeypatch, tmp_path, flag):
    monkeypatch.setattr(imagegen, "PAUSED", False)
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path)
    monkeypatch.setattr(imagegen, "LOG_FILE", tmp_path / "log")
    monkeypatch.setattr(imagegen, "_unload_chat_models", lambda *a, **k: None)
    seen = {}

    def fake_mflux(cmd, **kw):
        seen["gate_during_flux"] = heavy_job.active()
        out = cmd[cmd.index("--output") + 1]
        open(out, "wb").write(b"png")
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(imagegen.subprocess, "run", fake_mflux)
    path = imagegen.generate_image("a fox", seed=7)
    assert path.exists()
    assert seen["gate_during_flux"] == "image generation"
    assert heavy_job.active() is None and not flag.exists()


def test_if_the_model_wont_unload_flux_never_starts(monkeypatch, tmp_path, flag):
    monkeypatch.setattr(imagegen, "PAUSED", False)
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path)
    monkeypatch.setattr(imagegen, "LOG_FILE", tmp_path / "log")

    def refuse(*a, **k):
        raise heavy_job.JobBusy("couldn't unload the chat model")
    monkeypatch.setattr(imagegen, "_unload_chat_models", refuse)
    monkeypatch.setattr(imagegen.subprocess, "run", lambda *a, **k: pytest.fail("FLUX must not start"))
    with pytest.raises(heavy_job.JobBusy):
        imagegen.generate_image("a fox")
    assert not flag.exists()
    assert not imagegen._lock.locked()


# ── lower-bit FLUX: the setting reaches the mflux command ────────────────────

def _run_and_capture_cmd(monkeypatch, tmp_path):
    monkeypatch.setattr(imagegen, "PAUSED", False)
    monkeypatch.setattr(imagegen, "OUT_DIR", tmp_path)
    monkeypatch.setattr(imagegen, "LOG_FILE", tmp_path / "log")
    monkeypatch.setattr(imagegen, "_unload_chat_models", lambda *a, **k: None)
    seen = {}

    def fake_mflux(cmd, **kw):
        seen["cmd"] = cmd
        open(cmd[cmd.index("--output") + 1], "wb").write(b"png")
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(imagegen.subprocess, "run", fake_mflux)
    imagegen.generate_image("a fox", seed=7)
    return seen["cmd"]


def test_default_quantization_is_four_bit(monkeypatch, tmp_path):
    monkeypatch.setattr(imagegen, "MODEL_PATH", "")
    cmd = _run_and_capture_cmd(monkeypatch, tmp_path)
    assert cmd[cmd.index("-q") + 1] == "4"


def test_quantization_can_be_changed_and_a_bad_value_falls_back(monkeypatch):
    import importlib
    try:
        monkeypatch.setenv("AMY_FLUX_QUANT", "8")
        assert importlib.reload(imagegen).FLUX_QUANT == "8"
        monkeypatch.setenv("AMY_FLUX_QUANT", "nonsense")
        assert importlib.reload(imagegen).FLUX_QUANT == "4"
    finally:
        monkeypatch.delenv("AMY_FLUX_QUANT")
        importlib.reload(imagegen)
