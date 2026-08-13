"""Schema inspection, migration proposals, backups and seeding."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


from tide.compiler.compiler import compile_project
from tide.data import (
    DatabaseBackupError,
    MigrationPlanningError,
    RevisionGenerationError,
    RevisionSqlRenderingError,
    SQLAlchemyRepository,
    create_sqlite_backup,
    generate_revision,
    inspect_schema,
    render_project,
    propose_migration,
    render_revision_sql,
    verify_sqlite_backup,
)
from tide.runtime import Channel, Principal, RequestContext, TideRuntimeError
from tide.services import (
    ActionService,
    RecordsService,
)

from .output import print_json
from .storage import open_run_storage


def add_database_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Declare the `database` arguments."""

    database = commands.add_parser(
        "db",
        help="inspect, seed, back up, and verify application databases",
    )
    database_commands = database.add_subparsers(dest="database_command")
    database_check = database_commands.add_parser(
        "check",
        help="validate database connectivity, schema, durable state, and queries",
    )
    database_check.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    database_check.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        required=True,
        metavar="NAME",
        help=(
            "read the SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    database_check.set_defaults(handler=_db_check, create_schema=False)
    database_inspect = database_commands.add_parser(
        "inspect",
        help="propose legacy application metadata from an existing schema",
    )
    database_inspect.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        required=True,
        metavar="NAME",
        help=(
            "read the SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    database_inspect.add_argument(
        "--schema",
        metavar="NAME",
        help="reflect this database schema (default: the connection's default)",
    )
    database_inspect.add_argument(
        "--table",
        action="append",
        default=[],
        metavar="NAME",
        dest="tables",
        help="reflect only this table; repeat for several (default: all)",
    )
    database_inspect.add_argument(
        "--namespace",
        default="legacy",
        metavar="NAME",
        help="entity name prefix when the tables have no schema (default: legacy)",
    )
    database_inspect.add_argument(
        "--application",
        default="Legacy Application",
        metavar="NAME",
        help="application name to write into the proposed tide.yaml",
    )
    database_inspect.add_argument(
        "--output",
        metavar="DIRECTORY",
        help="write the proposal into DIRECTORY (default: print it)",
    )
    database_inspect.set_defaults(handler=_db_inspect)
    database_diff = database_commands.add_parser(
        "diff",
        help="produce a deterministic read-only schema migration proposal",
    )
    database_diff.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    database_diff.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        required=True,
        metavar="NAME",
        help=(
            "read the SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    database_diff.add_argument(
        "--json",
        action="store_true",
        help="write the complete deterministic proposal as JSON",
    )
    database_diff.add_argument(
        "--require-clean",
        action="store_true",
        help="return a failure status when any schema difference is present",
    )
    database_diff.set_defaults(handler=_db_diff)
    database_revision = database_commands.add_parser(
        "revision",
        help="render an approval-bound Alembic revision without applying it",
    )
    database_revision.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    database_revision.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        required=True,
        metavar="NAME",
        help=(
            "read the SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    database_revision.add_argument(
        "--name",
        required=True,
        help="short human revision name used in the artifact filename",
    )
    database_revision.add_argument(
        "--proposal-fingerprint",
        required=True,
        metavar="SHA256",
        help="exact proposal fingerprint from the reviewed tide db diff",
    )
    database_revision.add_argument(
        "--database-fingerprint",
        required=True,
        metavar="SHA256",
        help="exact database fingerprint from the reviewed tide db diff",
    )
    database_revision.add_argument(
        "--backup-evidence",
        required=True,
        metavar="REFERENCE",
        help="non-secret backup/restore evidence reference recorded in the manifest",
    )
    database_revision.add_argument(
        "--acknowledge",
        action="append",
        default=[],
        metavar="CHANGE_KEY",
        help="exact non-additive change key from the proposal; repeat as required",
    )
    database_revision.add_argument(
        "--down-revision",
        metavar="REVISION",
        help="existing Alembic parent revision (omit only for the first revision)",
    )
    database_revision.add_argument(
        "--output-dir",
        type=Path,
        metavar="DIRECTORY",
        help="directory inside the application (default: migrations/versions)",
    )
    database_revision.set_defaults(handler=_db_revision)
    database_render_sql = database_commands.add_parser(
        "render-sql",
        help="verify a review revision and render SQL without a database connection",
    )
    database_render_sql.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    database_render_sql.add_argument(
        "revision",
        type=Path,
        metavar="REVISION",
        help="generated revision Python file inside the application",
    )
    database_render_sql.add_argument(
        "--manifest",
        type=Path,
        metavar="PATH",
        help="revision manifest (default: REVISION.manifest.json)",
    )
    database_render_sql.add_argument(
        "--direction",
        choices=("upgrade", "downgrade"),
        default="upgrade",
        help="migration direction to render (default: upgrade)",
    )
    database_render_sql.add_argument(
        "--output",
        type=Path,
        metavar="PATH",
        help="new SQL file inside the application (default: beside revision)",
    )
    database_render_sql.set_defaults(handler=_db_render_sql)
    database_backup = database_commands.add_parser(
        "backup",
        help="create a verified, non-overwriting path-based SQLite backup",
    )
    database_backup.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    database_backup.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        required=True,
        metavar="NAME",
        help=(
            "read the SQLite SQLAlchemy URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    database_backup.add_argument(
        "--output",
        type=Path,
        required=True,
        metavar="PATH",
        help="new backup file path; an existing file is never overwritten",
    )
    database_backup.set_defaults(handler=_db_backup)
    database_verify_backup = database_commands.add_parser(
        "verify-backup",
        help="verify a SQLite backup manifest, integrity, and application schema",
    )
    database_verify_backup.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    database_verify_backup.add_argument(
        "backup",
        type=Path,
        metavar="BACKUP",
        help="SQLite backup file to verify",
    )
    database_verify_backup.add_argument(
        "--manifest",
        type=Path,
        metavar="PATH",
        help="manifest path (default: BACKUP.manifest.json)",
    )
    database_verify_backup.set_defaults(handler=_db_verify_backup)
    seed = database_commands.add_parser(
        "seed",
        help="seed an empty managed database with application-owned fake data",
    )
    seed.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    seed.add_argument(
        "--database-env",
        nargs="?",
        const="TIDE_DATABASE_URL",
        required=True,
        metavar="NAME",
        help=(
            "read the SQLAlchemy database URL from environment variable NAME "
            "(default name: TIDE_DATABASE_URL)"
        ),
    )
    seed.add_argument(
        "--count",
        action="append",
        default=[],
        metavar="NAME=NUMBER",
        help=(
            "how many records the application's fake-data provider should "
            "make of NAME; repeat for each name the provider understands, and "
            "omit a name to accept the provider's own default"
        ),
    )
    seed.add_argument("--random-seed", type=int, default=20260716)
    seed.add_argument("--locale", default="en_US")
    seed.add_argument(
        "--role",
        required=True,
        help="application role used by the secured fake-data provider",
    )
    seed.set_defaults(handler=_db_seed, create_schema=False)


def _db_check(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    storage = open_run_storage(arguments, model, purpose="Read-only check")
    if storage is None:
        return 1
    try:
        repository = storage.repository
        dialect = (
            repository.engine.dialect.name
            if isinstance(repository, SQLAlchemyRepository)
            else "memory"
        )
        mode = str(model.database["mode"])
        state = "durable" if mode == "managed" else "process-local"
        print(
            f"Database check passed: {model.name} {model.version}; "
            f"dialect={dialect}; mode={mode}; framework_state={state}."
        )
        return 0
    finally:
        storage.dispose()


def _db_inspect(arguments: argparse.Namespace) -> int:
    environment_name = arguments.database_env
    database_url = os.environ.get(environment_name)
    if not database_url:
        print(
            "Database inspection failed: environment variable "
            f"{environment_name!r} is not set",
            file=sys.stderr,
        )
        return 1

    try:
        proposal = inspect_schema(
            database_url,
            schema=arguments.schema,
            namespace=arguments.namespace,
            tables=tuple(arguments.tables),
        )
    except TideRuntimeError as error:
        print(f"Database inspection failed: {error}", file=sys.stderr)
        return 1

    documents = render_project(proposal, application=arguments.application)
    # Everything the inspector declined goes to stderr, so redirecting the
    # proposal to a file still leaves the reader holding the list of what is
    # missing from it.
    for skipped in proposal.skipped:
        print(f"Not proposed -- {skipped}", file=sys.stderr)

    if arguments.output is None:
        for path in sorted(documents):
            print(f"# {path}")
            print(documents[path])
        return 0 if proposal.entities else 1

    destination = Path(arguments.output)
    existing = sorted(
        path for path in documents if (destination / path).exists()
    )
    if existing:
        print(
            "Database inspection failed: refusing to overwrite "
            f"{', '.join(existing)} in {destination}",
            file=sys.stderr,
        )
        return 1
    for path, text in documents.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(
        f"Proposed {len(proposal.entities)} entit"
        f"{'y' if len(proposal.entities) == 1 else 'ies'} in {destination}; "
        f"{len(proposal.skipped)} object(s) not proposed. "
        "Review the files, then: tide model validate "
        f"{destination}"
    )
    return 0 if proposal.entities else 1


def _db_diff(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    environment_name = arguments.database_env
    database_url = os.environ.get(environment_name)
    if not database_url:
        print(
            "Database diff failed: environment variable "
            f"{environment_name!r} is not set",
            file=sys.stderr,
        )
        return 1
    try:
        proposal = propose_migration(model, database_url)
    except MigrationPlanningError as error:
        print(f"Database diff failed: {error}", file=sys.stderr)
        return 1

    if arguments.json:
        print_json(proposal.as_dict())
    else:
        label = (
            "Migration proposal"
            if proposal.kind == "migration_proposal"
            else "Legacy compatibility report"
        )
        print(
            f"{label}: {proposal.application} {proposal.application_version}; "
            f"dialect={proposal.dialect}; mode={proposal.database_mode}."
        )
        print(f"Fingerprint: {proposal.fingerprint}")
        if proposal.database_fingerprint is not None:
            print(f"Database fingerprint: {proposal.database_fingerprint}")
        print("Writes performed: no. Rename inference performed: no.")
        revision_availability = (
            "yes (render-only)"
            if proposal.kind == "migration_proposal"
            else "no"
        )
        print(
            f"Revision generation available: {revision_availability}. "
            "Migration apply available: no."
        )
        if proposal.clean:
            print("No schema differences detected.")
        else:
            for change in proposal.changes:
                transition = ""
                if change.current is not None or change.desired is not None:
                    transition = (
                        f" (current={change.current or '-'}; "
                        f"desired={change.desired or '-'})"
                    )
                print(
                    f"[{change.safety.upper()}] {change.operation} "
                    f"{change.object_name}{transition}: {change.reason}"
                )
            counts = proposal.as_dict()["counts"]
            print(
                "Changes: "
                + ", ".join(f"{name}={count}" for name, count in counts.items())
                + "."
            )
        if proposal.requires_backup:
            print("A verified restorable backup is required before any future apply.")
        if proposal.revision_blocked and proposal.changes:
            print(
                "Revision rendering is blocked because at least one operation is "
                "unsupported by the initial renderer."
            )
        elif proposal.required_acknowledgements:
            print(
                "Revision rendering requires exact acknowledgement of every "
                "listed non-additive change key."
            )
    return 1 if arguments.require_clean and not proposal.clean else 0


def _db_revision(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    environment_name = arguments.database_env
    database_url = os.environ.get(environment_name)
    if not database_url:
        print(
            "Database revision failed: environment variable "
            f"{environment_name!r} is not set",
            file=sys.stderr,
        )
        return 1
    try:
        artifact = generate_revision(
            model,
            database_url,
            name=arguments.name,
            proposal_fingerprint=arguments.proposal_fingerprint,
            database_fingerprint=arguments.database_fingerprint,
            backup_evidence=arguments.backup_evidence,
            acknowledgements=arguments.acknowledge,
            down_revision=arguments.down_revision,
            output_dir=arguments.output_dir,
        )
    except (MigrationPlanningError, RevisionGenerationError) as error:
        print(f"Database revision failed: {error}", file=sys.stderr)
        return 1
    print(
        "Review revision rendered: "
        f"revision={artifact.revision}; operations={artifact.operation_count}; "
        f"sha256={artifact.sha256}."
    )
    print(f"Script: {artifact.path}")
    print(f"Manifest: {artifact.manifest_path}")
    print("Database writes performed: no. Migration apply available: no.")
    return 0


def _db_render_sql(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    try:
        artifact = render_revision_sql(
            model,
            arguments.revision,
            direction=arguments.direction,
            manifest_path=arguments.manifest,
            output=arguments.output,
        )
    except RevisionSqlRenderingError as error:
        print(f"Offline SQL rendering failed: {error}", file=sys.stderr)
        return 1
    print(
        "Offline SQL review rendered: "
        f"revision={artifact.revision}; direction={artifact.direction}; "
        f"dialect={artifact.dialect}; operations={artifact.operation_count}; "
        f"sha256={artifact.sha256}."
    )
    print(f"SQL: {artifact.path}")
    print(f"Manifest: {artifact.manifest_path}")
    print("Database connection used: no. Database writes performed: no.")
    print("Migration apply available: no.")
    return 0


def _db_backup(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    environment_name = arguments.database_env
    database_url = os.environ.get(environment_name)
    if not database_url:
        print(
            "Database backup failed: environment variable "
            f"{environment_name!r} is not set",
            file=sys.stderr,
        )
        return 1
    try:
        artifact = create_sqlite_backup(model, database_url, arguments.output)
    except DatabaseBackupError as error:
        print(f"Database backup failed: {error}", file=sys.stderr)
        return 1
    print(
        "Database backup complete: "
        f"{artifact.path} ({artifact.size_bytes} bytes; sha256={artifact.sha256})."
    )
    print(f"Manifest: {artifact.manifest_path}")
    return 0


def _db_verify_backup(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    try:
        verification = verify_sqlite_backup(
            model,
            arguments.backup,
            manifest=arguments.manifest,
        )
    except DatabaseBackupError as error:
        print(f"Database backup verification failed: {error}", file=sys.stderr)
        return 1
    print(
        "Database backup verification passed: "
        f"{verification.application} {verification.application_version}; "
        f"dialect=sqlite; mode={verification.database_mode}; "
        f"bytes={verification.size_bytes}; sha256={verification.sha256}."
    )
    return 0


def _seed_counts(values: list[str]) -> dict[str, int]:
    """Read `--count NAME=NUMBER` options into what the provider is asked for.

    The framework used to spell these as `--customers`, `--products` and
    `--invoices`: three of the sample application's collections, in the CLI of
    a framework that is supposed to know nothing about invoicing, and useless
    to anyone whose application counts something else. The decision of
    2026-07-16 already put fake-data profiles in application-owned providers;
    the flags had stayed behind.

    Nothing here knows which names are meaningful -- that is the provider's
    business -- so an unrecognised one is not an error at this layer. What is
    checked is the shape, and a repeated name, because a second spelling of
    the same thing silently overriding the first is how a typo becomes a
    surprise.
    """

    counts: dict[str, int] = {}
    for value in values:
        name, separator, number = value.partition("=")
        name = name.strip()
        if not separator or not name:
            raise ValueError(f"count {value!r} is not NAME=NUMBER")
        if name in counts:
            raise ValueError(f"count {name!r} was given more than once")
        try:
            counts[name] = int(number)
        except ValueError as error:
            raise ValueError(f"count for {name!r} is not a number: {number!r}") from error
        if counts[name] < 0:
            raise ValueError(f"count for {name!r} must not be negative")
    return counts


def _db_seed(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    if str(model.database["mode"]) != "managed":
        print(
            "Fake-data seeding is available only for managed databases; "
            "legacy schemas are never seeded automatically.",
            file=sys.stderr,
        )
        return 1
    try:
        counts = _seed_counts(arguments.count)
    except ValueError as error:
        print(f"Fake-data seeding failed: {error}", file=sys.stderr)
        return 1

    storage = open_run_storage(arguments, model, purpose="Fake-data")
    if storage is None:
        return 1
    try:
        existing = [
            entity_name
            for entity_name in model.entities
            if storage.repository.all(entity_name)
        ]
        if existing:
            print(
                "Fake-data seeding refused because the database is not empty; "
                "entities with records: " + ", ".join(existing),
                file=sys.stderr,
            )
            return 1

        records = RecordsService(
            model,
            storage.repository,
            cursor_store=storage.cursor_store,
            audit_store=storage.execution_store,
        )
        actions = ActionService(
            model,
            records,
            execution_store=storage.execution_store,
        )
        from tide.tui.application_runtime import (
            ApplicationRuntimeError,
            configure_application_runtime,
        )

        configure_application_runtime(model, records, actions)
        from tide.development import FakeDataError, seed_fake_data

        context = RequestContext(
            principal=Principal(
                "development:seed",
                roles=frozenset({arguments.role}),
            ),
            channel=Channel.SYSTEM,
        )
        seeded = seed_fake_data(
            model,
            records,
            actions,
            context,
            counts=counts,
            random_seed=arguments.random_seed,
            locale=arguments.locale,
        )
    except (ApplicationRuntimeError, FakeDataError, TideRuntimeError, ValueError) as error:
        print(f"Fake-data seeding failed: {error}", file=sys.stderr)
        return 1
    finally:
        storage.dispose()

    summary = ", ".join(f"{name}={count}" for name, count in seeded.items())
    print(
        f"Fake-data seeding complete ({summary}; seed={arguments.random_seed})."
    )
    return 0
