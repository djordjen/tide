from __future__ import annotations

from pathlib import Path

import pytest

from tide import CompilationFailed, compile_project


def test_explicit_table_and_column_rename_metadata_is_normalized(
    tmp_path: Path,
) -> None:
    project = _project(
        tmp_path,
        """
entity: demo.Item
storage:
  table: current_item
  migration_id: demo.item
  renamed_from: {table: previous_item}
fields:
  id: {type: integer, primary_key: true}
  code:
    type: string
    length: 40
    column: current_code
    migration_id: demo.item.code
    renamed_from: previous_code
""",
    )

    model = compile_project(project)
    entity = model.entity("demo.Item")

    assert entity.metadata["storage"] == {
        "table": "current_item",
        "migration_id": "demo.item",
        "renamed_from": {"table": "previous_item"},
    }
    assert entity.field("code").metadata["migration_id"] == "demo.item.code"
    assert entity.field("code").metadata["renamed_from"] == "previous_code"


@pytest.mark.parametrize(
    ("database", "model_yaml", "expected_code", "message"),
    [
        (
            "managed",
            """
entity: demo.Item
storage:
  migration_id: item
fields:
  id: {type: integer, primary_key: true}
""",
            "TIDE245",
            "qualified dotted identifier",
        ),
        (
            "managed",
            """
entity: demo.Item
storage:
  migration_id: demo.item
fields:
  id: {type: integer, primary_key: true, migration_id: demo.item}
""",
            "TIDE246",
            "already used",
        ),
        (
            "managed",
            """
entity: demo.Item
storage:
  table: current_item
  renamed_from: {table: previous_item}
fields:
  id: {type: integer, primary_key: true}
  code: {type: string, renamed_from: previous_code}
""",
            "TIDE247",
            "requires",
        ),
        (
            "legacy",
            """
entity: demo.Item
storage:
  table: current_item
  migration_id: demo.item
  renamed_from: {table: previous_item}
fields:
  id: {type: integer, primary_key: true}
""",
            "TIDE247",
            "managed database storage",
        ),
        (
            "managed",
            """
entity: demo.Item
fields:
  id: {type: integer, primary_key: true}
  lines:
    type: collection
    target: demo.Line
    inverse: item
    migration_id: demo.item.lines
    renamed_from: previous_lines
""",
            "TIDE247",
            "persisted fields",
        ),
    ],
)
def test_invalid_migration_metadata_is_rejected(
    tmp_path: Path,
    database: str,
    model_yaml: str,
    expected_code: str,
    message: str,
) -> None:
    project = _project(tmp_path, model_yaml, database=database)

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    assert any(
        diagnostic.code == expected_code and message in diagnostic.message
        for diagnostic in caught.value.diagnostics
    )


def test_rename_sources_cannot_collide_with_current_or_previous_names(
    tmp_path: Path,
) -> None:
    project = _project(
        tmp_path,
        """
entity: demo.Item
storage:
  table: item
  migration_id: demo.item
fields:
  id: {type: integer, primary_key: true}
  code:
    type: string
    column: current_code
    migration_id: demo.item.code
    renamed_from: id
  name:
    type: string
    migration_id: demo.item.name
    renamed_from: id
""",
    )

    with pytest.raises(CompilationFailed) as caught:
        compile_project(project)

    messages = {
        diagnostic.message
        for diagnostic in caught.value.diagnostics
        if diagnostic.code == "TIDE248"
    }
    assert any("current column" in message for message in messages)
    assert any("already claimed" in message for message in messages)


def _project(tmp_path: Path, model_yaml: str, *, database: str = "managed") -> Path:
    project = tmp_path / "migration-metadata"
    models = project / "models"
    models.mkdir(parents=True)
    (project / "tide.yaml").write_text(
        "\n".join(
            [
                'schema_version: "0.1"',
                "application: {name: Migration Metadata, version: 0.1.0}",
                f"database: {{mode: {database}}}",
                "model: {paths: [models]}",
            ]
        ),
        encoding="utf-8",
    )
    (models / "item.yaml").write_text(model_yaml.strip() + "\n", encoding="utf-8")
    return project
