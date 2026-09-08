/**
 * Usage — token usage tracking, snapshots, and thresholds.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

type UsageSummary = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  remaining: number;
  limit: number;
  percent_used: number;
  warning_threshold: number;
  warning_triggered: boolean;
  last_snapshot: string | null;
  credentials: {
    credential_id: string;
    provider_id: string;
    key_masked: string | null;
    state: string;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  }[];
  legacy: { input: number; output: number; total: number; remaining: number | null; last_updated: string | null } | null;
};

type Snapshot = {
  id: number;
  provider_id: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  remaining: number;
  limit: number;
  snapshot_at: string;
};

type SnapshotList = { snapshots: Snapshot[]; total: number };


export function UsagePage() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [limit, setLimit] = useState('');
  const [warning, setWarning] = useState('');
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [s, snaps] = await Promise.all([
        api.get<UsageSummary>('/api/usage/summary'),
        api.get<SnapshotList>('/api/usage/snapshots?limit=50'),
      ]);
      setSummary(s);
      setSnapshots(snaps.snapshots);
      if (!limit) setLimit(String(s.limit));
      if (!warning) setWarning(String(s.warning_threshold));
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load usage');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const saveThresholds = async () => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      await api.put('/api/usage/thresholds', {
        usage_limit: Number(limit),
        usage_warning_threshold: Number(warning),
      });
      setSuccess('Thresholds updated');
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update thresholds');
    } finally {
      setLoading(false);
    }
  };

  const capture = async () => {
    setLoading(true);
    try {
      await api.post('/api/usage/capture');
      setSuccess('Snapshot captured');
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Capture failed');
    } finally {
      setLoading(false);
    }
  };

  const pct = summary?.percent_used ?? 0;

  return (
    <div className="page">
      <h1 className="page-title">Usage</h1>
      <p className="page-description">
        Token consumption across credentials, history snapshots, and warning thresholds.
      </p>

      {error && <div style={{ color: 'var(--color-error)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)' }}>{error}</div>}
      {success && <div style={{ color: 'var(--color-success)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)' }}>{success}</div>}

      {/* Big numbers */}
      {summary && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 'var(--space-3)', marginBottom: 'var(--space-4)' }}>
            <div style={cardStyle}><span style={cardLabel}>Total tokens</span><span style={cardValue}>{summary.total_tokens.toLocaleString()}</span></div>
            <div style={cardStyle}><span style={cardLabel}>Input</span><span style={cardValue}>{summary.input_tokens.toLocaleString()}</span></div>
            <div style={cardStyle}><span style={cardLabel}>Output</span><span style={cardValue}>{summary.output_tokens.toLocaleString()}</span></div>
            <div style={cardStyle}><span style={cardLabel}>Remaining</span><span style={cardValue}>{summary.remaining.toLocaleString()}</span></div>
          </div>

          <div style={{ padding: 'var(--space-3) var(--space-4)', marginBottom: 'var(--space-4)', background: 'var(--color-bg-surface)', border: `1px solid ${summary.warning_triggered ? 'var(--color-warning)' : 'var(--color-border)'}`, borderRadius: 'var(--radius-lg)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-2)' }}>
              <span>{pct.toFixed(1)}% of {summary.limit.toLocaleString()} used</span>
              <span>warning at {summary.warning_threshold.toLocaleString()}</span>
            </div>
            <div style={{ height: 10, background: 'var(--color-bg-overlay)', borderRadius: 5, overflow: 'hidden' }}>
              <div style={{ width: `${Math.min(pct, 100)}%`, height: '100%', background: summary.warning_triggered ? 'var(--color-warning)' : 'var(--color-accent)', borderRadius: 5 }} />
            </div>
            {summary.warning_triggered && (
              <div style={{ marginTop: 'var(--space-2)', fontSize: 'var(--text-xs)', color: 'var(--color-warning)', fontFamily: 'var(--font-mono)' }}>
                ⚠ Warning threshold reached — consider replacing your credential.
              </div>
            )}
          </div>
        </>
      )}

      {/* Thresholds */}
      <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'flex-end', marginBottom: 'var(--space-4)', flexWrap: 'wrap', padding: 'var(--space-4)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)' }}>
        <div>
          <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>Total limit (tokens)</label>
          <input type="number" value={limit} onChange={e => setLimit(e.target.value)} style={inputStyle} />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>Warning threshold</label>
          <input type="number" value={warning} onChange={e => setWarning(e.target.value)} style={inputStyle} />
        </div>
        <button onClick={saveThresholds} disabled={loading} style={btnPrimary}>Save</button>
        <button onClick={capture} disabled={loading} style={btnSecondary}>Capture Snapshot Now</button>
      </div>

      {/* Per-credential usage */}
      {summary && summary.credentials.length > 0 && (
        <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', overflow: 'hidden', marginBottom: 'var(--space-4)' }}>
          <div style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)', fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
            Usage by credential
          </div>
          <div>
            {summary.credentials.map(c => (
              <div key={c.credential_id} style={{ display: 'flex', gap: 'var(--space-4)', padding: 'var(--space-2) var(--space-4)', borderBottom: '1px solid var(--color-border-subtle)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--color-text-primary)' }}>{c.key_masked ?? c.credential_id}</span>
                <span style={{ color: c.state === 'active' ? 'var(--color-success)' : 'var(--color-text-tertiary)' }}>{c.state}</span>
                <span style={{ color: 'var(--color-text-tertiary)' }}>in {c.input_tokens.toLocaleString()}</span>
                <span style={{ color: 'var(--color-text-tertiary)' }}>out {c.output_tokens.toLocaleString()}</span>
                <span style={{ color: 'var(--color-text-secondary)', marginLeft: 'auto' }}>total {c.total_tokens.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Legacy usage file */}
      {summary?.legacy && (
        <div style={{ padding: 'var(--space-3) var(--space-4)', marginBottom: 'var(--space-4)', border: '1px dashed var(--color-border)', borderRadius: 'var(--radius-lg)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--color-text-tertiary)' }}>
          Legacy gateway usage file: {summary.legacy.total.toLocaleString()} tokens
          {summary.legacy.last_updated ? ` · updated ${summary.legacy.last_updated}` : ''}
        </div>
      )}

      {/* Snapshots */}
      <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', overflow: 'hidden' }}>
        <div style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)', fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
          History
        </div>
        {snapshots.length === 0 ? (
          <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)' }}>
            No snapshots yet — capture one, or wait for the monitor.
          </div>
        ) : (
          <div>
            {snapshots.slice(0, 20).map(s => (
              <div key={s.id} style={{ display: 'flex', gap: 'var(--space-4)', padding: 'var(--space-2) var(--space-4)', borderBottom: '1px solid var(--color-border-subtle)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--color-text-tertiary)' }}>{new Date(s.snapshot_at).toLocaleString()}</span>
                <span style={{ color: 'var(--color-text-secondary)' }}>total {s.total_tokens.toLocaleString()}</span>
                <span style={{ color: 'var(--color-text-tertiary)' }}>in {s.input_tokens.toLocaleString()} / out {s.output_tokens.toLocaleString()}</span>
                <span style={{ color: 'var(--color-text-tertiary)', marginLeft: 'auto' }}>remaining {s.remaining.toLocaleString()}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  padding: 'var(--space-4)', background: 'var(--color-bg-surface)',
  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
  display: 'flex', flexDirection: 'column', gap: 'var(--space-1)',
};

const cardLabel: React.CSSProperties = {
  fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)',
};

const cardValue: React.CSSProperties = {
  fontSize: 'var(--text-xl)', fontWeight: 600, color: 'var(--color-text-primary)', fontFamily: 'var(--font-mono)',
};

const inputStyle: React.CSSProperties = {
  width: 180, padding: 'var(--space-2) var(--space-3)',
  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', outline: 'none',
};

const btnPrimary: React.CSSProperties = {
  padding: 'var(--space-2) var(--space-4)', background: 'var(--color-accent)', color: 'white',
  border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)',
};

const btnSecondary: React.CSSProperties = {
  padding: 'var(--space-2) var(--space-4)', background: 'var(--color-bg-overlay)',
  color: 'var(--color-text-primary)', border: '1px solid var(--color-border)',
  borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)',
};
