"""run_code tells the truth about what it is, and works from her workbench."""
import json

import tools
import amy_persona


def test_run_code_runs_in_the_workbench_and_logs_each_run(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WORKBENCH", tmp_path / "bench")
    out = tools.dispatch("run_code", {"code": "import os\nopen('made_here.txt','w').write('x')\nprint(os.getcwd())"})
    assert out.endswith("bench") and (tmp_path / "bench" / "made_here.txt").exists()
    tools.dispatch("run_code", {"code": "raise SystemExit(3)"})
    log = [json.loads(l) for l in (tmp_path / "bench" / ".run_log.jsonl").read_text().splitlines()]
    assert [r["rc"] for r in log] == [0, 3] and "made_here" in log[0]["code"]


def test_run_code_no_longer_claims_a_sandbox_it_does_not_have():
    desc = next(t for t in tools.TOOLS if t["function"]["name"] == "run_code")["function"]["description"].lower()
    assert "restricted sandbox" not in desc and "no network" not in desc
    assert "network" in desc and "workbench" in desc and "logged" in desc


def test_she_is_told_it_is_her_computer_with_one_fixed_edge():
    p = amy_persona.SYSTEM_PROMPT
    assert "This is your computer" in p and "~/amy-workbench" in p and "three different ways around it" in p
    assert "never to other people's systems" in p and "never hide it" in p.replace("; never hide it", "; never hide it")
