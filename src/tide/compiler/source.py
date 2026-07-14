"""Strict YAML loading with duplicate-key detection and source positions."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from tide.diagnostics import Diagnostic, SourceLocation

PathPart = str | int
ModelPath = tuple[PathPart, ...]


class StrictLoader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2-style booleans.

    PyYAML's legacy resolver treats values such as ``on`` and ``yes`` as
    booleans. TIDE only accepts true/false as booleans so author intent is not
    silently changed.
    """


StrictLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
for first_character, resolvers in StrictLoader.yaml_implicit_resolvers.items():
    StrictLoader.yaml_implicit_resolvers[first_character] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]
StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


@dataclass(frozen=True, slots=True)
class SourceDocument:
    file: Path
    data: Any
    positions: dict[ModelPath, SourceLocation]

    def location_for(self, path: ModelPath) -> SourceLocation:
        candidate = path
        while candidate not in self.positions and candidate:
            candidate = candidate[:-1]
        return self.positions.get(candidate, SourceLocation(self.file))


class YamlSourceError(Exception):
    def __init__(self, diagnostic: Diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


def load_yaml_document(file: Path) -> SourceDocument:
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as error:
        raise YamlSourceError(
            Diagnostic(
                code="TIDE001",
                message=f"cannot read source file: {error}",
                location=SourceLocation(file),
            )
        ) from error

    loader = StrictLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            raise YamlSourceError(
                Diagnostic(
                    code="TIDE002",
                    message="source file is empty",
                    location=SourceLocation(file),
                )
            )
        positions: dict[ModelPath, SourceLocation] = {}
        data = _construct_node(loader, node, file, (), positions)
        return SourceDocument(file=file, data=data, positions=positions)
    except YamlSourceError:
        raise
    except yaml.MarkedYAMLError as error:
        mark = error.problem_mark
        raise YamlSourceError(
            Diagnostic(
                code="TIDE003",
                message=error.problem or str(error),
                location=SourceLocation(
                    file,
                    line=(mark.line + 1) if mark else 1,
                    column=(mark.column + 1) if mark else 1,
                ),
            )
        ) from error
    finally:
        loader.dispose()


def _construct_node(
    loader: StrictLoader,
    node: Node,
    file: Path,
    path: ModelPath,
    positions: dict[ModelPath, SourceLocation],
) -> Any:
    positions[path] = SourceLocation(file, node.start_mark.line + 1, node.start_mark.column + 1)

    if isinstance(node, ScalarNode):
        if node.tag == "tag:yaml.org,2002:merge":
            raise _source_error(
                "TIDE006", "YAML merge keys are not supported; use TIDE overlays", file, node
            )
        try:
            return loader.construct_object(node, deep=True)
        except yaml.YAMLError as error:
            raise _source_error("TIDE003", str(error), file, node) from error

    if isinstance(node, SequenceNode):
        return [
            _construct_node(loader, child, file, (*path, index), positions)
            for index, child in enumerate(node.value)
        ]

    if isinstance(node, MappingNode):
        result: dict[str, Any] = {}
        key_nodes: dict[str, Node] = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                raise _source_error(
                    "TIDE004", "metadata mapping keys must be scalar strings", file, key_node
                )
            key = loader.construct_object(key_node, deep=True)
            if not isinstance(key, str):
                raise _source_error(
                    "TIDE004", "metadata mapping keys must be strings", file, key_node
                )
            if key in result:
                first = key_nodes[key].start_mark
                raise _source_error(
                    "TIDE005",
                    f"duplicate key {key!r}; first declared at line {first.line + 1}",
                    file,
                    key_node,
                    (*path, key),
                )
            key_nodes[key] = key_node
            positions[(*path, key)] = SourceLocation(
                file, value_node.start_mark.line + 1, value_node.start_mark.column + 1
            )
            result[key] = _construct_node(
                loader, value_node, file, (*path, key), positions
            )
        return result

    raise _source_error("TIDE003", f"unsupported YAML node {type(node).__name__}", file, node)


def _source_error(
    code: str,
    message: str,
    file: Path,
    node: Node,
    path: ModelPath = (),
) -> YamlSourceError:
    return YamlSourceError(
        Diagnostic(
            code=code,
            message=message,
            location=SourceLocation(file, node.start_mark.line + 1, node.start_mark.column + 1),
            path=path,
        )
    )
