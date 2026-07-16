"""Read-only project inspection services for local developer tooling."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tide.api.openapi import generate_openapi
from tide.compiler.compiler import compile_project
from tide.compiler.normalized import ApplicationModel
from tide.diagnostics import CompilationFailed


class DeveloperProjectError(ValueError):
    """A developer inspection requires a project that compiles successfully."""


class DeveloperProjectValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    project: str
    application: str | None = None
    application_version: str | None = None
    schema_version: str | None = None
    entity_count: int = 0
    view_count: int = 0
    report_count: int = 0
    diagnostics: tuple[dict[str, Any], ...] = ()
    writes_performed: Literal[False] = False


class DeveloperProjectService:
    """Compile and inspect one project without mutating its source files."""

    def __init__(self, project: str | Path) -> None:
        self.project = Path(project).resolve()
        self.root = self.project.parent if self.project.is_file() else self.project

    def validate(self) -> DeveloperProjectValidation:
        try:
            model = compile_project(self.project)
        except CompilationFailed as error:
            return DeveloperProjectValidation(
                valid=False,
                project=self.root.name,
                diagnostics=tuple(
                    diagnostic.as_dict(root=self.root)
                    for diagnostic in error.diagnostics
                ),
            )
        return DeveloperProjectValidation(
            valid=True,
            project=self.root.name,
            application=model.name,
            application_version=model.version,
            schema_version=model.schema_version,
            entity_count=len(model.entities),
            view_count=len(model.views),
            report_count=len(model.reports),
            diagnostics=tuple(
                diagnostic.as_dict(root=model.project_root)
                for diagnostic in model.diagnostics
            ),
        )

    def model(self) -> ApplicationModel:
        try:
            return compile_project(self.project)
        except CompilationFailed as error:
            raise DeveloperProjectError(
                "project does not compile; inspect tide://developer/project"
            ) from error

    def application(self) -> dict[str, Any]:
        model = self.model()
        return {
            "schema_version": model.schema_version,
            "application": {"name": model.name, "version": model.version},
            "database": deepcopy(dict(model.database)),
            "entities": tuple(model.entities),
            "views": tuple(model.views),
            "reports": tuple(model.reports),
            "roles": tuple(model.roles),
            "writes_performed": False,
        }

    def normalized_model(self) -> dict[str, Any]:
        model = self.model()
        document = model.as_dict()
        for entity in document["entities"].values():
            source = Path(str(entity["source_file"]))
            try:
                entity["source_file"] = source.relative_to(model.project_root).as_posix()
            except ValueError:
                entity["source_file"] = source.name
        document["writes_performed"] = False
        return document

    def list_entities(self) -> tuple[dict[str, Any], ...]:
        model = self.model()
        return tuple(
            {
                "entity": entity.name,
                "label": entity.label,
                "display": entity.display,
                "fields": tuple(entity.fields),
                "actions": tuple(entity.actions),
            }
            for entity in model.entities.values()
        )

    def describe_entity(self, entity_name: str) -> dict[str, Any]:
        model = self.model()
        try:
            entity = model.entity(entity_name)
        except KeyError as error:
            raise DeveloperProjectError(f"unknown entity {entity_name!r}") from error
        result = entity.as_dict()
        source = Path(str(result["source_file"]))
        try:
            result["source_file"] = source.relative_to(model.project_root).as_posix()
        except ValueError:
            result["source_file"] = source.name
        result["writes_performed"] = False
        return result

    def resolved_view(self, view_name: str) -> dict[str, Any]:
        model = self.model()
        try:
            view = model.views[view_name]
        except KeyError as error:
            raise DeveloperProjectError(f"unknown view {view_name!r}") from error
        result = view.as_dict()
        result["name"] = view.name
        result["entity"] = view.entity
        result["kind"] = view.kind
        result["writes_performed"] = False
        return result

    def openapi_preview(self) -> dict[str, Any]:
        result = generate_openapi(self.model())
        result["x-tide"]["writes_performed"] = False
        return result
