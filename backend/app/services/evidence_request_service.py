import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence_exception import EvidenceRequest
from app.schemas.audit_engine import EvidenceRequestCreate
from app.services.audit_log_service import log_action

# Generous enough for a scanned PDF or a spreadsheet export, small enough
# that a handful of these per exception never meaningfully bloats the
# database — see EvidenceRequest's docstring for why file_data lives here
# at all rather than in object storage.
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


def create_request(
    db: Session, *, exception_id: uuid.UUID, payload: EvidenceRequestCreate, organization_id: uuid.UUID, requested_by_user_id: uuid.UUID
) -> EvidenceRequest:
    request = EvidenceRequest(
        exception_id=exception_id,
        description=payload.description,
        due_date=payload.due_date,
        requested_by=requested_by_user_id,
    )
    db.add(request)
    db.flush()
    log_action(
        db,
        action=f"Requested evidence: {payload.description}",
        organization_id=organization_id,
        user_id=requested_by_user_id,
        entity_type="evidence_requests",
        entity_id=request.request_id,
        new_value={"exception_id": str(exception_id), "due_date": str(payload.due_date) if payload.due_date else None},
    )
    db.commit()
    db.refresh(request)
    return request


def upload_evidence(
    db: Session, *, request: EvidenceRequest, file_name: str, content_type: str | None, file_data: bytes,
    organization_id: uuid.UUID, uploaded_by_user_id: uuid.UUID,
) -> EvidenceRequest:
    if len(file_data) > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"File is too large — the limit is {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB.")
    if not file_data:
        raise ValueError("The uploaded file is empty.")

    request.file_name = file_name
    request.content_type = content_type
    request.file_data = file_data
    request.uploaded_by = uploaded_by_user_id
    request.uploaded_at = datetime.now(timezone.utc)
    request.status = "received"
    log_action(
        db,
        action=f"Uploaded evidence: {file_name}",
        organization_id=organization_id,
        user_id=uploaded_by_user_id,
        entity_type="evidence_requests",
        entity_id=request.request_id,
        new_value={"file_name": file_name, "status": "received"},
    )
    db.commit()
    db.refresh(request)
    return request


def list_requests_for_exception(db: Session, *, exception_id: uuid.UUID) -> list[EvidenceRequest]:
    return list(
        db.scalars(
            select(EvidenceRequest)
            .where(EvidenceRequest.exception_id == exception_id)
            .order_by(EvidenceRequest.requested_at.asc())
        )
    )
