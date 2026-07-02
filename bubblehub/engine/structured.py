from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def load_example_schema(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("structure example must be a JSON object")
    return data


def build_structured_messages(example: dict[str, Any], text: str) -> list[dict[str, str]]:
    schema = json.dumps(example, indent=2, sort_keys=True)
    return [
        {
            "role": "system",
            "content": (
                "Return only valid JSON matching the shape and value types of this example. "
                "Do not include markdown fences or explanation.\n\n"
                f"{schema}"
            ),
        },
        {"role": "user", "content": text},
    ]


def build_repair_messages(example: dict[str, Any], original_text: str, invalid_output: str) -> list[dict[str, str]]:
    schema = json.dumps(example, indent=2, sort_keys=True)
    return [
        {
            "role": "system",
            "content": (f"Repair the assistant output into valid JSON only. It must match this example structure and value types:\n\n{schema}"),
        },
        {
            "role": "user",
            "content": f"Original request:\n{original_text}\n\nInvalid output:\n{invalid_output}",
        },
    ]


def parse_json_output(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("model returned JSON, but not an object")
    return data
