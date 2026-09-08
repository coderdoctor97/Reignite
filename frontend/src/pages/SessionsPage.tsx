/**
 * Sessions — session credential management.
 *
 * Phase 4.1: view, add, replace, validate, activate, and deactivate
 * provider dashboard sessions. Monitor-first, user-controlled:
 * no automatic session renewal.
 *
 * Session secrets are submitted through password-style inputs and are
 * cleared after submission. The backend never returns the raw secret —
 * only a masked representation.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

// ── Types ──────────────────────────────────────────────────────

type Session = {
  id: string;
  provider_id: string;
  label: string | null;
  session_masked: string | null;
  source: string;
  lifecycle_state: string;
  validation_state: string;
  health: string;
  last_validated: string | null;
  next_validation_at: string | null;
  last_validation_error: string | null;
  last_successful_fetch: string | null;
  activated_at: string | null;
  deactivated_at: string | null;
  created_at: string;
  updated_at: string;
};

type SessionHealth = {
  session_id: string;
  provider_id: string;
  session_masked: string | null;
  lifecycle_state: string;
  validation_state: string;
  health: string;
  last_validated: string | null;
  next_validation_at: string | null;
  last_validation_error: string | null;
};

type SessionListResponse = {
  sessions: Session[];
  total: number;
};

type SessionHealthListResponse = {
  sessions: SessionHealth[];
  total: number;
  summary: Record<string, number>;
};

type SessionActionResponse = {
  success: boolean;
  message: string;
  session: Session;
};

// ── Constants ──────────────────────────────────────────────────

const LIFECYCLE_LABELS: Record<string, string> = {
  active: 'Active',
  inactive: 'Inactive',
  expired: 'Expired',
  invalid: 'Invalid',
};

const LIFECYCLE_COLORS: Record<string, string> = {
  active: 'var(--color-success)',
  inactive: 'var(--color-text-tertiary)',
  expired: 'var(--color-warning)',
  invalid: 'var(--color-error)',
};

const VALIDATION_LABELS: Record<string, string> = {
  valid: 'Valid',
  invalid: 'Invalid',
  expired: 'Expired',
  unknown: 'Unknown',
  unavailable: 'Unavailable',
  error: 'Error',
};

const VALIDATION_COLORS: Record<string, string> = {
  valid: 'var(--color-success)',
  invalid: 'var(--color-error)',
  expired: 'var(--color-warning)',
  unknown: 'var(--color-text-tertiary)',
  unavailable: 'var(--color-warning)',
  error: 'var(--color-error)',
};

const HEALTH_LABELS: Record<string, string> = {
  healthy: 'Healthy',
  warning: 'Warning',
  critical: 'Critical',
  unknown: 'Unknown',
};

const HEALTH_COLORS: Record<string, string> = {
  healthy: 'var(--color-success)',
  warning: 'var(--color-warning)',
  critical: 'var(--color-error)',
  unknown: 'var(--color-text-tertiary)',
};

// ── Helpers ────────────────────────────────────────────────────

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '—';
  try {
    const now = Date.now();
    const then = new Date(dateStr).getTime();
    const diff = Math.floor((now - then) / 1000);
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  } catch {
    return '—';
  }
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '—';
  try {
    return new Date(dateStr).toLocaleString();
  } catch {
    return dateStr;
  }
}

// ── Component ──────────────────────────────────────────────────

export function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [healthSummary, setHealthSummary] = useState<Record<string, number>>({});
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Add session form
  const [showAddForm, setShowAddForm] = useState(false);
  const [newSecret, setNewSecret] = useState('');
  const [newProviderId, setNewProviderId] = useState('default');
  const [newLabel, setNewLabel] = useState('');

  // Replace session form (target = session being replaced)
  const [replaceTarget, setReplaceTarget] = useState<Session | null>(null);
  const [replaceSecret, setReplaceSecret] = useState('');
  const [replaceLabel, setReplaceLabel] = useState('');

  // Confirmation dialog
  const [confirmAction, setConfirmAction] = useState<(() => void) | null>(null);
  const [confirmMessage, setConfirmMessage] = useState('');

  const fetchData = useCallback(async () => {
    try {
      const [listResp, activeResp, healthResp] = await Promise.all([
        api.get<SessionListResponse>('/api/sessions'),
        api.get<Session | null>('/api/sessions/active').catch(() => null),
        api.get<SessionHealthListResponse>('/api/sessions/health').catch(() => null),
      ]);
      setSessions(listResp.sessions);
      setActiveSession(activeResp);
      if (healthResp) {
        setHealthSummary(healthResp.summary);
      }
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to fetch sessions');
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000); // Refresh every 10s
    return () => clearInterval(interval);
  }, [fetchData]);

  const clearMessages = () => {
    setError(null);
    setSuccess(null);
  };

  const handleAddSession = async () => {
    if (!newSecret.trim()) {
      setError('Session secret cannot be empty');
      return;
    }
    clearMessages();
    setLoading(true);
    try {
      await api.post('/api/sessions', {
        secret_value: newSecret,
        provider_id: newProviderId,
        label: newLabel.trim() || undefined,
        source: 'manual',
      });
      setSuccess('Session added successfully. Validate and activate it when ready.');
      // The raw secret must never remain visible after submission
      setNewSecret('');
      setNewLabel('');
      setShowAddForm(false);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add session');
    } finally {
      setLoading(false);
    }
  };

  const handleValidate = async (sessionId: string) => {
    clearMessages();
    setLoading(true);
    try {
      const resp = await api.post<SessionActionResponse>(
        `/api/sessions/${sessionId}/validate`
      );
      setSuccess(resp.message);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to validate session');
    } finally {
      setLoading(false);
    }
  };

  const handleActivate = async (sessionId: string) => {
    clearMessages();
    setLoading(true);
    try {
      const resp = await api.post<SessionActionResponse>(
        `/api/sessions/${sessionId}/activate`
      );
      setSuccess(resp.message);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to activate session');
    } finally {
      setLoading(false);
    }
  };

  const handleDeactivate = async (sessionId: string) => {
    setConfirmMessage(
      'Deactivate this session? Provider-side management features that depend on it will stop working until another session is activated.'
    );
    setConfirmAction(() => async () => {
      clearMessages();
      setLoading(true);
      try {
        const resp = await api.post<SessionActionResponse>(
          `/api/sessions/${sessionId}/deactivate`
        );
        setSuccess(resp.message);
        await fetchData();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : 'Failed to deactivate session');
      } finally {
        setLoading(false);
        setConfirmAction(null);
      }
    });
  };

  const openReplaceForm = (session: Session) => {
    clearMessages();
    setReplaceTarget(session);
    setReplaceSecret('');
    setReplaceLabel('');
    setShowAddForm(false);
  };

  const handleReplaceSession = async () => {
    if (!replaceTarget) return;
    if (!replaceSecret.trim()) {
      setError('Session secret cannot be empty');
      return;
    }
    clearMessages();
    setLoading(true);
    try {
      const resp = await api.post<SessionActionResponse>('/api/sessions/replace', {
        secret_value: replaceSecret,
        provider_id: replaceTarget.provider_id,
        label: replaceLabel.trim() || undefined,
        session_id: replaceTarget.id,
      });
      setSuccess(resp.message);
      // Clear the secret input immediately after successful submission
      setReplaceSecret('');
      setReplaceLabel('');
      setReplaceTarget(null);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to replace session');
    } finally {
      setLoading(false);
    }
  };

  // Sessions that require user attention (expired / invalid)
  const attentionSessions = sessions.filter(
    s => s.lifecycle_state === 'expired' || s.lifecycle_state === 'invalid'
  );

  return (
    <div className="page">
      <h1 className="page-title">Sessions</h1>
      <p className="page-description">
        Manage provider dashboard sessions. Add, validate, replace, activate, or
        deactivate sessions manually. No automatic session renewal — all changes
        require your explicit action.
      </p>

      {/* Messages */}
      {error && (
        <div style={{
          color: 'var(--color-error)', marginBottom: 'var(--space-4)',
          fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
          padding: 'var(--space-2) var(--space-3)',
          background: 'var(--color-error-subtle)', borderRadius: 'var(--radius-md)',
          border: '1px solid var(--color-error)',
        }}>
          {error}
        </div>
      )}
      {success && (
        <div style={{
          color: 'var(--color-success)', marginBottom: 'var(--space-4)',
          fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
          padding: 'var(--space-2) var(--space-3)',
          background: 'var(--color-success-subtle)', borderRadius: 'var(--radius-md)',
          border: '1px solid var(--color-success)',
        }}>
          {success}
        </div>
      )}

      {/* Confirmation dialog */}
      {confirmAction && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center',
          justifyContent: 'center', zIndex: 1000,
        }}>
          <div style={{
            background: 'var(--color-bg-surface)', border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-lg)', padding: 'var(--space-6)',
            maxWidth: 400, width: '100%',
          }}>
            <div style={{ marginBottom: 'var(--space-4)', color: 'var(--color-text-primary)', fontSize: 'var(--text-sm)' }}>
              {confirmMessage}
            </div>
            <div style={{ display: 'flex', gap: 'var(--space-2)', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setConfirmAction(null)}
                style={{
                  padding: 'var(--space-2) var(--space-4)',
                  background: 'var(--color-bg-overlay)',
                  color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-md)',
                  cursor: 'pointer', fontSize: 'var(--text-sm)',
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => confirmAction()}
                disabled={loading}
                style={{
                  padding: 'var(--space-2) var(--space-4)',
                  background: 'var(--color-error)',
                  color: 'white', border: 'none',
                  borderRadius: 'var(--radius-md)',
                  cursor: loading ? 'wait' : 'pointer',
                  opacity: loading ? 0.5 : 1,
                  fontSize: 'var(--text-sm)',
                }}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Attention banner for expired/invalid sessions */}
      {attentionSessions.length > 0 && (
        <div style={{
          marginBottom: 'var(--space-4)',
          padding: 'var(--space-3) var(--space-4)',
          background: 'var(--color-warning-subtle)',
          border: '1px solid var(--color-warning)',
          borderRadius: 'var(--radius-lg)',
        }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-warning)', marginBottom: 'var(--space-2)' }}>
            ⚠ Session requires attention
          </div>
          {attentionSessions.map(s => (
            <div key={s.id} style={{
              display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
              flexWrap: 'wrap',
              fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
              color: 'var(--color-text-secondary)', marginBottom: 'var(--space-2)',
            }}>
              <span>
                {s.session_masked || '—'}
                {s.label ? ` (${s.label})` : ''} · {s.provider_id}
              </span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>Status:</span>
              <span style={{ color: LIFECYCLE_COLORS[s.lifecycle_state] }}>
                {LIFECYCLE_LABELS[s.lifecycle_state] || s.lifecycle_state}
              </span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>
                Recommended action: Replace the session credential and validate it.
              </span>
              <button
                onClick={() => openReplaceForm(s)}
                disabled={loading}
                style={{
                  padding: 'var(--space-1) var(--space-3)',
                  background: 'var(--color-warning)',
                  color: '#000', border: 'none',
                  borderRadius: 'var(--radius-sm)',
                  cursor: loading ? 'wait' : 'pointer',
                  fontWeight: 600,
                  fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                }}
              >
                Replace Session
              </button>
            </div>
          ))}
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)' }}>
            Sessions are never refreshed automatically.
          </div>
        </div>
      )}

      {/* Health summary bar */}
      {sessions.length > 0 && (
        <div style={{
          display: 'flex', gap: 'var(--space-4)', marginBottom: 'var(--space-4)',
          padding: 'var(--space-3) var(--space-4)',
          background: 'var(--color-bg-surface)', border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-lg)',
          fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
        }}>
          {(['healthy', 'warning', 'critical', 'unknown'] as const).map(state => (
            <span key={state} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-1)' }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%',
                background: HEALTH_COLORS[state],
              }} />
              <span style={{ color: 'var(--color-text-secondary)' }}>
                {HEALTH_LABELS[state]}: {healthSummary[state] ?? 0}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* Action bar */}
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
        <button
          onClick={() => { setShowAddForm(!showAddForm); setReplaceTarget(null); clearMessages(); }}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-accent)', color: 'white', border: 'none',
            borderRadius: 'var(--radius-md)', cursor: 'pointer',
            fontSize: 'var(--text-sm)', fontWeight: 500,
          }}
        >
          Add Session
        </button>
      </div>

      {/* Add session form */}
      {showAddForm && (
        <div style={{
          padding: 'var(--space-4)', marginBottom: 'var(--space-4)',
          border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
          background: 'var(--color-bg-surface)',
        }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
            Add New Session
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
                Provider ID
              </label>
              <input
                type="text"
                value={newProviderId}
                onChange={(e) => setNewProviderId(e.target.value)}
                placeholder="default"
                style={{
                  width: '100%', padding: 'var(--space-2) var(--space-3)',
                  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
                  outline: 'none',
                }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
                Label (optional)
              </label>
              <input
                type="text"
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                placeholder="e.g. dashboard login"
                autoComplete="off"
                style={{
                  width: '100%', padding: 'var(--space-2) var(--space-3)',
                  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
                  outline: 'none',
                }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
                Session Secret (cookie value / token)
              </label>
              <input
                type="password"
                value={newSecret}
                onChange={(e) => setNewSecret(e.target.value)}
                placeholder="paste session secret…"
                autoComplete="off"
                spellCheck={false}
                style={{
                  width: '100%', padding: 'var(--space-2) var(--space-3)',
                  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
                  outline: 'none',
                }}
              />
              <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', marginTop: 'var(--space-1)' }}>
                Stored securely via the SecretStore. Never shown again after saving.
              </div>
            </div>
            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <button
                onClick={handleAddSession}
                disabled={loading || !newSecret.trim()}
                style={{
                  padding: 'var(--space-2) var(--space-4)',
                  background: 'var(--color-accent)', color: 'white', border: 'none',
                  borderRadius: 'var(--radius-md)',
                  cursor: loading ? 'wait' : 'pointer',
                  opacity: (loading || !newSecret.trim()) ? 0.5 : 1,
                  fontSize: 'var(--text-sm)',
                }}
              >
                Save Session
              </button>
              <button
                onClick={() => { setShowAddForm(false); setNewSecret(''); setNewLabel(''); }}
                style={{
                  padding: 'var(--space-2) var(--space-4)',
                  background: 'var(--color-bg-overlay)',
                  color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-md)',
                  cursor: 'pointer', fontSize: 'var(--text-sm)',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Replace session form */}
      {replaceTarget && (
        <div style={{
          padding: 'var(--space-4)', marginBottom: 'var(--space-4)',
          border: '1px solid var(--color-warning)', borderRadius: 'var(--radius-lg)',
          background: 'var(--color-warning-subtle)',
        }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
            Replace Session
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-3)' }}>
            Replacing {replaceTarget.session_masked || '—'}
            {replaceTarget.label ? ` (${replaceTarget.label})` : ''} for provider{' '}
            {replaceTarget.provider_id}. The new session is validated and then
            activated; the previous session is preserved as inactive.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
                Label (optional)
              </label>
              <input
                type="text"
                value={replaceLabel}
                onChange={(e) => setReplaceLabel(e.target.value)}
                placeholder="e.g. dashboard login (new)"
                autoComplete="off"
                style={{
                  width: '100%', padding: 'var(--space-2) var(--space-3)',
                  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
                  outline: 'none',
                }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
                New Session Secret
              </label>
              <input
                type="password"
                value={replaceSecret}
                onChange={(e) => setReplaceSecret(e.target.value)}
                placeholder="paste new session secret…"
                autoComplete="off"
                spellCheck={false}
                style={{
                  width: '100%', padding: 'var(--space-2) var(--space-3)',
                  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
                  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
                  outline: 'none',
                }}
              />
            </div>
            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <button
                onClick={handleReplaceSession}
                disabled={loading || !replaceSecret.trim()}
                style={{
                  padding: 'var(--space-2) var(--space-4)',
                  background: 'var(--color-warning)', color: '#000', border: 'none',
                  borderRadius: 'var(--radius-md)',
                  cursor: loading ? 'wait' : 'pointer',
                  opacity: (loading || !replaceSecret.trim()) ? 0.5 : 1,
                  fontSize: 'var(--text-sm)', fontWeight: 500,
                }}
              >
                Replace Session
              </button>
              <button
                onClick={() => { setReplaceTarget(null); setReplaceSecret(''); setReplaceLabel(''); }}
                style={{
                  padding: 'var(--space-2) var(--space-4)',
                  background: 'var(--color-bg-overlay)',
                  color: 'var(--color-text-primary)',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius-md)',
                  cursor: 'pointer', fontSize: 'var(--text-sm)',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Active session card */}
      {activeSession && (
        <div style={{
          padding: 'var(--space-4)', marginBottom: 'var(--space-4)',
          border: '1px solid var(--color-success)', borderRadius: 'var(--radius-lg)',
          background: 'var(--color-success-subtle)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: 'var(--color-success)',
            }} />
            <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
              Active Session
            </span>
            <span style={{
              fontSize: 'var(--text-xs)', padding: '1px 6px',
              borderRadius: 'var(--radius-sm)',
              background: `${HEALTH_COLORS[activeSession.health]}20`,
              color: HEALTH_COLORS[activeSession.health],
              fontFamily: 'var(--font-mono)',
            }}>
              {HEALTH_LABELS[activeSession.health]}
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 'var(--space-1) var(--space-4)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)' }}>
            <span style={{ color: 'var(--color-text-tertiary)' }}>ID:</span>
            <span style={{ color: 'var(--color-text-secondary)' }}>{activeSession.id}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Session:</span>
            <span style={{ color: 'var(--color-text-secondary)' }}>{activeSession.session_masked || '—'}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Label:</span>
            <span style={{ color: 'var(--color-text-secondary)' }}>{activeSession.label || '—'}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Provider:</span>
            <span style={{ color: 'var(--color-text-secondary)' }}>{activeSession.provider_id}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Validation:</span>
            <span style={{ color: VALIDATION_COLORS[activeSession.validation_state] }}>
              {VALIDATION_LABELS[activeSession.validation_state] || activeSession.validation_state}
            </span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Last validated:</span>
            <span style={{ color: 'var(--color-text-secondary)' }}>{formatDate(activeSession.last_validated)}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Next validation:</span>
            <span style={{ color: 'var(--color-text-secondary)' }}>{formatDate(activeSession.next_validation_at)}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Activated:</span>
            <span style={{ color: 'var(--color-text-secondary)' }}>{formatDate(activeSession.activated_at)}</span>
          </div>
        </div>
      )}

      {/* Session list */}
      <div style={{
        border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
        background: 'var(--color-bg-surface)', overflow: 'hidden',
      }}>
        <div style={{
          padding: 'var(--space-3) var(--space-4)',
          borderBottom: '1px solid var(--color-border)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
            All Sessions
          </span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            {sessions.length} total
          </span>
        </div>

        {sessions.length === 0 ? (
          <div style={{
            padding: 'var(--space-8)', textAlign: 'center',
            color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)',
          }}>
            No sessions yet. Click "Add Session" to get started.
          </div>
        ) : (
          <div>
            {sessions.map((s) => (
              <div key={s.id} style={{
                padding: 'var(--space-3) var(--space-4)',
                borderBottom: '1px solid var(--color-border-subtle)',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                gap: 'var(--space-4)',
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-1)', flexWrap: 'wrap' }}>
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%',
                      background: LIFECYCLE_COLORS[s.lifecycle_state] || 'var(--color-text-tertiary)',
                    }} />
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
                      color: 'var(--color-text-primary)',
                    }}>
                      {s.session_masked || '—'}
                    </span>
                    {s.label && (
                      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)' }}>
                        {s.label}
                      </span>
                    )}
                    <span style={{
                      fontSize: 'var(--text-xs)', padding: '1px 6px',
                      borderRadius: 'var(--radius-sm)',
                      background: s.lifecycle_state === 'active' ? 'var(--color-success-subtle)' : 'var(--color-bg-overlay)',
                      color: LIFECYCLE_COLORS[s.lifecycle_state],
                      fontFamily: 'var(--font-mono)',
                    }}>
                      {LIFECYCLE_LABELS[s.lifecycle_state] || s.lifecycle_state}
                    </span>
                    <span style={{
                      fontSize: 'var(--text-xs)', padding: '1px 6px',
                      borderRadius: 'var(--radius-sm)',
                      background: `${VALIDATION_COLORS[s.validation_state]}20`,
                      color: VALIDATION_COLORS[s.validation_state],
                      fontFamily: 'var(--font-mono)',
                    }}>
                      {VALIDATION_LABELS[s.validation_state] || s.validation_state}
                    </span>
                    <span style={{
                      fontSize: 'var(--text-xs)', padding: '1px 6px',
                      borderRadius: 'var(--radius-sm)',
                      background: `${HEALTH_COLORS[s.health]}20`,
                      color: HEALTH_COLORS[s.health],
                      fontFamily: 'var(--font-mono)',
                    }}>
                      {HEALTH_LABELS[s.health]}
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 'var(--space-3)', fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', flexWrap: 'wrap' }}>
                    <span>Provider: {s.provider_id}</span>
                    <span>Last validated: {timeAgo(s.last_validated)}</span>
                    <span>Next validation: {timeAgo(s.next_validation_at)}</span>
                    {s.last_validation_error && (
                      <span style={{ color: 'var(--color-error)' }}>Error: {s.last_validation_error}</span>
                    )}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 'var(--space-1)', flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                  <button
                    onClick={() => handleValidate(s.id)}
                    disabled={loading}
                    title="Validate now"
                    style={{
                      padding: 'var(--space-1) var(--space-2)',
                      background: 'var(--color-bg-overlay)',
                      color: 'var(--color-text-secondary)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 'var(--radius-sm)',
                      cursor: loading ? 'wait' : 'pointer',
                      fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                    }}
                  >
                    Validate
                  </button>
                  {s.lifecycle_state !== 'active' && (
                    <button
                      onClick={() => handleActivate(s.id)}
                      disabled={loading}
                      title="Activate"
                      style={{
                        padding: 'var(--space-1) var(--space-2)',
                        background: 'var(--color-accent-subtle)',
                        color: 'var(--color-accent)',
                        border: '1px solid var(--color-accent)',
                        borderRadius: 'var(--radius-sm)',
                        cursor: loading ? 'wait' : 'pointer',
                        fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                      }}
                    >
                      Activate
                    </button>
                  )}
                  {s.lifecycle_state === 'active' && (
                    <button
                      onClick={() => handleDeactivate(s.id)}
                      disabled={loading}
                      title="Deactivate"
                      style={{
                        padding: 'var(--space-1) var(--space-2)',
                        background: 'var(--color-error-subtle)',
                        color: 'var(--color-error)',
                        border: '1px solid var(--color-error)',
                        borderRadius: 'var(--radius-sm)',
                        cursor: loading ? 'wait' : 'pointer',
                        fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                      }}
                    >
                      Deactivate
                    </button>
                  )}
                  <button
                    onClick={() => openReplaceForm(s)}
                    disabled={loading}
                    title="Replace this session"
                    style={{
                      padding: 'var(--space-1) var(--space-2)',
                      background: 'var(--color-warning-subtle)',
                      color: 'var(--color-warning)',
                      border: '1px solid var(--color-warning)',
                      borderRadius: 'var(--radius-sm)',
                      cursor: loading ? 'wait' : 'pointer',
                      fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                    }}
                  >
                    Replace
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
