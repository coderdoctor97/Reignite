/** Gateway — lifecycle management and health. Phase 2.1: minimal status display. */

import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type GatewayStatus = {
  state: string;
  pid: number | null;
  uptime_seconds: number | null;
  restart_count: number;
  last_exit_code: number | null;
  last_error: string | null;
};

type GatewayHealth = {
  status: string;
  process_alive: boolean;
  port_reachable: boolean;
  checked_at: string;
};

const STATE_LABELS: Record<string, string> = {
  stopped: 'Stopped',
  starting: 'Starting',
  running: 'Running',
  failed: 'Failed',
  stopping: 'Stopping',
};

const STATE_COLORS: Record<string, string> = {
  stopped: 'var(--color-text-tertiary)',
  starting: 'var(--color-warning)',
  running: 'var(--color-success)',
  failed: 'var(--color-error)',
  stopping: 'var(--color-warning)',
};

export function GatewayPage() {
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [health, setHealth] = useState<GatewayHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = async () => {
    try {
      const [s, h] = await Promise.all([
        api.get<GatewayStatus>('/api/gateway/status'),
        api.get<GatewayHealth>('/api/gateway/health'),
      ]);
      setStatus(s);
      setHealth(h);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to fetch status');
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleAction = async (action: 'start' | 'stop' | 'restart') => {
    setLoading(true);
    setError(null);
    try {
      await api.post(`/api/gateway/${action}`);
      // Refresh status after action
      await fetchStatus();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `Failed to ${action} gateway`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="page">
      <h1 className="page-title">Gateway</h1>
      <p className="page-description">
        Start, stop, and restart the gateway. Monitor health and endpoint status.
      </p>

      {error && (
        <div style={{ color: 'var(--color-error)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)' }}>
          {error}
        </div>
      )}

      {/* Status card */}
      <div style={{
        padding: 'var(--space-4)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-bg-surface)',
        marginBottom: 'var(--space-4)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
          <span style={{
            width: 10, height: 10, borderRadius: '50%',
            background: status ? STATE_COLORS[status.state] : 'var(--color-text-tertiary)',
          }} />
          <span style={{ fontWeight: 600, color: 'var(--color-text-primary)' }}>
            {status ? STATE_LABELS[status.state] || status.state : 'Loading...'}
          </span>
          {status?.pid && (
            <span style={{ color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
              PID {status.pid}
            </span>
          )}
          {status?.uptime_seconds != null && (
            <span style={{ color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
              up {Math.floor(status.uptime_seconds)}s
            </span>
          )}
        </div>

        {health && (
          <div style={{ display: 'flex', gap: 'var(--space-4)', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', fontFamily: 'var(--font-mono)' }}>
            <span>Process: {health.process_alive ? 'alive' : 'dead'}</span>
            <span>Port: {health.port_reachable ? 'reachable' : 'unreachable'}</span>
            <span>Health: {health.status}</span>
          </div>
        )}

        {status?.last_error && (
          <div style={{ marginTop: 'var(--space-2)', color: 'var(--color-error)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)' }}>
            {status.last_error}
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
        <button
          onClick={() => handleAction('start')}
          disabled={loading || status?.state === 'running'}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-accent)',
            color: 'white',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            cursor: loading ? 'wait' : 'pointer',
            opacity: (loading || status?.state === 'running') ? 0.5 : 1,
            fontSize: 'var(--text-sm)',
          }}
        >
          Start
        </button>
        <button
          onClick={() => handleAction('stop')}
          disabled={loading || status?.state === 'stopped'}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-bg-overlay)',
            color: 'var(--color-text-primary)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-md)',
            cursor: loading ? 'wait' : 'pointer',
            opacity: (loading || status?.state === 'stopped') ? 0.5 : 1,
            fontSize: 'var(--text-sm)',
          }}
        >
          Stop
        </button>
        <button
          onClick={() => handleAction('restart')}
          disabled={loading || status?.state === 'stopped'}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-bg-overlay)',
            color: 'var(--color-text-primary)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-md)',
            cursor: loading ? 'wait' : 'pointer',
            opacity: (loading || status?.state === 'stopped') ? 0.5 : 1,
            fontSize: 'var(--text-sm)',
          }}
        >
          Restart
        </button>
      </div>
    </div>
  );
}
