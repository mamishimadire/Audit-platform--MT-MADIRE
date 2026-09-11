import pytest

from app.schemas.device import ApprovedSoftwareCreate, InstalledSoftwareItem
from app.services.software_compliance_service import (
    _list_approved_software_in_effect,
    approve_classification,
    classify_item,
    create_approved_software,
    reject_classification,
    update_approved_software,
    _policy_by_name,
)


def _payload(**overrides):
    defaults = dict(app_name="RemoteAccessTool.exe", publisher=None, classification="restricted", risk_level="critical")
    defaults.update(overrides)
    return ApprovedSoftwareCreate(**defaults)


def test_new_classification_starts_pending_approval(db, test_org, maker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(), created_by_user_id=maker_user.user_id)
    assert entry.approval_status == "pending_approval"
    assert entry.created_by == maker_user.user_id
    assert entry.approved_by is None


def test_classifier_cannot_approve_own_classification(db, test_org, maker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(), created_by_user_id=maker_user.user_id)
    with pytest.raises(ValueError, match="submitted this classification yourself"):
        approve_classification(db, entry=entry, approved_by_user_id=maker_user.user_id)


def test_classifier_cannot_reject_own_classification(db, test_org, maker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(), created_by_user_id=maker_user.user_id)
    with pytest.raises(ValueError, match="submitted this classification yourself"):
        reject_classification(db, entry=entry, reason="test", rejected_by_user_id=maker_user.user_id)


def test_different_user_can_approve_classification(db, test_org, maker_user, checker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(), created_by_user_id=maker_user.user_id)
    approved = approve_classification(db, entry=entry, approved_by_user_id=checker_user.user_id)
    assert approved.approval_status == "approved"
    assert approved.approved_by == checker_user.user_id


def test_reject_requires_a_reason(db, test_org, maker_user, checker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(), created_by_user_id=maker_user.user_id)
    with pytest.raises(ValueError, match="reason is required"):
        reject_classification(db, entry=entry, reason="", rejected_by_user_id=checker_user.user_id)


def test_pending_classification_does_not_affect_compliance(db, test_org, maker_user):
    create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(), created_by_user_id=maker_user.user_id)
    in_effect = _list_approved_software_in_effect(db, organization_id=test_org.organization_id)
    assert in_effect == []

    item = InstalledSoftwareItem(name="RemoteAccessTool.exe", version=None, publisher=None)
    classification, compliance_result, match = classify_item(item, _policy_by_name(in_effect))
    assert classification == "unknown"
    assert compliance_result == "review_required"
    assert match is None


def test_approved_classification_affects_compliance(db, test_org, maker_user, checker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(), created_by_user_id=maker_user.user_id)
    approve_classification(db, entry=entry, approved_by_user_id=checker_user.user_id)

    in_effect = _list_approved_software_in_effect(db, organization_id=test_org.organization_id)
    assert len(in_effect) == 1

    item = InstalledSoftwareItem(name="RemoteAccessTool.exe", version=None, publisher=None)
    classification, compliance_result, match = classify_item(item, _policy_by_name(in_effect))
    assert classification == "restricted"
    assert compliance_result == "non_compliant"
    assert match is not None


def test_editing_a_pending_classification_edits_in_place(db, test_org, maker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(classification="restricted"), created_by_user_id=maker_user.user_id)
    original_id = entry.approved_software_id
    updated = update_approved_software(db, entry=entry, payload=_payload(classification="ignored"), updated_by_user_id=maker_user.user_id)
    assert updated.approved_software_id == original_id
    assert updated.classification == "ignored"
    assert updated.version == 1


def test_editing_an_approved_classification_creates_a_new_pending_version(db, test_org, maker_user, checker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(classification="approved"), created_by_user_id=maker_user.user_id)
    approved = approve_classification(db, entry=entry, approved_by_user_id=checker_user.user_id)

    reclassified = update_approved_software(db, entry=approved, payload=_payload(classification="restricted"), updated_by_user_id=maker_user.user_id)
    assert reclassified.approved_software_id != approved.approved_software_id
    assert reclassified.approval_status == "pending_approval"
    assert reclassified.version == 2
    assert reclassified.supersedes_id == approved.approved_software_id

    # The OLD (still-approved) classification keeps enforcing while the
    # reclassification is pending — not the new, unreviewed one.
    in_effect = _list_approved_software_in_effect(db, organization_id=test_org.organization_id)
    assert len(in_effect) == 1
    assert in_effect[0].approved_software_id == approved.approved_software_id
    assert in_effect[0].classification == "approved"


def test_approving_a_reclassification_supersedes_the_old_version(db, test_org, maker_user, checker_user):
    entry = create_approved_software(db, organization_id=test_org.organization_id, payload=_payload(classification="approved"), created_by_user_id=maker_user.user_id)
    approved = approve_classification(db, entry=entry, approved_by_user_id=checker_user.user_id)
    reclassified = update_approved_software(db, entry=approved, payload=_payload(classification="restricted"), updated_by_user_id=maker_user.user_id)

    approve_classification(db, entry=reclassified, approved_by_user_id=checker_user.user_id)

    db.refresh(approved)
    assert approved.approval_status == "superseded"

    in_effect = _list_approved_software_in_effect(db, organization_id=test_org.organization_id)
    assert len(in_effect) == 1
    assert in_effect[0].classification == "restricted"
