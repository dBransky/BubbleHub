#!/usr/bin/env bash
set -euo pipefail

COVERAGE_DIR="${1:-.ci-artifacts/coverage}"

write_summary() {
    python3 - "$COVERAGE_DIR" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

coverage_dir = Path(sys.argv[1])
threshold = 45.0


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


def percent(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}%"


def status(value: float | None) -> str:
    if value is None:
        return "unavailable"
    return "pass" if value >= threshold else "fail"


def parse_coverage(xml_path: Path, *, production_c: bool = False) -> tuple[float | None, list[tuple[str, float | None]]]:
    if not xml_path.exists():
        return None, []

    root = ET.parse(xml_path).getroot()
    total = rate(root)
    total_valid = 0
    total_covered = 0
    files = []
    seen = set()

    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        if not filename or filename in seen:
            continue

        if production_c:
            basename = Path(filename).name
            if not basename.endswith(".c") or basename.endswith("_test.c"):
                continue
            valid, covered = line_totals(class_node)
            total_valid += valid
            total_covered += covered

        seen.add(filename)
        files.append((filename, class_rate(class_node)))

    if production_c:
        total = None if total_valid == 0 else round(total_covered / total_valid * 100, 2)

    files.sort(key=lambda item: item[0])
    return total, files


def line_totals(node: ET.Element) -> tuple[int, int]:
    lines = node.findall(".//line")
    valid = len(lines)
    covered = sum(1 for line in lines if int(line.attrib.get("hits", "0")) > 0)
    return valid, covered


def class_rate(node: ET.Element) -> float | None:
    valid, covered = line_totals(node)
    if valid == 0:
        return rate(node)
    return round(covered / valid * 100, 2)


def rate(node: ET.Element) -> float | None:
    line_rate = node.attrib.get("line-rate")
    if line_rate is not None:
        return round(float(line_rate) * 100, 2)

    lines_valid = int(node.attrib.get("lines-valid", "0"))
    lines_covered = int(node.attrib.get("lines-covered", "0"))
    if lines_valid == 0:
        return None
    return round(lines_covered / lines_valid * 100, 2)


def file_table(title: str, files: list[tuple[str, float | None]]) -> list[str]:
    lines = [f"### {title}", "", "| File | Line coverage | Status |", "|------|---------------|--------|"]
    if not files:
        lines.append("| unavailable | unavailable | unavailable |")
        return lines

    for filename, coverage in files:
        lines.append(f"| `{markdown_escape(filename)}` | {percent(coverage)} | {status(coverage)} |")
    return lines


c_total, c_files = parse_coverage(coverage_dir / "c" / "coverage.xml", production_c=True)
python_total, python_files = parse_coverage(coverage_dir / "python" / "coverage.xml")

lines = [
    "## Coverage",
    "",
    "| Component | Line coverage | Threshold | Status |",
    "|-----------|---------------|-----------|--------|",
    f"| C (`libbubble`) | {percent(c_total)} | {threshold:.0f}% | {status(c_total)} |",
    f"| Python (`bubblehub`) | {percent(python_total)} | {threshold:.0f}% | {status(python_total)} |",
    "",
]

lines.extend(file_table("C (`libbubble`) Per File", c_files))
lines.append("")
lines.extend(file_table("Python (`bubblehub`) Per File", python_files))
lines.append("")

print("\n".join(lines))
PY
}

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    write_summary >> "$GITHUB_STEP_SUMMARY"
else
    write_summary
fi
