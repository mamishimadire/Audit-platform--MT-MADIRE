import uuid
from types import SimpleNamespace

from app.core.canonical_model import LOW_CONTENT_FIT_THRESHOLD, score_table_content_fit, suggest_canonical_field
from app.services import control_binding_service

REAL_USERS_COLUMNS = [
    "id", "company_id", "department_id", "email", "first_name", "last_name", "password_hash",
    "role", "status", "created_at", "updated_at", "failed_login_attempts", "locked_until",
]
UNRELATED_USERS_COLUMNS = ["_id", "name", "email", "password"]


def test_real_users_table_fits_the_user_object():
    fit = score_table_content_fit(REAL_USERS_COLUMNS, "users")
    assert fit is not None and fit >= 50


def test_unrelated_table_sharing_the_name_is_low_fit():
    fit = score_table_content_fit(UNRELATED_USERS_COLUMNS, "users")
    assert fit is not None and fit < LOW_CONTENT_FIT_THRESHOLD


def test_no_modeled_object_means_not_applicable_not_bad():
    assert score_table_content_fit(["a", "b"], "service_agreements") is None


def test_in_object_only_skips_global_fallback():
    # "gender" has no match inside user; the default call falls through to a
    # global search, in_object_only must not.
    assert suggest_canonical_field("gender", preferred_object="user", in_object_only=True) == ("", 0.0)
    assert suggest_canonical_field("status", preferred_object="user", in_object_only=True)[0] == "user.status"


def test_same_named_decoy_is_ranked_below_the_real_table(monkeypatch):
    real = SimpleNamespace(entity_id=uuid.uuid4(), entity_name="system_users_v2", data_source_id=uuid.uuid4())
    decoy = SimpleNamespace(entity_id=uuid.uuid4(), entity_name="system_users", data_source_id=uuid.uuid4())
    monkeypatch.setattr(
        control_binding_service,
        "_field_names_by_entity",
        lambda db, ids: {
            real.entity_id: ["user_id", "username", "status", "last_login", "employee_id", "roles"],
            decoy.entity_id: ["_id", "title", "plot", "year"],
        },
    )
    control_binding_service._content_fit.cache_clear()

    result = control_binding_service._suggest_bindings(
        None, organization_id=uuid.uuid4(), unbound_tables=["system_users"], rows=[(decoy, "src"), (real, "src")]
    )["system_users"]

    # The decoy matches the name exactly (100) but its columns don't fit.
    assert [c.entity_name for c in result] == ["system_users_v2", "system_users"]
    assert result[1].confidence_score == 100.0
    assert result[1].content_fit_score is not None and result[1].content_fit_score < LOW_CONTENT_FIT_THRESHOLD
    assert result[0].content_fit_score == 100.0


def test_table_with_no_discovered_columns_is_not_applicable():
    assert score_table_content_fit([], "users") is None
