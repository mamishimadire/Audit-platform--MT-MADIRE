"""
Relationship measurement for Gateway-only data sources.

A Gateway reaches a client's database from INSIDE the client's network, so the
platform cannot measure how that database's tables relate. The Gateway does it for
itself, using the same pull pattern as due audit tests (it always initiates):

  1. GET  relationship-requests      -> the column pairs worth measuring (the platform
                                        already knows the schema, the profiles and what
                                        every control needs joined)
  2. the Gateway counts, per pair, how many distinct child values there are and how
     many of them exist on the parent side, on its own database
  3. POST relationship-measurements  -> ONLY those counts come back

No value ever leaves the client's network, and the platform accepts a count only for a
pair it asked about: a Gateway can never introduce an edge of its own.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.relationship_inference import Measurement
from app.models.data_source import DataConnection
from app.schemas.data_source import RelationshipMeasurementIn, RelationshipPairRequest, RelationshipRequestOut
from app.services.data_source_service import _connected_direct_connections, build_inference_candidates, store_inferred_edges

# How long a measurement stays fresh before the Gateway is asked again.
MEASUREMENT_FRESH_FOR = timedelta(hours=24)
# A count above this is not a real count (and would overflow nothing, but is never legitimate here).
_MAX_COUNT = 1_000_000_000


def _due(connection: DataConnection, now: datetime) -> bool:
    return connection.relationships_measured_at is None or now - connection.relationships_measured_at >= MEASUREMENT_FRESH_FOR


def relationship_requests_for_gateway(db: Session, *, gateway_id: uuid.UUID) -> list[RelationshipRequestOut]:
    """The pairs each of this Gateway's connections should measure now. A source
    that also has a connected direct connection is measured by the platform
    itself, so it is not asked of the Gateway."""
    now = datetime.now(timezone.utc)
    out: list[RelationshipRequestOut] = []
    for connection in db.scalars(select(DataConnection).where(DataConnection.gateway_id == gateway_id)):
        if not _due(connection, now) or _connected_direct_connections(db, connection.data_source_id):
            continue
        pairs = [
            RelationshipPairRequest(
                child_field_id=uuid.UUID(c.child.field_id),
                parent_field_id=uuid.UUID(c.parent.field_id),
                child_entity=c.child.entity_name,
                child_column=c.child.name,
                child_type=c.child.data_type,
                parent_entity=c.parent.entity_name,
                parent_column=c.parent.name,
                parent_type=c.parent.data_type,
            )
            for c in build_inference_candidates(db, connection.data_source_id)
        ]
        if pairs:
            out.append(RelationshipRequestOut(connection_id=connection.connection_id, pairs=pairs))
    return out


def _valid(m: RelationshipMeasurementIn) -> bool:
    counts = [m.child_distinct, m.matched_distinct, m.parent_distinct] + ([m.parent_rows] if m.parent_rows is not None else [])
    if any(c < 0 or c > _MAX_COUNT for c in counts):
        return False
    if m.matched_distinct > m.child_distinct:
        return False
    if m.parent_rows is not None and m.parent_distinct > m.parent_rows:
        return False
    return True


def store_gateway_measurements(
    db: Session, *, connection: DataConnection, measurements: list[RelationshipMeasurementIn]
) -> int:
    """Stores what a Gateway measured for its connection. Returns how many edges the
    data supported. Pairs the platform did not ask about, and impossible numbers,
    are ignored. The connection is stamped as measured even when nothing was
    accepted, so a Gateway that can't measure isn't asked again on every poll."""
    by_pair = {(c.child.field_id, c.parent.field_id): c for c in build_inference_candidates(db, connection.data_source_id)}
    measured = []
    scope: set[tuple[str, str]] = set()
    for m in measurements:
        key = (str(m.child_field_id), str(m.parent_field_id))
        candidate = by_pair.get(key)
        if candidate is None or not _valid(m):
            continue
        measured.append(
            (
                candidate,
                Measurement(
                    child_distinct=m.child_distinct, matched_distinct=m.matched_distinct,
                    parent_distinct=m.parent_distinct, capped=m.capped, parent_rows=m.parent_rows,
                ),
            )
        )
        scope.add(key)
    stored = store_inferred_edges(db, connection.data_source_id, measured, scope=scope)
    connection.relationships_measured_at = datetime.now(timezone.utc)
    db.commit()
    return stored
