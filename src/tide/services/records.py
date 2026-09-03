"""Secured, UI-independent record and query services."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from enum import StrEnum
import re
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID, uuid4

from tide.compiler.expressions import (
    EVALUATION_ERRORS,
    PARAMETER_PATTERN,
    evaluate_expression,
)
from tide.appearance import record_appearance
from tide.compiler.normalized import (
    ApplicationModel,
    NormalizedEntity,
    NormalizedField,
    field_is_writable,
)
from tide.data.repository import (
    FILTER_OPERATORS,
    DeleteCollection,
    DeletedRecord,
    DeleteReference,
    FilterCondition as FilterCondition,
    QuerySpec,
    RelationshipLoad,
    RelationshipLoadPlan,
    Repository,
    RowPolicyMismatch,
    SummaryRequest,
    WriteIntegrityError,
    SortField,
)
from tide.model.source import SUMMARIZABLE_FIELD_TYPES, SUMMARY_FUNCTIONS
from tide.display import display_fields, record_display
from tide.labels import declared_values
from tide.runtime.context import RequestContext
from tide.runtime.errors import (
    AuthorizationError,
    ConcurrencyError,
    ImmutableFieldError,
    InvalidQueryCursor,
    MassAssignmentError,
    NotFoundError,
    NullVersion,
    RelationshipExpansionLimit,
    ValidationFailed,
    ValidationIssue,
    VersionPreconditionRequired,
    QueryFieldError,
)
from tide.security.engine import PROTECTED, SecurityEngine
from tide.services.attachments import (
    AttachmentService,
    claim_plan,
    file_fields,
    released_guids,
)
from tide.services.cursors import (
    CURSOR_VERSION,
    CursorShape,
    CursorState,
    CursorStore,
    InMemoryCursorStore,
    QueryPage,
)
from tide.services.references import ReferenceDisplays
from tide.services.action_store import (
    ActionExecutionStore,
    AuditFieldChange,
    AuditValueMode,
    InMemoryActionExecutionStore,
    RecordAuditEvent,
    RecordAuditOperation,
    serialize_action_value,
)
from tide.sessions.record_session import RecordSession


_MAX_AUDIT_VALUE_BYTES = 4_096


class MutationSource(StrEnum):
    USER = "user"
    ACTION = "action"
    SYSTEM = "system"


Generator = Callable[[dict[str, Any], RequestContext, Repository], Any]

# Selection-only by design, so the bound is generous for a hand-made
# selection and still keeps one request's outcome report readable.
MASS_UPDATE_TARGET_LIMIT = 1_000


@dataclass(frozen=True, slots=True)
class MassUpdateTarget:
    """One selected row: its identity, and the version the caller observed."""

    identity: Any
    expected_version: int | NullVersion | None = None


@dataclass(frozen=True, slots=True)
class MassUpdateRowOutcome:
    """One row's answer: updated, or refused with the single-record reason."""

    identity: Any
    status: str
    code: str | None = None
    message: str | None = None
    issues: tuple[ValidationIssue, ...] = ()
    notices: tuple[ValidationIssue, ...] = ()
    version: int | None = None


@dataclass(frozen=True, slots=True)
class MassUpdateResult:
    """Per-row outcomes in request order, with the counts already summed."""

    outcomes: tuple[MassUpdateRowOutcome, ...]
    updated: int
    refused: int


def require_mass_assignable(
    model: ApplicationModel, entity_name: str, changes: Mapping[str, Any]
) -> None:
    """The declaration gate: which fields a mass update may name.

    A module function so the local service and the remote twin judge a
    request with the same body of rules -- both TUI modes must refuse a
    bad request identically, and nothing unassignable should ever reach
    the wire.
    """

    entity = model.entity(entity_name)
    if not changes:
        raise MassAssignmentError("mass update requires at least one field")
    refused = sorted(
        name
        for name in changes
        if name not in entity.fields
        or entity.field(name).metadata["type"] == "collection"
        or not field_is_writable(entity.field(name), "update")
    )
    if refused:
        raise MassAssignmentError(
            "mass update cannot assign: " + ", ".join(refused)
        )


def validate_mass_update_request(
    model: ApplicationModel,
    entity_name: str,
    changes: Mapping[str, Any],
    targets: Sequence[MassUpdateTarget],
) -> None:
    """Every declaration-level check a mass update must pass before row one."""

    require_mass_assignable(model, entity_name, changes)
    if not targets:
        raise MassAssignmentError("mass update requires at least one target")
    if len(targets) > MASS_UPDATE_TARGET_LIMIT:
        raise MassAssignmentError(
            f"mass update accepts at most {MASS_UPDATE_TARGET_LIMIT} targets"
        )


class RecordsService:
    def __init__(
        self,
        model: ApplicationModel,
        repository: Repository,
        security: SecurityEngine | None = None,
        cursor_store: CursorStore | None = None,
        relationship_max_depth: int = 3,
        relationship_max_items: int = 1_000,
        audit_store: ActionExecutionStore | None = None,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        attachments: AttachmentService | None = None,
    ) -> None:
        if relationship_max_depth < 1:
            raise ValueError("relationship expansion depth must be positive")
        if relationship_max_items < 1:
            raise ValueError("relationship expansion item limit must be positive")
        self.model = model
        self.repository = repository
        self.security = security or SecurityEngine(model)
        self.cursor_store = (
            cursor_store if cursor_store is not None else InMemoryCursorStore()
        )
        self.relationship_max_depth = relationship_max_depth
        self.relationship_max_items = relationship_max_items
        self.audit_store = audit_store or InMemoryActionExecutionStore()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._event_id_factory = event_id_factory or (lambda: str(uuid4()))
        self._generators: dict[str, Generator] = {}
        self.attachments = attachments

    def register_generator(self, reference: str, generator: Generator) -> None:
        self._generators[reference] = generator

    @property
    def registered_generators(self) -> frozenset[str]:
        """Return the references an application runtime hook has registered."""

        return frozenset(self._generators)

    def create(
        self,
        entity_name: str,
        context: RequestContext,
        values: Mapping[str, Any] | None = None,
    ) -> RecordSession:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "create", context)
        defaults: dict[str, Any] = {}
        for field_name, field in entity.fields.items():
            metadata = field.metadata
            if field.target_entity and metadata["type"] == "collection":
                defaults[field_name] = []
            elif metadata.get("default_factory") == "today":
                defaults[field_name] = date.today()
            elif "default" in metadata:
                defaults[field_name] = deepcopy(metadata["default"])
        initial = deepcopy(defaults)
        initial.update(deepcopy(dict(values or {})))
        version_field = _version_field(entity)
        return RecordSession(
            entity=entity_name,
            identity=initial.get(_primary_key(entity)),
            original=defaults,
            values=initial,
            expected_version=initial.get(version_field) if version_field else None,
            is_new=True,
        )

    def begin_edit(self, entity_name: str, identity: Any, context: RequestContext) -> RecordSession:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "read", context)
        self.security.authorize_entity(entity, "update", context)
        values = self._load_authorized(
            entity_name, identity, context, operations=("read", "update")
        )
        version_field = _version_field(entity)
        return RecordSession(
            entity=entity_name,
            identity=identity,
            original=deepcopy(values),
            values=deepcopy(values),
            expected_version=values.get(version_field) if version_field else None,
        )

    def begin_action(self, entity_name: str, identity: Any, context: RequestContext) -> RecordSession:
        """Open an action target without requiring the separate entity-update grant."""

        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "read", context)
        values = self._load_authorized(
            entity_name, identity, context, operations=("read",)
        )
        version_field = _version_field(entity)
        return RecordSession(
            entity=entity_name,
            identity=identity,
            original=deepcopy(values),
            values=deepcopy(values),
            expected_version=values.get(version_field) if version_field else None,
        )

    def get(self, entity_name: str, identity: Any, context: RequestContext) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "read", context)
        values = self._load_authorized(
            entity_name, identity, context, operations=("read",)
        )
        return self._project(entity, values, context)

    def authorize_record_visibility(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
    ) -> None:
        """Refuse when this principal's read row policies hide the record.

        Deliberately not `get`: the caller holds its own entity-level gate
        -- `audit` grants history without granting `read` -- but no grant
        reaches a row the reader's own read policies hide, and a record
        that is gone refuses the way its read refuses.
        """

        self._load_authorized(
            entity_name, identity, context, operations=("read",)
        )

    def duplicate_draft(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
    ) -> dict[str, Any]:
        """The values a create form opens with to duplicate one record.

        What copies is what a person could have typed on the original:
        writable scalars, chosen references, and the rows of collections
        this record owns -- each row minus its own identity, readonly,
        system-written and computed fields. Identity, workflow state,
        stamps, file bytes and anything field security protected stay
        behind, so the new record allocates and computes its own and a
        duplicate is never a way to read what the grid would not show.

        This stores nothing and authorizes only the read; the draft goes
        through the ordinary create path, which owns creation as always.
        """

        entity = self.model.entity(entity_name)
        source = self.get(entity_name, identity, context)
        draft: dict[str, Any] = {}
        for name, field in entity.fields.items():
            if not _field_duplicates(field):
                continue
            value = source.get(name)
            if value is None or value is PROTECTED:
                continue
            if str(field.metadata["type"]) != "collection":
                draft[name] = value
                continue
            child = self.model.entity(str(field.metadata["target"]))
            inverse = field.metadata.get("inverse")
            rows: list[dict[str, Any]] = []
            for row in value:
                rows.append(
                    {
                        child_name: row[child_name]
                        for child_name, child_field in child.fields.items()
                        if child_name != inverse
                        and _field_duplicates(child_field)
                        and child_name in row
                        and row[child_name] is not None
                        and row[child_name] is not PROTECTED
                    }
                )
            draft[name] = rows
        return draft

    def delete(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
        *,
        expected_version: int | NullVersion | None = None,
    ) -> None:
        """Delete one authorized row using metadata-defined reference behavior."""

        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "delete", context)
        version_field = _version_field(entity)
        if version_field is not None and expected_version is None:
            raise VersionPreconditionRequired(entity_name)
        if isinstance(expected_version, NullVersion):
            # The caller read a row whose token was never written; the
            # repository compares IS NULL, which None expresses.
            expected_version = None
        original = self._load_authorized(
            entity_name,
            identity,
            context,
            operations=("delete",),
        )
        try:
            removed = self.repository.delete(
                entity_name,
                identity,
                primary_key=_primary_key(entity),
                version_field=version_field,
                expected_version=expected_version,
                row_criteria=self.security.row_criteria(entity_name, "delete"),
                criteria_parameters=self.security.policy_parameters(context),
                references=_delete_references(self.model),
                collections=_delete_collections(self.model),
                on_deleted=lambda connection, removed: self._audit_removals(
                    entity,
                    identity,
                    original,
                    removed,
                    context,
                    connection=connection,
                ),
            )
        except RowPolicyMismatch as error:
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not delete this "
                f"{entity_name} record"
            ) from error
        # Every removal was audited inside the delete; what is left to do is
        # let go of the files those rows held -- cascaded children included,
        # since only the repository knows which rows a delete actually
        # reached. Releasing rather than deleting: the record is gone, but
        # its documents wait out the grace like any other released file.
        if self.attachments is not None:
            for gone in (*removed, DeletedRecord(entity_name, identity, original)):
                if file_fields(self.model.entity(gone.entity)):
                    self.attachments.release_record(gone.entity, str(gone.identity))

    def _audit_removals(
        self,
        entity: NormalizedEntity,
        identity: Any,
        original: Mapping[str, Any],
        removed: tuple[DeletedRecord, ...],
        context: RequestContext,
        *,
        connection: Any,
    ) -> None:
        """Record every row a delete took, on the delete's own connection.

        A create that goes unaudited can still be inspected afterwards; a
        delete that goes unaudited leaves nothing to inspect, so the gap
        between removing the rows and accounting for them is the one least
        affordable. A cascade removes several at once and they are audited
        together: some rows accounted for and others not is worse than
        neither, because it reads as if the rest were never removed.
        """

        for record in removed:
            if record.entity == entity.name and record.identity == identity:
                # Prefer the authorized copy already loaded for the target: it
                # carries the hydrated relationships the repository row lacks.
                continue
            self._record_audit(
                self.model.entity(record.entity),
                RecordAuditOperation.DELETE,
                record.identity,
                record.values,
                {},
                context,
                MutationSource.USER,
                connection=connection,
            )
        self._record_audit(
            entity,
            RecordAuditOperation.DELETE,
            identity,
            original,
            {},
            context,
            MutationSource.USER,
            connection=connection,
        )

    def mass_update(
        self,
        entity_name: str,
        changes: Mapping[str, Any],
        targets: Sequence[MassUpdateTarget],
        context: RequestContext,
        *,
        acknowledged_warnings: frozenset[str] = frozenset(),
    ) -> MassUpdateResult:
        """Apply one change set to each selected row, answering row by row.

        The single-record update run N times: every per-record rule -- row
        and field policies, `immutable_when`, validation, the warning
        acknowledgement gate, optimistic versions, per-row audit -- meets
        each row exactly as a hand-made edit would. Each row is its own
        commit, so a refusal rolls back nothing about its siblings, and the
        answers come back as outcomes in request order rather than as one
        exception. Only declaration-level problems -- fields no update may
        assign, an empty or oversized request -- refuse the whole call.
        """

        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "update", context)
        validate_mass_update_request(self.model, entity_name, changes, targets)
        outcomes = tuple(
            self._mass_update_one(
                entity, changes, target, context, acknowledged_warnings
            )
            for target in targets
        )
        updated = sum(1 for outcome in outcomes if outcome.status == "updated")
        return MassUpdateResult(
            outcomes=outcomes,
            updated=updated,
            refused=len(outcomes) - updated,
        )

    def require_mass_assignable(
        self, entity_name: str, changes: Mapping[str, Any]
    ) -> None:
        """The declaration gate alone: which fields a mass update may name.

        Public because the REST door needs it when no target survived
        identity coercion -- the request must still be judged as a request.
        """

        require_mass_assignable(self.model, entity_name, changes)

    def _mass_update_one(
        self,
        entity: NormalizedEntity,
        changes: Mapping[str, Any],
        target: MassUpdateTarget,
        context: RequestContext,
        acknowledged_warnings: frozenset[str],
    ) -> MassUpdateRowOutcome:
        try:
            session = self.begin_edit(entity.name, target.identity, context)
            if entity.version_field is not None:
                if target.expected_version is None:
                    raise VersionPreconditionRequired(entity.name)
                asserted = (
                    None
                    if isinstance(target.expected_version, NullVersion)
                    else target.expected_version
                )
                if session.expected_version != asserted:
                    raise ConcurrencyError(asserted, session.expected_version)
                session.expected_version = asserted
            for field_name, value in changes.items():
                session.set(field_name, value)
            stored = self.commit(
                session,
                context,
                acknowledged_warnings=acknowledged_warnings,
            )
        except ValidationFailed as error:
            return MassUpdateRowOutcome(
                identity=target.identity,
                status="refused",
                code=error.code,
                message=str(error),
                issues=error.issues,
            )
        except (
            ImmutableFieldError,
            ConcurrencyError,
            VersionPreconditionRequired,
            NotFoundError,
            AuthorizationError,
        ) as error:
            return MassUpdateRowOutcome(
                identity=target.identity,
                status="refused",
                code=error.code,
                message=str(error),
            )
        except RowPolicyMismatch as error:
            return MassUpdateRowOutcome(
                identity=target.identity,
                status="refused",
                code="forbidden",
                message=str(error) or "row policy refuses this record",
            )
        version: int | None = None
        if entity.version_field is not None:
            value = stored.get(entity.version_field.name)
            if isinstance(value, int) and not isinstance(value, bool):
                version = value
        return MassUpdateRowOutcome(
            identity=target.identity,
            status="updated",
            notices=session.notices,
            version=version,
        )

    def lookup_criteria(
        self, entity_name: str, field_name: str
    ) -> tuple[str, ...]:
        """The declared eligibility criteria for one reference edge.

        Every picker resolves the edge here, so the rule stays declared in
        one place. An undeclared edge answers with silence rather than an
        error -- callers may ask about any reference.
        """

        entity = self.model.entity(entity_name)
        if field_name not in entity.fields:
            raise ValueError(f"unknown field {field_name!r}")
        field = entity.field(field_name)
        if field.metadata["type"] != "reference" or not field.target_entity:
            raise ValueError(f"field {field_name!r} is not a reference")
        declared = field.metadata.get("lookup_filter")
        return (str(declared),) if declared else ()

    def _resolve_lookup_source(
        self, queried_entity: str, source: tuple[str, str]
    ) -> tuple[str, ...]:
        """Turn a query's reference edge into its declared criteria.

        Raises ``QueryFieldError`` -- the caller-facing refusal every query
        door already maps -- because a bad edge is a bad query, not a server
        fault. The edge must point at the entity being queried: anything
        else is a claim about some other picker's rows.
        """

        owner_name, field_name = source
        try:
            owner = self.model.entity(owner_name)
        except (KeyError, ValueError) as error:
            raise QueryFieldError(
                f"unknown lookup source entity {owner_name!r}"
            ) from error
        if field_name not in owner.fields:
            raise QueryFieldError(
                f"unknown lookup source field {field_name!r}"
            )
        field = owner.field(field_name)
        if field.metadata["type"] != "reference" or not field.target_entity:
            raise QueryFieldError(
                f"lookup source field {field_name!r} is not a reference"
            )
        if field.target_entity != queried_entity:
            raise QueryFieldError(
                f"lookup source {owner_name}.{field_name} does not target "
                f"{queried_entity}"
            )
        return self.lookup_criteria(owner_name, field_name)

    def lookup_records(
        self,
        entity_name: str,
        search_fields: tuple[str, ...],
        search_text: str,
        context: RequestContext,
        *,
        limit: int = 20,
        source: tuple[str, str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return a bounded secured lookup result, matching any search field."""

        if not search_fields:
            raise ValueError("lookup search requires at least one field")
        if len(set(search_fields)) != len(search_fields):
            raise ValueError("lookup search fields must not be repeated")
        if limit < 1 or limit > 500:
            raise ValueError("lookup limit must be between 1 and 500")
        entity = self.model.entity(entity_name)
        primary_key = _primary_key(entity)
        if not self.security.can_read_field(entity_name, primary_key, context):
            raise AuthorizationError("lookup primary key is not readable")
        sort = (SortField(search_fields[0]),)
        candidate = search_text.strip()
        if not candidate:
            return tuple(
                self.query(
                    entity_name,
                    QuerySpec(sort=sort, limit=limit, lookup_source=source),
                    context,
                )
            )
        matches: dict[Any, dict[str, Any]] = {}
        for field_name in search_fields:
            page = self.query_page(
                entity_name,
                QuerySpec(
                    filters=(FilterCondition(field_name, "icontains", candidate),),
                    sort=sort,
                    limit=limit,
                    lookup_source=source,
                ),
                context,
            )
            for record in page.records:
                matches.setdefault(record[primary_key], record)
                if len(matches) >= limit:
                    return tuple(matches.values())
        return tuple(matches.values())

    def apply_reference_selection(
        self,
        entity_name: str,
        field_name: str,
        values: Mapping[str, Any],
        identity: Any,
        context: RequestContext,
    ) -> dict[str, Any]:
        """Apply a secured reference choice and its declarative draft assignments."""

        entity = self.model.entity(entity_name)
        if field_name not in entity.fields:
            raise ValueError(f"unknown field {field_name!r}")
        reference = entity.field(field_name)
        if reference.metadata["type"] != "reference" or not reference.target_entity:
            raise ValueError(f"field {field_name!r} is not a reference")
        if reference.metadata.get("readonly") or reference.metadata.get(
            "write", "normal"
        ) != "normal":
            raise ImmutableFieldError(field_name, "reference field is not user-writable")
        if not self.security.can_write_field(entity_name, field_name, context):
            raise AuthorizationError(f"field {field_name!r} is not writable")

        selected = self.get(reference.target_entity, identity, context)
        target_entity = self.model.entity(reference.target_entity)
        target_key = _primary_key(target_entity)
        if selected.get(target_key) is PROTECTED:
            raise AuthorizationError("lookup primary key is not readable")
        result = deepcopy(dict(values))
        result[field_name] = deepcopy(selected[target_key])
        on_select = reference.metadata.get("on_select", {})
        for destination_name, assignment in on_select.get("assign", {}).items():
            destination = entity.field(destination_name)
            if destination.metadata.get("readonly") or destination.metadata.get(
                "write", "normal"
            ) != "normal":
                raise ImmutableFieldError(
                    destination_name,
                    "selection assignment target is not user-writable",
                )
            if not self.security.can_write_field(entity_name, destination_name, context):
                raise AuthorizationError(f"field {destination_name!r} is not writable")
            current = result.get(destination_name)
            if (
                assignment.get("overwrite", "always") == "when_blank"
                and current is not None
                and current != ""
            ):
                continue
            source_name = assignment["from"]
            source_value = selected.get(source_name)
            if source_value is PROTECTED:
                raise AuthorizationError(
                    f"field {reference.target_entity}.{source_name!s} is not readable"
                )
            result[destination_name] = deepcopy(source_value)
        return result

    def _load_authorized(
        self,
        entity_name: str,
        identity: Any,
        context: RequestContext,
        *,
        operations: tuple[str, ...],
    ) -> dict[str, Any]:
        criteria = tuple(
            criterion
            for operation in operations
            for criterion in self.security.row_criteria(entity_name, operation)
        )
        try:
            values = self.repository.get(
                entity_name,
                identity,
                row_criteria=criteria,
                criteria_parameters=self.security.policy_parameters(context),
                relationships=self._relationship_plan(
                    entity_name,
                    context,
                    operations=operations,
                ),
            )
        except RowPolicyMismatch as error:
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not access this {entity_name} record"
            ) from error
        for operation in operations:
            self.security.require_row(
                entity_name,
                operation,
                self._policy_values(entity_name, values, operation, context),
                context,
            )
        return values

    def query(
        self,
        entity_name: str,
        query: QuerySpec,
        context: RequestContext,
    ) -> list[dict[str, Any]]:
        return list(self.query_page(entity_name, query, context).records)

    def query_page(
        self,
        entity_name: str,
        query: QuerySpec,
        context: RequestContext,
    ) -> QueryPage:
        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "list", context)
        if query.limit < 1 or query.limit > 500:
            raise ValueError("query limit must be between 1 and 500")
        if query.after is not None:
            raise ValueError("query cursor boundaries are internal to RecordsService")
        if query.cursor is not None and (
            not isinstance(query.cursor, str) or not query.cursor
        ):
            raise InvalidQueryCursor
        requested_sort_names = [sort.field for sort in query.sort]
        if len(set(requested_sort_names)) != len(requested_sort_names):
            raise ValueError("query sort fields must not be repeated")
        for field_name in [condition.field for condition in query.filters] + [
            sort.field for sort in query.sort
        ] + [request.field for request in query.summaries]:
            self._require_queryable_field(entity, entity_name, field_name, context)
        seen_summaries: set[SummaryRequest] = set()
        for request in query.summaries:
            if request in seen_summaries:
                raise ValueError("summary requests must not be repeated")
            seen_summaries.add(request)
            if request.function not in SUMMARY_FUNCTIONS:
                raise ValueError(
                    f"unknown summary function {request.function!r}"
                )
            field_type = str(entity.fields[request.field].metadata["type"])
            if field_type not in SUMMARIZABLE_FIELD_TYPES[request.function]:
                raise ValueError(
                    f"{request.function} cannot summarize {field_type} "
                    f"field {request.field!r}"
                )
        criteria = (
            self._resolve_lookup_source(entity_name, query.lookup_source)
            if query.lookup_source is not None
            else ()
        )
        normalized_filters = tuple(
            _normalize_filter(self.model, entity, condition)
            for condition in query.filters
        )
        primary_key = _primary_key(entity)
        sort_fields = list(query.sort)
        if not any(sort.field == primary_key for sort in sort_fields):
            sort_fields.append(SortField(primary_key))
        effective_sort = tuple(sort_fields)
        shape = CursorShape(
            model=(self.model.name, self.model.version, self.model.schema_version),
            entity=entity_name,
            filters=normalized_filters,
            sort=effective_sort,
            limit=query.limit,
            principal=(
                context.principal.identifier,
                tuple(sorted(self.security.effective_permissions(context.principal))),
            ),
            criteria=criteria,
        )
        after: tuple[Any, ...] | None = None
        if query.cursor is not None:
            state = self.cursor_store.resolve(query.cursor)
            if (
                state.version != CURSOR_VERSION
                or state.shape != shape
                or len(state.values) != len(effective_sort)
            ):
                raise InvalidQueryCursor
            after = state.values
        repository_query = QuerySpec(
            filters=normalized_filters,
            sort=effective_sort,
            limit=query.limit + 1,
            after=after,
        )
        records = self.repository.query(
            entity_name,
            repository_query,
            # The declared criteria ride beside the row policies: same
            # evaluation, different owner -- the model's rule about the rows,
            # not the principal's right to them.
            row_criteria=(
                *self.security.row_criteria(entity_name, "list"),
                *criteria,
            ),
            criteria_parameters=self.security.policy_parameters(context),
            relationships=self._relationship_plan(
                entity_name,
                context,
                operations=("list",),
            ),
        )
        policy_cache: dict[tuple[str, Any], dict[str, Any]] = {}
        authorized: list[dict[str, Any]] = []
        for record in records:
            if not self.security.row_allowed(
                entity_name,
                "list",
                self._policy_values(
                    entity_name,
                    record,
                    "list",
                    context,
                    cache=policy_cache,
                ),
                context,
            ):
                raise AuthorizationError("query result failed its row-policy recheck")
            authorized.append(record)

        has_more = len(authorized) > query.limit
        page_records = authorized[: query.limit]
        next_cursor = None
        if has_more and page_records:
            next_cursor = self.cursor_store.issue(
                CursorState(
                    version=CURSOR_VERSION,
                    shape=shape,
                    values=tuple(
                        page_records[-1].get(sort.field)
                        for sort in effective_sort
                    ),
                )
            )
        projected = tuple(
            self._project(entity, record, context) for record in page_records
        )
        return QueryPage(
            records=projected,
            next_cursor=next_cursor,
            references=self.reference_displays(entity_name, projected, context),
            summaries=self._page_summaries(
                entity,
                entity_name,
                query.summaries,
                normalized_filters,
                context,
                criteria=criteria,
            ),
        )

    def _require_queryable_field(
        self,
        entity: NormalizedEntity,
        entity_name: str,
        field_name: str,
        context: RequestContext,
    ) -> None:
        if field_name not in entity.fields:
            raise QueryFieldError(f"unknown query field {field_name!r}")
        if not self.security.can_read_field(entity_name, field_name, context):
            raise AuthorizationError(
                f"field {field_name!r} cannot be used to filter, sort "
                "or summarize"
            )
        field = entity.fields[field_name]
        computed = field.metadata.get("computed")
        if field.metadata["type"] == "collection" or (
            computed and computed.get("materialization") == "virtual"
        ):
            raise ValueError(
                f"field {field_name!r} is not stored and cannot be queried"
            )
        if field.metadata["type"] == "file":
            # Stored, but what is stored is an attachment's key. Ordering by
            # it, enumerating it or comparing it are all questions about a
            # random uuid rather than about the document, so the one gate
            # every surface asks refuses them together.
            raise ValueError(
                f"field {field_name!r} holds a file and cannot be queried"
            )

    def distinct_values(
        self,
        entity_name: str,
        field_name: str,
        filters: tuple[FilterCondition, ...],
        context: RequestContext,
        *,
        limit: int = 200,
    ) -> DistinctValues:
        """The column's distinct values under the caller's conditions.

        Bounded, policy-bound, ordered the way pages order, and -- for a
        reference column -- named the way grids name references, through
        the same batched display machinery, so the list reads "Canon"
        rather than 14. A value whose target the caller may not read keeps
        its identity and no name, exactly like the grid cell would.
        """

        entity = self.model.entity(entity_name)
        self.security.authorize_entity(entity, "list", context)
        if limit < 1 or limit > 500:
            raise ValueError("distinct limit must be between 1 and 500")
        self._require_queryable_field(entity, entity_name, field_name, context)
        for condition in filters:
            self._require_queryable_field(
                entity, entity_name, condition.field, context
            )
        normalized = tuple(
            _normalize_filter(self.model, entity, condition)
            for condition in filters
        )
        values, truncated = self.repository.distinct(
            entity_name,
            field_name,
            filters=normalized,
            row_criteria=self.security.row_criteria(entity_name, "list"),
            criteria_parameters=self.security.policy_parameters(context),
            relationships=self._relationship_plan(
                entity_name,
                context,
                operations=("list",),
            ),
            limit=limit,
        )
        field = entity.fields[field_name]
        displays: dict[Any, str | None] = {}
        if field.metadata["type"] == "reference" and field.target_entity:
            resolved = self.reference_displays(
                entity_name,
                tuple(
                    {field_name: value}
                    for value in values
                    if value is not None
                ),
                context,
            )
            displays = {
                value: resolved.display(field.target_entity, value)
                for value in values
                if value is not None
            }
        return DistinctValues(
            values=tuple((value, displays.get(value)) for value in values),
            truncated=truncated,
        )

    def _page_summaries(
        self,
        entity: NormalizedEntity,
        entity_name: str,
        requests: tuple[SummaryRequest, ...],
        filters: tuple[FilterCondition, ...],
        context: RequestContext,
        *,
        criteria: tuple[str, ...] = (),
    ) -> tuple[tuple[SummaryRequest, Any], ...]:
        """Answer each summary over the whole set the page's query admits.

        The repository receives the page's own filters, row criteria and
        parameters -- and none of its sort, limit or cursor boundary --
        so the values describe everything the caller could page through.
        ``avg`` never reaches the repository: it is sum over count, divided
        here so null handling and rounding are one contract, not one per
        dialect.
        """

        if not requests:
            return ()
        primitives: list[SummaryRequest] = []
        for request in requests:
            wanted = (
                (SummaryRequest(request.field, "sum"), SummaryRequest(request.field, "count"))
                if request.function == "avg"
                else (request,)
            )
            for primitive in wanted:
                if primitive not in primitives:
                    primitives.append(primitive)
        computed = self.repository.aggregate(
            entity_name,
            tuple(primitives),
            filters=filters,
            row_criteria=(
                *self.security.row_criteria(entity_name, "list"),
                *criteria,
            ),
            criteria_parameters=self.security.policy_parameters(context),
            relationships=self._relationship_plan(
                entity_name,
                context,
                operations=("list",),
            ),
        )
        results: list[tuple[SummaryRequest, Any]] = []
        for request in requests:
            if request.function == "avg":
                value = _summary_average(
                    computed[SummaryRequest(request.field, "sum")],
                    computed[SummaryRequest(request.field, "count")],
                    scale=_summary_scale(entity.fields[request.field].metadata),
                )
            else:
                value = computed[request]
            results.append((request, value))
        return tuple(results)

    def reference_displays(
        self,
        entity_name: str,
        records: Sequence[Mapping[str, Any]],
        context: RequestContext,
    ) -> ReferenceDisplays:
        """Resolve how every reference in ``records`` names its target.

        One load per target entity, however many rows point at it, carrying
        the same authority a single read would have had: the target entity
        must be readable, the fields its display names must be readable, and
        its read policy still decides row by row.

        Every refusal degrades to absence rather than raising: a display it
        may not resolve is simply not there, and a renderer that gets nothing
        shows the stored identity, which is what it did before this existed.
        Absence is the only negative answer, so nothing here can distinguish
        a row the policy hid from one that was never there.
        """

        wanted: dict[str, dict[Any, None]] = {}
        self._collect_reference_identities(entity_name, records, context, wanted)
        entries: dict[tuple[str, Any], str] = {}
        for target_name, identities in wanted.items():
            if not identities:
                continue
            target = self.model.entity(target_name)
            rows = self.repository.get_many(
                target_name,
                list(identities),
                row_criteria=self.security.row_criteria(target_name, "read"),
                criteria_parameters=self.security.policy_parameters(context),
            )
            for identity, values in rows.items():
                if not self.security.row_allowed(
                    target_name,
                    "read",
                    values,
                    context,
                ):
                    # The adapter's criteria and this predicate disagreeing
                    # is worth knowing about, but the safe reading is the
                    # strict one, and it is indistinguishable to the caller
                    # from a row the policy refused outright.
                    continue
                entries[(target_name, identity)] = record_display(target, values)
        return ReferenceDisplays(entries)

    def _collect_reference_identities(
        self,
        entity_name: str,
        records: Sequence[Mapping[str, Any]],
        context: RequestContext,
        wanted: dict[str, dict[Any, None]],
        skip: str | None = None,
    ) -> None:
        """Gather what each readable reference in ``records`` points at.

        Children are walked too: a collection grid shows references of its
        own, and resolving them with the page costs one more load rather
        than one per visible row.

        ``skip`` is the child's pointer back at the parent it arrived
        inside, which names a record the reader is already looking at. The
        adapters disagree about whether it is even populated on a hydrated
        child, so resolving it would put an adapter's habit on the wire.
        """

        entity = self.model.entity(entity_name)
        for field_name, field in entity.fields.items():
            field_type = field.metadata["type"]
            if field_name == skip:
                continue
            if field.target_entity is None or field_type not in {
                "reference",
                "collection",
            }:
                continue
            if not self.security.can_read_field(entity_name, field_name, context):
                continue
            target = self.model.entity(field.target_entity)
            if not self.security.can_access_entity(target, "read", context):
                continue
            if field_type == "collection":
                children = [
                    item
                    for record in records
                    for item in (record.get(field_name) or ())
                    if isinstance(item, Mapping)
                ]
                if children:
                    inverse = field.metadata.get("inverse")
                    self._collect_reference_identities(
                        target.name,
                        children,
                        context,
                        wanted,
                        str(inverse) if inverse else None,
                    )
                continue
            if not self._display_is_visible(target, context):
                continue
            identities = wanted.setdefault(target.name, {})
            for record in records:
                value = record.get(field_name)
                if value is None or value is PROTECTED:
                    continue
                identities.setdefault(value, None)

    def _display_is_visible(
        self,
        target: NormalizedEntity,
        context: RequestContext,
    ) -> bool:
        """Report whether this principal may be shown how ``target`` names itself.

        A batched load reads stored scalars, so a display over a collection
        or a virtual computed field is not resolvable this way. Those fall
        back to the per-record fetch rather than being rendered from a value
        the batch never read.
        """

        names = display_fields(target)
        if not names:
            return False
        for field_name in names:
            if field_name not in target.fields:
                return False
            if not self.security.can_read_field(target.name, field_name, context):
                return False
            field = target.fields[field_name]
            computed = field.metadata.get("computed")
            if field.metadata["type"] == "collection" or (
                computed and computed.get("materialization") == "virtual"
            ):
                return False
        return True

    def commit(
        self,
        session: RecordSession,
        context: RequestContext,
        *,
        source: MutationSource = MutationSource.USER,
        acknowledged_warnings: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        session.ensure_active()
        entity = self.model.entity(session.entity)
        operation = "create" if session.is_new else "update"
        if source is MutationSource.ACTION:
            self.security.authorize_entity(entity, "read", context)
        else:
            self.security.authorize_entity(entity, operation, context)
        if not session.is_new and source is MutationSource.ACTION:
            self.security.require_row(
                entity.name,
                "read",
                self._policy_values(
                    entity.name,
                    session.original,
                    "read",
                    context,
                ),
                context,
            )
        elif not session.is_new:
            self.security.require_row(
                entity.name,
                "update",
                self._policy_values(
                    entity.name,
                    session.original,
                    "update",
                    context,
                ),
                context,
            )
        self._enforce_changes(entity, session, context, source)
        self._enforce_collection_changes(entity, session, context, source)
        values = deepcopy(session.values)
        input_issues = [
            *self._coerce_values(entity.name, values),
            *self._collection_membership_issues(entity, session, values),
            *self._missing_required_inputs(entity, values),
        ]
        if input_issues:
            raise ValidationFailed(input_issues)
        self._apply_generators(entity, values, context)
        if session.is_new:
            self._assign_generated_identity(entity, values)
        self._compute_entity(entity.name, values)
        derived_issues = self._coerce_values(entity.name, values)
        if derived_issues:
            raise ValidationFailed(derived_issues)
        issues = [
            *self._validate_entity(entity.name, values),
            *self._reference_filter_issues(entity, session, values),
            *self._attachment_issues(entity, session, values, context),
        ]
        # Errors always refuse; a warning refuses until its rule id is in the
        # acknowledged set. Info never blocks -- it rides the success as a
        # notice, alongside the warnings that were acknowledged, so the
        # caller sees what was accepted. Acknowledging an id that did not
        # fire is ignored: a client may echo a previous refusal wholesale.
        blockers = [
            issue
            for issue in issues
            if issue.severity == "error"
            or (
                issue.severity == "warning"
                and issue.rule not in acknowledged_warnings
            )
        ]
        if blockers:
            raise ValidationFailed(blockers)
        pending_notices = tuple(
            issue for issue in issues if issue.severity != "error"
        )
        if session.is_new:
            self.security.require_row(
                entity.name,
                "create",
                self._policy_values(entity.name, values, "create", context),
                context,
            )
        self._validate_uniqueness(entity, values, session.identity)
        write_operation = "read" if source is MutationSource.ACTION else operation
        claimed: list[str] = []
        attachment_plan = self._attachment_plan(entity, session, values)
        if attachment_plan and not session.is_new:
            self._claim_attachments(attachment_plan, str(session.identity), claimed)
        try:
            stored = self.repository.write(
                entity.name,
                values,
                primary_key=_primary_key(entity),
                version_field=_version_field(entity),
                expected_version=session.expected_version,
                is_new=session.is_new,
                row_criteria=(
                    ()
                    if session.is_new
                    else self.security.row_criteria(entity.name, write_operation)
                ),
                criteria_parameters=self.security.policy_parameters(context),
                references=_delete_references(self.model),
                collections=_delete_collections(self.model),
                on_written=lambda connection, written: self._record_audit(
                    entity,
                    (
                        RecordAuditOperation.CREATE
                        if session.is_new
                        else RecordAuditOperation.UPDATE
                    ),
                    written[_primary_key(entity)],
                    {} if session.is_new else deepcopy(session.original),
                    written,
                    context,
                    source,
                    connection=connection,
                ),
            )
        except RowPolicyMismatch as error:
            self._release_claims(claimed)
            raise AuthorizationError(
                f"{context.principal.identifier!r} may not {write_operation} this "
                f"{entity.name} record"
            ) from error
        except WriteIntegrityError as error:
            # The pre-check ran on its own connection before this transaction
            # opened, so a duplicate could have landed in between. Asking again
            # says which field collided; a constraint this service does not
            # model is not a validation failure and must keep its own error.
            self._release_claims(claimed)
            issues = self._unique_conflicts(entity, values, session.identity)
            if not issues:
                raise
            raise ValidationFailed(issues) from error
        except BaseException:
            self._release_claims(claimed)
            raise
        if attachment_plan and session.is_new:
            self._claim_attachments(
                attachment_plan, str(stored[_primary_key(entity)]), claimed
            )
        self._release_replaced_attachments(entity, session, values)
        was_new = session.is_new
        original = {} if was_new else deepcopy(session.original)
        session.identity = stored[_primary_key(entity)]
        version_field = _version_field(entity)
        session.expected_version = (
            stored.get(version_field) if version_field is not None else None
        )
        session.notices = pending_notices
        session.mark_committed(stored)
        del original, was_new  # the audit entry was written with the record
        return self._project(entity, stored, context)

    def _record_audit(
        self,
        entity: NormalizedEntity,
        operation: RecordAuditOperation,
        identity: Any,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        context: RequestContext,
        source: MutationSource,
        connection: Any = None,
    ) -> None:
        changes = _audit_changes(self.model, entity, operation, before, after)
        if not changes:
            return
        self.audit_store.record_audit(
            RecordAuditEvent(
                event_id=self._event_id_factory(),
                entity=entity.name,
                operation=operation,
                identity=deepcopy(identity),
                principal=context.principal.identifier,
                channel=str(context.channel),
                correlation_id=context.correlation_id,
                occurred_at=self._now(),
                source=str(source),
                changes=changes,
            ),
            connection=connection,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("record audit timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _missing_required_inputs(
        self, entity: NormalizedEntity, values: Mapping[str, Any]
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for field_name, field in entity.fields.items():
            metadata = field.metadata
            if not metadata.get("required"):
                continue
            if (
                metadata.get("generated_by")
                or metadata.get("primary_key")
                or metadata.get("computed")
                # The write assigns the token itself; a NULL one TIDE did
                # not write is healed by the commit, not refused by it.
                or metadata.get("concurrency_token")
            ):
                continue
            value = values.get(field_name)
            if value is None or value == "":
                issues.append(
                    ValidationIssue("required", f"{field_name} is required", (field_name,))
                )
        return issues

    def rollback(self, session: RecordSession) -> None:
        session.rollback()

    def _enforce_changes(
        self,
        entity: NormalizedEntity,
        session: RecordSession,
        context: RequestContext,
        source: MutationSource,
    ) -> None:
        unknown = set(session.values) - set(entity.fields)
        if unknown:
            raise ValidationFailed(
                [ValidationIssue("unknown_field", f"unknown field {name!r}", (name,)) for name in sorted(unknown)]
            )
        # One resolution of the entity's appearance rules for the whole write,
        # so a rule that disables a field is refused here rather than only
        # withheld by whichever renderer happened to ask.
        appearance = record_appearance(
            entity.metadata.get("appearance") or (),
            session.original,
        )
        for field_name in session.changed_fields:
            self._enforce_field_write(entity, field_name, context, source)
            if appearance.locks_record or field_name in appearance.locked:
                raise ImmutableFieldError(
                    field_name,
                    "an appearance rule disables it for this record",
                )
            immutable_when = entity.fields[field_name].metadata.get("immutable_when")
            if not immutable_when:
                continue
            try:
                locked = bool(evaluate_expression(immutable_when, session.original))
            except EVALUATION_ERRORS as error:
                # The same fallback `field_is_immutable` promised the renderer:
                # a condition this record's values defeat withholds the edit.
                raise ImmutableFieldError(
                    field_name,
                    f"condition {immutable_when!r} could not be evaluated",
                ) from error
            if locked:
                raise ImmutableFieldError(field_name, f"condition {immutable_when!r} is true")

    def _enforce_field_write(
        self,
        entity: NormalizedEntity,
        field_name: str,
        context: RequestContext,
        source: MutationSource,
    ) -> None:
        """Check that this mutation source may write this field at all."""

        metadata = entity.fields[field_name].metadata
        write_mode = metadata.get("write", "normal")
        if source is not MutationSource.SYSTEM and metadata.get("primary_key"):
            raise ImmutableFieldError(field_name, "primary keys are system-owned")
        if source is MutationSource.USER and (metadata.get("readonly") or write_mode != "normal"):
            raise ImmutableFieldError(field_name, f"write mode is {write_mode}")
        if source is MutationSource.ACTION and write_mode == "system":
            raise ImmutableFieldError(field_name, "field is system-owned")
        if (
            source is MutationSource.ACTION
            and metadata.get("readonly")
            and write_mode == "normal"
            and not metadata.get("computed")
        ):
            raise ImmutableFieldError(field_name, "readonly field is not action-owned")
        if source is not MutationSource.SYSTEM and not self.security.can_write_field(entity.name, field_name, context):
            raise AuthorizationError(f"field {field_name!r} is not writable")

    def _enforce_collection_changes(
        self,
        entity: NormalizedEntity,
        session: RecordSession,
        context: RequestContext,
        source: MutationSource,
    ) -> None:
        """Apply the same write rules to the items inside a collection.

        A child declares its own readonly, write-mode and field-policy rules,
        and nothing evaluated them: enforcement read only the owning record's
        changed fields. Comparison is against the child as loaded, because every
        renderer sends a collection back whole -- echoing a value the caller may
        not write is not an attempt to write it.

        `immutable_when` is deliberately not evaluated here. A child rule
        routinely addresses its parent (`invoice.status != 'draft'`), which is a
        scalar foreign key in this context rather than a record, so evaluating
        it would raise. The owning record's own rule already covers that case.
        """

        for field_name, field in entity.fields.items():
            if field.metadata.get("type") != "collection" or not field.target_entity:
                continue
            items = session.values.get(field_name)
            if not isinstance(items, list):
                continue
            target = self.model.entity(field.target_entity)
            key = _primary_key(target)
            loaded = {
                item[key]: item
                for item in (session.original.get(field_name) or ())
                if isinstance(item, Mapping) and item.get(key) is not None
            }
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                original = loaded.get(item.get(key), {})
                for child_field, value in item.items():
                    if child_field == key or child_field not in target.fields:
                        continue
                    if child_field in original and original[child_field] == value:
                        continue
                    if target.fields[child_field].metadata.get("computed"):
                        # Recomputed on the way in, so a supplied value cannot
                        # take effect. Renderers preview these totals in the
                        # inline editor and send them back; refusing would
                        # reject a value that was never going to be stored.
                        continue
                    self._enforce_field_write(target, child_field, context, source)

    def _collection_membership_issues(
        self,
        entity: NormalizedEntity,
        session: RecordSession,
        values: dict[str, Any],
    ) -> list[ValidationIssue]:
        """Check which rows a collection may claim, keep, and drop.

        Returning the key of a row already under this parent is how an edit is
        expressed. Inventing a key, or naming a row that belongs to a different
        parent, is not: the first lets a caller choose storage identity, and the
        second reassigns somebody else's row through this record.

        Dropping a row only means something when the collection deletes its
        orphans. Otherwise the row keeps pointing here, so the removal reverses
        itself on the next read; refusing is the honest answer.
        """

        issues: list[ValidationIssue] = []
        for field_name, field in entity.fields.items():
            if field.metadata.get("type") != "collection" or not field.target_entity:
                continue
            items = values.get(field_name)
            if not isinstance(items, list):
                # An absent collection means "leave it alone", not "empty it".
                continue
            key = _primary_key(self.model.entity(field.target_entity))
            owned = {
                item[key]
                for item in (session.original.get(field_name) or ())
                if isinstance(item, Mapping) and item.get(key) is not None
            }
            retained: set[Any] = set()
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                identity = item.get(key)
                if identity is None:
                    continue
                if identity in owned:
                    retained.add(identity)
                else:
                    issues.append(
                        ValidationIssue(
                            "identity",
                            f"{field_name} item {key} {identity!r} does not belong to this record",
                            (field_name,),
                        )
                    )
            if owned - retained and not field.metadata.get("orphan_delete"):
                issues.append(
                    ValidationIssue(
                        "orphan",
                        f"{field_name} does not delete orphans, so its items "
                        "cannot be removed through this record",
                        (field_name,),
                    )
                )
        return issues

    def _coerce_values(self, entity_name: str, values: dict[str, Any]) -> list[ValidationIssue]:
        """Coerce present values to their declared field types before any evaluation."""

        entity = self.model.entity(entity_name)
        issues: list[ValidationIssue] = [
            ValidationIssue("unknown_field", f"unknown field {name!r}", (name,))
            # _enforce_changes rejects unknown values on the record being
            # committed, but it never descends; a collection item has to be
            # held to the same contract or it reaches storage unchecked.
            for name in sorted(set(values) - set(entity.fields))
        ]
        for field_name, field in entity.fields.items():
            value = values.get(field_name)
            if value is None:
                continue
            field_type = field.metadata["type"]
            if field_type == "reference":
                if field.target_entity is None:
                    issues.append(
                        ValidationIssue(
                            "reference",
                            f"{field_name} has no reference target",
                            (field_name,),
                        )
                    )
                    continue
                target = self.model.entity(field.target_entity)
                target_key = target.field(_primary_key(target))
                target_key_type = target_key.metadata["type"]
                coerced, valid = _coerce_scalar(target_key_type, value)
                if not valid:
                    issues.append(
                        ValidationIssue(
                            "type",
                            f"{field_name} must be a {target_key_type} reference value",
                            (field_name,),
                        )
                    )
                    continue
                values[field_name] = coerced
                if not self.repository.exists(field.target_entity, coerced):
                    issues.append(
                        ValidationIssue(
                            "reference",
                            f"{field_name} must reference an existing {field.target_entity}",
                            (field_name,),
                        )
                    )
                continue
            if field_type == "collection":
                if not isinstance(value, list):
                    issues.append(
                        ValidationIssue(
                            "type", f"{field_name} must be a list of records", (field_name,)
                        )
                    )
                    continue
                if field.target_entity:
                    for item in value:
                        if not isinstance(item, dict):
                            issues.append(
                                ValidationIssue(
                                    "type",
                                    f"{field_name} items must be records",
                                    (field_name,),
                                )
                            )
                            continue
                        issues.extend(self._coerce_values(field.target_entity, item))
                continue
            coerced, valid = _coerce_scalar(field_type, value)
            if valid:
                values[field_name] = coerced
            else:
                issues.append(
                    ValidationIssue(
                        "type", f"{field_name} must be a {field_type} value", (field_name,)
                    )
                )
        return issues

    def _apply_generators(self, entity: NormalizedEntity, values: dict[str, Any], context: RequestContext) -> None:
        for field_name, field in entity.fields.items():
            reference = field.metadata.get("generated_by")
            if reference and values.get(field_name) in {None, ""}:
                generator = self._generators.get(reference)
                if generator is None:
                    raise RuntimeError(f"no generator registered for {reference}")
                values[field_name] = generator(values, context, self.repository)

    def _assign_generated_identity(
        self, entity: NormalizedEntity, values: dict[str, Any]
    ) -> None:
        """Give a new record its uuid key here, before anything is written.

        An integer key is allocated by the database and read back afterwards,
        but a uuid needs no round trip -- and having it up front is what lets a
        master-detail write point child rows at a parent row that does not
        exist yet.

        A field declaring a ``server_default`` is deferring to the database's
        own generator instead. That is how a legacy ``NEWSEQUENTIALID()``
        column keeps its sequential keys, and with them the clustered index
        that random values would fragment, so that one is left alone and read
        back like an integer.
        """

        field = entity.field(_primary_key(entity))
        if field.metadata.get("type") != "uuid":
            return
        if field.metadata.get("server_default") is not None:
            return
        if values.get(field.name) is None:
            values[field.name] = uuid4()

    def _compute_entity(self, entity_name: str, values: dict[str, Any]) -> None:
        entity = self.model.entity(entity_name)
        for field_name, field in entity.fields.items():
            if field.metadata["type"] == "collection" and field.target_entity:
                items = values.get(field_name) or []
                for item in items:
                    self._compute_entity(field.target_entity, item)
        remaining = {
            name
            for name, field in entity.fields.items()
            if field.metadata.get("computed", {}).get("materialization") == "stored"
        }
        while remaining:
            progressed = False
            for field_name in tuple(remaining):
                field = entity.fields[field_name]
                local_dependencies = {dependency.split(".", 1)[0] for dependency in field.dependencies}
                if local_dependencies & remaining:
                    continue
                values[field_name] = evaluate_expression(
                    field.metadata["computed"]["expression"], values
                )
                remaining.remove(field_name)
                progressed = True
            if not progressed:
                raise RuntimeError(f"computed dependency cycle in {entity_name}")

    def _reference_filter_issues(
        self,
        entity: NormalizedEntity,
        session: RecordSession,
        values: dict[str, Any],
    ) -> list[ValidationIssue]:
        """Refuse newly chosen rows a reference's lookup_filter excludes.

        Only the choosing moment is gated: a value that arrives unchanged
        never re-fires, whoever wrote it -- TIDE tolerates rows it did not
        write, and a history referencing a since-retired row stays editable.
        A target that does not load keeps the repository's own behaviour;
        eligibility is a question about rows that exist.
        """

        issues: list[ValidationIssue] = []
        original: Mapping[str, Any] = {} if session.is_new else session.original
        self._collect_reference_filter_issues(entity, values, original, issues)
        return issues

    def _collect_reference_filter_issues(
        self,
        entity: NormalizedEntity,
        values: Mapping[str, Any],
        original: Mapping[str, Any],
        issues: list[ValidationIssue],
    ) -> None:
        for field_name, field in entity.fields.items():
            metadata = field.metadata
            if metadata["type"] == "reference" and field.target_entity:
                declared = metadata.get("lookup_filter")
                value = values.get(field_name)
                if not declared or value is None:
                    continue
                if original.get(field_name) == value:
                    continue
                try:
                    # A model-level read: eligibility is the model's rule
                    # about the row, so the writer's row policies on the
                    # target do not participate.
                    target_row = self.repository.get(field.target_entity, value)
                except NotFoundError:
                    continue
                if not evaluate_expression(str(declared), target_row):
                    issues.append(
                        ValidationIssue(
                            "lookup_filter",
                            f"{field_name} references a row its lookup filter excludes",
                            (field_name,),
                        )
                    )
            elif metadata["type"] == "collection" and field.target_entity:
                child_entity = self.model.entity(field.target_entity)
                child_key = _primary_key(child_entity)
                original_children: dict[Any, Mapping[str, Any]] = {}
                for item in original.get(field_name) or ():
                    identity = item.get(child_key)
                    if identity is not None:
                        original_children[identity] = item
                for item in values.get(field_name) or ():
                    self._collect_reference_filter_issues(
                        child_entity,
                        item,
                        original_children.get(item.get(child_key), {}),
                        issues,
                    )

    def _validate_entity(
        self,
        entity_name: str,
        values: dict[str, Any],
        *,
        skip_fields: frozenset[str] = frozenset(),
    ) -> list[ValidationIssue]:
        entity = self.model.entity(entity_name)
        issues: list[ValidationIssue] = []
        for field_name, field in entity.fields.items():
            if field_name in skip_fields:
                continue
            metadata = field.metadata
            if metadata.get("concurrency_token"):
                # The write assigns the token itself: a NULL one TIDE did
                # not write is healed by the commit, so validating its
                # stored value would refuse the very edit that fixes it.
                continue
            value = values.get(field_name)
            if metadata.get("required") and (value is None or value == ""):
                issues.append(ValidationIssue("required", f"{field_name} is required", (field_name,)))
                continue
            if value is not None and metadata.get("minimum") is not None and value < metadata["minimum"]:
                issues.append(ValidationIssue("minimum", f"{field_name} is below its minimum", (field_name,)))
            if value is not None and metadata.get("maximum") is not None and value > metadata["maximum"]:
                issues.append(ValidationIssue("maximum", f"{field_name} exceeds its maximum", (field_name,)))
            if value is not None and metadata["type"] == "choice" and value not in metadata.get("choices", ()):
                issues.append(ValidationIssue("choice", f"{field_name} has an invalid choice", (field_name,)))
            if value is not None and not _value_is_captioned(value, metadata):
                # A captioned field stores a code that stands for something.
                # Refusing an uncaptioned one is what makes the map a contract
                # rather than a display convenience, and it applies on every
                # surface because it is enforced here.
                issues.append(
                    ValidationIssue(
                        "value",
                        f"{field_name} is not one of its declared values",
                        (field_name,),
                    )
                )
            if value is not None and metadata["type"] == "decimal":
                issues.extend(_decimal_shape_issues(field_name, value, metadata))
            edit_mask = metadata.get("edit_mask")
            if (
                value is not None
                and metadata["type"] == "string"
                and isinstance(edit_mask, Mapping)
                and re.fullmatch(str(edit_mask["regex"]), value) is None
            ):
                issues.append(
                    ValidationIssue(
                        "edit_mask",
                        f"{field_name} does not match its required format",
                        (field_name,),
                    )
                )
            if metadata["type"] == "collection" and field.target_entity:
                inverse = metadata.get("inverse")
                for item in value or []:
                    issues.extend(
                        self._validate_entity(
                            field.target_entity,
                            item,
                            skip_fields=frozenset({inverse}) if inverse else frozenset(),
                        )
                    )
        for rule in entity.metadata.get("validations", ()):
            when = rule.get("when")
            if when and not evaluate_expression(when, values):
                continue
            assertion = rule.get("assert")
            if assertion and not evaluate_expression(assertion, values):
                issues.append(
                    ValidationIssue(
                        rule["id"],
                        rule["message"],
                        tuple(rule.get("fields", ())),
                        rule.get("severity", "error"),
                    )
                )
        return issues

    def _unique_conflicts(
        self, entity: NormalizedEntity, values: dict[str, Any], identity: Any
    ) -> list[ValidationIssue]:
        """Name the unique fields whose value another record already holds.

        A null never collides, matching the database: SQL uniqueness ignores
        NULL, so two customers may both omit an email.
        """

        issues: list[ValidationIssue] = []
        for field_name, field in entity.fields.items():
            if not field.metadata.get("unique") or values.get(field_name) is None:
                continue
            if self.repository.unique_conflict(
                entity.name,
                field_name,
                values[field_name],
                exclude_identity=identity,
            ):
                issues.append(
                    ValidationIssue(
                        "unique",
                        f"{field_name} must be unique",
                        (field_name,),
                    )
                )
        return issues

    def _attachment_plan(
        self,
        entity: NormalizedEntity,
        session: RecordSession,
        values: Mapping[str, Any],
    ) -> tuple[tuple[str, str], ...]:
        original: Mapping[str, Any] = {} if session.is_new else session.original
        return claim_plan(entity, values, original)

    def _attachment_issues(
        self,
        entity: NormalizedEntity,
        session: RecordSession,
        values: Mapping[str, Any],
        context: RequestContext,
    ) -> list[ValidationIssue]:
        plan = self._attachment_plan(entity, session, values)
        if not plan:
            return []
        if self.attachments is None:
            # Said in the field's own words rather than raised as a server
            # fault: a deployment that never configured a file store is a
            # misconfiguration, and the person holding the form should be
            # told which field cannot be saved.
            return [
                ValidationIssue(
                    "attachment",
                    "this server has nowhere to keep files",
                    (field_name,),
                )
                for field_name, _ in plan
            ]
        issues: list[ValidationIssue] = []
        for field_name, guid in plan:
            issues.extend(
                self.attachments.claim_issues(
                    entity.name,
                    field_name,
                    guid,
                    principal=context.principal.identifier,
                )
            )
        return issues

    def _claim_attachments(
        self,
        plan: Sequence[tuple[str, str]],
        record_id: str,
        claimed: list[str],
    ) -> None:
        """Attach each named upload to this record, remembering what stuck.

        Claiming before the write where the identity is already known, so a
        crash in between leaves a claimed row a reconciliation can see rather
        than bytes the sweep would reclaim out from under a written record.
        A create cannot do that -- its identity does not exist until the row
        does -- so it claims immediately afterwards instead, which is why the
        caller passes the list to compensate from.
        """

        if self.attachments is None:
            return
        for _, guid in plan:
            self.attachments.claim(guid, record_id)
            claimed.append(guid)

    def _release_claims(self, claimed: Sequence[str]) -> None:
        if self.attachments is None or not claimed:
            return
        self.attachments.release(claimed)

    def _release_replaced_attachments(
        self,
        entity: NormalizedEntity,
        session: RecordSession,
        values: Mapping[str, Any],
    ) -> None:
        """Let go of what a save replaced or cleared, once it is durable.

        After the write, never before: a save that failed must leave the
        record holding exactly the file it held.
        """

        if self.attachments is None or session.is_new:
            return
        replaced = released_guids(entity, values, session.original)
        if replaced:
            self.attachments.release(replaced)

    def _validate_uniqueness(self, entity: NormalizedEntity, values: dict[str, Any], identity: Any) -> None:
        issues = self._unique_conflicts(entity, values, identity)
        if issues:
            raise ValidationFailed(issues)

    def _relationship_plan(
        self,
        entity_name: str,
        context: RequestContext,
        *,
        operations: tuple[str, ...],
    ) -> RelationshipLoadPlan:
        loads: dict[tuple[str, str], RelationshipLoad] = {}
        visited: set[tuple[str, tuple[str, ...]]] = set()

        def visit(current_name: str, policy_operations: tuple[str, ...]) -> None:
            visit_key = current_name, policy_operations
            if visit_key in visited:
                return
            visited.add(visit_key)
            current = self.model.entity(current_name)
            required = _policy_collection_edges(
                self.model,
                self.security,
                current_name,
                policy_operations,
            )
            for field_name, field in current.fields.items():
                if field.metadata["type"] != "collection" or not field.target_entity:
                    continue
                target = self.model.entity(field.target_entity)
                visible = self.security.can_read_field(
                    current_name,
                    field_name,
                    context,
                ) and self.security.can_access_entity(target, "read", context)
                if not visible and (current_name, field_name) not in required:
                    continue
                loads[(current_name, field_name)] = RelationshipLoad(
                    source_entity=current_name,
                    field=field_name,
                    target_entity=target.name,
                    order_by=field.metadata.get("order_by"),
                )
                visit(target.name, ("read",))

        visit(entity_name, operations)
        entity_criteria = tuple(
            (candidate, criteria)
            for candidate in self.model.entities
            if (criteria := self.security.row_criteria(candidate, "read"))
        )
        return RelationshipLoadPlan(
            loads=tuple(loads.values()),
            entity_criteria=entity_criteria,
            criteria_parameters=self.security.policy_parameters(context),
            max_depth=self.relationship_max_depth,
            max_items=self.relationship_max_items,
        )

    def _project(
        self,
        entity: NormalizedEntity,
        source: Mapping[str, Any],
        context: RequestContext,
        *,
        depth: int = 0,
    ) -> dict[str, Any]:
        values = deepcopy(dict(source))
        for field_name, field in entity.fields.items():
            computed = field.metadata.get("computed")
            if computed and computed.get("materialization") == "virtual":
                try:
                    values[field_name] = evaluate_expression(
                        computed["expression"], values
                    )
                except EVALUATION_ERRORS:
                    # A value this record's stored fields defeat -- a null or
                    # a zero divisor in a row TIDE did not write -- projects
                    # as empty, the same answer the editor's preview gives.
                    values[field_name] = None
        result: dict[str, Any] = {}
        for field_name, field in entity.fields.items():
            if not self.security.can_read_field(entity.name, field_name, context):
                result[field_name] = PROTECTED
                continue
            value = values.get(field_name)
            if field.metadata["type"] == "collection" and field.target_entity:
                target = self.model.entity(field.target_entity)
                if not self.security.can_access_entity(target, "read", context):
                    result[field_name] = PROTECTED
                    continue
                items = value or []
                if not isinstance(items, (list, tuple)):
                    raise ValueError(
                        f"relationship {entity.name}.{field_name!s} is not a collection"
                    )
                if not all(isinstance(item, Mapping) for item in items):
                    raise ValueError(
                        f"relationship {entity.name}.{field_name!s} contains an invalid record"
                    )
                visible_items = [
                    item
                    for item in items
                    if self.security.row_allowed(
                        target.name,
                        "read",
                        self._policy_values(target.name, item, "read", context),
                        context,
                    )
                ]
                relationship = f"{entity.name}.{field_name}"
                if visible_items and depth >= self.relationship_max_depth:
                    raise RelationshipExpansionLimit(relationship, "depth")
                if len(visible_items) > self.relationship_max_items:
                    raise RelationshipExpansionLimit(relationship, "item")
                result[field_name] = [
                    self._project(target, item, context, depth=depth + 1)
                    for item in visible_items
                ]
            else:
                result[field_name] = deepcopy(value)
        return result

    def _policy_values(
        self,
        entity_name: str,
        source: Mapping[str, Any],
        operation: str,
        context: RequestContext,
        *,
        cache: dict[tuple[str, Any], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        entity = self.model.entity(entity_name)
        paths = {
            path
            for criteria in self.security.row_criteria(entity_name, operation)
            for path in _expression_paths(criteria)
            if path and path[0] in entity.fields
        }
        if not paths:
            # Nothing to graft means nothing gets mutated: evaluation only
            # reads, so the page-sized deepcopy would buy nothing. Most
            # entities declare no row policy at all.
            return dict(source)
        values = deepcopy(dict(source))
        relationship_cache = cache if cache is not None else {}
        for path in paths:
            self._expand_policy_path(
                entity,
                values,
                path,
                relationship_cache,
                context,
            )
        return values

    def _expand_policy_path(
        self,
        entity: NormalizedEntity,
        values: dict[str, Any],
        path: tuple[str, ...],
        cache: dict[tuple[str, Any], dict[str, Any]],
        context: RequestContext,
    ) -> None:
        field = entity.fields.get(path[0])
        if field is None or len(path) == 1 or field.target_entity is None:
            return
        value = values.get(field.name)
        if value is None:
            return
        target = self.model.entity(field.target_entity)
        remainder = path[1:]

        if field.metadata["type"] == "collection":
            if not isinstance(value, (list, tuple)):
                raise ValueError(
                    f"collection {entity.name}.{field.name} is not available for policy evaluation"
                )
            expanded: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, Mapping):
                    raise ValueError(
                        f"collection {entity.name}.{field.name} contains an invalid policy value"
                    )
                related = deepcopy(dict(item))
                self._expand_policy_path(
                    target,
                    related,
                    remainder,
                    cache,
                    context,
                )
                expanded.append(related)
            values[field.name] = expanded
            return

        if field.metadata["type"] != "reference":
            return
        if isinstance(value, Mapping):
            related = deepcopy(dict(value))
            if not self.security.row_allowed(target.name, "read", related, context):
                raise AuthorizationError("related record failed its row-policy recheck")
        else:
            key = (target.name, value)
            if key not in cache:
                try:
                    cache[key] = self.repository.get(
                        target.name,
                        value,
                        row_criteria=self.security.row_criteria(target.name, "read"),
                        criteria_parameters=self.security.policy_parameters(context),
                        relationships=self._relationship_plan(
                            target.name,
                            context,
                            operations=("read",),
                        ),
                    )
                except RowPolicyMismatch as error:
                    raise AuthorizationError(
                        "related record failed its row-policy recheck"
                    ) from error
            related = deepcopy(cache[key])
        self._expand_policy_path(
            target,
            related,
            remainder,
            cache,
            context,
        )
        values[field.name] = related


@dataclass(frozen=True, slots=True)
class DistinctValues:
    """A column's bounded distinct values, each beside its display name.

    The display is None for anything that is not a resolvable reference;
    ``truncated`` says the column held more than the answer carries.
    """

    values: tuple[tuple[Any, str | None], ...]
    truncated: bool


def _primary_key(entity: NormalizedEntity) -> str:
    return entity.primary_key.name


def _summary_scale(metadata: Mapping[str, Any]) -> int:
    """The scale an average carries: the field's own, or two places.

    Two is the fallback for integer fields -- a mean of integers is still a
    mean -- and for a decimal that declared no scale.
    """

    scale = metadata.get("scale")
    return scale if isinstance(scale, int) else 2


def _summary_average(total: Any, count: Any, *, scale: int) -> Decimal | None:
    """Exact sum over count, in the rounding the expression engine uses."""

    if not count or total is None:
        return None
    quantum = Decimal(1).scaleb(-scale)
    return (Decimal(total) / Decimal(count)).quantize(
        quantum, rounding=ROUND_HALF_EVEN
    )


def _audit_changes(
    model: ApplicationModel,
    entity: NormalizedEntity,
    operation: RecordAuditOperation,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> tuple[AuditFieldChange, ...]:
    changes: list[AuditFieldChange] = []
    for field_name, field in entity.fields.items():
        capture = str(field.metadata.get("audit", "changes"))
        if capture == "none":
            continue
        before_present = (
            operation is not RecordAuditOperation.CREATE and field_name in before
        )
        after_present = (
            operation is not RecordAuditOperation.DELETE and field_name in after
        )
        before_value = before.get(field_name)
        after_value = after.get(field_name)
        if (
            before_present == after_present
            and before_value == after_value
        ):
            continue

        value_mode = AuditValueMode.FIELD_ONLY
        if capture == "values":
            if _field_has_restricted_read(model, entity.name, field_name):
                value_mode = AuditValueMode.REDACTED
            elif field.metadata["type"] != "collection" and _audit_values_fit(
                before_value if before_present else None,
                after_value if after_present else None,
            ):
                value_mode = AuditValueMode.RECORDED
        changes.append(
            AuditFieldChange(
                field=field_name,
                before_present=before_present,
                after_present=after_present,
                value_mode=value_mode,
                before=(deepcopy(before_value) if value_mode is AuditValueMode.RECORDED else None),
                after=(deepcopy(after_value) if value_mode is AuditValueMode.RECORDED else None),
            )
        )
    return tuple(changes)


def _audit_values_fit(before: Any, after: Any) -> bool:
    try:
        return all(
            len(serialize_action_value(value).encode("utf-8"))
            <= _MAX_AUDIT_VALUE_BYTES
            for value in (before, after)
        )
    except (TypeError, ValueError):
        return False


def _field_has_restricted_read(
    model: ApplicationModel,
    entity_name: str,
    field_name: str,
    visited: frozenset[tuple[str, str]] = frozenset(),
) -> bool:
    key = entity_name, field_name
    if key in visited:
        return False
    if any(
        policy["entity"] == entity_name
        and policy["field"] == field_name
        and policy.get("read") is not None
        for policy in model.field_policies
    ):
        return True
    field = model.entity(entity_name).field(field_name)
    if not field.metadata.get("computed"):
        return False
    visited = visited | {key}
    for dependency in field.dependencies:
        current = model.entity(entity_name)
        for part in dependency.split("."):
            if _field_has_restricted_read(model, current.name, part, visited):
                return True
            dependency_field = current.field(part)
            if dependency_field.target_entity is None:
                break
            current = model.entity(dependency_field.target_entity)
    return False


def _delete_references(model: ApplicationModel) -> tuple[DeleteReference, ...]:
    return tuple(
        DeleteReference(
            source_entity=entity.name,
            source_field=field.name,
            source_primary_key=_primary_key(entity),
            target_entity=field.target_entity,
            on_delete=str(field.metadata.get("on_delete") or "restrict"),
        )
        for entity in model.entities.values()
        for field in entity.fields.values()
        if field.metadata["type"] == "reference" and field.target_entity is not None
    )


def _delete_collections(model: ApplicationModel) -> tuple[DeleteCollection, ...]:
    return tuple(
        DeleteCollection(
            parent_entity=entity.name,
            parent_field=field.name,
            child_entity=field.target_entity,
            child_primary_key=_primary_key(model.entity(field.target_entity)),
        )
        for entity in model.entities.values()
        for field in entity.fields.values()
        if field.metadata["type"] == "collection" and field.target_entity is not None
    )


class _ExpressionPathCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.paths: set[tuple[str, ...]] = set()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parts = _attribute_parts(node)
        if parts:
            self.paths.add(parts)

    def visit_Name(self, node: ast.Name) -> None:
        self.paths.add((node.id,))

    def visit_Call(self, node: ast.Call) -> None:
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)


def _expression_paths(expression: str) -> tuple[tuple[str, ...], ...]:
    """Return the record paths an expression reads.

    Policy criteria may name a ``$`` parameter, which is not valid Python, so
    apply the same rewrite the evaluators use and drop the results: a parameter
    is supplied by the caller, not read from the record.
    """

    rewritten = PARAMETER_PATTERN.sub(r"__tide_parameter_\1", expression)
    collector = _ExpressionPathCollector()
    collector.visit(ast.parse(rewritten, mode="eval"))
    return tuple(
        sorted(
            path
            for path in collector.paths
            if not path[0].startswith("__tide_parameter_")
        )
    )


def _policy_collection_edges(
    model: ApplicationModel,
    security: SecurityEngine,
    entity_name: str,
    operations: tuple[str, ...],
) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for operation in operations:
        for criteria in security.row_criteria(entity_name, operation):
            for path in _expression_paths(criteria):
                current = model.entity(entity_name)
                for part in path:
                    field = current.fields.get(part)
                    if field is None:
                        break
                    if field.metadata["type"] == "collection":
                        edges.add((current.name, field.name))
                    if field.target_entity is None:
                        break
                    current = model.entity(field.target_entity)
    return edges


def _attribute_parts(node: ast.Attribute) -> tuple[str, ...]:
    parts: list[str] = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return ()
    parts.append(value.id)
    return tuple(reversed(parts))


def _field_duplicates(field: NormalizedField) -> bool:
    """Whether a duplicate draft carries this field of the original.

    The line each exclusion draws: a person could not have typed it on a
    new record. Identity and system/action-written values are allocated,
    computed values are derived, and a file's bytes belong to the record
    that received them. A collection travels only when this record owns
    row creation -- the same `cascade` that lets a create carry rows.
    """

    metadata = field.metadata
    if metadata.get("primary_key") or metadata.get("readonly"):
        return False
    if metadata.get("write", "normal") != "normal":
        return False
    if metadata.get("computed"):
        return False
    kind = str(metadata["type"])
    if kind == "file":
        return False
    if kind == "collection":
        return "create" in tuple(metadata.get("cascade") or ())
    return True


def _version_field(entity: NormalizedEntity) -> str | None:
    field = entity.version_field
    return None if field is None else field.name


def _coerce_scalar(field_type: str, value: Any) -> tuple[Any, bool]:
    if field_type == "decimal":
        decimal_value = _as_decimal(value)
        return (decimal_value, True) if decimal_value is not None else (value, False)
    if field_type == "integer":
        return value, isinstance(value, int) and not isinstance(value, bool)
    if field_type in {"string", "choice"}:
        return value, isinstance(value, str)
    if field_type == "boolean":
        return value, isinstance(value, bool)
    if field_type == "date":
        return value, isinstance(value, date) and not isinstance(value, datetime)
    if field_type == "datetime":
        return value, isinstance(value, datetime)
    if field_type == "uuid":
        return value, isinstance(value, UUID)
    if field_type == "file":
        # The stored value is an attachment's key, not its contents: 36
        # characters of `uuid4`. Whether that key is one this record may
        # claim is the attachment service's question, not this one's.
        return value, isinstance(value, str) and len(value) == 36
    return value, True


def _value_is_captioned(value: Any, metadata: Mapping[str, Any]) -> bool:
    """True when a field declares no value map, or the code is in it."""

    captions = declared_values(metadata)
    return not captions or any(
        code == value and type(code) is type(value) for code, _ in captions
    )


def _decimal_shape_issues(
    field_name: str,
    value: Decimal,
    metadata: Mapping[str, Any],
) -> list[ValidationIssue]:
    digits, exponent = value.as_tuple().digits, value.as_tuple().exponent
    if not isinstance(exponent, int):
        return [
            ValidationIssue(
                "decimal",
                f"{field_name} must be a finite decimal value",
                (field_name,),
            )
        ]
    issues: list[ValidationIssue] = []
    fractional_digits = max(-exponent, 0)
    scale = metadata.get("scale")
    if scale is not None and fractional_digits > int(scale):
        issues.append(
            ValidationIssue(
                "scale",
                f"{field_name} allows at most {scale} decimal places",
                (field_name,),
            )
        )
    precision = metadata.get("precision")
    if precision is not None:
        integer_digits = 0 if value.is_zero() else max(len(digits) + exponent, 0)
        allowed_integer_digits = int(precision) - int(scale or 0)
        if integer_digits > allowed_integer_digits:
            issues.append(
                ValidationIssue(
                    "precision",
                    f"{field_name} allows at most {allowed_integer_digits} "
                    "integer digits",
                    (field_name,),
                )
            )
    return issues


def _normalize_filter(
    model: ApplicationModel,
    entity: NormalizedEntity,
    condition: FilterCondition,
) -> FilterCondition:
    field = entity.fields[condition.field]
    operator = condition.operator
    if operator not in FILTER_OPERATORS:
        raise ValueError(f"unsupported filter operator {operator!r}")
    field_type = field.metadata["type"]
    if operator == "in":
        values = condition.value
        if not isinstance(values, (list, tuple)) or len(values) == 0:
            raise ValueError(
                "in filters require a non-empty list of values"
            )
        element_type = field_type
        if field_type == "reference" and field.target_entity:
            target = model.entity(field.target_entity)
            element_type = target.field(_primary_key(target)).metadata["type"]
        coerced: list[Any] = []
        for element in values:
            if element is None:
                # The blank entry: unset counts as chosen.
                coerced.append(None)
                continue
            value, valid = _coerce_scalar(element_type, element)
            if not valid:
                raise ValueError(
                    f"filter value for {condition.field!r} must be a "
                    f"{element_type} value"
                )
            coerced.append(value)
        return FilterCondition(condition.field, "in", tuple(coerced))
    if operator in {"contains", "icontains"}:
        if field_type not in {"string", "choice"} or not isinstance(
            condition.value, str
        ):
            raise ValueError(
                f"{operator} filters require a string field and value"
            )
        return condition
    if condition.value is None:
        if operator not in {"eq", "ne"}:
            raise ValueError("null supports only eq and ne filters")
        return condition
    if field_type == "reference" and field.target_entity:
        target = model.entity(field.target_entity)
        field_type = target.field(_primary_key(target)).metadata["type"]
    coerced, valid = _coerce_scalar(field_type, condition.value)
    if not valid:
        raise ValueError(
            f"filter value for {condition.field!r} must be a {field_type} value"
        )
    if field_type == "boolean" and operator not in {"eq", "ne"}:
        raise ValueError("boolean fields support only eq and ne filters")
    return FilterCondition(condition.field, operator, coerced)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, float):
        candidate = Decimal(str(value))
    elif isinstance(value, str):
        try:
            candidate = Decimal(value.strip())
        except InvalidOperation:
            return None
    else:
        return None
    return candidate if candidate.is_finite() else None
