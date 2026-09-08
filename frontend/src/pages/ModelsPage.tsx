/**
 * Models — model configuration and routing.
 *
 * Models are what the gateway routes requests to. Enable models, set one
 * default and one fallback per provider.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

type Model = {
  id: string;
  provider_id: string;
  display_name: string;
  model_id: string;
  context_window: number | null;
  capabilities: string[];
  enabled: boolean;
  is_default: boolean;
  is_fallback: boolean;
};

type ModelList = { models: Model[]; total: number };
type ProviderList = { providers: { id: string; name: string }[]; total: number };

export function ModelsPage() {
  const [models, setModels] = useState<Model[]>([]);
  const [providers, setProviders] = useState<{ id: string; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [formProvider, setFormProvider] = useState('');
  const [formDisplay, setFormDisplay] = useState('');
  const [formModelId, setFormModelId] = useState('');
  const [formContext, setFormContext] = useState('');

  const fetchData = useCallback(async () => {
    try {
      const [ml, pl] = await Promise.all([
        api.get<ModelList>('/api/models'),
        api.get<ProviderList>('/api/providers'),
      ]);
      setModels(ml.models);
      setProviders(pl.providers);
      if (!formProvider && pl.providers.length > 0) setFormProvider(pl.providers[0].id);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load models');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const clearMessages = () => { setError(null); setSuccess(null); };

  const submitForm = async () => {
    clearMessages();
    setLoading(true);
    try {
      await api.post('/api/models', {
        provider_id: formProvider,
        display_name: formDisplay,
        model_id: formModelId,
        context_window: formContext ? Number(formContext) : undefined,
        capabilities: ['chat'],
      });
      setSuccess('Model added');
      setShowForm(false);
      setFormDisplay('');
      setFormModelId('');
      setFormContext('');
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add model');
    } finally {
      setLoading(false);
    }
  };

  const updateModel = async (m: Model, patch: Record<string, unknown>, message: string) => {
    clearMessages();
    try {
      await api.put(`/api/models/${m.id}`, patch);
      setSuccess(message);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to update model');
    }
  };

  const deleteModel = async (m: Model) => {
    if (!window.confirm(`Delete model "${m.display_name}"?`)) return;
    clearMessages();
    try {
      await api.delete(`/api/models/${m.id}`);
      setSuccess('Model deleted');
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to delete model');
    }
  };

  const providerName = (id: string) => providers.find(p => p.id === id)?.name ?? id;

  return (
    <div className="page">
      <h1 className="page-title">Models</h1>
      <p className="page-description">
        Configure models, set defaults and fallbacks. The gateway routes requests
        by model — "auto" uses the default model.
      </p>

      {error && <div style={msgStyle('var(--color-error)', 'var(--color-error-subtle)')}>{error}</div>}
      {success && <div style={msgStyle('var(--color-success)', 'var(--color-success-subtle)')}>{success}</div>}

      <div style={{ marginBottom: 'var(--space-4)' }}>
        <button onClick={() => { clearMessages(); setShowForm(!showForm); }} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-accent)', color: 'white', border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
          Add Model
        </button>
      </div>

      {showForm && (
        <div style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)' }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>Add Model</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 'var(--space-3)' }}>
            <div>
              <label style={labelStyle}>Provider</label>
              <select value={formProvider} onChange={e => setFormProvider(e.target.value)} style={inputStyle}>
                {providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div>
              <label style={labelStyle}>Display name</label>
              <input type="text" value={formDisplay} onChange={e => setFormDisplay(e.target.value)} style={inputStyle} placeholder="GPT-4o" />
            </div>
            <div>
              <label style={labelStyle}>Model ID (upstream)</label>
              <input type="text" value={formModelId} onChange={e => setFormModelId(e.target.value)} style={inputStyle} placeholder="gpt-4o" />
            </div>
            <div>
              <label style={labelStyle}>Context window (optional)</label>
              <input type="number" value={formContext} onChange={e => setFormContext(e.target.value)} style={inputStyle} placeholder="128000" />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
            <button onClick={submitForm} disabled={loading || !formProvider || !formDisplay.trim() || !formModelId.trim()} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-accent)', color: 'white', border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)', opacity: (!formProvider || !formDisplay.trim() || !formModelId.trim()) ? 0.5 : 1 }}>
              Save
            </button>
            <button onClick={() => setShowForm(false)} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-bg-overlay)', color: 'var(--color-text-primary)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', overflow: 'hidden' }}>
        <div style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)', display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>All Models</span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>{models.length} total</span>
        </div>
        {models.length === 0 ? (
          <div style={{ padding: 'var(--space-8)', textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)' }}>
            No models yet. Add one manually or use "Discover Models" on a provider.
          </div>
        ) : (
          <div>
            {models.map(m => (
              <div key={m.id} style={{ padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border-subtle)', display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: m.enabled ? 'var(--color-success)' : 'var(--color-text-tertiary)' }} />
                <span style={{ fontSize: 'var(--text-sm)', fontFamily: 'var(--font-mono)', color: 'var(--color-text-primary)' }}>{m.model_id}</span>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
                  {providerName(m.provider_id)}{m.context_window ? ` · ${m.context_window.toLocaleString()} ctx` : ''}
                </span>
                {m.is_default && <span style={{ fontSize: 'var(--text-xs)', padding: '1px 6px', borderRadius: 'var(--radius-sm)', background: 'var(--color-accent-subtle)', color: 'var(--color-accent)', fontFamily: 'var(--font-mono)' }}>default</span>}
                {m.is_fallback && <span style={{ fontSize: 'var(--text-xs)', padding: '1px 6px', borderRadius: 'var(--radius-sm)', background: 'var(--color-warning-subtle)', color: 'var(--color-warning)', fontFamily: 'var(--font-mono)' }}>fallback</span>}
                <span style={{ fontSize: 'var(--text-xs)', color: m.enabled ? 'var(--color-success)' : 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', marginLeft: 'auto' }}>
                  {m.enabled ? 'enabled' : 'disabled'}
                </span>
                <button onClick={() => updateModel(m, { enabled: !m.enabled }, m.enabled ? 'Model disabled' : 'Model enabled')} style={miniBtn}>{m.enabled ? 'Disable' : 'Enable'}</button>
                {!m.is_default && <button onClick={() => updateModel(m, { is_default: true }, `${m.model_id} is now the default`)} style={miniBtn}>Set Default</button>}
                {!m.is_fallback && <button onClick={() => updateModel(m, { is_fallback: true }, `${m.model_id} is now the fallback`)} style={miniBtn}>Set Fallback</button>}
                <button onClick={() => deleteModel(m)} style={{ ...miniBtn, color: 'var(--color-error)', border: '1px solid var(--color-error)' }}>Delete</button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function msgStyle(color: string, bg: string): React.CSSProperties {
  return {
    color, marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)',
    padding: 'var(--space-2) var(--space-3)', background: bg, borderRadius: 'var(--radius-md)',
    border: `1px solid ${color}`,
  };
}

const labelStyle: React.CSSProperties = {
  display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)',
};

const inputStyle: React.CSSProperties = {
  width: '100%', padding: 'var(--space-2) var(--space-3)',
  background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
  fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', outline: 'none',
};

const miniBtn: React.CSSProperties = {
  padding: 'var(--space-1) var(--space-2)',
  background: 'var(--color-bg-overlay)', color: 'var(--color-text-secondary)',
  border: '1px solid var(--color-border)', borderRadius: 'var(--radius-sm)',
  cursor: 'pointer', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
};
