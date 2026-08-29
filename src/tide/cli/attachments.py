"""Reconciling the two stores an attachment lives in.

Rows are in the database and bytes are on a filesystem, and nothing can
make a write to one part of a transaction in the other. That is a
deliberate design rather than an oversight, and this is the command that
makes it operable: it asks both stores what they hold and reports every way
they disagree.

Three directions, because they mean different things. A row whose file is
missing is a document somebody will ask for and not receive. Bytes no row
names are disk, and `--sweep` reclaims them. A digest that no longer
matches is the one that says something happened outside TIDE -- a restore
from the wrong backup, a hand-edited tree -- and it is never swept, because
the bytes are still the only copy of whatever they now are.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import sys

from tide.compiler.compiler import compile_project
from tide.data.attachment_files import FilesystemAttachmentBytes
from tide.runtime import TideRuntimeError
from tide.services.attachments import AttachmentService, file_fields

from .storage import open_run_storage


def add_attachment_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Declare the `attachments` arguments."""

    attachments = commands.add_parser(
        "attachments",
        help="reconcile stored files against the rows that name them",
    )
    attachment_commands = attachments.add_subparsers(dest="attachments_command")
    check = attachment_commands.add_parser(
        "check",
        help="report rows without files, files without rows, and changed files",
    )
    check.add_argument(
        "project",
        nargs="?",
        default=".",
        metavar="APPLICATION",
        help="application root or tide.yaml (default: current directory)",
    )
    check.add_argument(
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
    check.add_argument(
        "--attachments-root",
        required=True,
        metavar="DIRECTORY",
        help="the directory this application's uploads are kept in",
    )
    check.add_argument(
        "--sweep",
        action="store_true",
        help=(
            "also forget files no record has referred to for longer than the "
            "grace period, and delete their bytes"
        ),
    )
    check.add_argument(
        "--grace",
        type=int,
        default=24,
        metavar="HOURS",
        help=(
            "how long a released or abandoned upload is kept before --sweep "
            "reclaims it (default: 24)"
        ),
    )
    check.set_defaults(handler=_attachments_check, create_schema=False)


def _attachments_check(arguments: argparse.Namespace) -> int:
    model = compile_project(arguments.project)
    declaring = [name for name, entity in model.entities.items() if file_fields(entity)]
    if not declaring:
        print(
            f"{model.name} declares no file fields, so it keeps no attachments."
        )
        return 0
    if arguments.grace < 0:
        print(
            "Attachment check failed: --grace must not be negative",
            file=sys.stderr,
        )
        return 1

    storage = open_run_storage(arguments, model, purpose="Attachment check")
    if storage is None:
        return 1
    try:
        if storage.attachment_rows is None:
            print(
                "Attachment check failed: this database keeps no attachment "
                "rows, so there is nothing to reconcile",
                file=sys.stderr,
            )
            return 1
        try:
            attachments = AttachmentService(
                model,
                storage.attachment_rows,
                FilesystemAttachmentBytes(arguments.attachments_root),
            )
            reclaimed = (
                attachments.sweep(grace=timedelta(hours=arguments.grace))
                if arguments.sweep
                else ()
            )
            report = attachments.check()
        except TideRuntimeError as error:
            print(f"Attachment check failed: {error}", file=sys.stderr)
            return 1

        held = len(attachments.rows.all_records())
        if arguments.sweep:
            print(f"Swept: reclaimed {len(reclaimed)} unreferenced file(s).")
        for record in report.rows_without_bytes:
            print(
                f"{record.guid}: no file, though {record.entity}.{record.field} "
                f"names one ({record.filename})."
            )
        for guid in report.bytes_without_rows:
            print(f"{guid}: no row names this file.")
        for record in report.digest_mismatches:
            print(
                f"{record.guid}: the file changed since it was uploaded "
                f"({record.filename})."
            )
        if report.is_clean:
            print(
                f"Attachment check passed: {model.name} {model.version}; "
                f"{held} file(s) in {arguments.attachments_root}."
            )
            return 0
        print(
            "Attachment check failed: "
            f"{len(report.rows_without_bytes)} missing, "
            f"{len(report.bytes_without_rows)} unreferenced, "
            f"{len(report.digest_mismatches)} changed.",
            file=sys.stderr,
        )
        return 1
    finally:
        storage.dispose()
