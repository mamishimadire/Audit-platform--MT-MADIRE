// Moved to shared context so the selected organization survives page
// navigation instead of resetting per-page — see organization/OrganizationContext.tsx.
export { useActiveOrganization } from '../organization/OrganizationContext'
