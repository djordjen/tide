from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tide.api import build_openapi_preview, generate_openapi
from tide.compiler.compiler import compile_project

ROOT = Path(__file__).parents[1]
INVOICING = ROOT / "applications" / "invoicing"


def test_preview_exposes_only_declared_read_routes() -> None:
    document = generate_openapi(compile_project(INVOICING))

    assert document["openapi"] == "3.1.0"
    assert list(document["paths"]) == [
        "/api/v1/products",
        "/api/v1/products/{id}",
        "/api/v1/customers",
        "/api/v1/customers/{id}",
        "/api/v1/invoices",
        "/api/v1/invoices/{id}",
    ]
    assert all(set(path_item) == {"get"} for path_item in document["paths"].values())
    assert document["x-tide"] == {
        "preview": True,
        "read_only": True,
        "schema_version": "0.1",
    }
    assert all(
        operation["security"] == [{"bearerAuth": []}]
        for path_item in document["paths"].values()
        for operation in path_item.values()
    )


def test_preview_generates_typed_pydantic_record_and_page_models() -> None:
    preview = build_openapi_preview(compile_project(INVOICING))
    schemas = preview.document["components"]["schemas"]
    invoice = schemas["SalesInvoiceRecord"]

    assert preview.record_models["sales.Invoice"].__name__ == "SalesInvoiceRecord"
    assert preview.page_models["sales.Invoice"].__name__ == "SalesInvoicePage"
    assert invoice["additionalProperties"] is False
    assert invoice["properties"]["status"]["anyOf"][0] == {
        "enum": ["draft", "posted", "cancelled"],
        "type": "string",
    }
    assert invoice["properties"]["lines"]["anyOf"][0]["items"] == {
        "$ref": "#/components/schemas/SalesInvoiceLineRecord"
    }
    assert invoice["properties"]["total"]["anyOf"][0]["type"] == "string"
    assert invoice["properties"]["invoice_date"]["anyOf"][0]["format"] == "date"
    product = schemas["CatalogProductRecord"]
    assert product["properties"]["code"]["anyOf"][0]["pattern"] == (
        "^(?:[A-Z][A-Z0-9-]{0,29})$"
    )
    assert invoice["properties"]["_tide"]["anyOf"][0] == {
        "$ref": "#/components/schemas/TideProtectionMetadata"
    }
    assert "_tide" not in invoice["required"]

    page = schemas["SalesInvoicePage"]
    assert page["properties"]["records"]["items"] == {
        "$ref": "#/components/schemas/SalesInvoiceRecord"
    }
    assert page["properties"]["next_cursor"]["default"] is None

    references = _references(preview.document)
    assert all(
        reference.removeprefix("#/components/schemas/") in schemas
        for reference in references
    )


def test_list_preview_matches_core_pagination_bounds() -> None:
    document = generate_openapi(compile_project(INVOICING))
    parameters = document["paths"]["/api/v1/invoices"]["get"]["parameters"]

    assert parameters == [
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "description": "Maximum records in this page.",
            "schema": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "default": 100,
            },
        },
        {
            "name": "cursor",
            "in": "query",
            "required": False,
            "description": "Opaque continuation token returned by the previous page.",
            "schema": {"type": "string", "minLength": 1},
        },
    ]


def test_rest_true_uses_safe_read_defaults_and_a_namespaced_path(
    tmp_path: Path,
) -> None:
    application = _write_application(
        tmp_path,
        """
entity: crm.Person
expose: {rest: true}
fields:
  code: {type: string, primary_key: true, length: 20}
""",
    )

    document = generate_openapi(compile_project(application), base_path="/preview/")

    assert set(document["paths"]) == {
        "/preview/crm/person",
        "/preview/crm/person/{code}",
    }
    identity = document["paths"]["/preview/crm/person/{code}"]["get"]["parameters"][0]
    assert identity["schema"] == {"type": "string", "maxLength": 20}


def test_preview_rejects_colliding_resource_paths(tmp_path: Path) -> None:
    application = _write_application(
        tmp_path,
        """
entity: crm.Person
expose: {rest: {path: records, operations: [list]}}
fields:
  id: {type: integer, primary_key: true}
---
entity: sales.Record
expose: {rest: {path: records, operations: [get]}}
fields:
  id: {type: integer, primary_key: true}
""",
        split_documents=True,
    )

    with pytest.raises(ValueError, match="REST path 'records' is shared"):
        generate_openapi(compile_project(application))


def _write_application(
    root: Path,
    entity_text: str,
    *,
    split_documents: bool = False,
) -> Path:
    application = root / "application"
    models = application / "models"
    models.mkdir(parents=True)
    (application / "tide.yaml").write_text(
        """
schema_version: '0.1'
application: {name: Test API, version: 1.0.0}
model: {paths: [models]}
""".lstrip(),
        encoding="utf-8",
    )
    documents = (
        entity_text.strip().split("\n---\n") if split_documents else [entity_text]
    )
    for index, document in enumerate(documents):
        (models / f"entity_{index}.yaml").write_text(
            document.strip() + "\n",
            encoding="utf-8",
        )
    return application


def _references(value: Any) -> set[str]:
    if isinstance(value, dict):
        result = {value["$ref"]} if "$ref" in value else set()
        for child in value.values():
            result.update(_references(child))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for child in value:
            result.update(_references(child))
        return result
    return set()
