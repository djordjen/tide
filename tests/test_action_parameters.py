"""Declared action parameters: the compiled shape and the compile-time gate.

An action's `parameters:` block reuses the report parameter declaration --
the same scalar types, `required`, `default` -- and lands in the compiled
action metadata under the names it was written with. The compiler's only
own rule is that a name must be a plain identifier, because each one
becomes a field on the generated MCP tool arguments model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tide import CompilationFailed, compile_project

HANDLERS = "def run(record, context, payload):\n    return record\n"

BASE_LINES = [
    "entity: demo.Thing",
    "fields:",
    "  id: {type: integer, primary_key: true}",
    "  title: {type: string, length: 40}",
    "actions:",
    "  archive:",
    "    label: Archive",
    "    unrestricted: true",
    "    execute: handlers.run",
]


def _project(tmp_path: Path, name: str, entity_lines: list[str]) -> Path:
    project = tmp_path / name
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                f"application: {{name: {name}, version: 0.1.0}}",
                "model: {paths: [models]}",
            ]
        ),
        encoding="utf-8",
    )
    (project / "handlers.py").write_text(HANDLERS, encoding="utf-8")
    (models / "entity.yaml").write_text(
        "\n".join(entity_lines) + "\n", encoding="utf-8"
    )
    return project


def test_declared_parameters_reach_the_compiled_action(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        "declared-parameters",
        [
            *BASE_LINES,
            "    parameters:",
            "      reason: {type: string, required: true}",
            "      occurred_at: {type: datetime}",
        ],
    )

    model = compile_project(project)
    action = model.entity("demo.Thing").actions["archive"]

    assert action["parameters"] == {
        "reason": {"type": "string", "required": True},
        "occurred_at": {"type": "datetime", "required": False},
    }


def test_an_action_without_the_block_compiles_to_no_parameters(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "no-parameters", BASE_LINES)

    model = compile_project(project)
    action = model.entity("demo.Thing").actions["archive"]

    assert action.get("parameters", {}) == {}


def test_a_parameter_name_must_be_a_plain_identifier(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        "bad-parameter-name",
        [
            *BASE_LINES,
            "    parameters:",
            "      bad-name: {type: string}",
        ],
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    codes = {
        diagnostic.code: diagnostic.message
        for diagnostic in caught.value.diagnostics
    }
    assert "TIDE292" in codes
    assert "'bad-name'" in codes["TIDE292"]
