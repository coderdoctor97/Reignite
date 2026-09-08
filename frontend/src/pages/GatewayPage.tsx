/**
 * Gateway — the first-party data plane served in-process under /v1.
 *
 * Shows state, statistics, the stable endpoint contract, and lets the
 * user enable/disable the data plane and test it with a live request.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

type GatewayStatus = {
  state: string;
  enabled: boolean;
  started_at: string | null;
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_errors: number;
  error_rate: number;
  last_request_at: string | null;
  last_error: string | null;
  requests_by_model: Record<string, number>;
  endpoint_url: string;
  base_path: string;
};

type GatewayAction = {
  success: boolean;
  message: string;
  status: GatewayStatus;
};

type GatewayHealth = {
  healthy: boolean;
  state: string;
  base_path: string;
  detail: string | null;
};

type GatewayConfig = {
  enabled: boolean;
  base_path: string;
  endpoint_url: string;
  public_base_url: string;
  auth_token_configured: boolean;
  upstream_timeout: number;
  default_max_tokens: number;
};

type ModelsList = { data: { id: string; owned_by: string }[] };

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '—';
  try {
    return new Date(dateStr).toLocaleString();
  } catch {
    return dateStr;
  }
}

export function GatewayPage() {
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [health, setHealth] = useState<GatewayHealth | null>(null);
  const [config, setConfig] = useState<GatewayConfig | null>(null);
  const [models, setModels] = useState<ModelsList | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Test request state
  const [testPrompt, setTestPrompt] = useState('Say hello in one sentence.');
  const [testModel, setTestModel] = useState('auto');
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testLoading, setTestLoading] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [s, h, c, m] = await Promise.all([
        api.get<GatewayStatus>('/api/gateway/status'),
        api.get<GatewayHealth>('/api/gateway/health').catch(() => null),
        api.get<GatewayConfig>('/api/gateway/config').catch(() => null),
        api.get<ModelsList>('/v1/models').catch(() => null),
      ]);
      setStatus(s);
      setHealth(h);
      setConfig(c);
      setModels(m);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load gateway status');
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const runAction = async (action: 'start' | 'stop' | 'restart') => {
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const resp = await api.post<GatewayAction>(`/api/gateway/${action}`);
      setSuccess(resp.message);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `Failed to ${action} gateway`);
    } finally {
      setLoading(false);
    }
  };

  const copyEndpoint = async () => {
    if (!config) return;
    try {
      await navigator.clipboard.writeText(config.endpoint_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const runTestRequest = async () => {
    setTestLoading(true);
    setTestResult(null);
    setError(null);
    try {
      const resp = await fetch(`${window.location.protocol}//${window.location.host}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: testModel,
          messages: [{ role: 'user', content: testPrompt }],
        }),
      });
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 300)}`);
      }
      const data = await resp.json();
      setTestResult(data.choices?.[0]?.message?.content ?? JSON.stringify(data));
    } catch (err: unknown) {
      setTestResult(null);
      setError(err instanceof Error ? err.message : 'Test request failed');
    } finally {
      setTestLoading(false);
    }
  };

  return (
    <div className="page">
      <h1 className="page-title">Gateway</h1>
      <p className="page-description">
        The built-in gateway data plane serves a unified OpenAI / Anthropic / Responses
        endpoint under <code>{config?.base_path ?? '/v1'}</code>. Enable or disable it here.
      </p>

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

      {/* Status card */}
      <div style={{
        padding: 'var(--space-4)', marginBottom: 'var(--space-4)',
        border: `1px solid ${status?.enabled ? 'var(--color-success)' : 'var(--color-border)'}`,
        borderRadius: 'var(--radius-lg)',
        background: status?.enabled ? 'var(--color-success-subtle)' : 'var(--color-bg-surface)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
          <span style={{
            width: 10, height: 10, borderRadius: '50%',
            background: status?.enabled ? 'var(--color-success)' : 'var(--color-text-tertiary)',
          }} />
          <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
            Gateway {status?.enabled ? 'running' : 'stopped'}
          </span>
          {health && !health.healthy && health.detail && (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-error)', fontFamily: 'var(--font-mono)' }}>
              {health.detail}
            </span>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 'var(--space-1) var(--space-4)', fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)', marginBottom: 'var(--space-3)' }}>
          <span style={{ color: 'var(--color-text-tertiary)' }}>Endpoint:</span>
          <span style={{ color: 'var(--color-text-secondary)', display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            {config?.endpoint_url ?? '—'}
            <button
              onClick={copyEndpoint}
              style={{
                padding: '1px var(--space-2)', fontSize: 'var(--text-xs)',
                background: 'var(--color-bg-overlay)', color: 'var(--color-text-secondary)',
                border: '1px solid var(--color-border)', borderRadius: 'var(--radius-sm)',
                cursor: 'pointer', fontFamily: 'var(--font-mono)',
              }}
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </span>
          <span style={{ color: 'var(--color-text-tertiary)' }}>Started:</span>
          <span style={{ color: 'var(--color-text-secondary)' }}>{formatDate(status?.started_at ?? null)}</span>
          <span style={{ color: 'var(--color-text-tertiary)' }}>Requests:</span>
          <span style={{ color: 'var(--color-text-secondary)' }}>{status?.total_requests ?? 0}</span>
          <span style={{ color: 'var(--color-text-tertiary)' }}>Tokens in/out:</span>
          <span style={{ color: 'var(--color-text-secondary)' }}>
            {(status?.total_input_tokens ?? 0).toLocaleString()} / {(status?.total_output_tokens ?? 0).toLocaleString()}
          </span>
          <span style={{ color: 'var(--color-text-tertiary)' }}>Errors:</span>
          <span style={{ color: status && status.total_errors > 0 ? 'var(--color-error)' : 'var(--color-text-secondary)' }}>
            {status?.total_errors ?? 0} ({(status?.error_rate ?? 0).toFixed(2)}%)
          </span>
          {status?.last_error && (
            <>
              <span style={{ color: 'var(--color-text-tertiary)' }}>Last error:</span>
              <span style={{ color: 'var(--color-error)' }}>{status.last_error}</span>
            </>
          )}
        </div>

        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          {!status?.enabled && (
            <button
              onClick={() => runAction('start')}
              disabled={loading}
              style={{
                padding: 'var(--space-2) var(--space-4)',
                background: 'var(--color-success)', color: '#000', border: 'none',
                borderRadius: 'var(--radius-md)', cursor: loading ? 'wait' : 'pointer',
                fontSize: 'var(--text-sm)', fontWeight: 600,
              }}
            >
              Start Gateway
            </button>
          )}
          {status?.enabled && (
            <button
              onClick={() => runAction('stop')}
              disabled={loading}
              style={{
                padding: 'var(--space-2) var(--space-4)',
                background: 'var(--color-error)', color: 'white', border: 'none',
                borderRadius: 'var(--radius-md)', cursor: loading ? 'wait' : 'pointer',
                fontSize: 'var(--text-sm)', fontWeight: 600,
              }}
            >
              Stop Gateway
            </button>
          )}
          <button
            onClick={() => runAction('restart')}
            disabled={loading}
            style={{
              padding: 'var(--space-2) var(--space-4)',
              background: 'var(--color-bg-overlay)', color: 'var(--color-text-primary)',
              border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
              cursor: loading ? 'wait' : 'pointer', fontSize: 'var(--text-sm)',
            }}
          >
            Restart
          </button>
        </div>
      </div>

      {/* Models served */}
      {models && (
        <div style={{
          border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
          background: 'var(--color-bg-surface)', marginBottom: 'var(--space-4)', overflow: 'hidden',
        }}>
          <div style={{
            padding: 'var(--space-3) var(--space-4)', borderBottom: '1px solid var(--color-border)',
            fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)',
          }}>
            Models served by the gateway
          </div>
          {models.data.length === 0 ? (
            <div style={{ padding: 'var(--space-6)', textAlign: 'center', color: 'var(--color-text-tertiary)', fontSize: 'var(--text-sm)' }}>
              No enabled models. Configure models in the Models page.
            </div>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', padding: 'var(--space-3) var(--space-4)' }}>
              {models.data.map(m => (
                <span key={m.id} style={{
                  fontSize: 'var(--text-xs)', fontFamily: 'var(--font-mono)',
                  padding: '2px 8px', borderRadius: 'var(--radius-sm)',
                  background: 'var(--color-bg-overlay)', color: 'var(--color-text-secondary)',
                  border: '1px solid var(--color-border)',
                }}>
                  {m.id} <span style={{ color: 'var(--color-text-tertiary)' }}>({m.owned_by})</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Live test */}
      <div style={{
        border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)',
        background: 'var(--color-bg-surface)', padding: 'var(--space-4)',
      }}>
        <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
          Live test request
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <div>
            <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
              Model (use "auto" for the default)
            </label>
            <input
              type="text"
              value={testModel}
              onChange={(e) => setTestModel(e.target.value)}
              style={{
                width: '100%', padding: 'var(--space-2) var(--space-3)',
                background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
                border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
                fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', outline: 'none',
              }}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', marginBottom: 'var(--space-1)' }}>
              Prompt
            </label>
            <textarea
              value={testPrompt}
              onChange={(e) => setTestPrompt(e.target.value)}
              rows={2}
              style={{
                width: '100%', padding: 'var(--space-2) var(--space-3)',
                background: 'var(--color-bg-elevated)', color: 'var(--color-text-primary)',
                border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
                fontFamily: 'var(--font-mono)', fontSize: 'var(--text-sm)', outline: 'none',
                resize: 'vertical',
              }}
            />
          </div>
          <div>
            <button
              onClick={runTestRequest}
              disabled={testLoading || !status?.enabled}
              style={{
                padding: 'var(--space-2) var(--space-4)',
                background: 'var(--color-accent)', color: 'white', border: 'none',
                borderRadius: 'var(--radius-md)',
                cursor: testLoading || !status?.enabled ? 'wait' : 'pointer',
                opacity: testLoading || !status?.enabled ? 0.6 : 1,
                fontSize: 'var(--text-sm)',
              }}
            >
              {testLoading ? 'Sending…' : 'Send test request'}
            </button>
          </div>
          {testResult && (
            <div style={{
              padding: 'var(--space-3)', background: 'var(--color-bg-elevated)',
              border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)',
              fontSize: 'var(--text-sm)', color: 'var(--color-text-secondary)',
              fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}>
              {testResult}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
