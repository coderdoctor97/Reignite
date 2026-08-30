/**
 * Gateway Control Center — API Client
 *
 * Centralized HTTP client for all backend communication.
 * Future phases call api.get/post/put/delete without spreading raw fetch().
 *
 * The base URL is configured via environment variable or defaults to
 * the backend's default port.
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8400';

type RequestOptions = {
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
};

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  private async request<T>(
    method: string,
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    const init: RequestInit = {
      method,
      headers,
      signal: options.signal,
    };

    if (options.body !== undefined) {
      init.body = JSON.stringify(options.body);
    }

    const response = await fetch(url, init);

    if (!response.ok) {
      const text = await response.text().catch(() => '');
      throw new ApiError(response.status, text || response.statusText, path);
    }

    // Handle 204 No Content
    if (response.status === 204) {
      return undefined as T;
    }

    return response.json() as Promise<T>;
  }

  get<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>('GET', path, options);
  }

  post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>('POST', path, { ...options, body });
  }

  put<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.request<T>('PUT', path, { ...options, body });
  }

  delete<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.request<T>('DELETE', path, options);
  }
}

export class ApiError extends Error {
  status: number;
  path: string;

  constructor(status: number, message: string, path: string) {
    super(`API ${status}: ${message}`);
    this.name = 'ApiError';
    this.status = status;
    this.path = path;
  }
}

/** Singleton API client — import and use throughout the frontend. */
export const api = new ApiClient(BASE_URL);
