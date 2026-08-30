/**
 * Gateway Control Center — App Root
 *
 * Sets up routing. All pages are lazy-loadable placeholders.
 * Business logic does NOT live in route components.
 */

import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { DashboardPage } from './pages/DashboardPage';
import { GatewayPage } from './pages/GatewayPage';
import { CredentialsPage } from './pages/CredentialsPage';
import { SessionsPage } from './pages/SessionsPage';
import { ProvidersPage } from './pages/ProvidersPage';
import { ModelsPage } from './pages/ModelsPage';
import { UsagePage } from './pages/UsagePage';
import { LogsPage } from './pages/LogsPage';
import { SettingsPage } from './pages/SettingsPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/gateway" element={<GatewayPage />} />
          <Route path="/credentials" element={<CredentialsPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/providers" element={<ProvidersPage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/usage" element={<UsagePage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
