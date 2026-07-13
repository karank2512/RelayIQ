import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, RequireAuth } from './lib/auth'
import { Layout } from './components/Layout'
import { LoginPage } from './pages/Login'
import { OverviewPage } from './pages/Overview'
import { RequestsPage } from './pages/Requests'
import { EntitiesPage } from './pages/Entities'
import { EntityDetailPage } from './pages/EntityDetail'
import { LineagePage } from './pages/Lineage'
import { ReviewPage } from './pages/Review'
import { ReviewDetailPage } from './pages/ReviewDetail'
import { ProvidersPage } from './pages/Providers'
import { PoliciesPage } from './pages/Policies'
import { CampaignsPage } from './pages/Campaigns'
import { AnalyticsPage } from './pages/Analytics'
import { CrmPage } from './pages/Crm'
import { AuditPage } from './pages/Audit'
import { SettingsPage } from './pages/Settings'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              element={
                <RequireAuth>
                  <Layout />
                </RequireAuth>
              }
            >
              <Route path="/" element={<OverviewPage />} />
              <Route path="/requests" element={<RequestsPage />} />
              <Route path="/entities" element={<EntitiesPage />} />
              <Route path="/entities/:entityType/:entityId" element={<EntityDetailPage />} />
              <Route path="/lineage/:entityType/:entityId/:fieldName" element={<LineagePage />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/review/:taskId" element={<ReviewDetailPage />} />
              <Route path="/providers" element={<ProvidersPage />} />
              <Route path="/policies" element={<PoliciesPage />} />
              <Route path="/campaigns" element={<CampaignsPage />} />
              <Route path="/analytics" element={<AnalyticsPage />} />
              <Route path="/crm" element={<CrmPage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  )
}
