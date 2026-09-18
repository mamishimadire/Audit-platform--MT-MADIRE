import uuid
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import enforce_same_organization, get_current_user, require_permissions
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.rbac import User
from app.schemas.audit_log import AuditLogOut

router = APIRouter(tags=["audit-logs"])

# Keys inside old_value/new_value that hold a USER id, not some other
# entity's id (audit_test_id, control_id, etc. are deliberately excluded
# — resolving those against a user lookup would never match and there's
# no value in even trying). Covers the "who did this to it" convention
# already used consistently across every log_action call site.
_USER_REFERENCE_KEYS = {"owner_id", "user_id", "responsible_user_id", "assigned_to"}


def _is_user_reference_key(key: str) -> bool:
    return key in _USER_REFERENCE_KEYS or key.endswith("_by")


def _humanize_key(key: str, *, resolved_to_name: bool = False) -> str:
    # A user-reference key that resolved to an actual name reads as "Owner:
    # Jane Smith", not the misleading "Owner ID: Jane Smith" — the "id"
    # suffix only makes sense when the raw id is what's actually shown.
    if resolved_to_name and key.endswith("_id"):
        key = key[: -len("_id")]
    words = key.replace("_", " ").split()
    return " ".join(w.upper() if w.lower() == "id" else w.capitalize() for w in words)


def _display_value(key: str, value, names_by_id: dict[uuid.UUID, str]) -> tuple[str, bool]:
    """Returns (display_text, resolved_to_name) — the second flag lets the
    caller adjust the KEY's own label (see _humanize_key) once the value
    turned out to be a name rather than a raw id."""
    if value is None:
        return "(none)", False
    if isinstance(value, bool):
        return ("Yes" if value else "No"), False
    if _is_user_reference_key(key):
        try:
            resolved = names_by_id.get(uuid.UUID(str(value)))
        except (ValueError, TypeError):
            resolved = None
        if resolved:
            return resolved, True
    return str(value), False


def _change_summary(old_value: dict | None, new_value: dict | None, names_by_id: dict[uuid.UUID, str]) -> str | None:
    """Turns old_value/new_value into a plain-English sentence instead of
    raw JSON — 'Status: open, Owner: Jane Smith' rather than
    '{"status":"open","owner_id":"71c4ed76-..."}'. A key whose value is
    unchanged between old and new is skipped (nothing to say about it);
    a key that changed shows 'old -> new'; a key only in new_value (the
    common case — most log_action calls only ever pass new_value) shows
    just its value."""
    if not old_value and not new_value:
        return None
    keys = list((new_value or {}).keys())
    for key in old_value or {}:
        if key not in keys:
            keys.append(key)

    parts = []
    for key in keys:
        has_old, has_new = (old_value or {}).__contains__(key), (new_value or {}).__contains__(key)
        old_v = (old_value or {}).get(key)
        new_v = (new_value or {}).get(key)
        if has_old and has_new:
            if old_v == new_v:
                continue
            old_text, _ = _display_value(key, old_v, names_by_id)
            new_text, resolved = _display_value(key, new_v, names_by_id)
            parts.append(f"{_humanize_key(key, resolved_to_name=resolved)}: {old_text} → {new_text}")
        elif has_new:
            new_text, resolved = _display_value(key, new_v, names_by_id)
            parts.append(f"{_humanize_key(key, resolved_to_name=resolved)}: {new_text}")
        else:
            old_text, resolved = _display_value(key, old_v, names_by_id)
            parts.append(f"{_humanize_key(key, resolved_to_name=resolved)}: {old_text} (removed)")
    return ", ".join(parts) if parts else None


@router.get("/organizations/{organization_id}/audit-logs", response_model=list[AuditLogOut])
def list_all(
    organization_id: uuid.UUID,
    limit: int = 200,
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions("audit_log:read")),
) -> list[AuditLogOut]:
    enforce_same_organization(organization_id, user, db)
    stmt = select(AuditLog).where(AuditLog.organization_id == organization_id)
    if from_date is not None:
        stmt = stmt.where(AuditLog.timestamp >= datetime.combine(from_date, time.min, tzinfo=timezone.utc))
    if to_date is not None:
        stmt = stmt.where(AuditLog.timestamp <= datetime.combine(to_date, time.max, tzinfo=timezone.utc))
    stmt = stmt.order_by(AuditLog.timestamp.desc()).limit(min(limit, 2000))
    logs = list(db.scalars(stmt))

    # Resolved here (one bulk query) rather than making the frontend match
    # user_id against a users list — no single users endpoint a client-side
    # viewer can call returns BOTH their own org's users and an internal
    # auditor's name (see AuditLogOut.user_name's docstring). Also pulls in
    # any user id embedded INSIDE old_value/new_value (owner_id, approved_
    # by, ...) so _change_summary can resolve those to names too, not just
    # the log's own top-level actor.
    user_ids = {log.user_id for log in logs if log.user_id is not None}
    for log in logs:
        for value_dict in (log.old_value, log.new_value):
            for key, value in (value_dict or {}).items():
                if _is_user_reference_key(key) and value:
                    try:
                        user_ids.add(uuid.UUID(str(value)))
                    except (ValueError, TypeError):
                        pass

    names_by_id: dict[uuid.UUID, str] = {}
    if user_ids:
        for row_user_id, first_name, last_name in db.execute(
            select(User.user_id, User.first_name, User.last_name).where(User.user_id.in_(user_ids))
        ):
            names_by_id[row_user_id] = f"{first_name} {last_name}"

    return [
        AuditLogOut.model_validate(log).model_copy(
            update={
                "user_name": names_by_id.get(log.user_id),
                "change_summary": _change_summary(log.old_value, log.new_value, names_by_id),
            }
        )
        for log in logs
    ]
