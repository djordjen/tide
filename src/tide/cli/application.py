"""Reviewing and applying generated application and designer changes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from tide.development import (
    ApplicationApplyApproval,
    ApplicationApplyError,
    ApplicationApplyPreparation,
    ApplicationApplyService,
    ApplicationGenerationPlan,
    DesignerCommandBatch,
    DesignerError,
    DesignerRecoveryApproval,
    DesignerRecoveryError,
    DesignerRecoveryPreparation,
    DesignerRecoveryService,
    DesignerSaveApproval,
    DesignerSaveError,
    DesignerSavePreparation,
    DesignerSaveService,
    DesignerService,
)

from .output import print_json


def add_application_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Declare the `application` arguments."""

    application = commands.add_parser(
        "app",
        help="preview and explicitly apply generated applications",
    )
    application_commands = application.add_subparsers(dest="application_command")
    application_preview = application_commands.add_parser(
        "preview",
        help="validate an application plan and prepare an approval challenge",
    )
    application_preview.add_argument(
        "plan",
        type=Path,
        metavar="PLAN.json",
        help="structured application generation plan",
    )
    application_preview.add_argument(
        "--workspace",
        type=Path,
        default=Path("."),
        help="workspace containing applications/ (default: current directory)",
    )
    application_preview.add_argument(
        "--json",
        action="store_true",
        help="emit the approval preparation as structured JSON",
    )
    application_preview.set_defaults(handler=_app_preview)

    application_apply = application_commands.add_parser(
        "apply",
        help="interactively approve and atomically publish a new application",
    )
    application_apply.add_argument(
        "plan",
        type=Path,
        metavar="PLAN.json",
        help="structured application generation plan",
    )
    application_apply.add_argument(
        "--workspace",
        type=Path,
        default=Path("."),
        help="workspace containing applications/ (default: current directory)",
    )
    application_apply.set_defaults(handler=_app_apply)


def add_designer_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Declare the `designer` arguments."""

    designer = commands.add_parser(
        "designer",
        help="preview and explicitly save structured application edits",
    )
    designer_commands = designer.add_subparsers(dest="designer_command")
    designer_preview = designer_commands.add_parser(
        "preview",
        help="apply commands in memory and prepare an exact save challenge",
    )
    designer_preview.add_argument(
        "project",
        metavar="APPLICATION",
        help="application root or tide.yaml",
    )
    designer_preview.add_argument(
        "changes",
        type=Path,
        metavar="CHANGES.json",
        help="structured Designer command batch",
    )
    designer_preview.add_argument(
        "--json",
        action="store_true",
        help="emit the save preparation as structured JSON",
    )
    designer_preview.set_defaults(handler=_designer_preview)

    designer_save = designer_commands.add_parser(
        "save",
        help="interactively approve and save an exact Designer candidate",
    )
    designer_save.add_argument(
        "project",
        metavar="APPLICATION",
        help="application root or tide.yaml",
    )
    designer_save.add_argument(
        "changes",
        type=Path,
        metavar="CHANGES.json",
        help="structured Designer command batch",
    )
    designer_save.set_defaults(handler=_designer_save)

    designer_recover = designer_commands.add_parser(
        "recover",
        help="inspect or explicitly recover an interrupted Designer save",
    )
    designer_recover.add_argument(
        "project",
        metavar="APPLICATION",
        help="application root or tide.yaml",
    )
    designer_recover.add_argument(
        "--preview",
        action="store_true",
        help="inspect recovery evidence without changing files",
    )
    designer_recover.add_argument(
        "--json",
        action="store_true",
        help="emit a read-only recovery preview as structured JSON",
    )
    designer_recover.set_defaults(handler=_designer_recover)


def _app_preview(arguments: argparse.Namespace) -> int:
    try:
        plan = _load_application_plan(arguments.plan)
    except ApplicationApplyError as error:
        if arguments.json:
            print_json(
                {
                    "ready": False,
                    "writes_performed": False,
                    "blockers": [{"code": error.code, "message": error.message}],
                }
            )
        else:
            print(f"Application preview failed: {error}", file=sys.stderr)
        return 1

    preparation = ApplicationApplyService(arguments.workspace).prepare(plan)
    if arguments.json:
        print_json(preparation.model_dump(mode="json"))
    else:
        _print_application_preparation(preparation)
    return 0 if preparation.ready else 1


def _app_apply(arguments: argparse.Namespace) -> int:
    try:
        plan = _load_application_plan(arguments.plan)
        service = ApplicationApplyService(arguments.workspace)
        preparation = service.prepare(plan)
    except ApplicationApplyError as error:
        print(f"Application apply failed: {error}", file=sys.stderr)
        return 1

    _print_application_preparation(preparation)
    if not preparation.ready or preparation.approval_prompt is None:
        print("Application apply refused; no files were written.", file=sys.stderr)
        return 1

    print(
        "\nThis command creates a new application source tree. It never overwrites "
        "an existing application."
    )
    try:
        response = input(
            f"Type exactly {preparation.approval_prompt!r} to publish: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nApplication apply cancelled; no files were written.", file=sys.stderr)
        return 1
    if response != preparation.approval_prompt:
        print("Application apply cancelled; no files were written.", file=sys.stderr)
        return 1

    try:
        approval = ApplicationApplyApproval.from_preparation(preparation)
        result = service.apply(plan, approval)
    except (ApplicationApplyError, ValueError) as error:
        print(f"Application apply failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Applied {result.artifact_count} generated artifacts to {result.target_path}."
    )
    print(f"Approval receipt: {result.receipt_path}")
    print(f"Candidate fingerprint: {result.candidate_fingerprint}")
    return 0


def _load_application_plan(path: Path) -> ApplicationGenerationPlan:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ApplicationApplyError(
            "TIDEAPPLY008",
            f"the application plan could not be read: {error}",
        ) from error
    except json.JSONDecodeError as error:
        raise ApplicationApplyError(
            "TIDEAPPLY008",
            f"the application plan is not valid JSON at line {error.lineno}",
        ) from error
    try:
        return ApplicationGenerationPlan.model_validate(document)
    except ValidationError as error:
        raise ApplicationApplyError(
            "TIDEAPPLY008",
            f"the application plan does not match the structured schema: {error}",
        ) from error


def _designer_preview(arguments: argparse.Namespace) -> int:
    try:
        batch = _load_designer_batch(arguments.changes)
        session = DesignerService(arguments.project).open_session()
        session.execute_batch(batch)
        preparation = DesignerSaveService().prepare(session)
    except (DesignerError, DesignerSaveError) as error:
        if arguments.json:
            print_json(
                {
                    "ready": False,
                    "writes_performed": False,
                    "blockers": [{"code": error.code, "message": error.message}],
                }
            )
        else:
            print(f"Designer preview failed: {error}", file=sys.stderr)
        return 1

    if arguments.json:
        print_json(preparation.model_dump(mode="json"))
    else:
        _print_designer_preparation(preparation)
    return 0 if preparation.ready else 1


def _designer_save(arguments: argparse.Namespace) -> int:
    try:
        batch = _load_designer_batch(arguments.changes)
        session = DesignerService(arguments.project).open_session()
        session.execute_batch(batch)
        service = DesignerSaveService()
        preparation = service.prepare(session)
    except (DesignerError, DesignerSaveError) as error:
        print(f"Designer save failed: {error}", file=sys.stderr)
        return 1

    _print_designer_preparation(preparation)
    if not preparation.ready or preparation.approval_prompt is None:
        print("Designer save refused; no source files were written.", file=sys.stderr)
        return 1

    print(
        "\nThis command replaces only the listed YAML source files. "
        "It rechecks the live application before writing."
    )
    try:
        response = input(
            f"Type exactly {preparation.approval_prompt!r} to save: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nDesigner save cancelled; no files were written.", file=sys.stderr)
        return 1
    if response != preparation.approval_prompt:
        print("Designer save cancelled; no files were written.", file=sys.stderr)
        return 1

    try:
        approval = DesignerSaveApproval.from_preparation(preparation)
        result = service.save(session, approval)
    except (DesignerSaveError, ValueError) as error:
        print(f"Designer save failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Saved {len(result.changed_files)} YAML source file(s) in "
        f"{result.project_path}."
    )
    print(f"Approval receipt: {result.receipt_path}")
    print(f"Candidate fingerprint: {result.candidate_fingerprint}")
    return 0


def _load_designer_batch(path: Path) -> DesignerCommandBatch:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DesignerSaveError(
            "TIDEDSAVE008",
            f"the Designer command batch could not be read: {error}",
        ) from error
    except json.JSONDecodeError as error:
        raise DesignerSaveError(
            "TIDEDSAVE008",
            f"the Designer command batch is not valid JSON at line {error.lineno}",
        ) from error
    try:
        return DesignerCommandBatch.model_validate(document)
    except ValidationError as error:
        raise DesignerSaveError(
            "TIDEDSAVE008",
            f"the Designer command batch does not match its schema: {error}",
        ) from error


def _designer_recover(arguments: argparse.Namespace) -> int:
    try:
        service = DesignerRecoveryService(arguments.project)
        preparation = service.prepare()
    except DesignerRecoveryError as error:
        if arguments.json:
            print_json(
                {
                    "ready": False,
                    "recovery_required": True,
                    "writes_performed": False,
                    "blockers": [{"code": error.code, "message": error.message}],
                }
            )
        else:
            print(f"Designer recovery failed: {error}", file=sys.stderr)
        return 1

    if arguments.json:
        print_json(preparation.model_dump(mode="json"))
        if not preparation.recovery_required:
            return 0
        return 0 if preparation.ready else 1

    _print_designer_recovery_preparation(preparation)
    if arguments.preview or not preparation.recovery_required:
        return 0 if preparation.ready or not preparation.recovery_required else 1
    if not preparation.ready or preparation.approval_prompt is None:
        print("Designer recovery refused; no files were changed.", file=sys.stderr)
        return 1

    print(
        "\nRecovery uses the displayed file hashes to either restore the original "
        "YAML set or finalize an already receipted save."
    )
    try:
        response = input(
            f"Type exactly {preparation.approval_prompt!r} to recover: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nDesigner recovery cancelled; no files were changed.", file=sys.stderr)
        return 1
    if response != preparation.approval_prompt:
        print("Designer recovery cancelled; no files were changed.", file=sys.stderr)
        return 1

    try:
        approval = DesignerRecoveryApproval.from_preparation(preparation)
        result = service.recover(approval)
    except (DesignerRecoveryError, ValueError) as error:
        print(f"Designer recovery failed: {error}", file=sys.stderr)
        return 1
    print(
        f"Designer transaction {result.transaction_id} recovered by "
        f"{result.recovery_action}."
    )
    print(f"Restored YAML files: {result.restored_files}")
    return 0


def _print_designer_recovery_preparation(
    preparation: DesignerRecoveryPreparation,
) -> None:
    print(preparation.summary)
    print(f"Project path: {preparation.project_path}")
    if preparation.transaction_id is not None:
        print(f"Transaction: {preparation.transaction_id}")
    if preparation.recovery_action is not None:
        print(f"Recovery action: {preparation.recovery_action}")
    if preparation.journal_phase is not None:
        print(f"Last durable phase: {preparation.journal_phase}")
    for artifact in preparation.artifacts:
        print(
            f"{artifact.path}: target={artifact.target_state}, "
            f"backup={artifact.backup_state}, "
            f"candidate={artifact.candidate_state}"
        )
    for blocker in preparation.blockers:
        print(f"{blocker.code}: {blocker.message}", file=sys.stderr)


def _print_designer_preparation(
    preparation: DesignerSavePreparation,
) -> None:
    print(preparation.summary)
    print(f"Application: {preparation.project}")
    print(f"Project path: {preparation.project_path}")
    print(f"Base: {preparation.base_state} ({preparation.base_fingerprint})")
    print(f"Candidate: {preparation.candidate_id}")
    print(f"Candidate fingerprint: {preparation.candidate_fingerprint}")
    if preparation.changed_files:
        print("Changed YAML: " + ", ".join(preparation.changed_files))
    for blocker in preparation.blockers:
        print(f"{blocker.code}: {blocker.message}", file=sys.stderr)
    if preparation.diff:
        print("\nExact candidate diff:\n")
        print(preparation.diff.rstrip())


def _print_application_preparation(
    preparation: ApplicationApplyPreparation,
) -> None:
    print(preparation.summary)
    if preparation.application_id is not None:
        print(f"Application: {preparation.application_id}")
    if preparation.target_path is not None:
        print(
            f"Destination: {preparation.target_path} ({preparation.destination_state})"
        )
    print(f"Proposal: {preparation.proposal_id}")
    if preparation.candidate_id is not None:
        print(f"Candidate: {preparation.candidate_id}")
    if preparation.candidate_fingerprint is not None:
        print(f"Candidate fingerprint: {preparation.candidate_fingerprint}")
    if preparation.base_fingerprint is not None:
        print(f"Base fingerprint: {preparation.base_fingerprint}")
    passed = sum(check.status == "passed" for check in preparation.checks)
    failed = sum(check.status == "failed" for check in preparation.checks)
    skipped = sum(check.status == "skipped" for check in preparation.checks)
    print(f"Checks: {passed} passed, {failed} failed, {skipped} skipped")
    for blocker in preparation.blockers:
        print(f"{blocker.code}: {blocker.message}", file=sys.stderr)
    if preparation.diff:
        print("\nExact candidate diff:\n")
        print(preparation.diff.rstrip())
