from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def load_eval_module() -> ModuleType:
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
        "profile": "conflicts",
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

    cases, fixture_name, profile_name = module.load_dataset(dataset_path)
    assert fixture_name == "conflicts.pdf"
    assert profile_name == "conflicts"
    assert len(cases) == 1

    fixture_path = module.resolve_fixture_path(fixture_name)
    assert fixture_path.name == "conflicts.pdf"
    assert fixture_path.exists()

    assert module.is_conflicts_dataset(dataset_path, fixture_name, profile_name) is True


def test_conflicts_dataset_filename_detection(tmp_path: Path) -> None:
    module = load_eval_module()
    dataset = [
        {
            "id": "fixture-002",
            "question": "What is in the fixture?",
            "expected_behavior": "ANSWERABLE",
        }
    ]
    dataset_path = tmp_path / "conflicts_cases.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    cases, fixture_name, profile_name = module.load_dataset(dataset_path)
    assert fixture_name is None
    assert profile_name is None
    assert len(cases) == 1
    assert module.is_conflicts_dataset(dataset_path, fixture_name, profile_name) is True
