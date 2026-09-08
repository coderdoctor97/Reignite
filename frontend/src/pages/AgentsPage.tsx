/**
 * Agents — Apply Config: point installed apps and CLI agents at the
 * gateway endpoint.
 *
 * Master toggle + per-target toggles + Apply / Revert. Writes happen only
 * on explicit Apply and are backed up for Revert.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

type TargetStatus = {
  id: string;
  label: string;
  kind: string;
  best_effort: boolean;
  note: string;
  installed: boolean;
  path: string | null;
  applied: boolean;
  enabled: boolean;
  error: string | null;
};

type ApplyConfigStatus = {
  master_enabled: boolean;
  gateway_base_url: string;
  auth_token: string;
  default_model: string;
  targets: TargetStatus[];
};

type ApplyResult = {
  results: { target: string; status: string; message: string }[];
  applied: number;
  errors: number;
};

const KIND_LABELS: Record<string, string> = {
  cli: 'CLI',
  app: 'App',
  vscode: 'VS Code extension',
};

export function AgentsPage() {
  const [status, setStatus] = useState<ApplyConfigStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [results, setResults] = useState<ApplyResult['results'] | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const s = await api.get<ApplyConfigStatus>('/api/apply-config');
      setStatus(s);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load apply-config status');
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const setMaster = async (enabled: boolean) => {
    setError(null);
    try {
      await api.put('/api/apply-config/master', { enabled });
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to toggle');
    }
  };

  const setTarget = async (targetId: string, enabled: boolean) => {
    setError(null);
    try {
      await api.put(`/api/apply-config/targets/${targetId}`, { enabled });
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to toggle target');
    }
  };

  const apply = async () => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    setResults(null);
    try {
      const r = await api.post<ApplyResult>('/api/apply-config/apply');
      setResults(r.results);
      setSuccess(`Applied to ${r.applied} target${r.applied === 1 ? '' : 's'}${r.errors ? `, ${r.errors} failed` : ''}`);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Apply failed');
    } finally {
      setLoading(false);
    }
  };

  const revert = async () => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    setResults(null);
    try {
      const r = await api.post<ApplyResult>('/api/apply-config/revert');
      setSuccess(`Reverted ${r.applied} target${r.applied === 1 ? '' : 's'}`);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Revert failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page">
      <h1 className="page-title">Agents &amp; Apps</h1>
      <p className="page-description">
        Apply the gateway endpoint configuration to installed applications and CLI
        agents. Writes happen only when you click Apply; Revert restores the
        previous configuration.
      </p>

      {error && <div style={{ color: 'var(--color-error)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', padding: 'var(--space-2) var(--space-3)', background: 'var(--color-error-subtle)', borderRadius: 'var(--radius-md)', border: '1px solid var(--color-error)' }}>{error}</div>}
      {success && <div style={{ color: 'var(--color-success)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', padding: 'var(--space-2) var(--space-3)', background: 'var(--color-success-subtle)', borderRadius: 'var(--radius-md)', border: '1px solid var(--color-success)' }}>{success}</div>}

      {/* Endpoint card */}
      {status && (
        <div style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
            <div style={{ fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--color-text-tertiary)' }}>
              Endpoint: <span style={{ color: 'var(--color-text-secondary)' }}>{status.gateway_base_url}/v1</span>
            </div>
            <div style={{ fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--color-text-tertiary)' }}>
              Token: <span style={{ color: 'var(--color-text-secondary)' }}>{status.auth_token}</span>
            </div>
            <div style={{ fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--color-text-tertiary)' }}>
              Model: <span style={{ color: 'var(--color-text-secondary)' }}>{status.default_model}</span>
            </div>
          </div>

          {/* Master toggle */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
            <button
              onClick={() => setMaster(!status.master_enabled)}
              role="switch"
              aria-checked={status.master_enabled}
              style={{
                width: 44, height: 24, borderRadius: 12, border: 'none', cursor: 'pointer',
                background: status.master_enabled ? 'var(--color-success)' : 'var(--color-bg-overlay)',
                position: 'relative', transition: 'background 0.15s',
              }}
            >
              <span style={{
                position: 'absolute', top: 2, left: status.master_enabled ? 22 : 2,
                width: 20, height: 20, borderRadius: '50%', background: 'white',
                transition: 'left 0.15s',
              }} />
            </button>
            <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: status.master_enabled ? 'var(--color-success)' : 'var(--color-text-secondary)' }}>
              {status.master_enabled ? 'Apply Config is ON' : 'Apply Config is OFF'}
            </span>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 'var(--space-2)' }}>
              <button onClick={apply} disabled={loading || !status.master_enabled} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-accent)', color: 'white', border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)', opacity: (!status.master_enabled || loading) ? 0.5 : 1 }}>
                Apply Config
              </button>
              <button onClick={revert} disabled={loading} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-bg-overlay)', color: 'var(--color-text-primary)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
                Revert All
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Result details */}
      {results && (
        <div style={{ padding: 'var(--space-3) var(--space-4)', marginBottom: 'var(--space-4)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)' }}>
          {results.map(r => (
            <div key={r.target} style={{ color: r.status === 'applied' ? 'var(--color-success)' : r.status === 'error' ? 'var(--color-error)' : 'var(--color-text-tertiary)' }}>
              {r.target}: {r.message}
            </div>
          ))}
        </div>
      )}

      {/* Targets */}
      {status && (
        <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', overflow: 'hidden' }}>
          <div style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)', fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
            Targets
          </div>
          {status.targets.map(t => (
            <div key={t.id} style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border-subtle)', display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 'var(--text-sm)', fontWeight: 500, color: 'var(--color-text-primary)' }}>{t.label}</span>
              <span style={{ fontSize: 'var(--text-xs)', padding: '1px 6px', borderRadius: 'var(--radius-sm)', background: 'var(--color-bg-overlay)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                {KIND_LABELS[t.kind] ?? t.kind}
              </span>
              <span style={{ fontSize: 'var(--text-xs)', color: t.installed ? 'var(--color-success)' : 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                {t.installed ? 'detected' : 'not detected'}
              </span>
              <span style={{ fontSize: 'var(--text-xs)', color: t.applied ? 'var(--color-success)' : 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                {t.applied ? 'applied' : 'not applied'}
              </span>
              {t.best_effort && (
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-warning)', fontFamily: 'var(--font-mono)' }} title={t.note}>
                  best-effort
                </span>
              )}
              {t.note && !t.best_effort && (
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>{t.note}</span>
              )}
              <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={t.path ?? undefined}>
                  {t.path ?? '—'}
                </span>
                <button
                  onClick={() => setTarget(t.id, !t.enabled)}
                  disabled={!status.master_enabled}
                  role="switch"
                  aria-checked={t.enabled}
                  style={{
                    width: 36, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer',
                    background: t.enabled ? 'var(--color-accent)' : 'var(--color-bg-overlay)',
                    position: 'relative', opacity: status.master_enabled ? 1 : 0.5,
                  }}
                >
                  <span style={{
                    position: 'absolute', top: 2, left: t.enabled ? 18 : 2,
                    width: 16, height: 16, borderRadius: '50%', background: 'white',
                    transition: 'left 0.15s',
                  }} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <p style={{ marginTop: 'var(--space-4)', fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)' }}>
        Tip: change the endpoint URL and auth token in Settings. Restart the target app/CLI after applying.
      </p>
    </div>
  );
}
