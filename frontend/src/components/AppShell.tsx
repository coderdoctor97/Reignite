/**
 * Application shell — sidebar navigation + content area.
 *
 * This is the top-level layout component. It provides:
 * - A fixed sidebar with navigation links
 * - A scrollable content area for routed pages
 *
 * Business logic does NOT live here. This is presentation only.
 */

import { NavLink, Outlet } from 'react-router-dom';
import './AppShell.css';

const NAV_ITEMS = [
  { to: '/dashboard',    label: 'Dashboard',    icon: '◉' },
  { to: '/gateway',      label: 'Gateway',      icon: '⇅' },
  { to: '/credentials',  label: 'Credentials',  icon: '⚿' },
  { to: '/sessions',     label: 'Sessions',     icon: '◎' },
  { to: '/providers',    label: 'Providers',    icon: '⬡' },
  { to: '/models',       label: 'Models',       icon: '◈' },
  { to: '/usage',        label: 'Usage',        icon: '▤' },
  { to: '/logs',         label: 'Logs',         icon: '≡' },
  { to: '/settings',     label: 'Settings',     icon: '⚙' },
] as const;

export function AppShell() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="sidebar-logo">GCC</span>
          <span className="sidebar-title">Control Center</span>
        </div>
        <nav className="sidebar-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `nav-link ${isActive ? 'nav-link--active' : ''}`
              }
            >
              <span className="nav-icon">{item.icon}</span>
              <span className="nav-label">{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="sidebar-version">v0.1.0</span>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
