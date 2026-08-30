/**
 * Gateway — lifecycle management, health monitoring, and endpoint contract.
 *
 * Phase 2.2: Functional gateway page with endpoint display, logs, and copy UX.
 */

import { useEffect, useState, useCallback } from 'react';
import { api } from '../lib/api';

// ── Types ──────────────────────────────────────────────────────

type EndpointInfo = {
  host: string;
  port: number;
  protocol: string;
  base_path: string;
  url: string;
  base_url: string;
};

type ProcessInfo = {
  state: string;
  pid: number | null;
  uptime_seconds: number | null;
  restart_count: number;
  last_exit_code: number | null;
  last_error: string | null;
  start_time: string | null;
  stop_time: string | null;
  command: string | null;
  working_dir: string | null;
};

type GatewayStatus = {
  process: ProcessInfo;
  endpoint: EndpointInfo;
};

type GatewayHealth = {
  status: string;
  process_alive: boolean;
  port_reachable: boolean;
  http_responsive: boolean;
  checked_at: string;
};

type GatewayConfig = {
  host: string;
  port: number;
  protocol: string;
  base_path: string;
  endpoint_url: string;
  base_url: string;
  script: string;
  working_directory: string;
  startup_timeout: number;
  shutdown_timeout: number;
};

type LogLine = {
  stream: string;
  text: string;
  timestamp: string;
};

type GatewayLogs = {
  lines: LogLine[];
  total: number;
};

// ── Constants ──────────────────────────────────────────────────

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

const HEALTH_LABELS: Record<string, string> = {
  healthy: 'Healthy',
  starting: 'Starting',
  stopped: 'Stopped',
  failed: 'Failed',
  unknown: 'Unknown',
};

const HEALTH_COLORS: Record<string, string> = {
  healthy: 'var(--color-success)',
  starting: 'var(--color-warning)',
  stopped: 'var(--color-text-tertiary)',
  failed: 'var(--color-error)',
  unknown: 'var(--color-text-tertiary)',
};

// ── Helpers ────────────────────────────────────────────────────

function formatUptime(seconds: number | null): string {
  if (seconds == null) return '—';
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return `${h}h ${rm}m`;
}

// ── Component ──────────────────────────────────────────────────

export function GatewayPage() {
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [health, setHealth] = useState<GatewayHealth | null>(null);
  const [config, setConfig] = useState<GatewayConfig | null>(null);
  const [logs, setLogs] = useState<GatewayLogs | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [s, h, c, l] = await Promise.all([
        api.get<GatewayStatus>('/api/gateway/status'),
        api.get<GatewayHealth>('/api/gateway/health'),
        api.get<GatewayConfig>('/api/gateway/config'),
        api.get<GatewayLogs>('/api/gateway/logs?limit=50'),
      ]);
      setStatus(s);
      setHealth(h);
      setConfig(c);
      setLogs(l);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to fetch gateway data');
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleAction = async (action: 'start' | 'stop' | 'restart') => {
    setLoading(true);
    setError(null);
    try {
      await api.post(`/api/gateway/${action}`);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `Failed to ${action} gateway`);
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // Fallback for environments without clipboard API
      const textarea = document.createElement('textarea');
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(label);
      setTimeout(() => setCopied(null), 2000);
    }
  };

  const endpointUrl = config?.endpoint_url || status?.endpoint?.url || '—';
  const process = status?.process;
  const endpoint = status?.endpoint || config;

  return (
    <div className="page">
      <h1 className="page-title">Gateway</h1>
      <p className="page-description">
        Control the local gateway process. Monitor health and endpoint status.
      </p>

      {error && (
        <div style={{
          color: 'var(--color-error)',
          marginBottom: 'var(--space-4)',
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--text-sm)',
          padding: 'var(--space-2) var(--space-3)',
          background: 'var(--color-error-subtle)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--color-error)',
        }}>
          {error}
        </div>
      )}

      {/* ── Status + Health card ─────────────────────────────── */}
      <div style={{
        padding: 'var(--space-4)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-bg-surface)',
        marginBottom: 'var(--space-4)',
      }}>
        {/* State indicator row */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
          <span style={{
            width: 10, height: 10, borderRadius: '50%',
            background: process ? STATE_COLORS[process.state] : 'var(--color-text-tertiary)',
          }} />
          <span style={{ fontWeight: 600, color: 'var(--color-text-primary)' }}>
            {process ? STATE_LABELS[process.state] || process.state : 'Loading...'}
          </span>
          {process?.pid && (
            <span style={{ color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
              PID {process.pid}
            </span>
          )}
          {process?.uptime_seconds != null && (
            <span style={{ color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
              up {formatUptime(process.uptime_seconds)}
            </span>
          )}
          {process && process.restart_count > 0 && (
            <span style={{ color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)', fontSize: 'var(--text-xs)' }}>
              {process.restart_count} restart{process.restart_count !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* Health indicators */}
        {health && (
          <div style={{ display: 'flex', gap: 'var(--space-4)', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', fontFamily: 'var(--font-mono)', marginBottom: 'var(--space-2)' }}>
            <span>
              Health:{' '}
              <span style={{ color: HEALTH_COLORS[health.status] || 'var(--color-text-secondary)' }}>
                {HEALTH_LABELS[health.status] || health.status}
              </span>
            </span>
            <span>Process: {health.process_alive ? '✓ alive' : '✗ dead'}</span>
            <span>Port: {health.port_reachable ? '✓ reachable' : '✗ unreachable'}</span>
            <span>HTTP: {health.http_responsive ? '✓ responsive' : '✗ not responding'}</span>
          </div>
        )}

        {/* Last error */}
        {process?.last_error && (
          <div style={{
            marginTop: 'var(--space-2)',
            color: 'var(--color-error)',
            fontSize: 'var(--text-xs)',
            fontFamily: 'var(--font-mono)',
            padding: 'var(--space-1) var(--space-2)',
            background: 'var(--color-error-subtle)',
            borderRadius: 'var(--radius-sm)',
          }}>
            {process.last_error}
          </div>
        )}
      </div>

      {/* ── Endpoint card ────────────────────────────────────── */}
      <div style={{
        padding: 'var(--space-4)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-bg-surface)',
        marginBottom: 'var(--space-4)',
      }}>
        <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)', marginBottom: 'var(--space-3)' }}>
          Local Endpoint
        </div>

        {/* Main endpoint URL */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', marginBottom: 'var(--space-3)' }}>
          <code style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 'var(--text-base)',
            color: 'var(--color-accent)',
            background: 'var(--color-accent-subtle)',
            padding: 'var(--space-1) var(--space-3)',
            borderRadius: 'var(--radius-md)',
            flex: 1,
          }}>
            {endpointUrl}
          </code>
          <button
            onClick={() => handleCopy(endpointUrl, 'endpoint')}
            style={{
              padding: 'var(--space-1) var(--space-3)',
              background: copied === 'endpoint' ? 'var(--color-success-subtle)' : 'var(--color-bg-overlay)',
              color: copied === 'endpoint' ? 'var(--color-success)' : 'var(--color-text-secondary)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer',
              fontSize: 'var(--text-xs)',
              fontFamily: 'var(--font-mono)',
              whiteSpace: 'nowrap',
            }}
          >
            {copied === 'endpoint' ? '✓ Copied' : 'Copy'}
          </button>
        </div>

        {/* Endpoint details */}
        {endpoint && (
          <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 'var(--space-1) var(--space-4)', fontSize: 'var(--text-xs)', color: 'var(--color-text-secondary)', fontFamily: 'var(--font-mono)' }}>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Host:</span>
            <span>{endpoint.host}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Port:</span>
            <span>{endpoint.port}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Protocol:</span>
            <span>{endpoint.protocol}</span>
            <span style={{ color: 'var(--color-text-tertiary)' }}>Base path:</span>
            <span>{endpoint.base_path}</span>
          </div>
        )}
      </div>

      {/* ── Client configuration block ──────────────────────── */}
      <div style={{
        padding: 'var(--space-4)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-bg-surface)',
        marginBottom: 'var(--space-4)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 'var(--space-3)' }}>
          <div style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
            Client Configuration
          </div>
          <button
            onClick={() => handleCopy(
              `Base URL: ${endpointUrl}\nAPI Key: <your-api-key>`,
              'config'
            )}
            style={{
              padding: 'var(--space-1) var(--space-3)',
              background: copied === 'config' ? 'var(--color-success-subtle)' : 'var(--color-bg-overlay)',
              color: copied === 'config' ? 'var(--color-success)' : 'var(--color-text-secondary)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer',
              fontSize: 'var(--text-xs)',
              fontFamily: 'var(--font-mono)',
              whiteSpace: 'nowrap',
            }}
          >
            {copied === 'config' ? '✓ Copied' : 'Copy'}
          </button>
        </div>
        <pre style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 'var(--text-xs)',
          color: 'var(--color-text-secondary)',
          background: 'var(--color-bg-elevated)',
          padding: 'var(--space-3)',
          borderRadius: 'var(--radius-md)',
          border: '1px solid var(--color-border-subtle)',
          overflow: 'auto',
          lineHeight: 'var(--leading-relaxed)',
        }}>
{`# OpenAI-compatible client configuration
Base URL: ${endpointUrl}
API Key: <your-api-key>`}
        </pre>
        <div style={{ marginTop: 'var(--space-2)', fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)' }}>
          Use this configuration in any OpenAI-compatible client. The gateway handles authentication with the upstream provider.
        </div>
      </div>

      {/* ── Action buttons ──────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}>
        <button
          onClick={() => handleAction('start')}
          disabled={loading || process?.state === 'running'}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-accent)',
            color: 'white',
            border: 'none',
            borderRadius: 'var(--radius-md)',
            cursor: loading ? 'wait' : 'pointer',
            opacity: (loading || process?.state === 'running') ? 0.5 : 1,
            fontSize: 'var(--text-sm)',
            fontWeight: 500,
          }}
        >
          Start
        </button>
        <button
          onClick={() => handleAction('stop')}
          disabled={loading || process?.state === 'stopped'}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-bg-overlay)',
            color: 'var(--color-text-primary)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-md)',
            cursor: loading ? 'wait' : 'pointer',
            opacity: (loading || process?.state === 'stopped') ? 0.5 : 1,
            fontSize: 'var(--text-sm)',
            fontWeight: 500,
          }}
        >
          Stop
        </button>
        <button
          onClick={() => handleAction('restart')}
          disabled={loading || process?.state === 'stopped'}
          style={{
            padding: 'var(--space-2) var(--space-4)',
            background: 'var(--color-bg-overlay)',
            color: 'var(--color-text-primary)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius-md)',
            cursor: loading ? 'wait' : 'pointer',
            opacity: (loading || process?.state === 'stopped') ? 0.5 : 1,
            fontSize: 'var(--text-sm)',
            fontWeight: 500,
          }}
        >
          Restart
        </button>
      </div>

      {/* ── Gateway logs ────────────────────────────────────── */}
      <div style={{
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--color-bg-surface)',
        overflow: 'hidden',
      }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: 'var(--space-3) var(--space-4)',
          borderBottom: '1px solid var(--color-border)',
        }}>
          <span style={{ fontSize: 'var(--text-sm)', fontWeight: 600, color: 'var(--color-text-primary)' }}>
            Gateway Logs
          </span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--color-text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            {logs?.total ?? 0} lines
          </span>
        </div>
        <div style={{
          maxHeight: 300,
          overflow: 'auto',
          padding: 'var(--space-2)',
        }}>
          {logs && logs.lines.length > 0 ? (
            logs.lines.map((line, i) => (
              <div key={i} style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 'var(--text-xs)',
                lineHeight: 'var(--leading-relaxed)',
                color: line.stream === 'stderr' ? 'var(--color-warning)' : 'var(--color-text-secondary)',
                padding: '1px var(--space-2)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
              }}>
                <span style={{ color: 'var(--color-text-tertiary)', marginRight: 'var(--space-2)' }}>
                  {line.stream === 'stderr' ? 'E' : 'O'}
                </span>
                {line.text}
              </div>
            ))
          ) : (
            <div style={{
              padding: 'var(--space-4)',
              textAlign: 'center',
              color: 'var(--color-text-tertiary)',
              fontSize: 'var(--text-xs)',
              fontFamily: 'var(--font-mono)',
            }}>
              {process?.state === 'stopped' ? 'Gateway not running. Start it to see logs.' : 'No output captured yet.'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
