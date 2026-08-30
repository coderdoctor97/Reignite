"""
Opus Gateway Proxy - v5.0.0 (Usage persistence + Streaming fix + Error logging)
Author: Shadow Hacker
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import urllib.request
import urllib.error
import ssl
import threading
import time
import os
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ===================== CONFIGURATION =====================
LISTEN_PORT = 5800
REAL_OPUS_HOST = "opus.abhibots.com"
REAL_OPUS_BASE = f"https://{REAL_OPUS_HOST}"
ACTIVE_KEY_FILE = "active_key.txt"
KEY_REFRESH_INTERVAL = 3
TOTAL_LIMIT = 1_500_000
USAGE_FILE = "token_usage.json"

# ===================== STATE =====================
_current_key = None
_key_lock = threading.Lock()
_total_input_tokens = 0
_total_output_tokens = 0
_total_tokens = 0
_usage_lock = threading.Lock()
# Track the key that the current counters belong to. Used to decide whether to
# load persisted counters or start fresh on first run.
_counters_belong_to_key = None


def save_usage():
    with _usage_lock:
        data = {
            "input": _total_input_tokens,
            "output": _total_output_tokens,
            "total": _total_tokens,
            "remaining": TOTAL_LIMIT - _total_tokens,
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    try:
        tmp_path = USAGE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        # Atomic swap. On Windows, another process may briefly have the real
        # file open (the launcher's reader). In that case os.replace raises
        # PermissionError - just skip this write cycle. The in-memory counters
        # are still correct; they'll be flushed on the next add_usage call.
        try:
            os.replace(tmp_path, USAGE_FILE)
        except PermissionError:
            # Brief collision with reader — harmless, the next write will land.
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    except Exception as e:
        print(f"[!] save_usage error: {e}")


def add_usage(inp, out):
    global _total_input_tokens, _total_output_tokens, _total_tokens
    with _usage_lock:
        _total_input_tokens += inp
        _total_output_tokens += out
        _total_tokens = _total_input_tokens + _total_output_tokens
    save_usage()
    total_used, remaining = get_usage()
    pct = (total_used / TOTAL_LIMIT) * 100 if TOTAL_LIMIT else 0
    print(f"[USAGE] Total: {total_used:,} / {TOTAL_LIMIT:,} ({pct:.1f}%) | Remaining: {remaining:,}")
    if total_used >= 1_000_000:
        print("[USAGE] WARNING: THRESHOLD REACHED - Time to rotate!")


def get_usage():
    with _usage_lock:
        return _total_tokens, TOTAL_LIMIT - _total_tokens


def _load_persisted_usage_for_key(key_prefix):
    """Return (inp, out, total) from disk if the on-disk record belongs to this key,
    otherwise return (0, 0, 0). Matches key by checking a 'key_id' field if present;
    falls back to "use the existing numbers" if the file lacks a key_id (legacy data)."""
    try:
        if not os.path.exists(USAGE_FILE):
            return 0, 0, 0
        with open(USAGE_FILE, "r") as f:
            data = json.load(f)
        stored_id = data.get("key_id")
        if stored_id is None:
            # Legacy file without key_id: trust the numbers (best-effort restore).
            return (data.get("input", 0) or 0,
                    data.get("output", 0) or 0,
                    data.get("total", 0) or 0)
        if stored_id == key_prefix:
            return (data.get("input", 0) or 0,
                    data.get("output", 0) or 0,
                    data.get("total", 0) or 0)
        return 0, 0, 0
    except Exception as e:
        print(f"[!] load_persisted_usage error: {e}")
        return 0, 0, 0


def load_key():
    """Read active_key.txt. On first boot, restore counters from disk instead
    of zeroing. Only zero when the key *string* actually changes mid-session."""
    global _current_key, _counters_belong_to_key
    global _total_input_tokens, _total_output_tokens, _total_tokens
    try:
        if not os.path.exists(ACTIVE_KEY_FILE):
            return None
        with open(ACTIVE_KEY_FILE, "r") as f:
            new_key = f.read().strip()
        if not new_key:
            return None

        key_prefix = new_key[:12]
        with _key_lock:
            first_boot = (_current_key is None)
            key_changed = (new_key != _current_key)

            if first_boot:
                # Restore counters from disk (bug 1 fix: no more reset on startup).
                _total_input_tokens, _total_output_tokens, _total_tokens = (
                    _load_persisted_usage_for_key(key_prefix)
                )
                _counters_belong_to_key = key_prefix
                _current_key = new_key
                print(f"[KEY] Boot: loaded key {key_prefix}... | counters restored: {_total_tokens:,} tokens")
            elif key_changed:
                # Real rotation mid-session: reset and overwrite on disk.
                print(f"[KEY] Key rotated: {_current_key[:12]}... -> {key_prefix}...")
                with _usage_lock:
                    _total_input_tokens = 0
                    _total_output_tokens = 0
                    _total_tokens = 0
                _counters_belong_to_key = key_prefix
                _current_key = new_key
                save_usage()
            return new_key
    except Exception as e:
        print(f"[!] load_key error: {e}")
    return None


def save_usage_with_key_id(key_id):
    """Persist usage along with the key it's tied to. Use this so the next
    process boot can recognize and restore the counters (bug 1 fix)."""
    with _usage_lock:
        data = {
            "input": _total_input_tokens,
            "output": _total_output_tokens,
            "total": _total_tokens,
            "remaining": TOTAL_LIMIT - _total_tokens,
            "key_id": key_id[:12],
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    try:
        tmp_path = USAGE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        try:
            os.replace(tmp_path, USAGE_FILE)
        except PermissionError:
            # Brief collision with reader - skip this write cycle.
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    except Exception as e:
        print(f"[!] save_usage_with_key_id error: {e}")


def get_key():
    with _key_lock:
        return _current_key


def key_reloader():
    while True:
        load_key()
        # Also periodically flush counters with key_id so the on-disk record
        # is always tagged (so the next boot can restore).
        k = get_key()
        if k:
            save_usage_with_key_id(k)
        time.sleep(KEY_REFRESH_INTERVAL)


# ===================== MULTI-THREADED SERVER =====================

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ===================== HTTP HANDLER =====================

class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "OpusGateway"
    sys_version = ""

    def handle_one_request(self):
        """Suppress ConnectionResetError noise from impatient clients."""
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def do_POST(self):
        self.proxy_request("POST")

    def do_GET(self):
        if self.path == "/":
            body = b"Opus Gateway API Proxy - nothing here (use /v1...)"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Server", "OpusGateway")
            self.end_headers()
            self.wfile.write(body)
            return
        self.proxy_request("GET")

    def do_PUT(self):
        self.proxy_request("PUT")

    def do_DELETE(self):
        self.proxy_request("DELETE")

    def do_OPTIONS(self):
        self.proxy_request("OPTIONS")

    def proxy_request(self, method):
        target_url = ""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b""

            target_url = f"{REAL_OPUS_BASE}{self.path}"

            real_key = get_key()
            if not real_key:
                self.send_error(503, "No API key available.")
                return

            forward_headers = {}
            for h_name, h_value in self.headers.items():
                low = h_name.lower()
                if low in (
                    "host", "content-length", "transfer-encoding",
                    "accept-encoding", "connection", "keep-alive",
                    "proxy-authenticate", "proxy-authorization", "te",
                    "trailers", "upgrade",
                ):
                    continue
                if low == "x-api-key":
                    forward_headers[h_name] = real_key
                elif low == "authorization" and h_value.lower().startswith("bearer "):
                    # Replace any Bearer token with the real active key.
                    # This is what OpenAI-compatible clients (including codebase-memory-mcp)
                    # send when OPENAI_API_KEY is set — without this, the upstream sees the
                    # placeholder string and rejects it with 403.
                    forward_headers[h_name] = f"Bearer {real_key}"
                else:
                    forward_headers[h_name] = h_value

            forward_headers["Host"] = REAL_OPUS_HOST
            forward_headers["Accept-Encoding"] = "identity"
            if "Content-Type" not in forward_headers:
                forward_headers["Content-Type"] = "application/json"
            # Always inject the real API key — clients like Odysseus may send
            # no auth header at all when the endpoint's api_key field is empty,
            # and the upstream (opus.abhibots.com) rejects unauthenticated requests.
            forward_headers["x-api-key"] = real_key

            req = urllib.request.Request(target_url, data=body, headers=forward_headers, method=method)

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            resp = urllib.request.urlopen(req, timeout=120, context=ctx)

            resp_ct = ""
            resp_te = ""
            for k, v in resp.getheaders():
                if k.lower() == "content-type":
                    resp_ct = v.lower()
                if k.lower() == "transfer-encoding":
                    resp_te = v.lower()
            is_streaming = ("text/event-stream" in resp_ct) or ("chunked" in resp_te)

            if is_streaming:
                self.proxy_streaming(resp, method)
            else:
                self.proxy_buffered(resp, method)

        except urllib.error.HTTPError as e:
            error_body = b""
            try:
                error_body = e.read()
            except Exception:
                pass
            try:
                self.send_response(e.code)
                self.end_headers()
                self.wfile.write(error_body)
            except Exception:
                pass
            print(f"[ERR] {method} {self.path} -> ERROR {e.code}")

        except Exception as e:
            print(f"[!] Proxy crash on {method} {self.path}: {e}")
            try:
                self.send_error(502, str(e))
            except Exception:
                pass

    def proxy_buffered(self, resp, method):
        response_body = resp.read()

        self.send_response(resp.status)
        self.send_header("Server", "OpusGateway")
        for k, v in resp.getheaders():
            if k.lower() in ("server", "transfer-encoding", "content-encoding", "connection"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response_body)

        try:
            resp_json = json.loads(response_body)
            usage = resp_json.get("usage", {})
            input_toks = usage.get("input_tokens", 0) or 0
            output_toks = usage.get("output_tokens", 0) or 0
            if input_toks or output_toks:
                add_usage(input_toks, output_toks)
                print(f"[->] {method} {self.path} -> {resp.status} | +{input_toks}in +{output_toks}out")
            else:
                # Bug 3 fix (path 1): explicitly log when usage is missing so
                # silent counting issues are visible in the panel log.
                print(f"[->] {method} {self.path} -> {resp.status} ({len(response_body)} bytes, NO USAGE)")
        except Exception as e:
            # Bug 3 fix (path 1): no longer silent - this path means the API
            # returned non-JSON, which usually means a dead key or rate limit.
            print(f"[->] {method} {self.path} -> {resp.status} ({len(response_body)} bytes) [non-JSON response: {e}]")

    def proxy_streaming(self, resp, method):
        self.send_response(resp.status)
        self.send_header("Server", "OpusGateway")
        for k, v in resp.getheaders():
            if k.lower() in ("server", "transfer-encoding", "content-encoding", "connection"):
                continue
            self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()

        buf = b""
        total_in = 0
        total_out = 0
        bytes_sent = 0
        try:
            while True:
                chunk = resp.read()
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                bytes_sent += len(chunk)
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text.startswith("data:"):
                        continue
                    payload = text[5:].strip()
                    if not payload:
                        continue
                    # Bug 3 fix (path 2): handle [DONE] too - some providers emit
                    # usage in the final chunk immediately before/after [DONE].
                    if payload == "[DONE]":
                        continue
                    try:
                        obj = json.loads(payload)
                        # Try top-level first, then nested under "message".
                        usage = (obj.get("usage")
                                 or obj.get("message", {}).get("usage")
                                 or {})
                        if usage:
                            total_in += usage.get("input_tokens", 0) or 0
                            total_out += usage.get("output_tokens", 0) or 0
                    except Exception:
                        # Single bad chunk shouldn't kill the whole stream.
                        pass
        except Exception as e:
            print(f"[!] Streaming error: {e}")
        finally:
            if total_in or total_out:
                add_usage(total_in, total_out)
                print(f"[->] {method} {self.path} -> {resp.status} [stream] | +{total_in}in +{total_out}out ({bytes_sent} bytes)")
            else:
                # Bug 3 fix (path 1): surface the "no usage in stream" case.
                print(f"[->] {method} {self.path} -> {resp.status} [stream] ({bytes_sent} bytes, NO USAGE IN STREAM)")

    def log_message(self, format, *args):
        pass


# ===================== BOOT =====================

def main():
    print("=" * 55)
    print("  OPUS GATEWAY PROXY (v5.0.0)")
    print(f"  Local:     http://localhost:{LISTEN_PORT}")
    print(f"  Remote:    {REAL_OPUS_BASE}")
    print(f"  Key file:  {ACTIVE_KEY_FILE}")
    print(f"  Limit:     {TOTAL_LIMIT:,} tokens")
    print("=" * 55)

    load_key()
    k = get_key()
    print(f"[OK] Initial key: {k[:12] + '...' if k else 'None'}")

    threading.Thread(target=key_reloader, daemon=True).start()

    server = ThreadedHTTPServer(("127.0.0.1", LISTEN_PORT), GatewayHandler)
    print(f"[OK] Gateway running. API Host: http://localhost:{LISTEN_PORT}/v1")
    print("[OK] Connection noise suppressed. 403s mean the key is dead - rotate it.")
    print("[OK] Usage persists across restarts (key_id tagged).\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[OK] Gateway stopped.")
        save_usage()
        server.server_close()


if __name__ == "__main__":
    main()
