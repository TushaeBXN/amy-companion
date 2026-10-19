"""Amy's recursive self-improvement framework.

Three layers:
  1. Dependency map   — knows which files affect which, so Amy can reason about
                        blast radius before touching anything.
  2. Test suite       — static + structural checks that run in <2s without Ollama.
  3. Guarded self-edit — backup → edit → test → auto-revert if score drops.

All project files are editable. The safety net IS the test suite: syntax check +
backup + auto-revert if score drops. self_check.py itself is never self-edited.
"""
import ast
import importlib
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# ── Safety gates ──────────────────────────────────────────────────────────────

# self_check.py is the only file that cannot self-modify (guard integrity).
# All other files: syntax check + backup + auto-revert is the safety net.
PROTECTED_FILES = {"self_check.py"}

# Legacy aliases kept so existing callers don't break.
CORE_FILES = PROTECTED_FILES
AUTONOMOUS_FILES = set()  # everything is autonomous now

# Minimum test score required BEFORE editing high-blast-radius files.
# If the suite is already broken, don't make it worse.
MIN_SCORE_TO_EDIT_HIGH = 60

# ── Dependency map ─────────────────────────────────────────────────────────────

def _imports_in(filepath: str) -> list[str]:
    """Return local module names imported by filepath (stdlib/site excluded)."""
    local_mods = set()
    try:
        src = open(filepath).read()
        tree = ast.parse(src, filename=filepath)
    except Exception:
        return []
    local_py = {os.path.splitext(f)[0]
                for f in os.listdir(HERE) if f.endswith(".py")}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]
                if name in local_py:
                    local_mods.add(name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split(".")[0]
                if name in local_py:
                    local_mods.add(name)
    return sorted(local_mods)


def build_dep_map() -> dict:
    """Build {module: [modules it imports]} for every .py file in HERE."""
    dep_map = {}
    for fname in sorted(os.listdir(HERE)):
        if not fname.endswith(".py"):
            continue
        mod = fname[:-3]
        dep_map[mod] = _imports_in(os.path.join(HERE, fname))
    return dep_map


def check_impact(filename: str, dep_map: dict | None = None) -> dict:
    """Return what a change to `filename` would affect.

    Returns a dict with:
      - 'target'     : the module being changed
      - 'depends_on' : what it imports (if it breaks, these may help debug)
      - 'depended_by': modules that import it — these could break
      - 'blast_radius': 'low' / 'medium' / 'high'
      - 'needs_approval': True if file is in CORE_FILES
    """
    if dep_map is None:
        dep_map = build_dep_map()
    mod = os.path.splitext(os.path.basename(filename))[0]
    depends_on = dep_map.get(mod, [])
    depended_by = [m for m, deps in dep_map.items() if mod in deps]

    n = len(depended_by)
    blast = "low" if n == 0 else "medium" if n <= 3 else "high"

    return {
        "target": mod,
        "filename": os.path.basename(filename),
        "depends_on": depends_on,
        "depended_by": depended_by,
        "blast_radius": blast,
        "needs_approval": os.path.basename(filename) in CORE_FILES,
    }


def save_dep_map(path: str | None = None):
    """Write dep_map.json next to self_check.py (for human review)."""
    dm = build_dep_map()
    out = path or os.path.join(HERE, "dep_map.json")
    with open(out, "w") as f:
        json.dump(dm, f, indent=2)
    return out


# ── Test suite ─────────────────────────────────────────────────────────────────

# Required symbols per module (name → list of attr paths that must exist).
_REQUIRED_SYMBOLS = {
    "memory": [
        "Memory", "Memory.wake_up", "Memory.add", "Memory.search",
        "Memory.remember", "Memory.add_goal", "Memory.complete_goal",
    ],
    "tools": [
        "TOOLS", "dispatch",
    ],
    "state": [
        "InternalState", "InternalState.after_turn", "InternalState.state_summary",
    ],
    "journal": [
        "save_entry", "read_recent",
    ],
    "web": [
        "search",
    ],
    "income": [
        "PLATFORMS", "AMY_SKILLS",
    ],
    "work": [
        "execute",
    ],
}

# Files that must be present on disk.
_REQUIRED_FILES = [
    "companion.py", "telegram_amy.py", "memory.py", "tools.py",
    "state.py", "amy_persona.py", "amy_self.json",
]


def _syntax_check(filepath: str) -> str | None:
    """Return error string if file has a syntax error, else None."""
    try:
        with open(filepath) as f:
            src = f.read()
        ast.parse(src, filename=filepath)
        return None
    except SyntaxError as e:
        return f"SyntaxError line {e.lineno}: {e.msg}"
    except Exception as e:
        return str(e)


def _symbol_check(mod_name: str, required: list[str]) -> list[str]:
    """Import module and verify required attr paths exist. Returns list of failures."""
    failures = []
    # Temporarily add HERE to sys.path so we can import local modules.
    _added = HERE not in sys.path
    if _added:
        sys.path.insert(0, HERE)
    try:
        # Fresh import — reload if already cached so edits are reflected.
        if mod_name in sys.modules:
            mod = importlib.reload(sys.modules[mod_name])
        else:
            mod = importlib.import_module(mod_name)
        for attr_path in required:
            obj = mod
            for part in attr_path.split("."):
                try:
                    obj = getattr(obj, part)
                except AttributeError:
                    failures.append(f"{attr_path} missing")
                    break
    except Exception as e:
        failures.append(f"import failed: {e}")
    finally:
        if _added:
            sys.path.remove(HERE)
    return failures


def run_tests(verbose: bool = False) -> dict:
    """Run the full static test suite. Returns a results dict."""
    results = {}
    passed = 0
    failed = 0
    log = []

    def _record(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
            if verbose:
                log.append(f"  ✓ {name}")
        else:
            failed += 1
            log.append(f"  ✗ {name}: {detail}")
        results[name] = {"ok": ok, "detail": detail}

    # 1. Required files exist
    for fname in _REQUIRED_FILES:
        fpath = os.path.join(HERE, fname)
        ok = os.path.exists(fpath)
        _record(f"file_exists:{fname}", ok,
                "" if ok else "file missing")

    # 2. Syntax checks on all .py files
    for fname in sorted(os.listdir(HERE)):
        if not fname.endswith(".py"):
            continue
        err = _syntax_check(os.path.join(HERE, fname))
        _record(f"syntax:{fname}", err is None, err or "")

    # 3. Symbol checks on key modules
    for mod_name, symbols in _REQUIRED_SYMBOLS.items():
        fpath = os.path.join(HERE, mod_name + ".py")
        if not os.path.exists(fpath):
            _record(f"symbols:{mod_name}", False, "file missing — skipped")
            continue
        failures = _symbol_check(mod_name, symbols)
        _record(f"symbols:{mod_name}", not failures,
                "; ".join(failures) if failures else "")

    # 4. amy_self.json is valid JSON and has required keys
    self_path = os.path.join(HERE, "amy_self.json")
    if os.path.exists(self_path):
        try:
            with open(self_path) as f:
                data = json.load(f)
            for key in ("identity", "facts"):
                _record(f"amy_self:{key}", key in data,
                        "" if key in data else f"missing key '{key}'")
        except Exception as e:
            _record("amy_self:json", False, str(e))
    else:
        _record("amy_self:json", False, "file missing")

    score = round((passed / (passed + failed)) * 100) if (passed + failed) > 0 else 0
    return {
        "passed": passed,
        "failed": failed,
        "score": score,
        "log": log,
        "details": results,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def score_tests() -> int:
    """Quick score 0–100 for use in before/after comparison."""
    r = run_tests()
    return r["score"]


# ── Guarded self-edit ──────────────────────────────────────────────────────────

def _log_code_reward(filename: str, score_before: int, score_after: int,
                     reason: str, success: bool):
    """Log a code_improvement reward token to self_metrics.

    Reward scale:
      5 — change applied AND suite score improved
      4 — change applied, suite score same
      2 — change reverted (made things worse)
      1 — change reverted AND score dropped significantly
    """
    try:
        import self_metrics as _sm
        if success:
            reward = 5 if score_after > score_before else 4
        else:
            reward = 1 if (score_before - score_after) > 5 else 2
        note = (f"{'✓' if success else '✗'} edit {filename}: "
                f"suite {score_before}→{score_after} | {reason[:80]}")
        _sm.log_score("code_improvement", reward, note)
    except Exception:
        pass


def self_edit(
    filename: str,
    old_text: str,
    new_text: str,
    reason: str,
    *,
    force_autonomous: bool = False,
) -> dict:
    """Safely attempt to replace old_text with new_text in filename.

    Workflow:
      1. Resolve and validate path.
      2. Check impact (blast radius + approval gate).
      3. If needs_approval and not force_autonomous → return approval_required.
      4. Syntax-check the proposed change in isolation.
      5. Backup the file.
      6. Apply the change.
      7. Run test suite.
      8. If score drops → auto-revert and report what went wrong.
      9. Return result dict.

    Returns a dict with 'status' key:
      'approval_required'  — human must approve (core file)
      'high_blast_radius'  — too risky to auto-edit (>3 dependents)
      'syntax_error'       — proposed change has a syntax error
      'applied'            — change applied, tests passed
      'reverted'           — change applied but tests failed → reverted
      'error'              — unexpected failure
    """
    # 1. Resolve path
    if not os.path.isabs(filename):
        filepath = os.path.join(HERE, filename)
    else:
        filepath = filename
    filepath = os.path.normpath(filepath)
    basename = os.path.basename(filepath)

    # Must be inside ava-companion
    if not filepath.startswith(HERE):
        return {"status": "error", "message": f"Path outside project: {filepath}"}
    if not os.path.exists(filepath):
        return {"status": "error", "message": f"File not found: {basename}"}

    # 2. Impact analysis
    dm = build_dep_map()
    impact = check_impact(basename, dm)

    # 3. Protected file gate (only self_check.py)
    if basename in PROTECTED_FILES:
        return {
            "status": "approval_required",
            "file": basename,
            "message": "self_check.py cannot self-modify — it is the integrity guard.",
        }

    # High blast radius: require the suite to be healthy before proceeding
    if impact["blast_radius"] == "high":
        pre_score = score_tests()
        if pre_score < MIN_SCORE_TO_EDIT_HIGH:
            return {
                "status": "high_blast_radius",
                "file": basename,
                "score": pre_score,
                "message": (
                    f"'{basename}' has high blast radius and the test suite is at {pre_score}% "
                    f"(need {MIN_SCORE_TO_EDIT_HIGH}%+ before touching high-impact files). "
                    "Fix existing failures first."
                ),
            }

    # 4. Syntax-check proposed change
    with open(filepath) as f:
        original = f.read()
    if old_text not in original:
        return {
            "status": "error",
            "message": f"old_text not found verbatim in {basename}. No change made.",
        }
    proposed = original.replace(old_text, new_text, 1)
    if filepath.endswith(".py"):
        try:
            ast.parse(proposed, filename=basename)
        except SyntaxError as e:
            return {
                "status": "syntax_error",
                "file": basename,
                "error": f"line {e.lineno}: {e.msg}",
                "message": "Proposed change has a syntax error — not applied.",
            }

    # 5. Backup
    backup_path = filepath + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, backup_path)

    # 6. Score before
    score_before = score_tests()

    # 7. Apply
    try:
        with open(filepath, "w") as f:
            f.write(proposed)
    except Exception as e:
        shutil.copy2(backup_path, filepath)
        os.remove(backup_path)
        return {"status": "error", "message": f"Write failed: {e}"}

    # 8. Score after
    score_after = score_tests()
    if score_after < score_before:
        # Revert
        shutil.copy2(backup_path, filepath)
        os.remove(backup_path)
        mod = os.path.splitext(basename)[0]
        if mod in sys.modules:
            try:
                importlib.reload(sys.modules[mod])
            except Exception:
                pass
        # Reward token: revert = low score (change made things worse)
        _log_code_reward(basename, score_before, score_after, reason, success=False)
        return {
            "status": "reverted",
            "file": basename,
            "score_before": score_before,
            "score_after": score_after,
            "reason": reason,
            "message": (
                f"Score dropped {score_before}→{score_after} after edit — reverted. "
                "The change didn't pass the test suite."
            ),
        }

    # 9. Success — reload cached module
    os.remove(backup_path)
    mod = os.path.splitext(basename)[0]
    if mod in sys.modules:
        try:
            importlib.reload(sys.modules[mod])
        except Exception:
            pass

    # Reward token: success = high score, bonus if suite improved
    _log_code_reward(basename, score_before, score_after, reason, success=True)
    return {
        "status": "applied",
        "file": basename,
        "score_before": score_before,
        "score_after": score_after,
        "delta": score_after - score_before,
        "reason": reason,
        "impact": impact,
        "message": (
            f"Applied to {basename}. Suite: {score_before}→{score_after}"
            f"{' (+' + str(score_after-score_before) + ')' if score_after > score_before else ''}."
        ),
    }


def read_codebase(focus: str = "") -> str:
    """Return a structured map of every .py file in the project.

    For each file: size, public functions/classes, and a one-line docstring.
    Gives Amy deep knowledge of her own codebase before making changes.
    `focus` narrows to files whose name contains the search term.
    """
    lines = ["# Codebase map\n"]
    for fname in sorted(os.listdir(HERE)):
        if not fname.endswith(".py"):
            continue
        if focus and focus.lower() not in fname.lower():
            continue
        fpath = os.path.join(HERE, fname)
        try:
            src = open(fpath).read()
            tree = ast.parse(src, filename=fname)
        except Exception as e:
            lines.append(f"## {fname}  [parse error: {e}]\n")
            continue
        size_kb = len(src) / 1024
        # Top-level docstring
        top_doc = ""
        if (tree.body and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)):
            top_doc = str(tree.body[0].value.value).strip().splitlines()[0][:100]
        lines.append(f"## {fname}  ({size_kb:.1f} KB)")
        if top_doc:
            lines.append(f"   {top_doc}")
        # Public symbols
        symbols = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    doc = (ast.get_docstring(node) or "").splitlines()[0][:80]
                    symbols.append(f"  fn  {node.name}()" + (f" — {doc}" if doc else ""))
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    doc = (ast.get_docstring(node) or "").splitlines()[0][:80]
                    symbols.append(f"  cls {node.name}" + (f" — {doc}" if doc else ""))
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if not item.name.startswith("_"):
                                symbols.append(f"       .{item.name}()")
        if symbols:
            lines.extend(symbols[:30])  # cap at 30 per file
            if len(symbols) > 30:
                lines.append(f"  ... +{len(symbols)-30} more")
        lines.append("")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Amy self-check tools")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("test",     help="Run full test suite")
    sub.add_parser("depmap",   help="Build and print dep_map.json")
    p_impact = sub.add_parser("impact", help="Show impact of changing a file")
    p_impact.add_argument("file")
    args = parser.parse_args()

    if args.cmd == "test":
        r = run_tests(verbose=True)
        for line in r["log"]:
            print(line)
        print(f"\nScore: {r['score']}%  ({r['passed']} passed, {r['failed']} failed)")
    elif args.cmd == "depmap":
        p = save_dep_map()
        dm = json.load(open(p))
        print(json.dumps(dm, indent=2))
        print(f"\nSaved to {p}")
    elif args.cmd == "impact":
        info = check_impact(args.file)
        print(json.dumps(info, indent=2))
    else:
        parser.print_help()
