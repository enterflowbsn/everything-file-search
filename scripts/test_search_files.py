"""Offline regression checks; no Everything installation or personal files needed."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("search_files", Path(__file__).with_name("search_files.py"))
search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(search)


class SearchTests(unittest.TestCase):
    def invoke(self, *args):
        output = io.StringIO()
        with patch.object(sys, "argv", ["search_files.py", *args]), contextlib.redirect_stdout(output):
            search.main()
        return json.loads(output.getvalue())

    def test_exact_unicode_name_excludes_similar_files(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ["黑马[day03].xlsx", "旧-黑马[day03].xlsx", "黑马[day03].xlsx.bak"]:
                Path(folder, name).touch()
            result = self.invoke("--backend", "scan", "--root", folder,
                                 "--name", "黑马[day03].XLSX", "--exact")
            self.assertEqual([x["name"] for x in result["results"]], ["黑马[day03].xlsx"])

    def test_exact_requires_name(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke("--exact", "--ext", "xlsx")

    def test_zero_index_results_do_not_trigger_scan(self):
        empty = {"backend": "everything", "results": [], "warnings": []}
        with patch.object(search, "es_path", return_value="es.exe"), \
             patch.object(search, "everything", return_value=empty), patch.object(search, "scan") as scan:
            result = self.invoke("--name", "absent")
        scan.assert_not_called()
        self.assertEqual(result["returned"], 0)

    def test_auto_fallback_uses_remaining_budget(self):
        captured = []
        def fallback(args):
            captured.append(args.timeout)
            return {"backend": "scan", "results": [], "warnings": []}
        with patch.object(search, "es_path", return_value="es.exe"), \
             patch.object(search, "everything", side_effect=RuntimeError("ES exit 8: unavailable")), \
             patch.object(search.time, "monotonic", side_effect=[100, 104, 105]), \
             patch.object(search, "scan", side_effect=fallback):
            result = self.invoke("--name", "target", "--timeout", "10")
        self.assertEqual(captured, [6])
        self.assertEqual(result["elapsed_ms"], 5000)

    def test_expired_budget_does_not_enumerate(self):
        args = argparse.Namespace(root=["unused"], timeout=0, limit=30)
        with patch.object(search.os, "scandir") as scandir:
            result = search.scan(args)
        scandir.assert_not_called()
        self.assertTrue(result["partial"])

    def test_index_failure_never_scans_in_index_only_mode(self):
        with patch.object(search, "es_path", return_value="es.exe"), \
             patch.object(search, "everything", side_effect=RuntimeError("ES exit 8: unavailable")), \
             patch.object(search, "scan") as scan, self.assertRaises(RuntimeError) as error:
            self.invoke("--backend", "everything", "--name", "target")
        scan.assert_not_called()
        self.assertNotIn("advanced query/instance", str(error.exception))

    def test_query_exact_escapes_metacharacters(self):
        args = argparse.Namespace(query=None, name="a[1].xlsx", exact=True,
                                  kind=None, ext=[], after=None, before=None, root=None)
        self.assertEqual(search.query_for(args), 'regex:"^a\\[1\\]\\.xlsx$" file:')

    def test_es_default_wait_is_short(self):
        def inspect(args, executable):
            self.assertEqual(args.es_timeout, 3)
            return {"results": [], "warnings": []}
        with patch.object(search, "es_path", return_value="es.exe"), \
             patch.object(search, "everything", side_effect=inspect):
            self.invoke("--name", "target")

    def test_es_command_caps_wait_and_preserves_literal_query(self):
        args = argparse.Namespace(name="黑马[day03].xlsx", exact=True, query=None,
                                  kind=None, ext=[], after=None, before=None, root=None,
                                  timeout=2, es_timeout=3, limit=20, instance=None)
        def export(command, **kwargs):
            self.assertEqual(command[command.index("-timeout") + 1], "2000")
            self.assertEqual(kwargs["timeout"], 3)
            self.assertEqual(command[-2], "--")
            output = Path(command[command.index("-export-efu") + 1])
            output.write_text("Filename,Size\n", encoding="utf-8")
            return SimpleNamespace(returncode=0)
        with patch.object(search.subprocess, "run", side_effect=export):
            self.assertEqual(search.everything(args, "es.exe")["results"], [])

    def test_roots_keep_requested_priority_when_capped(self):
        with tempfile.TemporaryDirectory() as folder:
            roots = [Path(folder, "first"), Path(folder, "second")]
            for root in roots:
                root.mkdir()
                (root / "target.txt").touch()
            result = self.invoke("--backend", "scan", "--name", "target", "--limit", "1",
                                 "--root", str(roots[0]), "--root", str(roots[1]))
            self.assertEqual(Path(result["results"][0]["path"]).parent, roots[0])
            self.assertTrue(result["has_more"])


if __name__ == "__main__":
    unittest.main()
