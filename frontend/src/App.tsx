import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext'
import { ProtectedRoute } from './auth/ProtectedRoute'
import { OrganizationProvider } from './organization/OrganizationContext'
import { Layout } from './components/Layout'
import { LoginPage } from './pages/Login'
import { ActivatePage } from './pages/Activate'
import { DashboardPage } from './pages/Dashboard'
import { OrganizationsPage } from './pages/Organizations'
import { EngagementsPage } from './pages/Engagements'
import { UsersPage } from './pages/Users'
import { AdministrationPage } from './pages/Administration'
import { BusinessUnitsPage } from './pages/BusinessUnits'
import { BusinessProcessesPage } from './pages/BusinessProcesses'
import { RisksPage } from './pages/Risks'
import { ControlsPage } from './pages/Controls'
import { AuditTestsPage } from './pages/AuditTests'
import { RuleParametersPage } from './pages/RuleParameters'
import { GatewaysPage } from './pages/Gateways'
import { DevicesPage } from './pages/Devices'
import { DataSourcesPage } from './pages/DataSources'
import { ConnectionsPage } from './pages/Connections'
import { DataCataloguePage } from './pages/DataCatalogue'
import { ConnectorCataloguePage } from './pages/ConnectorCatalogue'
import { MonitoringPage } from './pages/Monitoring'
import { ExecutionsPage } from './pages/Executions'
import { ExceptionsPage } from './pages/Exceptions'
import { FindingsPage } from './pages/Findings'
import { RemediationPage } from './pages/Remediation'
import { RetestsPage } from './pages/Retests'
import { EvidencePage } from './pages/Evidence'
import { AuditTrailPage } from './pages/AuditTrail'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/activate" element={<ActivatePage />} />

          <Route element={<ProtectedRoute />}>
            <Route
              element={
                <OrganizationProvider>
                  <Layout />
                </OrganizationProvider>
              }
            >
              <Route path="/" element={<DashboardPage />} />
              <Route path="/organizations" element={<OrganizationsPage />} />
              <Route path="/engagements" element={<EngagementsPage />} />
              <Route path="/users" element={<UsersPage />} />
              <Route path="/administration" element={<AdministrationPage />} />

              <Route path="/business-units" element={<BusinessUnitsPage />} />
              <Route path="/business-processes" element={<BusinessProcessesPage />} />
              <Route path="/risks" element={<RisksPage />} />
              <Route path="/controls" element={<ControlsPage />} />
              <Route path="/audit-tests" element={<AuditTestsPage />} />
              <Route path="/rule-parameters" element={<RuleParametersPage />} />
              <Route path="/data-sources" element={<DataSourcesPage />} />
              <Route path="/gateways" element={<GatewaysPage />} />
              <Route path="/devices" element={<DevicesPage />} />
              <Route path="/connections" element={<ConnectionsPage />} />
              <Route path="/data-catalogue" element={<DataCataloguePage />} />
              <Route path="/connector-catalogue" element={<ConnectorCataloguePage />} />
              <Route path="/monitoring" element={<MonitoringPage />} />
              <Route path="/executions" element={<ExecutionsPage />} />
              <Route path="/exceptions" element={<ExceptionsPage />} />
              <Route path="/findings" element={<FindingsPage />} />
              <Route path="/remediation" element={<RemediationPage />} />
              <Route path="/retests" element={<RetestsPage />} />
              <Route path="/evidence" element={<EvidencePage />} />
              <Route path="/audit-trail" element={<AuditTrailPage />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
