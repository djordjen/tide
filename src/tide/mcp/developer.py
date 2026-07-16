"""Local stdio developer MCP for inspection and structured generation plans."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic_core import to_jsonable_python

from tide.development import (
    ApplicationGenerationPlan,
    ApplicationGenerationPreview,
    ApplicationGenerationProposal,
    ApplicationGenerationService,
    ApplicationMaterializationService,
    DeveloperProjectService,
    DeveloperProjectValidation,
)


def build_developer_mcp_server(project: str | Path) -> FastMCP[Any]:
    """Build a local read/propose-only developer server; no apply tools exist."""

    projects = DeveloperProjectService(project)
    generation = ApplicationGenerationService()
    materialization = ApplicationMaterializationService(generation)
    server: FastMCP[Any] = FastMCP(
        name="TIDE Developer",
        instructions=(
            "Inspect a TIDE project and propose new applications through structured "
            "logical operations. Candidate previews use a deleted temporary tree; "
            "only fixed TIDE-owned templates may run against isolated in-memory "
            "services. No tool writes the source workspace, applies a diff, runs "
            "an external command, or connects to an application database."
        ),
        json_response=True,
    )

    @server.resource(
        "tide://developer/project",
        name="TIDE project validation",
        description="Current compiler validity and source-located diagnostics.",
        mime_type="application/json",
    )
    def project_validation_resource() -> str:
        return _json(projects.validate())

    validation = projects.validate()
    if validation.valid:

        @server.resource(
            "tide://developer/application",
            name="TIDE application summary",
            description="Application identity and logical artifact inventory.",
            mime_type="application/json",
        )
        def application_resource() -> str:
            return _json_value(projects.application())

        @server.resource(
            "tide://developer/model",
            name="TIDE normalized model",
            description="Resolved immutable model with project-relative source names.",
            mime_type="application/json",
        )
        def model_resource() -> str:
            return _json_value(projects.normalized_model())

        model = projects.model()
        for entity_name in model.entities:
            server.resource(
                f"tide://developer/entities/{entity_name}",
                name=f"{entity_name} model",
                description=f"Normalized fields and actions for {entity_name}.",
                mime_type="application/json",
            )(_entity_reader(projects, entity_name))
        for view_name in model.views:
            server.resource(
                f"tide://developer/views/{view_name}",
                name=f"{view_name} resolved view",
                description=f"Resolved view and provenance for {view_name}.",
                mime_type="application/json",
            )(_view_reader(projects, view_name))
        _register_inspection_tools(server, projects)

    @server.tool(
        name="tide_validate_project",
        description="Compile the project and return stable source-located diagnostics.",
        structured_output=True,
    )
    def validate_project() -> DeveloperProjectValidation:
        return projects.validate()

    @server.tool(
        name="tide_propose_application",
        description=(
            "Validate an ordered no-write application plan made only of structured "
            "TIDE operations. Returns a deterministic proposal requiring approval."
        ),
        structured_output=True,
    )
    def propose_application(
        plan: ApplicationGenerationPlan,
    ) -> ApplicationGenerationProposal:
        return generation.propose(plan)

    @server.tool(
        name="tide_preview_application",
        description=(
            "Render a valid structured plan into an isolated temporary candidate, "
            "compile it, run bounded static and in-memory integration checks using "
            "only fixed TIDE-owned templates, delete it, and return exact artifacts "
            "and a new-tree diff. This never applies source changes."
        ),
        structured_output=True,
    )
    def preview_application(
        plan: ApplicationGenerationPlan,
    ) -> ApplicationGenerationPreview:
        return materialization.preview(plan)

    return server


def _register_inspection_tools(
    server: FastMCP[Any],
    projects: DeveloperProjectService,
) -> None:
    @server.tool(
        name="tide_list_entities",
        description="List compiled entities and their logical field/action names.",
        structured_output=True,
    )
    def list_entities() -> dict[str, Any]:
        return {
            "entities": projects.list_entities(),
            "writes_performed": False,
        }

    @server.tool(
        name="tide_describe_entity",
        description="Describe one compiled entity without reading arbitrary files.",
        structured_output=True,
    )
    def describe_entity(entity: str) -> dict[str, Any]:
        return projects.describe_entity(entity)

    @server.tool(
        name="tide_get_resolved_view",
        description="Return a compiled view plus property provenance.",
        structured_output=True,
    )
    def get_resolved_view(view: str) -> dict[str, Any]:
        return projects.resolved_view(view)

    @server.tool(
        name="tide_preview_openapi",
        description="Generate the dependency-free read-only OpenAPI preview.",
        structured_output=True,
    )
    def preview_openapi() -> dict[str, Any]:
        return projects.openapi_preview()


def _entity_reader(projects: DeveloperProjectService, entity_name: str) -> Any:
    def read_entity() -> str:
        return _json_value(projects.describe_entity(entity_name))

    read_entity.__name__ = f"read_{entity_name.replace('.', '_')}_developer_entity"
    return read_entity


def _view_reader(projects: DeveloperProjectService, view_name: str) -> Any:
    def read_view() -> str:
        return _json_value(projects.resolved_view(view_name))

    read_view.__name__ = f"read_{view_name.replace('.', '_')}_developer_view"
    return read_view


def _json(model: DeveloperProjectValidation) -> str:
    return json.dumps(model.model_dump(mode="json"), separators=(",", ":"))


def _json_value(value: Any) -> str:
    return json.dumps(
        to_jsonable_python(value),
        separators=(",", ":"),
    )
