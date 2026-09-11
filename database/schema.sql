-- =====================================================================
-- MT AUDIT / AuditIQ — Continuous Audit Intelligence & Assurance Platform
-- Core Database Schema (Version 1 / MVP)
-- Target: PostgreSQL 15+ (Neon)
-- =====================================================================
--
-- Design principles applied throughout:
--   * UUID primary keys (gen_random_uuid()) — safe for multi-tenant,
--     distributed inserts (gateway-originated data, offline queues).
--   * organization_id on every tenant-scoped table, enforcing the
--     tenant-isolation principle from the product spec.
--   * created_at / updated_at on every table; created_by where a human
--     or service actor originates the record.
--   * status/type/severity fields use CHECK constraints instead of
--     native ENUM types, so values can be extended with an ALTER TABLE
--     instead of an ALTER TYPE migration.
--   * No credentials are ever stored in these tables. data_connections
--     holds a secret_reference (pointer into a secrets manager / vault)
--     only — never a raw password or token.
--
-- Chain this schema is built to preserve (see product spec):
--   Business Process -> Risk -> Control -> Audit Test -> Test Rule ->
--   Data Source -> Table/Field -> Test Execution -> Evidence ->
--   Exception -> Finding -> Remediation -> Re-test
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- provides gen_random_uuid()

-- =====================================================================
-- 01. ORGANIZATION  (the client tenants)
-- =====================================================================

CREATE TABLE organizations (
    organization_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_name   VARCHAR(255) NOT NULL,
    trading_name         VARCHAR(255),
    registration_number VARCHAR(100),
    industry             VARCHAR(100),
    country              VARCHAR(100),
    status               VARCHAR(20) NOT NULL DEFAULT 'onboarding'
                         CHECK (status IN ('onboarding','active','inactive','suspended')),
    created_by           UUID,               -- FK to users, added after users exists
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE organization_settings (
    setting_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    setting_name    VARCHAR(150) NOT NULL,
    setting_value   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, setting_name)
);

CREATE TABLE business_units (
    business_unit_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    parent_business_unit_id UUID REFERENCES business_units(business_unit_id) ON DELETE SET NULL,
    business_unit_name      VARCHAR(255) NOT NULL,
    business_unit_code      VARCHAR(50),
    status                  VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_org_settings_org ON organization_settings(organization_id);
CREATE INDEX idx_business_units_org ON business_units(organization_id);
CREATE INDEX idx_business_units_parent ON business_units(parent_business_unit_id);

-- =====================================================================
-- 02. ACCESS MANAGEMENT
-- =====================================================================

CREATE TABLE users (
    user_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(organization_id) ON DELETE CASCADE,
    -- NULL organization_id = platform-level user (System Owner / MT AUDIT Administrator)
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','active','inactive','locked')),
    last_login      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Now that users exists, wire up the deferred FKs from Section 01
ALTER TABLE organizations
    ADD CONSTRAINT fk_organizations_created_by FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE SET NULL;

CREATE TABLE roles (
    role_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_name   VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE permissions (
    permission_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    permission_name VARCHAR(150) NOT NULL UNIQUE,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_roles (
    user_role_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    role_id      UUID NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, role_id)
);

CREATE TABLE role_permissions (
    role_permission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id             UUID NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    permission_id       UUID NOT NULL REFERENCES permissions(permission_id) ON DELETE CASCADE,
    UNIQUE (role_id, permission_id)
);

CREATE TABLE user_sessions (
    session_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    login_time  TIMESTAMPTZ NOT NULL DEFAULT now(),
    logout_time TIMESTAMPTZ,
    ip_address  INET,
    status      VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','revoked'))
);

CREATE INDEX idx_users_org ON users(organization_id);
CREATE INDEX idx_user_roles_user ON user_roles(user_id);
CREATE INDEX idx_user_roles_role ON user_roles(role_id);
CREATE INDEX idx_role_permissions_role ON role_permissions(role_id);
CREATE INDEX idx_user_sessions_user ON user_sessions(user_id);

-- =====================================================================
-- 03. BUSINESS PROCESSES
-- =====================================================================

CREATE TABLE business_processes (
    process_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    process_name     VARCHAR(255) NOT NULL,
    process_code     VARCHAR(50),
    description      TEXT,
    process_owner_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    created_by       UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE process_activities (
    activity_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    process_id           UUID NOT NULL REFERENCES business_processes(process_id) ON DELETE CASCADE,
    activity_name        VARCHAR(255) NOT NULL,
    activity_description TEXT,
    activity_owner_id    UUID REFERENCES users(user_id) ON DELETE SET NULL,
    sequence_number      INT NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_business_processes_org ON business_processes(organization_id);
CREATE INDEX idx_process_activities_process ON process_activities(process_id);

-- =====================================================================
-- 04. RISK & CONTROL
-- =====================================================================

CREATE TABLE risk_categories (
    risk_category_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_name    VARCHAR(100) NOT NULL UNIQUE,
    description      TEXT
);

CREATE TABLE risks (
    risk_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    process_id            UUID REFERENCES business_processes(process_id) ON DELETE SET NULL,
    risk_category_id      UUID REFERENCES risk_categories(risk_category_id) ON DELETE SET NULL,
    risk_name             VARCHAR(255) NOT NULL,
    risk_description      TEXT,
    inherent_risk_rating  VARCHAR(20) CHECK (inherent_risk_rating IN ('low','medium','high','critical')),
    residual_risk_rating  VARCHAR(20) CHECK (residual_risk_rating IN ('low','medium','high','critical')),
    risk_owner_id         UUID REFERENCES users(user_id) ON DELETE SET NULL,
    status                VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','closed')),
    created_by            UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE controls (
    control_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    control_code        VARCHAR(50),
    control_name        VARCHAR(255) NOT NULL,
    control_description TEXT,
    control_type        VARCHAR(20) CHECK (control_type IN ('preventive','detective','corrective')),
    control_nature       VARCHAR(20) CHECK (control_nature IN ('manual','automated','it_dependent')),
    control_frequency    VARCHAR(30),
    control_owner_id     UUID REFERENCES users(user_id) ON DELETE SET NULL,
    status               VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','retired')),
    created_by           UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE risk_controls (
    risk_control_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    risk_id         UUID NOT NULL REFERENCES risks(risk_id) ON DELETE CASCADE,
    control_id      UUID NOT NULL REFERENCES controls(control_id) ON DELETE CASCADE,
    UNIQUE (risk_id, control_id)
);

CREATE INDEX idx_risks_org ON risks(organization_id);
CREATE INDEX idx_risks_process ON risks(process_id);
CREATE INDEX idx_controls_org ON controls(organization_id);
CREATE INDEX idx_risk_controls_risk ON risk_controls(risk_id);
CREATE INDEX idx_risk_controls_control ON risk_controls(control_id);

-- =====================================================================
-- 05. DATA SOURCES  (client systems, the Audit Data Gateway, and the
--     tables/fields discovered from them)
-- =====================================================================

-- Not in the original table list, but required: data_connections.gateway_id
-- has nowhere to point without it, and the Gateway registration-code
-- lifecycle (single-use, time-limited, tenant-bound, device certificate)
-- is a first-class product concept that needs its own record.
CREATE TABLE gateways (
    gateway_id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id               UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    gateway_name                  VARCHAR(150) NOT NULL,
    registration_code             VARCHAR(20),
    registration_code_expires_at  TIMESTAMPTZ,
    registration_status           VARCHAR(20) NOT NULL DEFAULT 'unused'
                                   CHECK (registration_status IN ('unused','registered','expired','revoked')),
    device_certificate_fingerprint VARCHAR(255),
    status                        VARCHAR(20) NOT NULL DEFAULT 'pending'
                                   CHECK (status IN ('pending','online','offline','deregistered')),
    version                       VARCHAR(20),
    last_heartbeat                TIMESTAMPTZ,
    certificate_expiry            TIMESTAMPTZ,
    created_by                    UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE data_sources (
    data_source_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    source_name     VARCHAR(150) NOT NULL,
    source_type     VARCHAR(50) NOT NULL, -- e.g. sql_server, postgresql, mysql, oracle, sap, sage, api, file
    environment     VARCHAR(20) NOT NULL CHECK (environment IN ('on_premise','cloud')),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','connected','error','disabled')),
    created_by      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE data_connections (
    connection_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id    UUID NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
    gateway_id        UUID REFERENCES gateways(gateway_id) ON DELETE SET NULL,
    -- gateway_id is NULL for cloud-hosted sources connected directly (see product spec Section 10)
    secret_reference  VARCHAR(255), -- pointer into the secrets manager/vault; never a raw credential
    connection_status VARCHAR(20) NOT NULL DEFAULT 'pending'
                       CHECK (connection_status IN ('pending','connected','failed','revoked')),
    last_tested_at    TIMESTAMPTZ,
    created_by        UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE data_entities (
    entity_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_source_id  UUID NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
    entity_name     VARCHAR(150) NOT NULL,
    entity_type     VARCHAR(30) NOT NULL DEFAULT 'table' CHECK (entity_type IN ('table','view','api','file')),
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE data_fields (
    field_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id       UUID NOT NULL REFERENCES data_entities(entity_id) ON DELETE CASCADE,
    field_name      VARCHAR(150) NOT NULL,
    data_type       VARCHAR(50),
    is_primary_key  BOOLEAN NOT NULL DEFAULT false,
    is_sensitive    BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_gateways_org ON gateways(organization_id);
CREATE INDEX idx_data_sources_org ON data_sources(organization_id);
CREATE INDEX idx_data_connections_source ON data_connections(data_source_id);
CREATE INDEX idx_data_connections_gateway ON data_connections(gateway_id);
CREATE INDEX idx_data_entities_source ON data_entities(data_source_id);
CREATE INDEX idx_data_fields_entity ON data_fields(entity_id);

-- =====================================================================
-- 06. AUDIT TESTING
-- =====================================================================

CREATE TABLE audit_tests (
    audit_test_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    test_code        VARCHAR(50),
    test_name        VARCHAR(255) NOT NULL,
    test_description TEXT,
    test_type        VARCHAR(50),
    frequency        VARCHAR(30),
    status           VARCHAR(20) NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','pending_approval','active','disabled')),
    created_by       UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE control_audit_tests (
    control_test_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    control_id      UUID NOT NULL REFERENCES controls(control_id) ON DELETE CASCADE,
    audit_test_id   UUID NOT NULL REFERENCES audit_tests(audit_test_id) ON DELETE CASCADE,
    UNIQUE (control_id, audit_test_id)
);

-- The chain: Audit Test -> Data Source -> Table -> Field, plus the
-- canonical-model mapping and auditor approval described in the spec.
CREATE TABLE test_data_mappings (
    mapping_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_test_id     UUID NOT NULL REFERENCES audit_tests(audit_test_id) ON DELETE CASCADE,
    data_source_id    UUID NOT NULL REFERENCES data_sources(data_source_id) ON DELETE CASCADE,
    entity_id         UUID NOT NULL REFERENCES data_entities(entity_id) ON DELETE CASCADE,
    field_id          UUID REFERENCES data_fields(field_id) ON DELETE SET NULL,
    canonical_field   VARCHAR(150), -- e.g. employee.employment_status
    confidence_score  NUMERIC(5,2),
    mapping_status    VARCHAR(20) NOT NULL DEFAULT 'needs_review'
                      CHECK (mapping_status IN ('auto','needs_review','manually_mapped','approved')),
    approved_by       UUID REFERENCES users(user_id) ON DELETE SET NULL,
    approved_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE test_rules (
    rule_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_test_id   UUID NOT NULL REFERENCES audit_tests(audit_test_id) ON DELETE CASCADE,
    rule_name       VARCHAR(255) NOT NULL,
    rule_type       VARCHAR(50),           -- e.g. sql, python
    rule_definition TEXT NOT NULL,
    severity        VARCHAR(20) CHECK (severity IN ('low','medium','high','critical')),
    status          VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    created_by      UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_tests_org ON audit_tests(organization_id);
CREATE INDEX idx_control_audit_tests_control ON control_audit_tests(control_id);
CREATE INDEX idx_control_audit_tests_test ON control_audit_tests(audit_test_id);
CREATE INDEX idx_test_data_mappings_test ON test_data_mappings(audit_test_id);
CREATE INDEX idx_test_data_mappings_source ON test_data_mappings(data_source_id);
CREATE INDEX idx_test_rules_test ON test_rules(audit_test_id);

-- =====================================================================
-- 07. CONTINUOUS MONITORING
-- =====================================================================

CREATE TABLE monitoring_schedules (
    schedule_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_test_id UUID NOT NULL REFERENCES audit_tests(audit_test_id) ON DELETE CASCADE,
    frequency     VARCHAR(30) NOT NULL, -- real_time, hourly, daily, weekly, monthly
    next_run      TIMESTAMPTZ,
    last_run      TIMESTAMPTZ,
    is_active     BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE test_executions (
    execution_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_test_id     UUID NOT NULL REFERENCES audit_tests(audit_test_id) ON DELETE CASCADE,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    status            VARCHAR(20) NOT NULL DEFAULT 'running'
                      CHECK (status IN ('running','completed','failed')),
    records_analyzed  INT,
    exceptions_found  INT,
    execution_log     TEXT
);

CREATE INDEX idx_monitoring_schedules_test ON monitoring_schedules(audit_test_id);
CREATE INDEX idx_test_executions_test ON test_executions(audit_test_id);
CREATE INDEX idx_test_executions_started ON test_executions(started_at);

-- =====================================================================
-- 08. EVIDENCE & EXCEPTIONS
-- =====================================================================

CREATE TABLE evidence (
    evidence_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id      UUID NOT NULL REFERENCES test_executions(execution_id) ON DELETE CASCADE,
    evidence_type     VARCHAR(50), -- data_extract, test_result, report, screenshot, system_log
    evidence_location TEXT,        -- object storage path/key; metadata only, never the file itself
    evidence_hash     VARCHAR(128),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE exceptions (
    exception_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id          UUID NOT NULL REFERENCES test_executions(execution_id) ON DELETE CASCADE,
    exception_reference   VARCHAR(50),
    exception_description TEXT,
    severity              VARCHAR(20) CHECK (severity IN ('low','medium','high','critical')),
    status                VARCHAR(20) NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open','awaiting_evidence','in_progress','resolved','closed')),
    owner_id              UUID REFERENCES users(user_id) ON DELETE SET NULL,
    detected_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE exception_records (
    exception_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exception_id         UUID NOT NULL REFERENCES exceptions(exception_id) ON DELETE CASCADE,
    entity_id            UUID REFERENCES data_entities(entity_id) ON DELETE SET NULL,
    record_identifier    VARCHAR(255),
    exception_data       JSONB
);

CREATE INDEX idx_evidence_execution ON evidence(execution_id);
CREATE INDEX idx_exceptions_execution ON exceptions(execution_id);
CREATE INDEX idx_exceptions_status ON exceptions(status);
CREATE INDEX idx_exception_records_exception ON exception_records(exception_id);

-- =====================================================================
-- 09. FINDINGS & REMEDIATION
-- =====================================================================

CREATE TABLE findings (
    finding_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(organization_id) ON DELETE CASCADE,
    exception_id        UUID NOT NULL REFERENCES exceptions(exception_id) ON DELETE CASCADE,
    finding_title       VARCHAR(255) NOT NULL,
    finding_description TEXT,
    risk_rating         VARCHAR(20) CHECK (risk_rating IN ('low','medium','high','critical')),
    status              VARCHAR(20) NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','remediation_in_progress','awaiting_retest','closed','reopened')),
    created_by          UUID REFERENCES users(user_id) ON DELETE SET NULL,
    identified_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE finding_root_causes (
    root_cause_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id           UUID NOT NULL REFERENCES findings(finding_id) ON DELETE CASCADE,
    root_cause_category  VARCHAR(100),
    description           TEXT
);

CREATE TABLE remediation_actions (
    remediation_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id           UUID NOT NULL REFERENCES findings(finding_id) ON DELETE CASCADE,
    action_description    TEXT NOT NULL,
    responsible_user_id   UUID REFERENCES users(user_id) ON DELETE SET NULL,
    target_date           DATE,
    status                VARCHAR(20) NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','in_progress','completed','overdue')),
    created_by            UUID REFERENCES users(user_id) ON DELETE SET NULL,
    completed_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE retests (
    retest_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id    UUID NOT NULL REFERENCES findings(finding_id) ON DELETE CASCADE,
    audit_test_id UUID NOT NULL REFERENCES audit_tests(audit_test_id) ON DELETE CASCADE,
    retest_date   TIMESTAMPTZ NOT NULL DEFAULT now(),
    result        VARCHAR(20) CHECK (result IN ('pass','fail')),
    performed_by  UUID REFERENCES users(user_id) ON DELETE SET NULL,
    comments      TEXT
);

CREATE INDEX idx_findings_org ON findings(organization_id);
CREATE INDEX idx_findings_exception ON findings(exception_id);
CREATE INDEX idx_findings_status ON findings(status);
CREATE INDEX idx_finding_root_causes_finding ON finding_root_causes(finding_id);
CREATE INDEX idx_remediation_actions_finding ON remediation_actions(finding_id);
CREATE INDEX idx_retests_finding ON retests(finding_id);
CREATE INDEX idx_retests_test ON retests(audit_test_id);

-- =====================================================================
-- 10. GOVERNANCE
-- =====================================================================

CREATE TABLE audit_logs (
    log_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(organization_id) ON DELETE SET NULL,
    -- NULL organization_id = platform-level action (e.g. owner creating a new client)
    user_id         UUID REFERENCES users(user_id) ON DELETE SET NULL,
    action          VARCHAR(255) NOT NULL,
    entity_type     VARCHAR(100),
    entity_id       UUID,
    old_value       JSONB,
    new_value       JSONB,
    ip_address      INET,
    "timestamp"     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_logs_org ON audit_logs(organization_id);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_logs_timestamp ON audit_logs("timestamp");

-- =====================================================================
-- End of Version 1 schema — 34 tables across 10 modules.
-- =====================================================================
