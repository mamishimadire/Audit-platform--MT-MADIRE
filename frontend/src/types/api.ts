export interface EligibleApproverOut {
  user_id: string
  first_name: string
  last_name: string
  email: string
}

export interface UserOut {
  user_id: string
  organization_id: string | null
  first_name: string
  last_name: string
  email: string
  status: 'pending' | 'active' | 'inactive' | 'locked'
  roles: string[]
  temporary_password: string | null
}

export interface OrganizationOut {
  organization_id: string
  organization_name: string
  trading_name: string | null
  registration_number: string | null
  industry: string | null
  industries: string[]
  country: string | null
  status: 'onboarding' | 'active' | 'inactive' | 'suspended'
}

export interface IndustryOut {
  industry_id: string
  industry_name: string
}

export interface OrganizationCreatedOut extends OrganizationOut {
  primary_admin_email: string
  temporary_password: string
}

export interface RoleOut {
  role_id: string
  role_name: string
  description: string | null
  role_scope: 'platform' | 'client'
  account_classification: 'MT_AUDIT_INTERNAL' | 'CLIENT_EXTERNAL'
  permissions: string[]
}

export interface ScopeOut {
  scope_id: string
  user_id: string
  organization_id: string
  granted_by: string | null
  created_at: string
}

export interface PermissionOut {
  permission_id: string
  permission_name: string
  description: string | null
}

export interface SodWorkflowOut {
  action: string
  states: string[]
  maker: string
  checker: string
  enforcement: string
  mandatory: boolean
}

export interface BusinessUnitOut {
  business_unit_id: string
  organization_id: string
  parent_business_unit_id: string | null
  business_unit_name: string
  business_unit_code: string | null
  status: string
}

export interface BusinessProcessOut {
  process_id: string
  organization_id: string
  process_name: string
  process_code: string | null
  description: string | null
  process_owner_id: string | null
  status: string
}

export interface RiskCategoryOut {
  risk_category_id: string
  category_name: string
  description: string | null
}

export type RiskRating = 'low' | 'medium' | 'high' | 'critical' | ''

export interface RiskOut {
  risk_id: string
  organization_id: string
  risk_library_id: string | null
  process_id: string | null
  risk_category_id: string | null
  risk_name: string
  risk_description: string | null
  inherent_risk_rating: string | null
  residual_risk_rating: string | null
  risk_owner_id: string | null
  status: string
  has_active_control: boolean
}

export type ControlType = 'preventive' | 'detective' | 'corrective' | ''
export type ControlNature = 'manual' | 'automated' | 'it_dependent' | ''

export type ControlStatus = 'pending_mapping' | 'pending_activation' | 'active' | 'pending_deactivation' | 'inactive' | 'retired'

export interface ControlOut {
  control_id: string
  organization_id: string
  control_library_id: string | null
  domain: string | null
  control_code: string | null
  control_name: string
  control_description: string | null
  control_type: string | null
  control_nature: string | null
  control_frequency: string | null
  control_owner_id: string | null
  status: ControlStatus
  activation_requested_by: string | null
  activation_approved_by: string | null
  deactivation_requested_by: string | null
  deactivation_requested_reason: string | null
  deactivation_approved_by: string | null
  required_tables: string[]
  risk_ids: string[]
}

export interface ControlTableBindingOut {
  binding_id: string
  organization_id: string
  control_id: string
  canonical_table_name: string
  data_source_id: string | null
  entity_id: string | null
  entity_name: string | null
  source_name: string | null
  status: 'bound' | 'not_applicable'
  not_applicable_reason: string | null
  bound_by: string | null
  bound_at: string
}

export interface TableBindingProgressOut {
  required_tables: string[]
  bindings: ControlTableBindingOut[]
  total: number
  satisfied: number
  ready: boolean
}

export interface ControlLibraryOut {
  control_library_id: string
  domain: string
  control_code: string
  control_name: string
  audit_procedure: string
  required_tables: string[]
  default_control_type: string | null
  default_control_nature: string | null
  default_control_frequency: string | null
}

export interface DeviceOut {
  device_id: string
  organization_id: string
  device_name: string
  assigned_user_name: string | null
  hostname: string | null
  os_name: string | null
  os_version: string | null
  registration_status: string
  status: string
  agent_version: string | null
  last_heartbeat: string | null
  antivirus_enabled: boolean | null
  firewall_enabled: boolean | null
  disk_encryption_enabled: boolean | null
  os_up_to_date: boolean | null
  compliance_status: 'compliant' | 'non_compliant' | 'unknown'
  revocation_requested_by: string | null
  revocation_requested_at: string | null
  revocation_reason: string | null
  revocation_approved_by: string | null
  revocation_approved_at: string | null
  deletion_requested_by: string | null
  deletion_requested_at: string | null
  deletion_reason: string | null
  deletion_approved_by: string | null
  deletion_approved_at: string | null
}

export interface InstalledSoftwareItem {
  name: string
  version: string | null
  publisher: string | null
}

export type SoftwareClassification = 'approved' | 'required' | 'restricted' | 'system_component' | 'ignored' | 'review_required' | 'unknown'
export type SoftwareComplianceResult = 'compliant' | 'non_compliant' | 'outdated' | 'review_required' | 'not_evaluated'

export interface EnrichedSoftwareItem extends InstalledSoftwareItem {
  classification: SoftwareClassification
  compliance_result: SoftwareComplianceResult
}

export interface DeviceSoftwareOut {
  device_id: string
  collected_at: string
  items: EnrichedSoftwareItem[]
}

export interface DeviceCreatedOut extends DeviceOut {
  registration_code: string
  registration_code_expires_at: string
}

export interface GatewayOut {
  gateway_id: string
  organization_id: string
  gateway_name: string
  registration_status: string
  status: string
  version: string | null
  last_heartbeat: string | null
  certificate_expiry: string | null
}

export interface GatewayCreatedOut extends GatewayOut {
  registration_code: string
  registration_code_expires_at: string
}

export interface DataSourceOut {
  data_source_id: string
  organization_id: string
  source_name: string
  source_type: string
  environment: string
  status: string
}

export type DirectDbType = 'postgresql' | 'mysql' | 'mssql' | 'oracle' | 'sap_hana' | 'snowflake'
export type OracleConnectionType = 'service_name' | 'sid'
export type SnowflakeAuthMethod = 'password' | 'key_pair'

export interface DataConnectionOut {
  connection_id: string
  data_source_id: string
  gateway_id: string | null
  connection_mode: 'gateway' | 'direct'
  db_type: DirectDbType | null
  host: string | null
  port: number | null
  database_name: string | null
  username: string | null
  oracle_connection_type: OracleConnectionType | null
  sap_hana_encrypt: boolean
  snowflake_warehouse: string | null
  snowflake_schema: string | null
  snowflake_role: string | null
  snowflake_auth_method: SnowflakeAuthMethod
  connection_status: string
  last_tested_at: string | null
}

export interface ConnectionTestResult {
  success: boolean
  detail: string | null
}

export interface DevicePolicyOut {
  require_antivirus: boolean
  require_firewall: boolean
  require_disk_encryption: boolean
  require_os_up_to_date: boolean
  require_software_compliance: boolean
}

export interface DevicePolicyChangeOut {
  policy_change_id: string
  organization_id: string
  proposed_policy: DevicePolicyOut
  approval_status: 'pending_approval' | 'approved' | 'rejected'
  requested_by: string | null
  requested_at: string
  approved_by: string | null
  approved_at: string | null
  rejected_by: string | null
  rejected_at: string | null
  rejected_reason: string | null
}

export type ComplianceCheckState = 'pass' | 'fail' | 'unknown'

export interface ComplianceCheckDetail {
  field: string
  label: string
  state: ComplianceCheckState
  detected_at: string | null
  occurrence_count: number | null
}

export type DeviceCommandType = 'run_check_now' | 'restart'

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'
export type PolicyClassification = 'approved' | 'required' | 'restricted' | 'system_component' | 'ignored' | 'review_required'

export type ApprovalStatus = 'pending_approval' | 'approved' | 'rejected' | 'superseded'

export interface ApprovedSoftwareOut {
  approved_software_id: string
  organization_id: string
  app_name: string
  publisher: string | null
  approved_version_min: string | null
  category: string | null
  risk_level: string
  classification: string
  approval_status: ApprovalStatus
  created_by: string | null
  approved_by: string | null
  approved_at: string | null
  rejected_by: string | null
  rejected_at: string | null
  rejected_reason: string | null
  version: number
  supersedes_id: string | null
}

export interface DeviceCommandOut {
  command_id: string
  device_id: string
  command_type: string
  status: 'pending' | 'sent' | 'completed' | 'failed'
  requested_at: string
  completed_at: string | null
  result_message: string | null
}

export interface DataEntityOut {
  entity_id: string
  data_source_id: string
  entity_name: string
  entity_type: string
  description: string | null
}

export interface DataFieldOut {
  field_id: string
  entity_id: string
  field_name: string
  data_type: string | null
  is_primary_key: boolean
  is_sensitive: boolean
}

export interface AuditLogOut {
  log_id: string
  organization_id: string | null
  user_id: string | null
  action: string
  entity_type: string | null
  entity_id: string | null
  old_value: Record<string, unknown> | null
  new_value: Record<string, unknown> | null
  timestamp: string
}

export interface GatewayHealthCounts {
  online: number
  offline: number
  pending: number
  deregistered: number
}

export interface TrendPoint {
  date: string
  count: number
}

export interface DashboardStats {
  active_monitoring_tests: number
  tests_executed_total: number
  tests_executed_today: number
  failed_tests: number
  exceptions_open: number
  exceptions_high_risk: number
  open_findings: number
  overdue_findings: number
  closed_findings: number
  remediation_rate: number
  gateway_health: GatewayHealthCounts
  executions_trend: TrendPoint[]
  exceptions_trend: TrendPoint[]
}

export interface FindingOut {
  finding_id: string
  organization_id: string
  exception_id: string
  finding_title: string
  finding_description: string | null
  risk_rating: string | null
  status: string
  identified_at: string
}

export interface RootCauseOut {
  root_cause_id: string
  finding_id: string
  root_cause_category: string | null
  description: string | null
}

export interface RemediationActionOut {
  remediation_id: string
  finding_id: string
  action_description: string
  responsible_user_id: string | null
  target_date: string | null
  status: string
  completed_at: string | null
  is_overdue: boolean
}

export interface RetestOut {
  retest_id: string
  finding_id: string
  audit_test_id: string
  retest_date: string
  result: string | null
  performed_by: string | null
  comments: string | null
}

export interface TraceNode {
  level: string
  id: string
  label: string
}

export interface TestRuleOut {
  rule_id: string
  audit_test_id: string
  rule_name: string
  rule_type: string | null
  rule_definition: Record<string, unknown>
  severity: string | null
  status: 'pending_approval' | 'active' | 'rejected' | 'superseded' | 'deleted'
  origin: 'manual' | 'auto_generated' | 'auto_generated_edited'
  template_id: string | null
  created_by: string | null
  edited_by: string | null
  edited_at: string | null
  needs_review: boolean
  deleted_reason: string | null
  approved_by: string | null
  approved_at: string | null
  rejected_reason: string | null
  version: number
  supersedes_rule_id: string | null
}

export interface MonitoringScheduleOut {
  schedule_id: string
  audit_test_id: string
  frequency: string
  next_run: string | null
  last_run: string | null
  is_active: boolean
}

export interface TestExecutionOut {
  execution_id: string
  audit_test_id: string
  started_at: string
  completed_at: string | null
  status: 'running' | 'completed' | 'failed'
  records_analyzed: number | null
  exceptions_found: number | null
  execution_log: string | null
}

export interface EvidenceOut {
  evidence_id: string
  execution_id: string
  evidence_type: string | null
  evidence_location: string | null
  evidence_hash: string | null
  created_at: string
}

export interface ExceptionOut {
  exception_id: string
  execution_id: string
  exception_reference: string | null
  exception_description: string | null
  recommended_remediation: string | null
  severity: string | null
  status: string
  owner_id: string | null
  detected_at: string
  last_detected_at: string
  occurrence_count: number
  has_finding: boolean
}

export interface ExceptionRecordOut {
  exception_record_id: string
  exception_id: string
  record_identifier: string | null
  exception_data: Record<string, unknown> | null
}

export interface MappingSuggestion {
  field_id: string
  field_name: string
  data_type: string | null
  suggested_canonical_field: string
  confidence_score: number
}

export interface TestDataMappingOut {
  mapping_id: string
  audit_test_id: string
  data_source_id: string
  entity_id: string
  field_id: string | null
  canonical_field: string | null
  confidence_score: number | null
  mapping_status: string
  approved_by: string | null
  approved_at: string | null
  created_by: string | null
  rejected_by: string | null
  rejected_at: string | null
  rejected_reason: string | null
  version: number
  supersedes_mapping_id: string | null
}

export interface RequiredFieldStatus {
  canonical_field: string
  mapped: boolean
  mapping_id: string | null
  field_name: string | null
  mapping_status: string | null
}

export interface RequiredObjectStatus {
  canonical_object: string
  entity_id: string | null
  entity_name: string | null
  data_source_id: string | null
  source_name: string | null
  required_fields: RequiredFieldStatus[]
  fully_mapped: boolean
}

export interface MappingReadinessOut {
  has_rule: boolean
  ready: boolean
  objects: RequiredObjectStatus[]
}

export interface AuditTestOut {
  audit_test_id: string
  organization_id: string
  test_code: string | null
  test_name: string
  test_description: string | null
  test_type: string | null
  frequency: string | null
  status: string
  control_ids: string[]
  domain: string | null
  required_tables: string[]
}
