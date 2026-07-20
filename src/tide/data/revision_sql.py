"""Verified offline SQL rendering for TIDE review revisions."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
from typing import Any, Literal, Mapping

import sqlalchemy as sa
from sqlalchemy.dialects import mssql, sqlite

from tide.compiler.normalized import ApplicationModel
from tide.data.revisions import REVISION_FORMAT, REVISION_FORMAT_VERSION
from tide.runtime.errors import TideRuntimeError


SQL_REVIEW_FORMAT = "tide.offline-sql-review"
SQL_REVIEW_FORMAT_VERSION = 1
MAX_REVISION_BYTES = 2_000_000
MAX_MANIFEST_BYTES = 2_000_000
MAX_OPERATIONS = 10_000
MAX_OPERATION_BYTES = 100_000
RevisionDirection = Literal["upgrade", "downgrade"]

_DOWN_REVISION = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_REVISION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")

_OP_CALLS = frozenset(
    {
        "add_column",
        "alter_column",
        "create_index",
        "create_table",
        "drop_column",
        "drop_index",
        "drop_table",
        "rename_table",
    }
)
_UPGRADE_CALLS = frozenset(
    {
        "add_column",
        "alter_column",
        "create_index",
        "create_table",
        "rename_table",
    }
)
_DOWNGRADE_CALLS = frozenset(
    {
        "alter_column",
        "drop_column",
        "drop_index",
        "drop_table",
        "rename_table",
    }
)
_SA_TYPE_CALLS = frozenset(
    {
        "BigInteger",
        "Boolean",
        "Date",
        "DateTime",
        "Integer",
        "Numeric",
        "String",
        "Text",
        "Unicode",
        "UnicodeText",
    }
)
_SA_CALLS = frozenset(
    {
        "BigInteger",
        "Boolean",
        "Column",
        "Date",
        "DateTime",
        "ForeignKeyConstraint",
        "Identity",
        "Integer",
        "Numeric",
        "PrimaryKeyConstraint",
        "String",
        "Text",
        "Unicode",
        "UnicodeText",
        "UniqueConstraint",
        "text",
    }
)


class RevisionSqlRenderingError(TideRuntimeError):
    """A revision could not be verified and rendered safely offline."""

    code = "revision_sql_rendering_error"


@dataclass(frozen=True, slots=True)
class RevisionSqlArtifact:
    path: Path
    manifest_path: Path
    revision: str
    dialect: str
    direction: RevisionDirection
    sha256: str
    operation_count: int


@dataclass(frozen=True, slots=True)
class _VerifiedRevision:
    revision: str
    dialect: Literal["mssql", "sqlite"]
    proposal_fingerprint: str
    database_fingerprint: str
    script_path: Path
    script_sha256: str
    manifest_path: Path
    manifest_sha256: str
    operations: Mapping[str, tuple[str, ...]]


def render_revision_sql(
    model: ApplicationModel,
    revision: str | Path,
    *,
    direction: RevisionDirection = "upgrade",
    manifest_path: str | Path | None = None,
    output: str | Path | None = None,
) -> RevisionSqlArtifact:
    """Render verified migration operations with Alembic's offline context."""

    if direction not in {"upgrade", "downgrade"}:
        raise RevisionSqlRenderingError("direction must be 'upgrade' or 'downgrade'")
    verified = _verify_revision(model, revision, manifest_path=manifest_path)
    selected = verified.operations[direction]
    sql = _render_offline(
        selected,
        dialect=verified.dialect,
        direction=direction,
    )
    header = "\n".join(
        [
            "-- TIDE Framework offline migration review SQL",
            f"-- revision: {verified.revision}",
            f"-- direction: {direction}",
            f"-- dialect: {verified.dialect}",
            f"-- proposal fingerprint: {verified.proposal_fingerprint}",
            f"-- database fingerprint: {verified.database_fingerprint}",
            "-- database connection used: no",
            "-- database writes performed: no",
            "-- Review this SQL and its manifest; TIDE cannot apply it.",
            "",
        ]
    )
    content = header + sql.rstrip() + "\n"
    output_path = _output_path(model, verified, direction, output)
    output_manifest = output_path.with_name(f"{output_path.name}.manifest.json")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document = {
        "format": SQL_REVIEW_FORMAT,
        "format_version": SQL_REVIEW_FORMAT_VERSION,
        "application": {
            "name": model.name,
            "version": model.version,
            "schema_version": model.schema_version,
        },
        "revision": verified.revision,
        "direction": direction,
        "dialect": verified.dialect,
        "proposal_fingerprint": verified.proposal_fingerprint,
        "database_fingerprint": verified.database_fingerprint,
        "operation_count": len(selected),
        "source": {
            "revision_filename": verified.script_path.name,
            "revision_sha256": verified.script_sha256,
            "manifest_filename": verified.manifest_path.name,
            "manifest_sha256": verified.manifest_sha256,
        },
        "sql": {
            "filename": output_path.name,
            "sha256": digest,
        },
        "database_connection_used": False,
        "database_writes_performed": False,
        "migration_apply_available": False,
    }
    _write_output_pair(output_path, content, output_manifest, document)
    return RevisionSqlArtifact(
        path=output_path,
        manifest_path=output_manifest,
        revision=verified.revision,
        dialect=verified.dialect,
        direction=direction,
        sha256=digest,
        operation_count=len(selected),
    )


def _verify_revision(
    model: ApplicationModel,
    revision: str | Path,
    *,
    manifest_path: str | Path | None,
) -> _VerifiedRevision:
    root = model.project_root.resolve()
    script_path = _input_path(root, revision, "revision")
    manifest = (
        script_path.with_name(f"{script_path.name}.manifest.json")
        if manifest_path is None
        else _input_path(root, manifest_path, "revision manifest")
    )
    if manifest_path is None:
        manifest = _require_inside(root, manifest, "revision manifest")
    script_bytes = _read_bounded(script_path, MAX_REVISION_BYTES, "revision")
    manifest_bytes = _read_bounded(
        manifest,
        MAX_MANIFEST_BYTES,
        "revision manifest",
    )
    try:
        document = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RevisionSqlRenderingError(
            "revision manifest is not valid UTF-8 JSON"
        ) from error
    if not isinstance(document, dict):
        raise RevisionSqlRenderingError("revision manifest root must be an object")
    if document.get("format") != REVISION_FORMAT:
        raise RevisionSqlRenderingError("revision manifest format is unsupported")
    if document.get("format_version") != REVISION_FORMAT_VERSION:
        raise RevisionSqlRenderingError("revision manifest version is unsupported")
    _validate_application(model, document.get("application"))
    dialect = document.get("dialect")
    if dialect not in {"mssql", "sqlite"}:
        raise RevisionSqlRenderingError(
            "offline SQL currently supports only mssql and sqlite revision dialects"
        )
    revision_id = document.get("revision")
    if not isinstance(revision_id, str) or not revision_id:
        raise RevisionSqlRenderingError("revision manifest identifier is invalid")
    proposal_fingerprint = _fingerprint(document, "proposal_fingerprint")
    database_fingerprint = _fingerprint(document, "database_fingerprint")
    if revision_id != proposal_fingerprint[:12]:
        raise RevisionSqlRenderingError(
            "revision identifier is not bound to the proposal fingerprint"
        )
    change_keys = _validate_review_metadata(document)
    operation_count = document.get("operation_count")
    if (
        not isinstance(operation_count, int)
        or isinstance(operation_count, bool)
        or operation_count != len(change_keys)
        or operation_count > MAX_OPERATIONS
    ):
        raise RevisionSqlRenderingError("revision change-key contract is invalid")
    if document.get("database_writes_performed") is not False:
        raise RevisionSqlRenderingError("revision manifest database-write claim is invalid")
    if document.get("migration_apply_available") is not False:
        raise RevisionSqlRenderingError("revision manifest apply claim is invalid")
    script = document.get("script")
    if (
        not isinstance(script, dict)
        or set(script) != {"filename", "sha256"}
        or script.get("filename") != script_path.name
    ):
        raise RevisionSqlRenderingError("revision filename does not match its manifest")
    script_sha256 = hashlib.sha256(script_bytes).hexdigest()
    if script.get("sha256") != script_sha256:
        raise RevisionSqlRenderingError("revision SHA-256 does not match its manifest")
    operations = _operation_mapping(document.get("operations"))
    _validate_revision_source(
        script_bytes,
        document,
        operations,
    )
    return _VerifiedRevision(
        revision=revision_id,
        dialect=dialect,
        proposal_fingerprint=proposal_fingerprint,
        database_fingerprint=database_fingerprint,
        script_path=script_path,
        script_sha256=script_sha256,
        manifest_path=manifest,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        operations=operations,
    )


def _validate_review_metadata(document: Mapping[str, Any]) -> tuple[str, ...]:
    name = document.get("name")
    if not isinstance(name, str) or _REVISION_NAME.fullmatch(name) is None:
        raise RevisionSqlRenderingError("revision name is invalid")
    down_revision = document.get("down_revision")
    if down_revision is not None and (
        not isinstance(down_revision, str)
        or _DOWN_REVISION.fullmatch(down_revision) is None
    ):
        raise RevisionSqlRenderingError("parent revision identifier is invalid")
    backup_evidence = document.get("backup_evidence")
    if (
        not isinstance(backup_evidence, str)
        or not backup_evidence
        or len(backup_evidence) > 240
        or backup_evidence != backup_evidence.strip()
        or not backup_evidence.isprintable()
    ):
        raise RevisionSqlRenderingError("revision backup evidence is invalid")
    change_keys = document.get("change_keys")
    if (
        not isinstance(change_keys, list)
        or not change_keys
        or not all(
            isinstance(value, str)
            and value
            and value == value.strip()
            and value.isprintable()
            for value in change_keys
        )
        or len(set(change_keys)) != len(change_keys)
    ):
        raise RevisionSqlRenderingError("revision change-key contract is invalid")
    acknowledgements = document.get("acknowledged_change_keys")
    if (
        not isinstance(acknowledgements, list)
        or not all(isinstance(value, str) for value in acknowledgements)
        or len(set(acknowledgements)) != len(acknowledgements)
        or not set(acknowledgements).issubset(change_keys)
    ):
        raise RevisionSqlRenderingError("revision acknowledgement contract is invalid")
    return tuple(change_keys)


def _operation_mapping(value: Any) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(value, dict) or set(value) != {"upgrade", "downgrade"}:
        raise RevisionSqlRenderingError("revision operations are missing or malformed")
    result: dict[str, tuple[str, ...]] = {}
    for direction in ("upgrade", "downgrade"):
        operations = value.get(direction)
        if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
            raise RevisionSqlRenderingError(
                f"revision {direction} operation list is invalid or too large"
            )
        normalized: list[str] = []
        for operation in operations:
            if (
                not isinstance(operation, str)
                or not operation
                or len(operation.encode("utf-8")) > MAX_OPERATION_BYTES
            ):
                raise RevisionSqlRenderingError(
                    f"revision {direction} contains an invalid operation"
                )
            expression = _operation_expression(operation)
            _validate_operation_expression(expression)
            operation_name = _root_operation_name(expression)
            allowed = _UPGRADE_CALLS if direction == "upgrade" else _DOWNGRADE_CALLS
            if operation_name not in allowed:
                raise RevisionSqlRenderingError(
                    f"revision {direction} operation {operation_name!r} is not allowed"
                )
            normalized.append(operation)
        result[direction] = tuple(normalized)
    if not result["upgrade"]:
        raise RevisionSqlRenderingError("revision must contain at least one upgrade operation")
    return result


def _validate_revision_source(
    source: bytes,
    document: Mapping[str, Any],
    operations: Mapping[str, tuple[str, ...]],
) -> None:
    try:
        module = ast.parse(source.decode("utf-8"), mode="exec")
    except (UnicodeDecodeError, SyntaxError) as error:
        raise RevisionSqlRenderingError("revision is not valid UTF-8 Python") from error
    imports: set[str] = set()
    assignments: dict[str, Any] = {}
    functions: dict[str, ast.FunctionDef] = {}
    for index, node in enumerate(module.body):
        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "alembic"
            and node.level == 0
            and len(node.names) == 1
            and node.names[0].name == "op"
            and node.names[0].asname is None
        ):
            imports.add("op")
            continue
        if (
            isinstance(node, ast.Import)
            and len(node.names) == 1
            and node.names[0].name == "sqlalchemy"
            and node.names[0].asname == "sa"
        ):
            imports.add("sa")
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name) or target.id not in {
                "revision",
                "down_revision",
                "branch_labels",
                "depends_on",
                "tide_metadata",
            }:
                raise RevisionSqlRenderingError(
                    "revision contains an unapproved assignment"
                )
            try:
                assignments[target.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError) as error:
                raise RevisionSqlRenderingError(
                    "revision metadata assignment is not literal"
                ) from error
            continue
        if isinstance(node, ast.FunctionDef) and node.name in {"upgrade", "downgrade"}:
            if node.name in functions:
                raise RevisionSqlRenderingError("revision repeats a migration function")
            functions[node.name] = node
            continue
        raise RevisionSqlRenderingError("revision contains unapproved executable code")
    if imports != {"op", "sa"}:
        raise RevisionSqlRenderingError("revision imports do not match the safe template")
    if set(functions) != {"upgrade", "downgrade"}:
        raise RevisionSqlRenderingError("revision migration functions are incomplete")
    if set(assignments) != {
        "revision",
        "down_revision",
        "branch_labels",
        "depends_on",
        "tide_metadata",
    }:
        raise RevisionSqlRenderingError("revision metadata assignments are incomplete")
    if assignments.get("revision") != document.get("revision"):
        raise RevisionSqlRenderingError("revision identifier does not match its manifest")
    if assignments.get("down_revision") != document.get("down_revision"):
        raise RevisionSqlRenderingError("parent revision does not match its manifest")
    if assignments.get("branch_labels") is not None or assignments.get("depends_on") is not None:
        raise RevisionSqlRenderingError("branch labels and dependencies are unsupported")
    _validate_tide_metadata(assignments.get("tide_metadata"), document)
    for direction, function in functions.items():
        _validate_function(function, operations[direction])


def _validate_function(function: ast.FunctionDef, operations: tuple[str, ...]) -> None:
    arguments = function.args
    if (
        arguments.posonlyargs
        or arguments.args
        or arguments.kwonlyargs
        or arguments.vararg is not None
        or arguments.kwarg is not None
        or function.decorator_list
        or not (
            isinstance(function.returns, ast.Constant)
            and function.returns.value is None
        )
    ):
        raise RevisionSqlRenderingError("revision migration function shape is unsafe")
    body = function.body
    if not operations:
        if len(body) != 1 or not isinstance(body[0], ast.Pass):
            raise RevisionSqlRenderingError("empty revision function must contain pass")
        return
    if len(body) != len(operations):
        raise RevisionSqlRenderingError("revision operations do not match its manifest")
    for statement, expected_text in zip(body, operations, strict=True):
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            raise RevisionSqlRenderingError(
                "revision function contains a non-operation statement"
            )
        expected = _operation_expression(expected_text)
        if ast.dump(statement.value, include_attributes=False) != ast.dump(
            expected,
            include_attributes=False,
        ):
            raise RevisionSqlRenderingError("revision operations do not match its manifest")


def _validate_tide_metadata(value: Any, document: Mapping[str, Any]) -> None:
    if not isinstance(value, dict):
        raise RevisionSqlRenderingError("revision TIDE metadata is missing")
    application = document.get("application")
    expected = {
        "application": application.get("name") if isinstance(application, dict) else None,
        "application_version": (
            application.get("version") if isinstance(application, dict) else None
        ),
        "schema_version": (
            application.get("schema_version") if isinstance(application, dict) else None
        ),
        "dialect": document.get("dialect"),
        "proposal_fingerprint": document.get("proposal_fingerprint"),
        "database_fingerprint": document.get("database_fingerprint"),
        "backup_evidence": document.get("backup_evidence"),
        "acknowledged_change_keys": document.get("acknowledged_change_keys"),
        "change_keys": document.get("change_keys"),
        "render_only": True,
    }
    if value != expected:
        raise RevisionSqlRenderingError(
            "revision embedded metadata does not match its manifest"
        )


def _operation_expression(value: str) -> ast.Call:
    try:
        expression = ast.parse(value, mode="eval").body
    except SyntaxError as error:
        raise RevisionSqlRenderingError("revision operation is not valid Python") from error
    if not isinstance(expression, ast.Call):
        raise RevisionSqlRenderingError("revision operation must be one function call")
    if sum(1 for _node in ast.walk(expression)) > 500:
        raise RevisionSqlRenderingError("revision operation is structurally too complex")
    return expression


def _validate_operation_expression(expression: ast.AST) -> None:
    if isinstance(expression, ast.Call):
        _validate_call_target(expression.func)
        if (
            isinstance(expression.func, ast.Attribute)
            and isinstance(expression.func.value, ast.Name)
            and expression.func.value.id == "sa"
            and expression.func.attr == "text"
        ):
            _validate_text_call(expression)
        for argument in expression.args:
            _validate_operation_expression(argument)
        for keyword in expression.keywords:
            if keyword.arg is None or keyword.arg.startswith("_"):
                raise RevisionSqlRenderingError("revision operation keyword is unsafe")
            _validate_operation_expression(keyword.value)
        return
    if isinstance(expression, (ast.List, ast.Tuple)):
        for element in expression.elts:
            _validate_operation_expression(element)
        return
    if isinstance(expression, ast.Constant) and (
        expression.value is None
        or isinstance(expression.value, (str, int, bool))
    ):
        return
    raise RevisionSqlRenderingError("revision operation contains an unsafe expression")


def _validate_call_target(function: ast.AST) -> None:
    if not isinstance(function, ast.Attribute) or function.attr.startswith("_"):
        raise RevisionSqlRenderingError("revision operation call target is unsafe")
    if isinstance(function.value, ast.Name):
        if function.value.id == "op" and function.attr in _OP_CALLS:
            return
        if function.value.id == "sa" and function.attr in _SA_CALLS:
            return
    if (
        function.attr == "with_variant"
        and isinstance(function.value, ast.Call)
        and isinstance(function.value.func, ast.Attribute)
        and isinstance(function.value.func.value, ast.Name)
        and function.value.func.value.id == "sa"
        and function.value.func.attr in _SA_TYPE_CALLS
    ):
        _validate_operation_expression(function.value)
        return
    raise RevisionSqlRenderingError("revision operation call is not allow-listed")


def _root_operation_name(expression: ast.Call) -> str:
    function = expression.func
    if (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "op"
        and function.attr in _OP_CALLS
    ):
        return function.attr
    raise RevisionSqlRenderingError("revision operation root must be an Alembic call")


def _validate_text_call(expression: ast.Call) -> None:
    if (
        len(expression.args) != 1
        or expression.keywords
        or not isinstance(expression.args[0], ast.Constant)
        or not isinstance(expression.args[0].value, str)
    ):
        raise RevisionSqlRenderingError("SQL text expression shape is unsafe")
    value = expression.args[0].value
    if any(token in value for token in (";", "--", "/*", "*/", "\r", "\n")):
        raise RevisionSqlRenderingError("SQL text expression contains unsafe separators")


def _render_offline(
    operations_source: tuple[str, ...],
    *,
    dialect: Literal["mssql", "sqlite"],
    direction: RevisionDirection,
) -> str:
    try:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
    except ModuleNotFoundError as error:
        raise RevisionSqlRenderingError(
            "offline SQL rendering requires tide-framework[migration]"
        ) from error
    expressions = [_operation_expression(value) for value in operations_source]
    if dialect == "sqlite":
        for expression in expressions:
            if _sqlite_requires_batch(expression):
                raise RevisionSqlRenderingError(
                    "SQLite nullability changes require a future reviewed batch-table "
                    "rebuild contract"
                )
    buffer = io.StringIO()
    dialect_instance = mssql.dialect() if dialect == "mssql" else sqlite.dialect()
    context = MigrationContext.configure(
        dialect=dialect_instance,
        opts={
            "as_sql": True,
            "literal_binds": True,
            "output_buffer": buffer,
        },
    )
    alembic_operations = Operations(context)
    namespace = {"__builtins__": {}, "op": alembic_operations, "sa": sa}
    try:
        with context.begin_transaction():
            for expression in expressions:
                compiled = compile(ast.Expression(expression), "<tide-revision>", "eval")
                eval(compiled, namespace, {})  # noqa: S307 - strict AST allow-list above
    except RevisionSqlRenderingError:
        raise
    except Exception as error:
        raise RevisionSqlRenderingError(
            f"Alembic offline {direction} rendering failed safely "
            f"({type(error).__name__})"
        ) from error
    return buffer.getvalue()


def _sqlite_requires_batch(expression: ast.Call) -> bool:
    function = expression.func
    if not (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "op"
        and function.attr == "alter_column"
    ):
        return False
    return any(keyword.arg == "nullable" for keyword in expression.keywords)


def _validate_application(model: ApplicationModel, value: Any) -> None:
    expected = {
        "name": model.name,
        "version": model.version,
        "schema_version": model.schema_version,
    }
    if value != expected:
        raise RevisionSqlRenderingError(
            "revision manifest targets a different compiled application"
        )


def _fingerprint(document: Mapping[str, Any], key: str) -> str:
    value = document.get(key)
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RevisionSqlRenderingError(f"revision manifest {key} is invalid")
    return value


def _input_path(root: Path, value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return _require_inside(root, path, label)


def _require_inside(root: Path, value: Path, label: str) -> Path:
    resolved = value.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RevisionSqlRenderingError(
            f"{label} must remain inside the application root"
        ) from error
    if not resolved.is_file():
        raise RevisionSqlRenderingError(f"{label} does not exist")
    return resolved


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        if path.stat().st_size > limit:
            raise RevisionSqlRenderingError(f"{label} exceeds the safe size limit")
        return path.read_bytes()
    except RevisionSqlRenderingError:
        raise
    except OSError as error:
        raise RevisionSqlRenderingError(
            f"{label} could not be read ({type(error).__name__})"
        ) from error


def _output_path(
    model: ApplicationModel,
    verified: _VerifiedRevision,
    direction: RevisionDirection,
    output: str | Path | None,
) -> Path:
    root = model.project_root.resolve()
    candidate = (
        verified.script_path.with_name(
            f"{verified.script_path.stem}.{direction}.{verified.dialect}.sql"
        )
        if output is None
        else Path(output)
    )
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RevisionSqlRenderingError(
            "SQL output must remain inside the application root"
        ) from error
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RevisionSqlRenderingError(
            f"SQL output directory could not be created ({type(error).__name__})"
        ) from error
    if resolved in {verified.script_path, verified.manifest_path}:
        raise RevisionSqlRenderingError("SQL output cannot replace a source artifact")
    return resolved


def _write_output_pair(
    path: Path,
    sql: str,
    manifest_path: Path,
    manifest: Mapping[str, Any],
) -> None:
    lock = path.with_name(f".{path.name}.render.lock")
    reserved = False
    created: list[Path] = []
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(descriptor)
        reserved = True
        _write_new(path, sql)
        created.append(path)
        _write_new(
            manifest_path,
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        created.append(manifest_path)
    except FileExistsError as error:
        for item in created:
            item.unlink(missing_ok=True)
        raise RevisionSqlRenderingError(
            "offline SQL output already exists or is being rendered; files are never "
            "overwritten"
        ) from error
    except OSError as error:
        path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise RevisionSqlRenderingError(
            f"offline SQL artifacts could not be written ({type(error).__name__})"
        ) from error
    finally:
        if reserved:
            lock.unlink(missing_ok=True)


def _write_new(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
