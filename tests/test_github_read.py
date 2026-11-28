"""github_read tool — unit tests (no network; gh is monkeypatched)."""
import base64
import json
import sys
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, ".")


def _run_tool(args, stdout, returncode=0):
    """Helper: patch subprocess.run and dispatch github_read."""
    import tools
    mock_result = MagicMock()
    mock_result.returncode = returncode
    mock_result.stdout = stdout
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        return tools.dispatch("github_read", args)


class TestGithubReadDirectory(unittest.TestCase):
    def test_directory_listing(self):
        payload = json.dumps([
            {"type": "file", "name": "main.js"},
            {"type": "dir",  "name": "src"},
        ])
        result = _run_tool({"repo": "owner/repo"}, payload)
        self.assertIn("main.js", result)
        self.assertIn("src", result)

    def test_root_listing_no_path(self):
        payload = json.dumps([{"type": "file", "name": "README.md"}])
        result = _run_tool({"repo": "owner/repo"}, payload)
        self.assertIn("README.md", result)
        self.assertIn("owner/repo", result)


class TestGithubReadFile(unittest.TestCase):
    def test_file_content_decoded(self):
        content = "console.log('hello');"
        payload = json.dumps({
            "type": "file",
            "path": "src/main.js",
            "size": len(content),
            "content": base64.b64encode(content.encode()).decode() + "\n",
        })
        result = _run_tool({"repo": "owner/repo", "path": "src/main.js"}, payload)
        self.assertIn("console.log", result)
        self.assertIn("src/main.js", result)

    def test_large_file_truncated(self):
        content = "x" * 50000
        payload = json.dumps({
            "type": "file",
            "path": "big.txt",
            "size": 50000,
            "content": base64.b64encode(content.encode()).decode() + "\n",
        })
        result = _run_tool({"repo": "owner/repo", "path": "big.txt"}, payload)
        self.assertIn("truncated", result)


class TestGithubReadErrors(unittest.TestCase):
    def test_missing_repo_returns_error(self):
        result = _run_tool({"repo": ""}, "")
        self.assertIn("need repo", result)

    def test_gh_nonzero_exit(self):
        import tools
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "repository not found"
        with patch("subprocess.run", return_value=mock_result):
            result = tools.dispatch("github_read", {"repo": "owner/missing"})
        self.assertIn("error", result.lower())
        self.assertIn("repository not found", result)

    def test_ref_passed_to_gh(self):
        payload = json.dumps([{"type": "file", "name": "README.md"}])
        import tools
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = payload
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            tools.dispatch("github_read", {"repo": "owner/repo", "ref": "dev"})
        cmd = mock_run.call_args[0][0]
        self.assertIn("dev", " ".join(cmd))


if __name__ == "__main__":
    unittest.main()
