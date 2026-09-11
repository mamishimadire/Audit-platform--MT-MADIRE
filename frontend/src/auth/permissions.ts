/**
 * Role lists mirroring the backend's actual permission grants (see
 * backend/app/services/auth_service.py / the Administration page's live
 * role-permission dump). Kept in one place so "can this role do X" is
 * answered identically everywhere in the frontend instead of each screen
 * hardcoding its own copy that can drift out of sync with the others —
 * that drift is exactly what let the Map Data screen show bind/mapping
 * actions to a role with no audit_framework:manage permission.
 */

/** Roles holding the backend's audit_framework:manage permission — the
 * one that gates creating/editing risks, controls, table bindings, data
 * mappings, test rules, and their approvals. */
export const AUDIT_FRAMEWORK_ROLES = ['Platform Super Admin', 'Audit Manager', 'Auditor', 'IT/Audit Technical User', 'Compliance Manager']
