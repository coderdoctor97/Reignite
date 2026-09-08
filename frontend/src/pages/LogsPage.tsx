/**
 * Logs — structured event log viewer with filters.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

type EventItem = {
  id: number;
  event_type: string;
  severity: string;
  message: string;
  details_json: string | null;
  created_at: string;
};

type EventsList = { events: EventItem[]; total: number };
type EventTypes = { types: string[]; prefixes: string[] };

const SEVERITY_COLORS: Record<string, string> = {
  debug: 'var(--color-text-tertiary)',
  info: 'var(--color-info)',
  warn: 'var(--color-warning)',
  error: 'var(--color-error)',
  critical: 'var(--color-error)',
};

export function LogsPage() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [total, setTotal] = useState(0);
  const [prefixes, setPrefixes] = useState<string[]>([]);
  const [severityFilter, setSeverityFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (severityFilter) params.set('severity', severityFilter);
      if (typeFilter) params.set('event_type', typeFilter);
      params.set('limit', '200');
      const [list, types] = await Promise.all([
        api.get<EventsList>(`/api/events?${params.toString()}`),
        api.get<EventTypes>('/api/events/types').catch(() => ({ types: [], prefixes: [] })),
      ]);
      setEvents(list.events);
      setTotal(list.total);
      setPrefixes(types.prefixes);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load events');
    }
  }, [severityFilter, typeFilter]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  return (
    <div className="page">
      <h1 className="page-title">Logs</h1>
      <p className="page-description">
        Structured application events: gateway, credentials, sessions, usage, and monitor activity.
      </p>

      {error && <div style={{ color: 'var(--color-error)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)' }}>{error}</div>}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)', flexWrap: 'wrap' }}>
        <select value={severityFilter} onChange={e => setSeverityFilter(e.target.value)} style={selectStyle}>
          <option value="">All severities</option>
          {['debug', 'info', 'warn', 'error', 'critical'].map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)} style={selectStyle}>
          <option value="">All types</option>
          {prefixes.map(p => <option key={p} value={p}>{p}.*</option>)}
        </select>
        <span style={{ marginLeft: 'auto', fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', alignSelf: 'center' }}>
          {total} events
        </span>
      </div>

      <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', overflow: 'hidden' }}>
        {events.length === 0 ? (
          <div style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)' }}>
            No events match the current filters.
          </div>
        ) : (
          <div>
            {events.map(e => (
              <div key={e.id} style={{ borderBottom: '1px solid var(--color-border-subtle)' }}>
                <div
                  onClick={() => setExpanded(expanded === e.id ? null : e.id)}
                  style={{ display: 'flex', gap: 'var(--space-3)', padding: 'var(--space-2) var(--space-4)', alignItems: 'baseline', cursor: 'pointer', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)' }}
                >
                  <span style={{ color: SEVERITY_COLORS[e.severity] ?? 'var(--color-text-tertiary)', flexShrink: 0 }}>●</span>
                  <span style={{ color: 'var(--color-accent)', flexShrink: 0 }}>{e.event_type}</span>
                  <span style={{ color: 'var(--color-text-secondary)', flex: 1, wordBreak: 'break-word' }}>{e.message}</span>
                  <span style={{ color: 'var(--color-text-tertiary)', flexShrink: 0 }}>{new Date(e.created_at).toLocaleString()}</span>
                </div>
                {expanded === e.id && e.details_json && (
                  <pre style={{
                    margin: 0, padding: 'var(--space-2) var(--space-4) var(--space-3)',
                    fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--color-text-tertiary)',
                    whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                    background: 'var(--color-bg-elevated)',
                  }}>
                    {(() => { try { return JSON.stringify(JSON.parse(e.details_json), null, 2); } catch { return e.details_json; } })()}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  padding: 'var(--space-2) var(--space-3)',
  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)', outline: 'none',
};
