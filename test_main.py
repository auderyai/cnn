"""Behavior and error-isolation checks; python -m unittest -v."""
import contextlib
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import ConsoleApp, FlatMatrix, JsonAnalyzer, MacEngine, Matrix, normalize_label


class SimulatorTests(unittest.TestCase):
    def test_bonus_flat_mac_matches_2d_and_validates_storage(self):
        for size in (3, 5, 13, 25):
            pattern = Matrix.generate(size, "Cross")
            kernel = Matrix.generate(size, "X")
            flat_pattern, flat_kernel = pattern.to_flat(), kernel.to_flat()
            self.assertEqual(len(flat_pattern.values), size * size)
            self.assertEqual(MacEngine.mac(pattern, kernel),
                             MacEngine.mac_flat(flat_pattern, flat_kernel))
            self.assertGreaterEqual(MacEngine.average_flat_ms(
                flat_pattern, flat_kernel, 10), 0)
        with self.assertRaises(ValueError):
            FlatMatrix([1, 2], 2)
        with self.assertRaises(ValueError):
            MacEngine.mac_flat(Matrix.generate(3, "X").to_flat(),
                               Matrix.generate(5, "X").to_flat())

    def test_bonus_pattern_generator_reuses_generated_patterns(self):
        output = io.StringIO()
        with patch("builtins.input", side_effect=["0", "abc", "5", "?", "x"]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(ConsoleApp(10).generated(), 0)
        text = output.getvalue()
        self.assertEqual(text.count("입력 형식 오류"), 3)
        self.assertIn("생성된 Cross 패턴", text)
        self.assertIn("생성된 X 패턴", text)
        self.assertIn("입력 패턴: X", text)
        self.assertIn("완전 일치 판정: X", text)
        self.assertIn("2D 평균(ms)", text)
        self.assertIn("1D 평균(ms)", text)

    def test_exact_match_checks_all_values_and_both_filters(self):
        x, cross = Matrix.generate(3, "X"), Matrix.generate(3, "Cross")
        filters = {"A": x, "B": cross}
        self.assertEqual(MacEngine.exact_matches(x, filters), ["A"])
        self.assertEqual(MacEngine.exact_matches(cross, filters), ["B"])
        self.assertEqual(MacEngine.exact_matches(x, {"A": x, "B": x}), ["A", "B"])
        self.assertEqual(MacEngine.exact_matches(Matrix([[0]*3 for _ in range(3)]), filters), [])
        modified = Matrix(x.rows)
        modified.set(0, 1, 1e-12)
        self.assertEqual(MacEngine.exact_matches(modified, filters), [])
        self.assertEqual(MacEngine.exact_matches(Matrix([[1]]), filters), [])

    def test_user_pattern_reports_no_matching_filter(self):
        lines = ["1 0 1", "0 1 0", "1 0 1", "0 1 0", "1 1 1", "0 1 0",
                 "1 1 1", "0 0 0", "1 1 1"]
        output = io.StringIO()
        with patch("builtins.input", side_effect=lines), contextlib.redirect_stdout(output):
            self.assertEqual(ConsoleApp(10).manual(), 0)
        self.assertIn("A 점수: 4", output.getvalue())
        self.assertIn("B 점수: 2", output.getvalue())
        self.assertIn("MAC 점수 판정: A", output.getvalue())
        self.assertIn("완전 일치 판정: 일치하는 필터 없음", output.getvalue())

    def test_menu_repeats_after_both_modes_and_exits_on_four(self):
        app = str(Path(__file__).with_name("main.py"))
        matrix_lines = ["0 1 0", "1 1 1", "0 1 0"]
        output = io.StringIO()
        replies = ["invalid", "1"] + matrix_lines * 3 + ["2", "2", "4"]
        with patch.object(sys, "argv", [app, "--repeats", "10"]), \
                patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", side_effect=replies) as reader, \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(app, run_name="__main__")
            self.assertEqual(stopped.exception.code, 0)
            self.assertEqual(reader.call_count, len(replies))
        self.assertEqual(output.getvalue().count("[모드 선택]"), 5)
        self.assertEqual(output.getvalue().count("총 테스트: 6개"), 2)
        self.assertIn("프로그램을 종료합니다.", output.getvalue())

    def test_file_error_returns_to_menu(self):
        output = io.StringIO()
        with patch("builtins.input", side_effect=["2", "4"]) as reader, \
                contextlib.redirect_stdout(output):
            status = ConsoleApp(10).run(None, Path("missing_test_file.json"))
        self.assertEqual(status, 0)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(output.getvalue().count("[모드 선택]"), 2)
        self.assertIn("파일/스키마 오류", output.getvalue())

    def test_reference_mac_and_coordinate_access(self):
        cross, x = Matrix.generate(3, "Cross"), Matrix.generate(3, "X")
        self.assertEqual(MacEngine.mac(cross, cross), 5)
        self.assertEqual(MacEngine.mac(cross, x), 1)
        self.assertEqual(MacEngine.decide(1, 5, "A", "B"), "B")
        cross.set(0, 0, 0.5)
        self.assertEqual(cross.get(0, 0), 0.5)
        with self.assertRaises(IndexError):
            cross.get(-1, 0)

    def test_epsilon_boundary_and_normalization(self):
        self.assertEqual(MacEngine.decide(0.9, 0.8999999999999999), "UNDECIDED")
        self.assertEqual(MacEngine.decide(0, 1e-9), "X")
        for value, expected in [("+", "Cross"), ("cross", "Cross"),
                                ("x", "X"), (" X ", "X")]:
            self.assertEqual(normalize_label(value), expected)
        with self.assertRaises(ValueError):
            normalize_label("unknown")

    def test_invalid_matrices(self):
        for rows in ([], [[1, 2]], [[True]], [["1"]], [[float("nan")]],
                     [[float("inf")]], [[10 ** 400]]):
            with self.subTest(rows=str(rows)[:40]), self.assertRaises(ValueError):
                Matrix(rows)
        with self.assertRaises(ValueError):
            MacEngine.mac(Matrix([[1e308]]), Matrix([[1e308]]))

    def test_manual_retries_and_end_to_end(self):
        # Wrong column count, text, Infinity, and blank line must retry same row.
        lines = ["1 2", "a b c", "inf 0 0", "", "0 1 0", "1 1 1", "0 1 0",
                 "1 0 1", "0 1 0", "1 0 1", "1 0 1", "0 1 0", "1 0 1"]
        output = io.StringIO()
        with patch("builtins.input", side_effect=lines), contextlib.redirect_stdout(output):
            self.assertEqual(ConsoleApp(10).manual(), 0)
        text = output.getvalue()
        self.assertEqual(text.count("입력 형식 오류"), 4)
        self.assertIn("A 점수: 1", text)
        self.assertIn("B 점수: 5", text)
        self.assertIn("판정: B", text)
        self.assertIn("3x3", text)

    def test_supplied_data_exact_results(self):
        data = json.loads(Path(__file__).with_name("data.json").read_text(encoding="utf-8"))
        analyzer = JsonAnalyzer()
        with contextlib.redirect_stdout(io.StringIO()):
            analyzer.load_filters(data["filters"])
        results = [analyzer.analyze_case(k, v) for k, v in data["patterns"].items()]
        self.assertEqual(len(results), 6)
        self.assertEqual(sum(r.passed for r in results), 3)
        self.assertEqual([r.identifier for r in results if not r.passed],
                         ["size_5_1", "size_13_2", "size_25_1"])
        self.assertTrue(all(r.prediction == "UNDECIDED" for r in results if not r.passed))

    def test_case_failures_do_not_stop_later_valid_case(self):
        analyzer = JsonAnalyzer()
        cross, x = Matrix.generate(5, "Cross"), Matrix.generate(5, "X")
        with contextlib.redirect_stdout(io.StringIO()):
            analyzer.load_filters({"size_5": {"cross": cross.rows, "x": x.rows},
                                   "size_13": {"cross": [[1]], "x": [[1]]}})
        cases = [
            ("bad_key", {}),
            ("size_5_1", {"input": [[1]], "expected": "+"}),
            ("size_5_2", {"input": cross.rows, "expected": "unknown"}),
            ("size_13_1", {"input": Matrix.generate(13, "Cross").rows, "expected": "+"}),
            ("size_7_1", {"input": Matrix.generate(7, "Cross").rows, "expected": "+"}),
            ("size_5_3", {"input": cross.rows, "expected": "+"}),
        ]
        results = [analyzer.analyze_case(k, v) for k, v in cases]
        self.assertEqual(sum(r.passed for r in results), 1)
        self.assertTrue(results[-1].passed)
        self.assertTrue(all(r.reason for r in results[:-1]))

    def test_top_level_file_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            for text in ("{", "[]", '{"patterns": []}', '{"patterns": {}}'):
                path.write_text(text, encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(ConsoleApp(10).batch(path), 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ConsoleApp(10).batch(path.with_name("missing.json")), 1)

    def test_performance_repeats_and_cli(self):
        for size in (3, 5, 13, 25):
            matrix = Matrix.generate(size, "Cross")
            self.assertGreaterEqual(MacEngine.average_ms(matrix, matrix, 10), 0)
        with self.assertRaises(ValueError):
            MacEngine.average_ms(matrix, matrix, 9)
        app = str(Path(__file__).with_name("main.py"))
        result = subprocess.run([sys.executable, app, "--mode", "2", "--once", "--repeats", "10"],
                                capture_output=True, encoding="utf-8",
                                env=dict(__import__("os").environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("총 테스트: 6개\n통과: 3개\n실패: 3개", result.stdout)
        invalid = subprocess.run([sys.executable, app, "--repeats", "9"], capture_output=True)
        self.assertEqual(invalid.returncode, 2)


if __name__ == "__main__":
    unittest.main()
