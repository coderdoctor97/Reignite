/**
 * Providers — upstream provider management.
 *
 * Configure provider endpoints, protocols, capabilities, and run
 * user-initiated provider workflows (list/create/revoke keys, import the
 * latest key, model discovery). Includes an OpenRouter quick-add.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

type Provider = {
  id: string;
  name: string;
  protocol: string;
  base_url: string;
  auth_type: string;
  enabled: boolean;
  health_status: string;
  last_health_check: string | null;
  capabilities: Record<string, unknown>;
  metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

type ProviderList = { providers: Provider[]; total: number };
type HealthResult = { provider_id: string; status: string; latency_ms: number | null; http_status: number | null; error: string | null };
type DiscoveryResult = { imported: number; skipped: number; models: string[] };
type ProviderKey = { id: string | null; name: string | null; masked: string | null; createdAt: string | null; dailyTokenLimit: number | null };
type KeysResult = { keys: ProviderKey[]; total: number };

const PROTOCOL_LABELS: Record<string, string> = {
  'openai-completions': 'OpenAI-compatible',
  'anthropic-messages': 'Anthropic Messages',
};

const HEALTH_COLORS: Record<string, string> = {
  healthy: 'var(--color-success)',
  degraded: 'var(--color-warning)',
  unhealthy: 'var(--color-error)',
  unknown: 'var(--color-text-tertiary)',
};

export function ProvidersPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Add / edit form
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [formName, setFormName] = useState('');
  const [formProtocol, setFormProtocol] = useState('openai-completions');
  const [formBaseUrl, setFormBaseUrl] = useState('');
  const [formAuthType, setFormAuthType] = useState('api-key');

  // Workflows per provider
  const [workflowProvider, setWorkflowProvider] = useState<Provider | null>(null);
  const [keys, setKeys] = useState<ProviderKey[] | null>(null);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyLimit, setNewKeyLimit] = useState(1500000);

  const fetchData = useCallback(async () => {
    try {
      const resp = await api.get<ProviderList>('/api/providers');
      setProviders(resp.providers);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load providers');
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const clearMessages = () => { setError(null); setSuccess(null); };

  const openAdd = () => {
    clearMessages();
    setEditing(null);
    setFormName('');
    setFormProtocol('openai-completions');
    setFormBaseUrl('');
    setFormAuthType('api-key');
    setShowForm(true);
  };

  const openEdit = (p: Provider) => {
    clearMessages();
    setEditing(p);
    setFormName(p.name);
    setFormProtocol(p.protocol);
    setFormBaseUrl(p.base_url);
    setFormAuthType(p.auth_type);
    setShowForm(true);
  };

  const submitForm = async () => {
    clearMessages();
    setLoading(true);
    try {
      if (editing) {
        await api.put(`/api/providers/${editing.id}`, {
          name: formName, protocol: formProtocol, base_url: formBaseUrl, auth_type: formAuthType,
        });
        setSuccess('Provider updated');
      } else {
        await api.post('/api/providers', {
          name: formName, protocol: formProtocol, base_url: formBaseUrl, auth_type: formAuthType,
        });
        setSuccess('Provider added');
      }
      setShowForm(false);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save provider');
    } finally {
      setLoading(false);
    }
  };

  const addOpenRouter = async () => {
    clearMessages();
    setLoading(true);
    try {
      await api.post('/api/providers/openrouter');
      setSuccess('OpenRouter provider added — add your API key as a credential, then discover models.');
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add OpenRouter');
    } finally {
      setLoading(false);
    }
  };

  const toggleEnabled = async (p: Provider) => {
    clearMessages();
    try {
      await api.put(`/api/providers/${p.id}`, { enabled: !p.enabled });
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update provider');
    }
  };

  const deleteProvider = async (p: Provider) => {
    if (!window.confirm(`Delete provider "${p.name}"? Its models, credentials, and sessions will be removed too.`)) return;
    clearMessages();
    try {
      await api.delete(`/api/providers/${p.id}`);
      setSuccess('Provider deleted');
      if (workflowProvider?.id === p.id) setWorkflowProvider(null);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete provider');
    }
  };

  const checkHealth = async (p: Provider) => {
    clearMessages();
    setLoading(true);
    try {
      const r = await api.post<HealthResult>(`/api/providers/${p.id}/check`);
      setSuccess(`${p.name}: ${r.status}${r.latency_ms != null ? ` (${r.latency_ms} ms)` : ''}${r.error ? ` — ${r.error}` : ''}`);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Health check failed');
    } finally {
      setLoading(false);
    }
  };

  const discoverModels = async (p: Provider) => {
    clearMessages();
    setLoading(true);
    try {
      const r = await api.post<DiscoveryResult>(`/api/providers/${p.id}/discover-models`);
      setSuccess(`Model discovery: ${r.imported} imported, ${r.skipped} skipped (imported models start disabled — enable them in Models).`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Model discovery failed');
    } finally {
      setLoading(false);
    }
  };

  const openWorkflows = async (p: Provider) => {
    clearMessages();
    setWorkflowProvider(p);
    setKeys(null);
    if (p.capabilities.credential_discovery) {
      setLoading(true);
      try {
        const r = await api.get<KeysResult>(`/api/providers/${p.id}/keys`);
        setKeys(r.keys);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : 'Failed to list provider keys');
      } finally {
        setLoading(false);
      }
    }
  };

  const createKey = async (p: Provider) => {
    if (!newKeyName.trim()) { setError('Key name cannot be empty'); return; }
    clearMessages();
    setLoading(true);
    try {
      await api.post(`/api/providers/${p.id}/keys`, { name: newKeyName, daily_limit: newKeyLimit });
      setSuccess('Key created and stored as an inactive credential — validate and activate it in Credentials.');
      setNewKeyName('');
      const r = await api.get<KeysResult>(`/api/providers/${p.id}/keys`);
      setKeys(r.keys);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create key');
    } finally {
      setLoading(false);
    }
  };

  const revokeKey = async (p: Provider, keyId: string) => {
    if (!window.confirm(`Revoke key ${keyId} on ${p.name}? This cannot be undone.`)) return;
    clearMessages();
    setLoading(true);
    try {
      await api.delete(`/api/providers/${p.id}/keys/${keyId}`);
      setSuccess('Key revoked on the provider');
      const r = await api.get<KeysResult>(`/api/providers/${p.id}/keys`);
      setKeys(r.keys);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to revoke key');
    } finally {
      setLoading(false);
    }
  };

  const importLatest = async (p: Provider) => {
    clearMessages();
    setLoading(true);
    try {
      const r = await api.post<{ changed: boolean; message?: string }>(`/api/providers/${p.id}/keys/import-latest`);
      setSuccess(r.changed ? 'Latest key imported and activated' : (r.message ?? 'Already up to date'));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to import latest key');
    } finally {
      setLoading(false);
    }
  };

  const supports = (p: Provider, cap: string) => Boolean(p.capabilities[cap]);

  return (
    <div className="page">
      <h1 className="page-title">Providers</h1>
      <p className="page-description">
        Configure upstream AI providers (endpoints, protocols, capabilities) and run
        user-initiated provider workflows. Nothing here acts automatically.
      </p>

      {error && <div style={{ color: 'var(--color-error)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', padding: 'var(--space-2) var(--space-3)', background: 'var(--color-error-subtle)', borderRadius: 'var(--radius-md)', border: '1px solid var(--color-error)' }}>{error}</div>}
      {success && <div style={{ color: 'var(--color-success)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', padding: 'var(--space-2) var(--space-3)', background: 'var(--color-success-subtle)', borderRadius: 'var(--radius-md)', border: '1px solid var(--color-success)' }}>{success}</div>}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)', flexWrap: 'wrap' }}>
        <button onClick={openAdd} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-accent)', color: 'white', border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
          Add Provider
        </button>
        <button onClick={addOpenRouter} disabled={loading} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-bg-overlay)', color: 'var(--color-text-primary)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
          + Add OpenRouter
        </button>
      </div>

      {/* Form */}
      {showForm && (
        <div style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)' }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
            {editing ? `Edit ${editing.name}` : 'Add Provider'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 'var(--space-3)' }}>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>Name</label>
              <input type="text" value={formName} onChange={e => setFormName(e.target.value)} placeholder="e.g. OpenRouter" style={inputStyle} />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>Protocol</label>
              <select value={formProtocol} onChange={e => setFormProtocol(e.target.value)} style={inputStyle}>
                <option value="openai-completions">OpenAI-compatible</option>
                <option value="anthropic-messages">Anthropic Messages</option>
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>Base URL (include /v1)</label>
              <input type="text" value={formBaseUrl} onChange={e => setFormBaseUrl(e.target.value)} placeholder="https://openrouter.ai/api/v1" style={inputStyle} />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>Auth type</label>
              <select value={formAuthType} onChange={e => setFormAuthType(e.target.value)} style={inputStyle}>
                <option value="api-key">API key</option>
                <option value="session-cookie">Session cookie</option>
              </select>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
            <button onClick={submitForm} disabled={loading || !formName.trim() || !formBaseUrl.trim()} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-accent)', color: 'white', border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)', opacity: (loading || !formName.trim() || !formBaseUrl.trim()) ? 0.5 : 1 }}>
              Save
            </button>
            <button onClick={() => setShowForm(false)} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-bg-overlay)', color: 'var(--color-text-primary)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Workflows panel */}
      {workflowProvider && (
        <div style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)', border: '1px solid var(--color-accent)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
            <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
              {workflowProvider.name} — Provider keys
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
              user-initiated only · requires an active session
            </span>
            <button onClick={() => setWorkflowProvider(null)} style={{ marginLeft: 'auto', padding: 'var(--space-1) var(--space-2)', background: 'var(--color-bg-overlay)', color: 'var(--color-text-secondary)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontSize: 'var(--text-xs)' }}>
              Close
            </button>
          </div>

          <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-3)', flexWrap: 'wrap' }}>
            {supports(workflowProvider, 'credential_creation') && (
              <>
                <input type="text" value={newKeyName} onChange={e => setNewKeyName(e.target.value)} placeholder="Key name" style={{ ...inputStyle, width: 180 }} />
                <input type="number" value={newKeyLimit} onChange={e => setNewKeyLimit(Number(e.target.value))} style={{ ...inputStyle, width: 140 }} title="Daily token limit" />
                <button onClick={() => createKey(workflowProvider)} disabled={loading} style={actionBtn}>Create Key</button>
              </>
            )}
            {supports(workflowProvider, 'credential_discovery') && (
              <button onClick={() => importLatest(workflowProvider)} disabled={loading} style={{ ...actionBtn, background: 'var(--color-success)', color: '#000' }}>
                Import Latest Key
              </button>
            )}
          </div>

          {keys === null ? (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
              {supports(workflowProvider, 'credential_discovery') ? 'Loading keys…' : 'This provider cannot list keys.'}
            </div>
          ) : keys.length === 0 ? (
            <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
              No keys on the provider.
            </div>
          ) : (
            <div>
              {keys.map(k => (
                <div key={k.id ?? k.masked} style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-2) 0', borderBottom: '1px solid var(--color-border-subtle)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)' }}>
                  <span style={{ color: 'var(--color-text-primary)' }}>{k.masked}</span>
                  <span style={{ color: 'var(--color-text-tertiary)' }}>{k.name}</span>
                  <span style={{ color: 'var(--color-text-tertiary)' }}>{k.dailyTokenLimit?.toLocaleString()} tok/day</span>
                  <span style={{ color: 'var(--color-text-tertiary)' }}>{k.createdAt ?? '—'}</span>
                  {supports(workflowProvider, 'credential_revocation') && k.id && (
                    <button onClick={() => k.id && revokeKey(workflowProvider, k.id)} disabled={loading} style={{ marginLeft: 'auto', padding: 'var(--space-1) var(--space-2)', background: 'var(--color-error-subtle)', color: 'var(--color-error)', border: '1px solid var(--color-error)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', fontSize: 'var(--text-xs)' }}>
                      Revoke
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Provider list */}
      <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', overflow: 'hidden' }}>
        <div style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)', display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>All Providers</span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>{providers.length} total</span>
        </div>
        {providers.length === 0 ? (
          <div style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)' }}>
            No providers yet. Add one, or use "Add OpenRouter".
          </div>
        ) : (
          <div>
            {providers.map(p => (
              <div key={p.id} style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border-subtle)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', flexWrap: 'wrap', marginBottom: 'var(--space-1)' }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: HEALTH_COLORS[p.health_status] || HEALTH_COLORS.unknown }} />
                  <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>{p.name}</span>
                  <span style={{ fontSize: 'var(--text-xs)', padding: '1px 6px', borderRadius: 'var(--radius-sm)', background: p.enabled ? 'var(--color-success-subtle)' : 'var(--color-bg-overlay)', color: p.enabled ? 'var(--color-success)' : 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                    {p.enabled ? 'enabled' : 'disabled'}
                  </span>
                  <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                    {PROTOCOL_LABELS[p.protocol] ?? p.protocol}
                  </span>
                  {p.id === 'demo' && (
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-accent)', fontFamily: 'var(--font-mono)' }}>demo</span>
                  )}
                </div>
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', marginBottom: 'var(--space-2)' }}>
                  {p.base_url} · {p.auth_type} · health: {p.health_status}
                  {p.last_health_check ? ` · checked ${new Date(p.last_health_check).toLocaleTimeString()}` : ''}
                </div>
                {Object.keys(p.capabilities).length > 0 && (
                  <div style={{ display: 'flex', gap: 'var(--space-1)', flexWrap: 'wrap', marginBottom: 'var(--space-2)' }}>
                    {Object.entries(p.capabilities).filter(([, v]) => Boolean(v)).map(([cap]) => (
                      <span key={cap} style={{ fontSize: '10px', padding: '1px 6px', borderRadius: 'var(--radius-sm)', background: 'var(--color-bg-overlay)', color: 'var(--color-text-secondary)', fontFamily: 'var(--font-mono)' }}>
                        {cap}
                      </span>
                    ))}
                  </div>
                )}
                <div style={{ display: 'flex', gap: 'var(--space-1)', flexWrap: 'wrap' }}>
                  <button onClick={() => openEdit(p)} disabled={loading} style={miniBtn}>Edit</button>
                  <button onClick={() => toggleEnabled(p)} disabled={loading} style={miniBtn}>{p.enabled ? 'Disable' : 'Enable'}</button>
                  <button onClick={() => checkHealth(p)} disabled={loading} style={miniBtn}>Check</button>
                  {p.protocol === 'openai-completions' && (
                    <button onClick={() => discoverModels(p)} disabled={loading} style={miniBtn}>Discover Models</button>
                  )}
                  {supports(p, 'dashboard_adapter') && (
                    <button onClick={() => openWorkflows(p)} disabled={loading} style={{ ...miniBtn, color: 'var(--color-accent)', border: '1px solid var(--color-accent)' }}>
                      Keys…
                    </button>
                  )}
                  <button onClick={() => deleteProvider(p)} disabled={loading} style={{ ...miniBtn, color: 'var(--color-error)', border: '1px solid var(--color-error)' }}>
                    Delete
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

const inputStyle: React.CSSProperties = {
  width: '100%', padding: 'var(--space-2) var(--space-3)',
  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', outline: 'none',
};

const actionBtn: React.CSSProperties = {
  padding: 'var(--space-2) var(--space-4)',
  background: 'var(--color-accent)', color: 'white', border: 'none',
  borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)',
};

const miniBtn: React.CSSProperties = {
  padding: 'var(--space-1) var(--space-2)',
  background: 'var(--color-bg-overlay)', color: 'var(--color-text-secondary)',
  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-sm)',
  cursor: 'pointer', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
};
