import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence_exception import ExceptionComment
from app.services.audit_log_service import log_action


def add_comment(
    db: Session, *, exception_id: uuid.UUID, body: str, organization_id: uuid.UUID, author_user_id: uuid.UUID
) -> ExceptionComment:
    if not body or not body.strip():
        raise ValueError("A comment can't be empty.")
    comment = ExceptionComment(exception_id=exception_id, author_id=author_user_id, body=body.strip())
    db.add(comment)
    db.flush()
    log_action(
        db,
        action="Commented on exception",
        organization_id=organization_id,
        user_id=author_user_id,
        entity_type="exception_comments",
        entity_id=comment.comment_id,
        new_value={"exception_id": str(exception_id)},
    )
    db.commit()
    db.refresh(comment)
    return comment


def list_comments(db: Session, *, exception_id: uuid.UUID) -> list[ExceptionComment]:
    return list(
        db.scalars(
            select(ExceptionComment).where(ExceptionComment.exception_id == exception_id).order_by(ExceptionComment.created_at.asc())
        )
    )
