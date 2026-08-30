/**
 * Credentials — manual entry, validation, activation, replacement, health, monitoring.
 *
 * Phase 3.3: Background monitoring status, warnings, manual run.
 * Monitor-first, user-controlled. No automatic rotation.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

// ── Types ──────────────────────────────────────────────────────

type Credential = {
  id: string;
  provider_id: string;
  key_masked: string | null;
  source: string;
  state: string;
  validation_status: string;
  last_validated: string | null;
  last_validation_error: string | null;
  next_validation_at: string | null;
  usage_input: number;
  usage_output: number;
  usage_total: number;
  activated_at: string | null;
  deactivated_at: string | null;
  created_at: string;
  updated_at: string;
};

type CredentialHealth = {
  credential_id: string;
  provider_id: string;
  key_masked: string | null;
  state: string;
  validation_status: string;
  health: string;
  last_validated: string | null;
  next_validation_at: string | null;
  last_validation_error: string | null;
};

type CredentialListResponse = {
  credentials: Credential[];
  total: number;
};

type CredentialHealthListResponse = {
  credentials: CredentialHealth[];
  total: number;
  summary: Record<string, number>;
};

type CredentialActionResponse = {
  success: boolean;
  message: string;
  credential: Credential;
};

type MonitorStatus = {
  enabled: boolean;
  running: boolean;
  interval_seconds: number;
  last_run: string | null;
  last_success: string | null;
  last_error: string | null;
  credentials_checked: number;
  checks_succeeded: number;
  checks_failed: number;
  total_cycles: number;
  cycle_in_progress: boolean;
};

type MonitorRunResponse = {
  success: boolean;
  message: string;
  cycle_in_progress: boolean;
  credentials_checked: number;
  checks_succeeded: number;
  checks_failed: number;
  health_changes: number;
  cycle_number: number;
};

// ── Constants ──────────────────────────────────────────────────

const STATE_LABELS: Record<string, string> = {
  active: 'Active',
  inactive: 'Inactive',
  expired: 'Expired',
  invalid: 'Invalid',
  revoked: 'Revoked',
};

const STATE_COLORS: Record<string, string> = {
  active: 'var(--color-success)',
  inactive: 'var(--color-text-tertiary)',
  expired: 'var(--color-warning)',
  invalid: 'var(--color-error)',
  revoked: 'var(--color-error)',
};

const VALIDATION_LABELS: Record<string, string> = {
  valid: 'Valid',
  invalid: 'Invalid',
  expired: 'Expired',
  unknown: 'Unknown',
  pending: 'Pending',
  unavailable: 'Unavailable',
};

const VALIDATION_COLORS: Record<string, string> = {
  valid: 'var(--color-success)',
  invalid: 'var(--color-error)',
  expired: 'var(--color-warning)',
  unknown: 'var(--color-text-tertiary)',
  pending: 'var(--color-info)',
  unavailable: 'var(--color-warning)',
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

// ── Component ──────────────────────────────────────────────────

export function CredentialsPage() {
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [healthData, setHealthData] = useState<CredentialHealth[]>([]);
  const [healthSummary, setHealthSummary] = useState<Record<string, number>>({});
  const [activeCredential, setActiveCredential] = useState<Credential | null>(null);
  const [monitorStatus, setMonitorStatus] = useState<MonitorStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Add credential form
  const [showAddForm, setShowAddForm] = useState(false);
  const [newCredentialValue, setNewCredentialValue] = useState('');
  const [newProviderId, setNewProviderId] = useState('default');

  // Replace credential form
  const [showReplaceForm, setShowReplaceForm] = useState(false);
  const [replaceCredentialValue, setReplaceCredentialValue] = useState('');
  const [replaceProviderId, setReplaceProviderId] = useState('default');

  // Confirmation dialog
  const [confirmAction, setConfirmAction] = useState<(() => void) | null>(null);
  const [confirmMessage, setConfirmMessage] = useState('');

  const fetchData = useCallback(async () => {
    try {
      const [listResp, activeResp, healthResp, monitorResp] = await Promise.all([
        api.get<CredentialListResponse>('/api/credentials'),
        api.get<Credential | null>('/api/credentials/active').catch(() => null),
        api.get<CredentialHealthListResponse>('/api/credentials/health').catch(() => null),
        api.get<MonitorStatus>('/api/monitor/status').catch(() => null),
      ]);
      setCredentials(listResp.credentials);
      setActiveCredential(activeResp);
      if (healthResp) {
        setHealthData(healthResp.credentials);
        setHealthSummary(healthResp.summary);
      }
      if (monitorResp) {
        setMonitorStatus(monitorResp);
      }
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to fetch credentials');
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

  const handleAddCredential = async () => {
    if (!newCredentialValue.trim()) {
      setError('Credential value cannot be empty');
      return;
    }
    clearMessages();
    setLoading(true);
    try {
      await api.post('/api/credentials', {
        credential_value: newCredentialValue,
        provider_id: newProviderId,
        source: 'manual',
      });
      setSuccess('Credential added successfully');
      setNewCredentialValue('');
      setShowAddForm(false);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add credential');
    } finally {
      setLoading(false);
    }
  };

  const handleValidate = async (credentialId: string) => {
    clearMessages();
    setLoading(true);
    try {
      const resp = await api.post<CredentialActionResponse>(
        `/api/credentials/${credentialId}/validate`
      );
      setSuccess(resp.message);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to validate credential');
    } finally {
      setLoading(false);
    }
  };

  const handleActivate = async (credentialId: string) => {
    clearMessages();
    setLoading(true);
    try {
      const resp = await api.post<CredentialActionResponse>(
        `/api/credentials/${credentialId}/activate`
      );
      setSuccess(resp.message);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to activate credential');
    } finally {
      setLoading(false);
    }
  };

  const handleDeactivate = async (credentialId: string) => {
    setConfirmMessage('Deactivate this credential? The gateway will lose access until another credential is activated.');
    setConfirmAction(() => async () => {
      clearMessages();
      setLoading(true);
      try {
        const resp = await api.post<CredentialActionResponse>(
          `/api/credentials/${credentialId}/deactivate`
        );
        setSuccess(resp.message);
        await fetchData();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : 'Failed to deactivate credential');
      } finally {
        setLoading(false);
        setConfirmAction(null);
      }
    });
  };

  const handleReplaceCredential = async () => {
    if (!replaceCredentialValue.trim()) {
      setError('Credential value cannot be empty');
      return;
    }
    clearMessages();
    setLoading(true);
    try {
      const resp = await api.post<CredentialActionResponse>('/api/credentials/replace', {
        credential_value: replaceCredentialValue,
        provider_id: replaceProviderId,
      });
      setSuccess(resp.message);
      setReplaceCredentialValue('');
      setShowReplaceForm(false);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to replace credential');
    } finally {
      setLoading(false);
    }
  };

  const handleMonitorRun = async () => {
    clearMessages();
    setLoading(true);
    try {
      const resp = await api.post<MonitorRunResponse>('/api/monitor/run');
      if (resp.cycle_in_progress) {
        setSuccess('Monitor cycle already in progress');
      } else {
        setSuccess(`Monitor cycle completed: ${resp.credentials_checked} checked, ${resp.health_changes} health changes`);
      }
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to run monitor');
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '—';
    try {
      return new Date(dateStr).toLocaleString();
    } catch {
      return dateStr;
    }
  };

  const getHealthForCredential = (credentialId: string): CredentialHealth | undefined => {
    return healthData.find(h => h.credential_id === credentialId);
  };

  // Find credentials with critical/warning health for warnings
  const warningCredentials = healthData.filter(h => h.health === 'critical' || h.health === 'warning');

  return (
    <div className="page">
      <h1 className="page-title">Credentials</h1>
      <p className="page-description">
        Manage API credentials. Add, validate, activate, or replace credentials manually.
        No automatic rotation — all changes require your explicit action.
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

      {/* Monitor status card */}
      {monitorStatus && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 'var(--space-4)', marginBottom: 'var(--space-4)',
          padding: 'var(--space-3) var(--space-4)',
          background: 'var(--color-bg-surface)', border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius-lg)',
          fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
        }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-1)' }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: monitorStatus.running ? 'var(--color-success)' : 'var(--color-text-tertiary)',
            }} />
            <span style={{ color: 'var(--color-text-secondary)' }}>
              Monitor: {monitorStatus.running ? 'Running' : 'Stopped'}
            </span>
          </span>
          <span style={{ color: 'var(--color-text-tertiary)' }}>
            Last check: {timeAgo(monitorStatus.last_run)}
          </span>
          <span style={{ color: 'var(--color-text-tertiary)' }}>
            Interval: {monitorStatus.interval_seconds}s
          </span>
          <span style={{ color: 'var(--color-text-tertiary)' }}>
            Checked: {monitorStatus.credentials_checked}
          </span>
          <span style={{ color: 'var(--color-text-tertiary)' }}>
            Cycles: {monitorStatus.total_cycles}
          </span>
          <button
            onClick={handleMonitorRun}
            disabled={loading || monitorStatus.cycle_in_progress}
            style={{
              marginLeft: 'auto',
              padding: 'var(--space-1) var(--space-3)',
              background: 'var(--color-bg-overlay)',
              color: 'var(--color-text-secondary)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-sm)',
              cursor: loading ? 'wait' : 'pointer',
              opacity: (loading || monitorStatus.cycle_in_progress) ? 0.5 : 1,
              fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
            }}
          >
            {monitorStatus.cycle_in_progress ? 'Running...' : 'Run Now'}
          </button>
        </div>
      )}

      {/* Warning banner for critical/warning credentials */}
      {warningCredentials.length > 0 && (
        <div style={{
          marginBottom: 'var(--space-4)',
          padding: 'var(--space-3) var(--space-4)',
          background: 'var(--color-warning-subtle)',
          border: '1px solid var(--color-warning)',
          borderRadius: 'var(--radius-lg)',
        }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-warning)', marginBottom: 'var(--space-2)' }}>
            ⚠ {warningCredentials.length} credential{warningCredentials.length !== 1 ? 's' : ''} require{warningCredentials.length === 1 ? 's' : ''} attention
          </div>
          {warningCredentials.map(h => (
            <div key={h.credential_id} style={{
              fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
              color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)',
            }}>
              <span style={{ color: HEALTH_COLORS[h.health] }}>● {HEALTH_LABELS[h.health]}</span>
              {' — '}
              {h.key_masked || '—'} ({h.provider_id})
              {h.last_validation_error && (
                <span style={{ color: 'var(--color-error)' }}> — {h.last_validation_error}</span>
              )}
              {' — '}
              <span style={{ color: 'var(--color-text-tertiary)' }}>
                Recommended: Replace the credential and validate it.
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Health summary bar */}
      {credentials.length > 0 && (
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
          onClick={() => { setShowAddForm(!showAddForm); setShowReplaceForm(false); clearMessages(); }}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-accent)', color: 'white', border: 'none',
            borderRadius: 'var(--radius-md)', cursor: 'pointer',
            fontSize: 'var(--text-sm)', fontWeight: 500,
          }}
        >
          Add Credential
        </button>
        <button
          onClick={() => { setShowReplaceForm(!showReplaceForm); setShowAddForm(false); clearMessages(); }}
          disabled={!activeCredential}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-bg-overlay)',
            color: 'var(--color-text-primary)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-md)',
            cursor: activeCredential ? 'pointer' : 'not-allowed',
            opacity: activeCredential ? 1 : 0.5,
            fontSize: 'var(--text-sm)', fontWeight: 500,
          }}
        >
          Replace Credential
        </button>
      </div>

      {/* Add credential form */}
      {showAddForm && (
        <div style={{
          padding: 'var(--space-4)', marginBottom: 'var(--space-4)',
          border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
          background: 'var(--color-bg-surface)',
        }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
            Add New Credential
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
                API Key / Credential Value
              </label>
              <input
                type="password"
                value={newCredentialValue}
                onChange={(e) => setNewCredentialValue(e.target.value)}
                placeholder="sk-..."
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
            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <button
                onClick={handleAddCredential}
                disabled={loading || !newCredentialValue.trim()}
                style={{
                  padding: 'var(--space-2) var(--space-4)',
                  background: 'var(--color-accent)', color: 'white', border: 'none',
                  borderRadius: 'var(--radius-md)',
                  cursor: loading ? 'wait' : 'pointer',
                  opacity: (loading || !newCredentialValue.trim()) ? 0.5 : 1,
                  fontSize: 'var(--text-sm)',
                }}
              >
                Save Credential
              </button>
              <button
                onClick={() => { setShowAddForm(false); setNewCredentialValue(''); }}
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

      {/* Replace credential form */}
      {showReplaceForm && (
        <div style={{
          padding: 'var(--space-4)', marginBottom: 'var(--space-4)',
          border: '1px solid var(--color-warning)', borderRadius: 'var(--radius-lg)',
          background: 'var(--color-warning-subtle)',
        }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
            Replace Active Credential
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-3)' }}>
            This will deactivate the current credential and activate the new one.
            The previous credential will be preserved as inactive.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
                Provider ID
              </label>
              <input
                type="text"
                value={replaceProviderId}
                onChange={(e) => setReplaceProviderId(e.target.value)}
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
                New API Key / Credential Value
              </label>
              <input
                type="password"
                value={replaceCredentialValue}
                onChange={(e) => setReplaceCredentialValue(e.target.value)}
                placeholder="sk-..."
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
            <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <button
                onClick={handleReplaceCredential}
                disabled={loading || !replaceCredentialValue.trim()}
                style={{
                  padding: 'var(--space-2) var(--space-4)',
                  background: 'var(--color-warning)', color: '#000', border: 'none',
                  borderRadius: 'var(--radius-md)',
                  cursor: loading ? 'wait' : 'pointer',
                  opacity: (loading || !replaceCredentialValue.trim()) ? 0.5 : 1,
                  fontSize: 'var(--text-sm)', fontWeight: 500,
                }}
              >
                Replace Credential
              </button>
              <button
                onClick={() => { setShowReplaceForm(false); setReplaceCredentialValue(''); }}
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

      {/* Active credential card */}
      {activeCredential && (() => {
        const health = getHealthForCredential(activeCredential.id);
        return (
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
                Active Credential
              </span>
              {health && (
                <span style={{
                  fontSize: 'var(--text-xs)', padding: '1px 6px',
                  borderRadius: 'var(--radius-sm)',
                  background: `${HEALTH_COLORS[health.health]}20`,
                  color: HEALTH_COLORS[health.health],
                  fontFamily: 'var(--font-mono)',
                }}>
                  {HEALTH_LABELS[health.health]}
                </span>
              )}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 'var(--space-1) var(--space-4)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)' }}>
              <span style={{ color: 'var(--color-text-tertiary)' }}>ID:</span>
              <span style={{ color: 'var(--color-text-secondary)' }}>{activeCredential.id}</span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>Key:</span>
              <span style={{ color: 'var(--color-text-secondary)' }}>{activeCredential.key_masked || '—'}</span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>Provider:</span>
              <span style={{ color: 'var(--color-text-secondary)' }}>{activeCredential.provider_id}</span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>Validation:</span>
              <span style={{ color: VALIDATION_COLORS[activeCredential.validation_status] }}>
                {VALIDATION_LABELS[activeCredential.validation_status] || activeCredential.validation_status}
              </span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>Last validated:</span>
              <span style={{ color: 'var(--color-text-secondary)' }}>{formatDate(activeCredential.last_validated)}</span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>Next validation:</span>
              <span style={{ color: 'var(--color-text-secondary)' }}>{formatDate(activeCredential.next_validation_at)}</span>
              <span style={{ color: 'var(--color-text-tertiary)' }}>Activated:</span>
              <span style={{ color: 'var(--color-text-secondary)' }}>{formatDate(activeCredential.activated_at)}</span>
            </div>
          </div>
        );
      })()}

      {/* Credential list */}
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
            All Credentials
          </span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            {credentials.length} total
          </span>
        </div>

        {credentials.length === 0 ? (
          <div style={{
            padding: 'var(--space-8)', textAlign: 'center',
            color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)',
          }}>
            No credentials yet. Click "Add Credential" to get started.
          </div>
        ) : (
          <div>
            {credentials.map((cred) => {
              const health = getHealthForCredential(cred.id);
              return (
                <div key={cred.id} style={{
                  padding: 'var(--space-3) var(--space-4)',
                  borderBottom: '1px solid var(--color-border-subtle)',
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  gap: 'var(--space-4)',
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-1)' }}>
                      <span style={{
                        width: 8, height: 8, borderRadius: '50%',
                        background: STATE_COLORS[cred.state] || 'var(--color-text-tertiary)',
                      }} />
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
                        color: 'var(--color-text-primary)',
                      }}>
                        {cred.key_masked || '—'}
                      </span>
                      <span style={{
                        fontSize: 'var(--text-xs)', padding: '1px 6px',
                        borderRadius: 'var(--radius-sm)',
                        background: cred.state === 'active' ? 'var(--color-success-subtle)' : 'var(--color-bg-overlay)',
                        color: STATE_COLORS[cred.state],
                        fontFamily: 'var(--font-mono)',
                      }}>
                        {STATE_LABELS[cred.state] || cred.state}
                      </span>
                      {health && (
                        <span style={{
                          fontSize: 'var(--text-xs)', padding: '1px 6px',
                          borderRadius: 'var(--radius-sm)',
                          background: `${HEALTH_COLORS[health.health]}20`,
                          color: HEALTH_COLORS[health.health],
                          fontFamily: 'var(--font-mono)',
                        }}>
                          {HEALTH_LABELS[health.health]}
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: 'var(--space-3)', fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', flexWrap: 'wrap' }}>
                      <span>Provider: {cred.provider_id}</span>
                      <span>Validation: <span style={{ color: VALIDATION_COLORS[cred.validation_status] }}>{VALIDATION_LABELS[cred.validation_status]}</span></span>
                      <span>Last checked: {formatDate(cred.last_validated)}</span>
                      {cred.last_validation_error && (
                        <span style={{ color: 'var(--color-error)' }}>Error: {cred.last_validation_error}</span>
                      )}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 'var(--space-1)', flexShrink: 0 }}>
                    <button
                      onClick={() => handleValidate(cred.id)}
                      disabled={loading}
                      title="Validate"
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
                    {cred.state !== 'active' && (
                      <button
                        onClick={() => handleActivate(cred.id)}
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
                    {cred.state === 'active' && (
                      <button
                        onClick={() => handleDeactivate(cred.id)}
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
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
