from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def load_eval_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "eval"
        / "run_eval_verified.py"
    )
    spec = spec_from_file_location("run_eval_verified", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load run_eval_verified module")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dataset_fixture_selection(tmp_path: Path) -> None:
    module = load_eval_module()
    dataset = {
        "fixture": "conflicts.pdf",
        "cases": [
            {
                "id": "fixture-001",
                "question": "What is in the fixture?",
                "expected_behavior": "ANSWERABLE",
            }
        ],
    }
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    cases, fixture_name = module.load_dataset(dataset_path)
    assert fixture_name == "conflicts.pdf"
    assert len(cases) == 1

    fixture_path = module.resolve_fixture_path(fixture_name)
    assert fixture_path.name == "conflicts.pdf"
    assert fixture_path.exists()
