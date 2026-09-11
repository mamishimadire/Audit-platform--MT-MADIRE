"""Industries + risk library: multi-select industry on org creation, auto-created risks

The client asked (2026-09-05): when creating a client organization, they
must be able to select one or more industries from a full dropdown, and the
system should auto-create that organization's risk register from those
industries — same "no manual entry, select and confirm" pattern as the
control library. Auto-created risks stay manually editable afterward.

  industries              — global reference list (~20 industries).
  organization_industries — many-to-many, an org can span multiple industries.
  risk_library            — global pre-built risk catalogue: generic risks
                             (industry_id NULL, one per control domain so a
                             risk and its mitigating controls share a
                             category) + industry-specific risks.

`risks` gains a nullable `risk_library_id` FK for traceability, mirroring
`controls.control_library_id`.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-05

"""
from alembic import op

from app.core.risk_library_data import GENERIC_RISK_LIBRARY, INDUSTRIES, INDUSTRY_RISK_LIBRARY

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _escape(value: str) -> str:
    return value.replace("'", "''")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE industries (
            industry_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            industry_name VARCHAR(150) NOT NULL UNIQUE
        )
        """
    )
    industry_rows = ",\n".join(f"('{_escape(name)}')" for name in INDUSTRIES)
    op.execute(f"INSERT INTO industries (industry_name) VALUES {industry_rows}")

    op.execute(
        """
        CREATE TABLE organization_industries (
            organization_industry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
            industry_id     UUID NOT NULL REFERENCES industries(industry_id) ON DELETE CASCADE,
            UNIQUE (organization_id, industry_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE risk_library (
            risk_library_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            industry_id     UUID REFERENCES industries(industry_id) ON DELETE CASCADE,
            category_name   VARCHAR(100) NOT NULL,
            risk_name       VARCHAR(255) NOT NULL,
            risk_description TEXT NOT NULL,
            default_inherent_risk_rating VARCHAR(20) NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_risk_library_industry ON risk_library(industry_id)")

    generic_rows = []
    for category, name, description, rating in GENERIC_RISK_LIBRARY:
        generic_rows.append(
            "(NULL, '{category}', '{name}', '{description}', '{rating}')".format(
                category=_escape(category), name=_escape(name), description=_escape(description), rating=rating
            )
        )
    values_sql = ",\n".join(generic_rows)
    op.execute(
        "INSERT INTO risk_library (industry_id, category_name, risk_name, risk_description, default_inherent_risk_rating) "
        f"VALUES {values_sql}"
    )

    industry_rows2 = []
    for industry_name, name, description, rating in INDUSTRY_RISK_LIBRARY:
        industry_rows2.append(
            "((SELECT industry_id FROM industries WHERE industry_name = '{industry}'), "
            "'Industry & Regulatory Risk', '{name}', '{description}', '{rating}')".format(
                industry=_escape(industry_name), name=_escape(name), description=_escape(description), rating=rating
            )
        )
    values_sql2 = ",\n".join(industry_rows2)
    op.execute(
        "INSERT INTO risk_library (industry_id, category_name, risk_name, risk_description, default_inherent_risk_rating) "
        f"VALUES {values_sql2}"
    )

    # The 20 generic-risk category names double as risk_categories rows so a
    # risk and the controls that mitigate it share one label. Additive only —
    # never touches any pre-existing category.
    category_names = sorted({category for category, *_ in GENERIC_RISK_LIBRARY} | {"Industry & Regulatory Risk"})
    category_rows = ",\n".join(f"('{_escape(name)}')" for name in category_names)
    op.execute(f"INSERT INTO risk_categories (category_name) VALUES {category_rows} ON CONFLICT (category_name) DO NOTHING")

    op.execute(
        "ALTER TABLE risks ADD COLUMN risk_library_id UUID REFERENCES risk_library(risk_library_id) ON DELETE SET NULL"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX idx_risks_org_library_unique
            ON risks(organization_id, risk_library_id)
            WHERE risk_library_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_risks_org_library_unique")
    op.execute("ALTER TABLE risks DROP COLUMN IF EXISTS risk_library_id")
    op.execute("DROP TABLE IF EXISTS risk_library")
    op.execute("DROP TABLE IF EXISTS organization_industries")
    op.execute("DROP TABLE IF EXISTS industries")
