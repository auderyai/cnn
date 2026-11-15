"""Mini NPU Simulator: Python 3.8+, standard library only."""
import argparse
import json
import math
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import List, Optional

EPSILON = 1e-9


class Matrix:
    """Validated square matrix; coordinates are zero-based."""

    def __init__(self, rows, size=None):
        if not isinstance(rows, list) or not rows:
            raise ValueError("행렬은 비어 있지 않은 2차원 배열이어야 합니다.")
        self.size = len(rows) if size is None else size
        if self.size < 1 or len(rows) != self.size:
            raise ValueError("행 수 불일치: {}행 필요, {}행 입력".format(self.size, len(rows)))
        self.rows = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, list) or len(row) != self.size:
                raise ValueError("{}행 열 수 불일치: {}개 필요".format(index, self.size))
            self.rows.append([self.number(value) for value in row])

    @staticmethod
    def number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("행렬 원소는 숫자여야 합니다(boolean/문자열 제외).")
        try:
            number = float(value)
        except (OverflowError, ValueError):
            raise ValueError("숫자가 표현 가능한 실수 범위를 벗어났습니다.") from None
        if not math.isfinite(number):
            raise ValueError("NaN/Infinity는 사용할 수 없습니다.")
        return number

    def _check(self, row, col):
        if not (0 <= row < self.size and 0 <= col < self.size):
            raise IndexError("행렬 좌표 범위를 벗어났습니다.")

    def get(self, row, col):
        self._check(row, col)
        return self.rows[row][col]

    def set(self, row, col, value):
        self._check(row, col)
        self.rows[row][col] = self.number(value)

    def to_flat(self):
        """Return the same matrix in row-major one-dimensional storage."""
        return FlatMatrix(
            [self.rows[row][col] for row in range(self.size) for col in range(self.size)],
            self.size,
        )

    @classmethod
    def generate(cls, size, label):
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("패턴 크기 N은 1 이상의 정수여야 합니다.")
        if label not in {"Cross", "X"}:
            raise ValueError("생성할 패턴은 Cross 또는 X여야 합니다.")
        middle = size // 2
        return cls([[float((r == middle or c == middle) if label == "Cross"
                           else (r == c or r + c == size - 1))
                     for c in range(size)] for r in range(size)])


class FlatMatrix:
    """Validated N² row-major storage used by the bonus optimization."""

    def __init__(self, values, size):
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("1차원 행렬 크기는 1 이상의 정수여야 합니다.")
        if not isinstance(values, list) or len(values) != size * size:
            raise ValueError("1차원 행렬은 정확히 N²개의 원소가 필요합니다.")
        self.size = size
        self.values = [Matrix.number(value) for value in values]


def normalize_label(value):
    if not isinstance(value, str):
        raise ValueError("라벨은 문자열이어야 합니다.")
    labels = {"+": "Cross", "cross": "Cross", "x": "X"}
    try:
        return labels[value.strip().lower()]
    except KeyError:
        raise ValueError("지원하지 않는 라벨: {!r}".format(value)) from None


class MacEngine:
    @staticmethod
    def exact_matches(pattern, filters):
        """Compare every numeric value, including zero positions; no epsilon."""
        matches = []
        for label, kernel in filters.items():
            if pattern.size != kernel.size:
                continue
            identical = True
            for row in range(pattern.size):
                for col in range(pattern.size):
                    if pattern.rows[row][col] != kernel.rows[row][col]:
                        identical = False
                        break
                if not identical:
                    break
            if identical:
                matches.append(label)
        return matches

    @staticmethod
    def mac(pattern, kernel):
        if pattern.size != kernel.size:
            raise ValueError("필터와 패턴의 크기가 일치하지 않습니다.")
        score = 0.0
        for row in range(pattern.size):
            for col in range(pattern.size):
                score += pattern.rows[row][col] * kernel.rows[row][col]
        if not math.isfinite(score):
            raise ValueError("MAC 결과가 실수 범위를 벗어났습니다.")
        return score

    @staticmethod
    def mac_flat(pattern, kernel):
        """MAC over pre-flattened row-major data; conversion is not timed."""
        if pattern.size != kernel.size:
            raise ValueError("필터와 패턴의 크기가 일치하지 않습니다.")
        score = 0.0
        for index in range(pattern.size * pattern.size):
            score += pattern.values[index] * kernel.values[index]
        if not math.isfinite(score):
            raise ValueError("MAC 결과가 실수 범위를 벗어났습니다.")
        return score

    @staticmethod
    def decide(score_a, score_b, label_a="Cross", label_b="X"):
        if not math.isfinite(score_a) or not math.isfinite(score_b):
            raise ValueError("유한하지 않은 점수는 비교할 수 없습니다.")
        if abs(score_a - score_b) < EPSILON:
            return "UNDECIDED"
        return label_a if score_a > score_b else label_b

    @classmethod
    def average_ms(cls, pattern, kernel, repeats):
        if repeats < 10:
            raise ValueError("반복 측정은 최소 10회여야 합니다.")
        cls.mac(pattern, kernel)  # warm-up, outside measured interval
        elapsed = 0
        for _ in range(repeats):
            start = perf_counter_ns()
            cls.mac(pattern, kernel)
            elapsed += perf_counter_ns() - start
        return elapsed / repeats / 1_000_000

    @classmethod
    def average_flat_ms(cls, pattern, kernel, repeats):
        if repeats < 10:
            raise ValueError("반복 측정은 최소 10회여야 합니다.")
        cls.mac_flat(pattern, kernel)  # warm-up, outside measured interval
        elapsed = 0
        for _ in range(repeats):
            start = perf_counter_ns()
            cls.mac_flat(pattern, kernel)
            elapsed += perf_counter_ns() - start
        return elapsed / repeats / 1_000_000


@dataclass
class CaseResult:
    identifier: str
    expected: str = "미확인"
    prediction: str = "미실행"
    cross: Optional[float] = None
    x: Optional[float] = None
    reason: str = ""
    exact_matches: Optional[List[str]] = None

    @property
    def passed(self):
        return not self.reason and self.prediction == self.expected


class JsonAnalyzer:
    def __init__(self):
        self.filters = {}
        self.filter_errors = {}
        self.results: List[CaseResult] = []

    def load_filters(self, raw):
        if not isinstance(raw, dict):
            raise ValueError("filters는 객체여야 합니다.")
        for key in sorted(set(raw) | {"size_5", "size_13", "size_25"}):
            try:
                match = re.fullmatch(r"size_([1-9][0-9]*)", key)
                if match is None:
                    raise ValueError("필터 키 형식 오류(size_N 필요)")
                group = raw.get(key)
                if not isinstance(group, dict):
                    raise ValueError("필터 그룹 누락 또는 객체 형식 오류")
                kernels = {}
                for label, rows in group.items():
                    normalized = normalize_label(label)
                    if normalized in kernels:
                        raise ValueError("정규화 후 중복 필터: " + normalized)
                    kernels[normalized] = Matrix(rows, int(match.group(1)))
                if set(kernels) != {"Cross", "X"}:
                    raise ValueError("Cross와 X 필터가 모두 필요합니다.")
                self.filters[key] = kernels
                print("{} 필터 로드 완료 (Cross, X)".format(key))
            except ValueError as error:
                self.filter_errors[key] = str(error)
                print("{} 필터 오류: {}".format(key, error))

    def analyze_case(self, identifier, raw):
        result = CaseResult(identifier)
        try:
            match = re.fullmatch(r"size_([1-9][0-9]*)_([0-9]+)", identifier)
            if match is None:
                raise ValueError("패턴 키 형식 오류(size_N_idx 필요)")
            if not isinstance(raw, dict):
                raise ValueError("패턴 항목은 객체여야 합니다.")
            result.expected = normalize_label(raw.get("expected"))
            size = int(match.group(1))
            pattern = Matrix(raw.get("input"), size)
            key = "size_{}".format(size)
            if key not in self.filters:
                raise ValueError("{} 필터 사용 불가: {}".format(
                    key, self.filter_errors.get(key, "필터 누락")))
            kernels = self.filters[key]
            result.exact_matches = MacEngine.exact_matches(pattern, kernels)
            result.cross = MacEngine.mac(pattern, kernels["Cross"])
            result.x = MacEngine.mac(pattern, kernels["X"])
            result.prediction = MacEngine.decide(result.cross, result.x)
            if result.prediction == "UNDECIDED":
                result.reason = "동점 규칙: |Cross-X| < 1e-9"
            elif result.prediction != result.expected:
                result.reason = "expected 불일치: 점수가 큰 필터는 {}".format(result.prediction)
        except ValueError as error:
            result.reason = "데이터/연산 오류: " + str(error)
        self.results.append(result)
        return result


class ConsoleApp:
    @staticmethod
    def print_exact_matches(matches):
        if not matches:
            text = "일치하는 필터 없음"
        elif len(matches) == 1:
            text = matches[0] + " (모든 위치의 값이 일치)"
        else:
            text = ", ".join(matches) + " 모두 일치 (단일 필터 판정 불가)"
        print("완전 일치 판정: " + text)

    def __init__(self, repeats=100):
        self.repeats = repeats

    @staticmethod
    def heading(text):
        print("\n" + "-" * 56 + "\n" + text + "\n" + "-" * 56)

    @staticmethod
    def read_matrix(name):
        print("\n{} (3줄 입력, 각 줄에 숫자 3개를 공백으로 구분)".format(name))
        rows = []
        while len(rows) < 3:
            try:
                parts = input("{}행: ".format(len(rows) + 1)).split()
                if len(parts) != 3:
                    raise ValueError("열 수 오류")
                row = [Matrix.number(float(part)) for part in parts]
                rows.append(row)
            except (ValueError, OverflowError):
                print("입력 형식 오류: 각 줄에 3개의 유한한 숫자를 공백으로 구분해 입력하세요.")
        return Matrix(rows, 3)

    def performance(self, sizes, samples=None):
        self.heading("성능 분석 (2차원/1차원 MAC 평균 비교/{}회)".format(self.repeats))
        print("{:<10} {:>15} {:>15} {:>12} {:>12}".format(
            "크기(NxN)", "2D 평균(ms)", "1D 평균(ms)", "차이(%)", "MAC(N²)"))
        for size in sizes:
            pair = (samples or {}).get(size)
            if pair is None:
                kernel = Matrix.generate(size, "Cross")
                pair = (kernel, kernel)
            try:
                elapsed_2d = MacEngine.average_ms(pair[0], pair[1], self.repeats)
                flat_pair = (pair[0].to_flat(), pair[1].to_flat())
                if MacEngine.mac(pair[0], pair[1]) != MacEngine.mac_flat(*flat_pair):
                    raise ValueError("2차원/1차원 MAC 점수가 일치하지 않습니다.")
                elapsed_1d = MacEngine.average_flat_ms(*flat_pair, self.repeats)
                difference = ((elapsed_2d - elapsed_1d) / elapsed_2d * 100
                              if elapsed_2d else 0.0)
                print("{:<10} {:>15.6f} {:>15.6f} {:>+11.2f}% {:>12}".format(
                    "{}x{}".format(size, size), elapsed_2d, elapsed_1d,
                    difference, size * size))
            except ValueError as error:
                print("{}x{} 측정 실패: {}".format(size, size, error))
        print("동일 입력/반복 횟수, 변환/I/O 제외. 양수 차이는 1D가 빠름을 뜻합니다.")
        print("N²는 필터 1개의 곱셈·누적 쌍 수이며 실제 시간은 환경에 따라 달라집니다.")

    @staticmethod
    def print_matrix(matrix):
        for row in matrix.rows:
            print(" ".join("{:g}".format(value) for value in row))

    @staticmethod
    def read_generated_size():
        while True:
            try:
                size = int(input("패턴 크기 N (1 이상): ").strip())
                if size < 1:
                    raise ValueError
                return size
            except ValueError:
                print("입력 형식 오류: N은 1 이상의 정수여야 합니다.")

    @staticmethod
    def read_generated_label():
        while True:
            try:
                return normalize_label(input("판별할 패턴 (Cross/X): "))
            except ValueError:
                print("입력 형식 오류: Cross 또는 X를 입력하세요.")

    def generated(self):
        """Bonus generator: reuse generated Cross/X data as a mode-1 workload."""
        self.heading("[보너스] N×N 패턴 생성기")
        size = self.read_generated_size()
        label = self.read_generated_label()
        cross = Matrix.generate(size, "Cross")
        x_pattern = Matrix.generate(size, "X")
        pattern = cross if label == "Cross" else x_pattern
        print("\n생성된 Cross 패턴:")
        self.print_matrix(cross)
        print("\n생성된 X 패턴:")
        self.print_matrix(x_pattern)
        score_cross = MacEngine.mac(pattern, cross)
        score_x = MacEngine.mac(pattern, x_pattern)
        prediction = MacEngine.decide(score_cross, score_x)
        self.heading("[보너스] 생성 패턴 MAC 결과")
        print("입력 패턴: {}".format(label))
        print("Cross 점수: {:.17g}\nX 점수: {:.17g}".format(score_cross, score_x))
        print("MAC 점수 판정: " + prediction)
        self.print_exact_matches(MacEngine.exact_matches(
            pattern, {"Cross": cross, "X": x_pattern}))
        self.performance([size], {size: (pattern, cross)})
        return 0

    def manual(self):
        self.heading("[1] 필터 입력")
        a = self.read_matrix("필터 A")
        b = self.read_matrix("필터 B")
        print("필터 A, B 저장 완료")
        self.heading("[2] 패턴 입력")
        pattern = self.read_matrix("패턴")
        try:
            score_a = MacEngine.mac(pattern, a)
            score_b = MacEngine.mac(pattern, b)
            prediction = MacEngine.decide(score_a, score_b, "A", "B")
            elapsed_a = MacEngine.average_ms(pattern, a, self.repeats)
            elapsed_b = MacEngine.average_ms(pattern, b, self.repeats)
        except ValueError as error:
            print("연산 실패: {}".format(error))
            return 1
        self.heading("[3] MAC 결과")
        print("A 점수: {:.17g}\nB 점수: {:.17g}".format(score_a, score_b))
        print("연산 시간(두 필터 평균 시간 합/{}회): {:.6f} ms".format(
            self.repeats, elapsed_a + elapsed_b))
        print("MAC 점수 판정: " + ("판정 불가 (UNDECIDED, |A-B| < 1e-9)"
                           if prediction == "UNDECIDED" else prediction))
        self.print_exact_matches(MacEngine.exact_matches(pattern, {"A": a, "B": b}))
        self.performance([3], {3: (pattern, a)})
        return 0

    def batch(self, path):
        try:
            with path.open(encoding="utf-8-sig") as stream:
                document = json.load(stream)
            if not isinstance(document, dict):
                raise ValueError("JSON 최상위는 객체여야 합니다.")
            patterns = document.get("patterns")
            if not isinstance(patterns, dict) or not patterns:
                raise ValueError("patterns는 비어 있지 않은 객체여야 합니다.")
            analyzer = JsonAnalyzer()
            self.heading("[1] 필터 로드")
            # Invalid filter sections still produce a FAIL for every affected case.
            raw_filters = document.get("filters")
            if not isinstance(raw_filters, dict):
                print("filters 누락 또는 객체 형식 오류: 모든 케이스에서 검증합니다.")
                raw_filters = {}
            analyzer.load_filters(raw_filters)
        except (OSError, UnicodeError, ValueError) as error:
            print("파일/스키마 오류: {}".format(error))
            print("전체 테스트 수: 집계 불가 (patterns를 로드할 수 없습니다.)")
            return 1
        self.heading("[2] 패턴 분석 (라벨 정규화 적용)")
        for identifier, raw in patterns.items():
            result = analyzer.analyze_case(identifier, raw)
            print("\n--- {} ---".format(identifier))
            if result.cross is not None:
                print("Cross 점수: {:.17g}".format(result.cross))
            if result.x is not None:
                print("X 점수: {:.17g}".format(result.x))
            print("판정: {} | expected: {} | {}".format(
                result.prediction, result.expected, "PASS" if result.passed else "FAIL"))
            if result.exact_matches is not None:
                self.print_exact_matches(result.exact_matches)
            if result.reason:
                print("사유: " + result.reason)
        # Same generated binary Cross input for each size: reproducible workload,
        # independent of malformed JSON cases or unusually weighted filters.
        self.performance([3, 5, 13, 25])
        self.heading("[4] 결과 요약")
        failures = [result for result in analyzer.results if not result.passed]
        print("총 테스트: {}개\n통과: {}개\n실패: {}개".format(
            len(analyzer.results), len(analyzer.results) - len(failures), len(failures)))
        print("실패 케이스:")
        for result in failures:
            print("- {}: {}".format(result.identifier, result.reason))
        if not failures:
            print("없음")
        return 0  # A classification FAIL is a report result, not an execution error.

    def run(self, mode, path, once=False):
        print("=== Mini NPU Simulator ===")
        while True:
            if mode is None:
                print("\n[모드 선택]\n1. 사용자 입력 (3x3)\n2. data.json 분석"
                      "\n3. 보너스 패턴 생성기\n4. 종료")
                mode = input("선택: ").strip()
            if mode == "4":
                print("프로그램을 종료합니다.")
                return 0
            if mode == "1":
                status = self.manual()
            elif mode == "2":
                status = self.batch(path)
            elif mode == "3":
                status = self.generated()
            else:
                print("1, 2, 3 또는 4를 입력하세요.")
                mode = None
                continue
            if once:
                return status
            mode = None


def main():
    parser = argparse.ArgumentParser(description="Mini NPU Simulator")
    parser.add_argument("--mode", choices=["1", "2", "3", "4"], help="처음 실행할 모드, 생략하면 메뉴 표시")
    parser.add_argument("--once", action="store_true", help="선택 작업을 한 번 실행한 뒤 종료(자동 실행용)")
    parser.add_argument("--data", type=Path, default=Path(__file__).with_name("data.json"))
    parser.add_argument("--repeats", type=int, default=100, help="측정 반복 수(최소 10)")
    parser.add_argument("--no-pause", action="store_true", help="종료 시 Enter 대기 생략")
    args = parser.parse_args()
    if args.repeats < 10:
        parser.error("--repeats는 최소 10이어야 합니다.")
    try:
        return ConsoleApp(args.repeats).run(args.mode, args.data, args.once)
    except (EOFError, KeyboardInterrupt):
        print("\n입력이 종료되어 프로그램을 종료합니다.")
        return 1


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except Exception:
        print("\n예기치 않은 오류가 발생했습니다. 아래 오류 내용을 확인하세요.", file=sys.stderr)
        traceback.print_exc()
    finally:
        # Choosing 3 exits immediately; keep unexpected-error details visible.
        if exit_code != 0 and sys.stdin.isatty() and "--no-pause" not in sys.argv:
            try:
                input("\n결과 확인이 끝났으면 Enter 키를 눌러 종료하세요...")
            except (EOFError, KeyboardInterrupt):
                pass
    sys.exit(exit_code)
