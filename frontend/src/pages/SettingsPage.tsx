/**
 * Settings — runtime application settings.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

type SettingsData = {
  gateway_public_base_url: string;
  gateway_auth_token: string;
  credential_validation_interval: string;
  session_validation_interval: string;
  usage_limit: string;
  usage_warning_threshold: string;
  readonly: Record<string, unknown>;
};

export function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const s = await api.get<SettingsData>('/api/settings');
      setSettings(s);
      setForm({
        gateway_public_base_url: s.gateway_public_base_url,
        gateway_auth_token: s.gateway_auth_token,
        credential_validation_interval: s.credential_validation_interval,
        session_validation_interval: s.session_validation_interval,
        usage_limit: s.usage_limit,
        usage_warning_threshold: s.usage_warning_threshold,
      });
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load settings');
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const save = async () => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      await api.put('/api/settings', {
        settings: {
          gateway_public_base_url: form.gateway_public_base_url,
          gateway_auth_token: form.gateway_auth_token,
          credential_validation_interval: form.credential_validation_interval,
          session_validation_interval: form.session_validation_interval,
          usage_limit: form.usage_limit,
          usage_warning_threshold: form.usage_warning_threshold,
        },
      });
      setSuccess('Settings saved');
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setLoading(false);
    }
  };

  const set = (key: string, value: string) => setForm(f => ({ ...f, [key]: value }));

  return (
    <div className="page">
      <h1 className="page-title">Settings</h1>
      <p className="page-description">
        Runtime settings for the gateway endpoint, validation intervals, and usage thresholds.
      </p>

      {error && <div style={{ color: 'var(--color-error)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)' }}>{error}</div>}
      {success && <div style={{ color: 'var(--color-success)', marginBottom: 'var(--space-4)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)' }}>{success}</div>}

      <div style={{ padding: 'var(--space-4)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', marginBottom: 'var(--space-4)' }}>
        <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
          Gateway &amp; Apply-Config
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 'var(--space-3)' }}>
          <Field label="Public gateway base URL (written into agent configs)" value={form.gateway_public_base_url ?? ''} onChange={v => set('gateway_public_base_url', v)} />
          <Field label="Gateway auth token (sent to agents)" value={form.gateway_auth_token ?? ''} onChange={v => set('gateway_auth_token', v)} />
        </div>
      </div>

      <div style={{ padding: 'var(--space-4)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', marginBottom: 'var(--space-4)' }}>
        <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
          Validation intervals (seconds)
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 'var(--space-3)' }}>
          <Field label="Credential validation interval" value={form.credential_validation_interval ?? ''} onChange={v => set('credential_validation_interval', v)} />
          <Field label="Session validation interval" value={form.session_validation_interval ?? ''} onChange={v => set('session_validation_interval', v)} />
        </div>
      </div>

      <div style={{ padding: 'var(--space-4)', border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', background: 'var(--color-bg-surface)', marginBottom: 'var(--space-4)' }}>
        <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
          Usage thresholds
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 'var(--space-3)' }}>
          <Field label="Total token limit" value={form.usage_limit ?? ''} onChange={v => set('usage_limit', v)} />
          <Field label="Warning threshold" value={form.usage_warning_threshold ?? ''} onChange={v => set('usage_warning_threshold', v)} />
        </div>
      </div>

      <button onClick={save} disabled={loading} style={{ padding: 'var(--space-2) var(--space-4)', background: 'var(--color-accent)', color: 'white', border: 'none', borderRadius: 'var(--radius-md)', cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
        Save Settings
      </button>

      {settings?.readonly && (
        <div style={{ marginTop: 'var(--space-4)', padding: 'var(--space-3) var(--space-4)', border: '1px dashed var(--color-border)', borderRadius: 'var(--radius-lg)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', color: 'var(--color-text-tertiary)' }}>
          <div style={{ fontWeight: 600, marginBottom: 'var(--space-2)', color: 'var(--color-text-secondary)' }}>Read-only information</div>
          {Object.entries(settings.readonly).map(([k, v]) => (
            <div key={k}>{k}: {String(v)}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
        {label}
      </label>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          width: '100%', padding: 'var(--space-2) var(--space-3)',
          background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
          border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
          fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', outline: 'none',
        }}
      />
    </div>
  );
}
