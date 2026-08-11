"""`tide model schema` is a published artifact, so it has to describe real YAML.

The exported schema is what an editor validates against. If it disagrees with
the loader, every file in the repository grows red squiggles for constructs
the compiler accepts, and the export stops being usable for the one thing it
exists for.
"""

from __future__ import annotations

from copy import deepcopy
import datetime
import decimal
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from tide.cli.model import SCHEMA_TYPES, schema_document


ROOT = Path(__file__).parents[1]
APPLICATIONS = sorted(path.parent for path in ROOT.glob("applications/*/tide.yaml"))


def _documents(application: Path) -> list[tuple[Path, str]]:
    """Classify every source file the way the application's own manifest does.

    Derived rather than listed: `tide.yaml` already says where models, views,
    reports, security and presentation live, and a hand-kept second copy of
    that mapping is the shape that goes stale the first time an application
    puts its views somewhere else.
    """

    manifest = yaml.safe_load((application / "tide.yaml").read_text(encoding="utf-8"))
    found: list[tuple[Path, str]] = [(application / "tide.yaml", "project")]
    for section, kind in (
        ("model", "entity"),
        ("views", "view"),
        ("reports", "report"),
        ("security", "security"),
    ):
        for relative in (manifest.get(section) or {}).get("paths", ()):
            found.extend(
                (path, kind) for path in sorted((application / relative).rglob("*.yaml"))
            )
    presentation = manifest.get("presentation") or {}
    for key in ("defaults", "formats"):
        if presentation.get(key):
            found.append((application / presentation[key], key))
    for relative in presentation.get("presets", ()):
        found.append((application / relative, "presets"))
    return found


def _jsonify(value: Any) -> Any:
    """YAML yields dates and decimals; JSON Schema validates JSON types.

    An editor reads the file as text and applies the same schema, so coercing
    here matches what it would see rather than excusing a mismatch.
    """

    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    if isinstance(value, (datetime.date, datetime.datetime, decimal.Decimal)):
        return str(value)
    return value


@pytest.mark.parametrize("application", APPLICATIONS, ids=lambda path: path.name)
def test_every_checked_in_document_validates_against_its_exported_schema(
    application: Path,
) -> None:
    failures: list[str] = []
    for path, kind in _documents(application):
        schema = SCHEMA_TYPES[kind].model_json_schema(by_alias=True)
        document = _jsonify(yaml.safe_load(path.read_text(encoding="utf-8")))
        for error in Draft202012Validator(schema).iter_errors(document):
            location = "/".join(str(part) for part in error.path) or "(root)"
            failures.append(
                f"{path.relative_to(ROOT).as_posix()} [{kind}] {location}: "
                f"{error.message[:160]}"
            )

    assert failures == []


def test_the_exported_schema_accepts_both_spellings_of_a_transition_from() -> None:
    """Both applications write `from:` as a scalar, so the check above only
    exercises that spelling. `from: [draft, held]` is the form the generator
    emits for a multi-state transition, and nothing checked in uses it yet.
    """

    validator = Draft202012Validator(SCHEMA_TYPES["entity"].model_json_schema(by_alias=True))
    source = _jsonify(
        yaml.safe_load(
            (ROOT / "applications" / "invoicing" / "models" / "sales" / "invoice.yaml")
            .read_text(encoding="utf-8")
        )
    )

    def with_from(value: Any) -> dict[str, Any]:
        # Mutating a real document rather than writing a fixture: a hand-built
        # entity here would be a second copy of the entity contract, and it
        # would be wrong the first time the contract moved.
        document = deepcopy(source)
        document["actions"]["post"]["transition"]["from"] = value
        return document

    assert source["actions"]["post"]["transition"]["from"] == "draft"
    assert list(validator.iter_errors(with_from("draft"))) == []
    assert list(validator.iter_errors(with_from(["draft", "held"]))) == []
    # And it still refuses what the loader refuses.
    assert list(validator.iter_errors(with_from(42))) != []


@pytest.mark.parametrize("kind", sorted(SCHEMA_TYPES))
def test_the_checked_in_schema_is_a_fresh_export(kind: str) -> None:
    """`schemas/` is checked in so an editor works on a clone with no build step.

    A generated file nobody regenerates is the same defect as a list nobody
    updates, and this one would fail quietly: the editor keeps validating
    against last month's contract and says nothing.
    """

    checked_in = ROOT / "schemas" / f"{kind}.json"

    assert checked_in.read_text(encoding="utf-8") == schema_document(kind), (
        f"regenerate with: tide model schema {kind} --output schemas/{kind}.json"
    )


def test_the_editor_schema_map_agrees_with_the_manifests() -> None:
    """An editor cannot read `tide.yaml`, so its globs restate where sources live.

    That restatement is the drifting shape, so it is checked against the
    manifests rather than trusted: every file the manifests classify must be
    matched by exactly the glob for the same schema, and no other file may be
    matched at all.
    """

    settings = json.loads((ROOT / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    editor: dict[Path, str] = {}
    for schema_path, patterns in settings["yaml.schemas"].items():
        kind = Path(schema_path).stem
        assert kind in SCHEMA_TYPES, f"{schema_path} names no exportable schema"
        for pattern in [patterns] if isinstance(patterns, str) else patterns:
            for path in ROOT.glob(pattern):
                editor[path.resolve()] = kind

    declared = {
        path.resolve(): kind
        for application in APPLICATIONS
        for path, kind in _documents(application)
    }

    assert {str(path.relative_to(ROOT)): kind for path, kind in sorted(editor.items())} == {
        str(path.relative_to(ROOT)): kind for path, kind in sorted(declared.items())
    }


def test_the_manifests_account_for_every_checked_in_source_file() -> None:
    """A source kind nobody classifies is a source kind nobody validates.

    The check above can only be as complete as `_documents`, so this is what
    notices a new sort of file arriving beside the ones the manifest names.
    """

    classified = {
        path.resolve() for application in APPLICATIONS for path, _ in _documents(application)
    }
    on_disk = {path.resolve() for path in ROOT.glob("applications/**/*.yaml")}

    assert sorted(str(path.relative_to(ROOT)) for path in on_disk - classified) == []
