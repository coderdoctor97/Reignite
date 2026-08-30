/**
 * Gateway Control Center — Realtime Client (Placeholder)
 *
 * This module provides the abstraction for future WebSocket/SSE support.
 * Phase 1.1 only establishes the interface — no actual connection yet.
 *
 * Future phases will implement:
 * - WebSocket connection to /api/ws
 * - Automatic reconnection
 * - Event subscription by type
 * - Typed event handlers
 */

export type RealtimeEvent = {
  type: string;
  payload: unknown;
  timestamp: string;
};

type EventHandler = (event: RealtimeEvent) => void;

/**
 * Realtime client interface.
 *
 * Phase 1.1: stub implementation that logs to console.
 * Phase 6.1: will be replaced with actual WebSocket implementation.
 */
class RealtimeClient {
  private handlers = new Map<string, Set<EventHandler>>();
  private _connected = false;

  get connected(): boolean {
    return this._connected;
  }

  /**
   * Subscribe to events of a given type.
   * Returns an unsubscribe function.
   */
  on(type: string, handler: EventHandler): () => void {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)!.add(handler);

    return () => {
      this.handlers.get(type)?.delete(handler);
    };
  }

  /**
   * Connect to the realtime endpoint.
   * Phase 1.1: no-op stub.
   */
  connect(): void {
    // Placeholder — will be implemented in Phase 6.1
    this._connected = false;
  }

  /**
   * Disconnect from the realtime endpoint.
   * Phase 1.1: no-op stub.
   */
  disconnect(): void {
    this._connected = false;
  }

  /**
   * Emit an event to all registered handlers.
   * Used internally by the WebSocket message handler.
   */
  emit(event: RealtimeEvent): void {
    const handlers = this.handlers.get(event.type);
    if (handlers) {
      for (const handler of handlers) {
        try {
          handler(event);
        } catch (err) {
          console.error(`[realtime] handler error for ${event.type}:`, err);
        }
      }
    }
  }
}

/** Singleton realtime client — import and use throughout the frontend. */
export const realtime = new RealtimeClient();
