/**
 * Dashboard — real-time overview: gateway, credentials, sessions,
 * providers, usage, and recent events.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

type GatewayStatus = {
  state: string;
  enabled: boolean;
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_errors: number;
  error_rate: number;
  endpoint_url: string;
};

type CredentialHealthList = {
  total: number;
  summary: Record<string, number>;
};

type SessionHealthList = {
  total: number;
  summary: Record<string, number>;
};

type UsageSummary = {
  total_tokens: number;
  remaining: number;
  limit: number;
  percent_used: number;
  warning_triggered: boolean;
};

type ProviderList = {
  providers: { id: string; name: string; protocol: string; enabled: boolean; health_status: string }[];
  total: number;
};

type EventItem = {
  id: number;
  event_type: string;
  severity: string;
  message: string;
  created_at: string;
};

type EventsList = { events: EventItem[]; total: number };

const SEVERITY_COLORS: Record<string, string> = {
  info: 'var(--color-info)',
  warn: 'var(--color-warning)',
  error: 'var(--color-error)',
  critical: 'var(--color-error)',
  debug: 'var(--color-text-tertiary)',
};

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '—';
  try {
    const diff = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  } catch {
    return '—';
  }
}

function StatCard({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={{
      padding: 'var(--space-4)', background: 'var(--color-bg-surface)',
      border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
      display: 'flex', flexDirection: 'column', gap: 'var(--space-1)',
    }}>
      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
        {label}
      </span>
      <span style={{ fontSize: 'var(--text-xl)', fontWeight: 600, color: accent || 'var(--color-text-primary)', fontFamily: 'var(--font-mono)' }}>
        {value}
      </span>
    </div>
  );
}

export function DashboardPage() {
  const [gateway, setGateway] = useState<GatewayStatus | null>(null);
  const [credHealth, setCredHealth] = useState<CredentialHealthList | null>(null);
  const [sessionHealth, setSessionHealth] = useState<SessionHealthList | null>(null);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [providers, setProviders] = useState<ProviderList | null>(null);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [gw, cred, sess, usg, prov, evts] = await Promise.all([
        api.get<GatewayStatus>('/api/gateway/status'),
        api.get<CredentialHealthList>('/api/credentials/health').catch(() => null),
        api.get<SessionHealthList>('/api/sessions/health').catch(() => null),
        api.get<UsageSummary>('/api/usage/summary').catch(() => null),
        api.get<ProviderList>('/api/providers').catch(() => null),
        api.get<EventsList>('/api/events?limit=8').catch(() => null),
      ]);
      setGateway(gw);
      setCredHealth(cred);
      setSessionHealth(sess);
      setUsage(usg);
      setProviders(prov);
      setEvents(evts?.events ?? []);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const usagePct = usage?.percent_used ?? 0;

  return (
    <div className="page">
      <h1 className="page-title">Dashboard</h1>
      <p className="page-description">
        Gateway status, credential and session health, usage, and recent activity.
      </p>

      {error && (
        <div style={{ color: 'var(--color-error)', marginBottom: 'var(--space-4)', fontSize: 'var(--text-sm)', fontFamily: 'var(--font-mono)' }}>
          {error}
        </div>
      )}

      {/* Stat cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 'var(--space-3)', marginBottom: 'var(--space-4)' }}>
        <StatCard
          label="Gateway"
          value={gateway ? (gateway.enabled ? 'Running' : 'Stopped') : '—'}
          accent={gateway?.enabled ? 'var(--color-success)' : 'var(--color-text-tertiary)'}
        />
        <StatCard label="Endpoint" value={gateway?.endpoint_url ?? '—'} />
        <StatCard
          label="Credential health"
          value={credHealth ? `${credHealth.summary.critical ?? 0} critical` : '—'}
          accent={(credHealth?.summary.critical ?? 0) > 0 ? 'var(--color-error)' : 'var(--color-success)'}
        />
        <StatCard
          label="Session health"
          value={sessionHealth ? `${sessionHealth.summary.critical ?? 0} critical` : '—'}
          accent={(sessionHealth?.summary.critical ?? 0) > 0 ? 'var(--color-error)' : 'var(--color-success)'}
        />
        <StatCard
          label="Tokens used"
          value={usage ? `${(usage.total_tokens ?? 0).toLocaleString()} / ${(usage.limit ?? 0).toLocaleString()}` : '—'}
          accent={usage?.warning_triggered ? 'var(--color-warning)' : undefined}
        />
        <StatCard label="Requests" value={gateway ? String(gateway.total_requests) : '—'} />
      </div>

      {/* Usage bar */}
      {usage && (
        <div style={{
          padding: 'var(--space-3) var(--space-4)', marginBottom: 'var(--space-4)',
          background: 'var(--color-bg-surface)', border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-lg)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-2)' }}>
            <span>Token usage</span>
            <span>{usagePct.toFixed(1)}% · {usage.remaining.toLocaleString()} remaining</span>
          </div>
          <div style={{ height: 8, background: 'var(--color-bg-overlay)', borderRadius: 4, overflow: 'hidden' }}>
            <div style={{
              width: `${Math.min(usagePct, 100)}%`, height: '100%',
              background: usage.warning_triggered ? 'var(--color-warning)' : 'var(--color-accent)',
              borderRadius: 4,
            }} />
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 'var(--space-4)' }}>
        {/* Providers */}
        <div style={{
          border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
          background: 'var(--color-bg-surface)', overflow: 'hidden',
        }}>
          <div style={{
            padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)',
            fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)',
            display: 'flex', justifyContent: 'space-between',
          }}>
            <span>Providers</span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
              {providers?.total ?? 0} total
            </span>
          </div>
          {!providers || providers.providers.length === 0 ? (
            <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)' }}>
              No providers configured. Add one in Providers.
            </div>
          ) : (
            <div>
              {providers.providers.map(p => (
                <div key={p.id} style={{
                  padding: 'var(--space-2) var(--space-4)', borderBottom: '1px solid var(--color-border-subtle)',
                  display: 'flex', alignItems: 'center', gap: 'var(--space-2)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: !p.enabled ? 'var(--color-text-tertiary)' : p.health_status === 'healthy' ? 'var(--color-success)' : p.health_status === 'unhealthy' ? 'var(--color-error)' : 'var(--color-warning)',
                  }} />
                  <span style={{ color: 'var(--color-text-primary)' }}>{p.name}</span>
                  <span style={{ color: 'var(--color-text-tertiary)' }}>{p.protocol}</span>
                  <span style={{ marginLeft: 'auto', color: 'var(--color-text-tertiary)' }}>{p.health_status}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Recent events */}
        <div style={{
          border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
          background: 'var(--color-bg-surface)', overflow: 'hidden',
        }}>
          <div style={{
            padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)',
            fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)',
          }}>
            Recent Activity
          </div>
          {events.length === 0 ? (
            <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)' }}>
              No events yet.
            </div>
          ) : (
            <div>
              {events.map(e => (
                <div key={e.id} style={{
                  padding: 'var(--space-2) var(--space-4)', borderBottom: '1px solid var(--color-border-subtle)',
                  display: 'flex', gap: 'var(--space-2)', alignItems: 'baseline', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                }}>
                  <span style={{ color: SEVERITY_COLORS[e.severity], flexShrink: 0 }}>●</span>
                  <span style={{ color: 'var(--color-text-secondary)', wordBreak: 'break-word' }}>{e.message}</span>
                  <span style={{ marginLeft: 'auto', color: 'var(--color-text-tertiary)', flexShrink: 0 }}>{timeAgo(e.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
