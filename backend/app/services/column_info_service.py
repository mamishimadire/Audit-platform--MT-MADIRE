"""
One query that loads what the platform already knows about a set of tables'
columns — name, primary-key flag, declared type and (when profiling has run)
a value profile — as the immutable ColumnInfo the scorers in
app.core.value_profile take. No call to the client's database: everything here
was discovered or profiled earlier.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.value_profile import ColumnInfo, ColumnProfile
from app.models.data_source import DataField, DataFieldProfile


def _profile_from_row(row: DataFieldProfile | None) -> ColumnProfile | None:
    if row is None:
        return None
    return ColumnProfile(
        sample_size=row.sample_size,
        null_ratio=row.null_ratio,
        distinct_count=row.distinct_count,
        distinct_ratio=row.distinct_ratio,
        value_kind=row.value_kind,  # type: ignore[arg-type]
        top_values=tuple(row.top_values) if row.top_values is not None else None,
        max_length=row.max_length,
    )


def columns_for_entities(db: Session, entity_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ColumnInfo]]:
    if not entity_ids:
        return {}
    by_entity: dict[uuid.UUID, list[ColumnInfo]] = {}
    rows = db.execute(
        select(DataField, DataFieldProfile)
        .outerjoin(DataFieldProfile, DataFieldProfile.field_id == DataField.field_id)
        .where(DataField.entity_id.in_(entity_ids))
    )
    for field, profile in rows:
        by_entity.setdefault(field.entity_id, []).append(
            ColumnInfo(
                name=field.field_name,
                is_primary_key=bool(field.is_primary_key),
                data_type=field.data_type,
                profile=_profile_from_row(profile),
            )
        )
    return by_entity
