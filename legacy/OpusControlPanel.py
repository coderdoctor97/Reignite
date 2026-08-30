"""
Opus Control Panel v5.0.0 – CustomTkinter Dark UI + Glass-Morphism Aesthetic

Features:
  • Manages OpusGateway + KeyBinder subprocesses with auto-restart on crash
  • Live token-usage tracking + manual/auto key rotation
  • Token-limit warning popups at configurable thresholds
  • System tray integration (Show / Rotate Now / Quit)
  • Optional Windows auto-start via PowerShell .lnk
  • Modern dark UI (Linear/Raycast-inspired), card-based settings
  • Live appearance theme switching (Dark / Light / System)
  • Subprocess stdout/stderr capture and forwarding into the log panel
  • Network Monitor: page latency, model probes, quality score
  • Quick-link buttons: open Dashboard / Status page in browser
  • Fix Latency: measures improvement before/after

Tested with Python 3.14.6 on Windows 10/11.
See requirements.txt for runtime dependencies.
"""

import subprocess
import threading
import time
import json
import os
import sys
import tkinter as tk  # kept for legacy Tk internals (messagebox, filedialog, ttk)
from tkinter import messagebox, ttk, filedialog
import customtkinter as ctk
from collections import deque
from datetime import datetime  # used in log()
import math
import network_monitor
import key_poller

# ===================== VERSION =====================
__version__ = "5.0.0"

# ===================== PATHS & CONFIG FILE =====================
APP_NAME = "OpusControlPanel"
if sys.platform == "win32":
    CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), f".{APP_NAME.lower()}")
os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "base_dir": r"C:\Users\User\Hunter",
    "total_limit": 1_500_000,
    "refresh_ms": 2000,
    "startup_delay": 2,
    "auto_restart": True,
    "launch_on_startup": False,
    "confirm_stop": True,
    # Phase 3 additions
    "token_warning_pcts": [80, 95],   # show popup at each threshold (once per session)
    "auto_rotate_pct": 90,            # auto-trigger rotation at this usage % (0 = disabled)
    "capture_subprocess_logs": True,  # pipe Gateway/KeyBinder stdout into GUI log
    "appearance_mode": "Dark",        # Dark / Light / System
    "use_low_latency_gateway": True,  # True = Gateway_LowLatency.py, False = OpusGateway.py
    "auto_key_poll_enabled": False,   # Phase 5: auto-run pull_latest_key.py every 5s
}

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    # Atomic write: write to .tmp then os.replace — prevents corruption if process
    # dies mid-write (which previously could silently wipe the user's config).
    tmp = CONFIG_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except Exception as e:
        print(f"Could not save config: {e}")
        # Clean up the .tmp if replace failed
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

config = load_config()

# ===================== DERIVED PATHS =====================
BASE_DIR = config["base_dir"]
# Gateway script honors the low-latency toggle at startup (not just after settings save).
_gw_script_name = ("Gateway_LowLatency.py"
                   if config.get("use_low_latency_gateway", True)
                   else "OpusGateway.py")
GATEWAY_SCRIPT = os.path.join(BASE_DIR, _gw_script_name)
KEYBINDER_SCRIPT = os.path.join(BASE_DIR, "KeyBinder.py")
ROTATE_SCRIPT = os.path.join(BASE_DIR, "rotate_now.py")
ACTIVE_KEY_FILE = os.path.join(BASE_DIR, "active_key.txt")
USAGE_FILE = os.path.join(BASE_DIR, "token_usage.json")
ROTATION_FLAG_FILE = os.path.join(BASE_DIR, "rotation_needed.flag")

# ===================== CONFIG CONSTANTS =====================
MAX_LOG_LINES = 500

# ===================== GLOBAL STATE =====================
# Bug B-4 / C-1 / C-7 fix: replace bare bools with thread-safe Events.
running_event = threading.Event()
running_event.set()  # app starts in running state
rotating_event = threading.Event()
rotating_event.clear()

gateway_proc = None
keybinder_proc = None
tray_icon = None
window_hidden = False

# Key Poller: UI-bound polling flag and singleton instance.
# Poller construction is deferred to after log() is defined (see POLLER_INIT below).
# We stash the helper via a small module-level shim so that callers can use it
# before the poller is constructed — it will simply be a no-op until then.
def _poller_log(msg, level="INFO"):
    """Deferred logger binding — uses log() if defined, else print."""
    try:
        log(msg, level)
    except NameError:
        print(f"[{level}] {msg}")

key_poller_instance = key_poller.KeyPoller(
    base_dir=config["base_dir"],
    log=_poller_log,
)
key_poller_polling_var = None  # Tkinter BooleanVar, set in UI section

# Crash-loop guard for auto-restart
_restart_counts = {"gateway": 0, "keybinder": 0}
_last_restart_ts = {"gateway": 0.0, "keybinder": 0.0}
MAX_RESTARTS_PER_MIN = 3

# Log buffer (deque for automatic size cap)
log_buffer = deque(maxlen=MAX_LOG_LINES)

# Session-level tracking (not persisted — resets each app launch)
_warned_thresholds: set = set()          # token % thresholds already warned this session
_auto_rotated_this_session: bool = False  # prevents repeated auto-rotation

# Tk root reference (set after creation). All UI-thread `after` calls must check this.
_root_ref = None


# ===================== LOGGING =====================

def log(message, level="INFO"):
    """Add a message to the log buffer and UI. Safe to call from any thread."""
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {level}: {message}"
    log_buffer.append(line)
    print(line)

    # Pick activity tag from content; falls back to "" (no extra tag).
    activity_tag = _activity_tag_for(message)

    def _update():
        # If root is gone or being torn down, skip silently.
        if _root_ref is None:
            return
        try:
            if not _root_ref.winfo_exists():
                return
        except Exception:
            return
        try:
            log_text.config(state="normal")
            # Apply "meta" tag to the timestamp/level prefix for visual hierarchy.
            # The full line always gets the level tag (gray/red/amber/etc.)
            # so severity stays visible. Activity tags only add context for
            # INFO lines — never override ERROR/WARN/OK colors.
            full_tags = ("meta", level.lower())
            if level == "INFO" and activity_tag:
                full_tags = ("meta", activity_tag)
            log_text.insert("end", line + "\n", full_tags)
            log_text.see("end")
            log_text.config(state="disabled")
        except Exception as e:
            # Re-raise so the outer log() handler can report the UI error
            # instead of silently swallowing it (was: bare pass).
            raise RuntimeError(f"log UI update failed: {e}") from e

    if _root_ref is not None:
        try:
            _root_ref.after(0, _update)
        except Exception as e:
            print(f"[log] after() failed: {e}")


def log_tags_setup():
    """Configure text widget tags for colored log lines (dark theme)."""
    # Apply generous line spacing for readability — the default Tk line height
    # is cramped at 10pt; spacing1=top, spacing3=bottom (in pixels).
    log_text.tag_config("info",     foreground="#9CA3AF", spacing1=2, spacing3=2)  # gray
    log_text.tag_config("ok",       foreground="#10B981", spacing1=2, spacing3=2)  # green
    log_text.tag_config("warn",     foreground="#FBBF24", spacing1=2, spacing3=2)  # amber
    log_text.tag_config("error",    foreground="#F87171", spacing1=2, spacing3=2,
                        font=("Cascadia Mono", 10, "bold"))                         # red
    log_text.tag_config("rotate",   foreground="#60A5FA", spacing1=2, spacing3=2)  # blue
    log_text.tag_config("key_new",  foreground="#EC4899", spacing1=2, spacing3=2)  # pink
    log_text.tag_config("key_del",  foreground="#F59E0B", spacing1=2, spacing3=2)  # amber
    log_text.tag_config("gateway",  foreground="#06B6D4", spacing1=2, spacing3=2)  # cyan
    log_text.tag_config("keybinder",foreground="#8B5CF6", spacing1=2, spacing3=2)  # violet

    # Highlight the timestamp + level word in dim color so the message stands out
    log_text.tag_config("meta", foreground="#6B7280", spacing1=2, spacing3=2)


# ===================== HELPERS =====================

def set_status(text, color="gray"):
    """Thread-safe status bar update."""
    # CTkLabel uses text_color, not fg.
    _color_map = {
        "gray": TEXT_DIM,
        "green": "#10B981",
        "blue": ACCENT,
        "red": "#F87171",
        "orange": "#FBBF24",
    }
    ctk_color = _color_map.get(color, color)

    def _update():
        if _root_ref is None:
            return
        try:
            if not _root_ref.winfo_exists():
                return
        except Exception:
            return
        try:
            status_label.configure(text=text, text_color=ctk_color)
        except Exception as e:
            print(f"[set_status] widget update failed: {e}")

    if _root_ref is not None:
        try:
            _root_ref.after(0, _update)
        except Exception as e:
            print(f"[set_status] after() failed: {e}")


def relaunch_dependent_scripts():
    """Re-derive script paths after BASE_DIR change."""
    global GATEWAY_SCRIPT, KEYBINDER_SCRIPT, ROTATE_SCRIPT
    global ACTIVE_KEY_FILE, USAGE_FILE, ROTATION_FLAG_FILE, BASE_DIR
    BASE_DIR = config["base_dir"]
    # Gateway script is selected by config: low-latency variant or original.
    _gw_script_name = ("Gateway_LowLatency.py"
                       if config.get("use_low_latency_gateway", True)
                       else "OpusGateway.py")
    GATEWAY_SCRIPT = os.path.join(BASE_DIR, _gw_script_name)
    KEYBINDER_SCRIPT = os.path.join(BASE_DIR, "KeyBinder.py")
    ROTATE_SCRIPT = os.path.join(BASE_DIR, "rotate_now.py")
    ACTIVE_KEY_FILE = os.path.join(BASE_DIR, "active_key.txt")
    USAGE_FILE = os.path.join(BASE_DIR, "token_usage.json")
    ROTATION_FLAG_FILE = os.path.join(BASE_DIR, "rotation_needed.flag")


# ===================== LAUNCHER =====================

def _launch_python(script_path, name="proc"):
    """Launch a python script silently and return Popen.

    If capture_subprocess_logs is enabled, stdout/stderr are piped and a daemon
    thread drains them into the GUI log with a [name] prefix. This prevents
    the subprocess from blocking on a full pipe buffer if it logs a lot.
    """
    kwargs = {"cwd": BASE_DIR}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    # Force UTF-8 on subprocess stdout/stderr so Unicode characters (e.g. ✓)
    # don't crash with UnicodeEncodeError on Windows cp1252 consoles.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    kwargs["env"] = env

    if config.get("capture_subprocess_logs", True):
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.STDOUT  # merge stderr into stdout
        kwargs["text"] = True
        kwargs["bufsize"] = 1  # line-buffered
        proc = subprocess.Popen(["python", script_path], **kwargs)
        # Drain in background thread
        threading.Thread(
            target=_drain_subprocess_output,
            args=(proc, name),
            daemon=True,
        ).start()
        return proc
    else:
        return subprocess.Popen(["python", script_path], **kwargs)


def _drain_subprocess_output(proc, name):
    """Read lines from a subprocess's stdout until EOF, log each to GUI.
    Detects level from line content: '[!]' / '[FATAL]' / '[ERR]' / errors -> WARN/ERROR,
    '[+]' / '[OK]' -> OK, etc. Subprocess-specific keywords (key, deleted, etc.)
    still trigger activity tags via _activity_tag_for.
    """
    try:
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip()
            if not line:
                continue
            # Infer level from the bracketed prefix the scripts already use.
            level = "INFO"
            upper = line.upper()
            if "[!]" in line or "[ERR]" in upper or "[FATAL]" in upper:
                level = "ERROR"
            elif "[-]" in line:
                level = "WARN"
            elif "[+]" in line or "[OK]" in upper:
                level = "OK"
            log(f"[{name}] {line}", level)
        proc.stdout.close()
    except Exception as e:
        log(f"[{name}] log drain error: {e}", "WARN")


def start_services():
    """Start Gateway and KeyBinder as background subprocesses."""
    global gateway_proc, keybinder_proc

    if not running_event.is_set():
        return

    # If a gateway is already alive from a prior session, kill it first so we
    # don't double-launch (fixes the race in fix_latency and on repeated calls).
    if is_process_alive(gateway_proc):
        log("Killing stale Gateway process before restart…", "WARN")
        try:
            gateway_proc.terminate()
            gateway_proc.wait(timeout=5)
        except Exception:
            try:
                gateway_proc.kill()
            except Exception:
                pass
        gateway_proc = None

    log("Starting Gateway…", "INFO")
    set_status("Starting Gateway…", "gray")
    try:
        gateway_proc = _launch_python(GATEWAY_SCRIPT, name="gateway")
        log(f"Gateway started (PID {gateway_proc.pid})", "OK")
        set_status("✓ Gateway started", "green")
    except Exception as e:
        log(f"Gateway failed: {e}", "ERROR")
        set_status(f"Gateway failed: {e}", "red")
        return

    time.sleep(config["startup_delay"])

    if not running_event.is_set():
        return

    log("Starting KeyBinder…", "INFO")
    set_status("Starting KeyBinder…", "gray")
    try:
        keybinder_proc = _launch_python(KEYBINDER_SCRIPT, name="keybinder")
        log(f"KeyBinder started (PID {keybinder_proc.pid})", "OK")
        set_status("✓ All services running", "green")
    except Exception as e:
        log(f"KeyBinder failed: {e}", "ERROR")
        set_status(f"KeyBinder failed: {e}", "red")
        if gateway_proc and gateway_proc.poll() is None:
            gateway_proc.terminate()
            log("Gateway terminated due to KeyBinder failure", "WARN")


def _restart_one(name, proc_ref_name):
    """Restart a dead service, with crash-loop protection."""
    global gateway_proc, keybinder_proc
    now = time.time()
    if proc_ref_name == "gateway":
        # Reset window if last restart was > 60s ago
        if now - _last_restart_ts["gateway"] > 60:
            _restart_counts["gateway"] = 0
        _last_restart_ts["gateway"] = now
        _restart_counts["gateway"] += 1
        if _restart_counts["gateway"] > MAX_RESTARTS_PER_MIN:
            log("Gateway crash-looped; auto-restart paused. Use 'Restart' button.", "ERROR")
            set_status("⚠ Gateway crash-loop — paused", "red")
            return
    else:
        if now - _last_restart_ts["keybinder"] > 60:
            _restart_counts["keybinder"] = 0
        _last_restart_ts["keybinder"] = now
        _restart_counts["keybinder"] += 1
        if _restart_counts["keybinder"] > MAX_RESTARTS_PER_MIN:
            log("KeyBinder crash-looped; auto-restart paused.", "ERROR")
            return

    try:
        if proc_ref_name == "gateway":
            gateway_proc = _launch_python(GATEWAY_SCRIPT, name="gateway")
            log(f"Gateway auto-restarted (PID {gateway_proc.pid})", "OK")
        else:
            keybinder_proc = _launch_python(KEYBINDER_SCRIPT, name="keybinder")
            log(f"KeyBinder auto-restarted (PID {keybinder_proc.pid})", "OK")
        set_status(f"✓ {name} auto-restarted", "green")
    except Exception as e:
        log(f"{name} auto-restart failed: {e}", "ERROR")


def stop_services(tray_too=True, exit_app=False):
    """Terminate both subprocesses. Optionally close tray + window."""
    running_event.clear()

    for proc, name, ref in (
        (gateway_proc, "Gateway", "gateway"),
        (keybinder_proc, "KeyBinder", "keybinder"),
    ):
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                    log(f"{name} stopped", "OK")
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                    log(f"{name} force-killed", "WARN")
            except Exception as e:
                log(f"Error stopping {name}: {e}", "ERROR")

    # Phase 5: stop the background key poller worker
    try:
        key_poller_instance.stop(join_timeout=2.0)
        log("Key poller stopped", "INFO")
    except Exception as e:
        log(f"Key poller stop error: {e}", "WARN")

    if tray_too:
        stop_tray()
    if exit_app:
        try:
            _root_ref.after(0, _root_ref.destroy)
        except Exception:
            pass


def restart_services():
    """User-initiated restart (no crash-loop guard)."""
    log("Manual restart requested…", "INFO")
    set_status("Restarting services…", "blue")
    for proc, name in ((gateway_proc, "Gateway"), (keybinder_proc, "KeyBinder")):
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            except Exception:
                pass
    # Re-run start sequence after a brief pause
    def _start_later():
        start_services()
    threading.Thread(target=lambda: (time.sleep(2), _start_later()), daemon=True).start()


# ===================== ROTATION =====================

def rotate_key():
    """Run rotate_now.py in a non-blocking thread."""
    if not rotating_event.is_set():
        rotating_event.set()
    else:
        log("Rotation already in progress, ignoring click", "WARN")
        return

    # Thread-safety: ask main thread to update button state
    def _disable_btn():
        try:
            rotate_btn.configure(state="disabled", text="🔄  Rotating…")
        except Exception:
            pass
    if _root_ref is not None:
        _root_ref.after(0, _disable_btn)

    set_status("Rotating key…", "blue")
    log("Starting rotation…", "ROTATE")

    def _rotate():
        rotation_ok = False
        try:
            result = subprocess.run(
                ["python", ROTATE_SCRIPT],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
            )
            if result.returncode == 0:
                rotation_ok = True
                log("Rotation complete — restarting services to pick up new key…", "OK")
                set_status("Restarting services…", "blue")
                if result.stdout.strip():
                    log(result.stdout.strip(), "INFO")
                # Restart services so the new key is loaded into memory immediately
                # (otherwise the old key stays cached for up to 3s during which
                # requests still fail with the dead key).
                threading.Thread(target=_restart_services_after_rotation, daemon=True).start()
            else:
                err = (result.stderr or result.stdout or "Unknown error").strip()
                log(f"Rotation failed: {err}", "ERROR")
                set_status("✗ Rotation failed", "red")
        except subprocess.TimeoutExpired:
            log("Rotation timed out", "ERROR")
            set_status("✗ Rotation timed out", "red")
        except Exception as e:
            log(f"Rotation error: {e}", "ERROR")
            set_status(f"✗ Rotation error: {e}", "red")
        finally:
            # Always re-enable the button. The restart handler clears the flag.
            rotating_event.clear()
            if not rotation_ok:
                # Only clear the flag here if rotation didn't succeed
                # (the restart handler clears it on success).
                try:
                    if os.path.exists(ROTATION_FLAG_FILE):
                        os.remove(ROTATION_FLAG_FILE)
                except Exception:
                    pass

            def _re_enable():
                try:
                    rotate_btn.configure(state="normal", text="🔄  Rotate Now")
                except Exception:
                    pass
            if _root_ref is not None:
                try:
                    _root_ref.after(0, _re_enable)
                except Exception:
                    pass

    threading.Thread(target=_rotate, daemon=True).start()


def refresh_token_usage():
    """Refresh token usage by reading token_usage.json — triggered by button."""
    log("Refresh requested — re-reading token_usage.json", "INFO")
    update_display()


# ===================== TOKEN WARNING + AUTO-ROTATE (Phase 3) =====================

def _check_token_thresholds(pct: float):
    """Fire a warning popup once per threshold per session (e.g. 80%, 95%).
    Uses a global set so each threshold fires at most once."""
    global _warned_thresholds
    try:
        thresholds = config.get("token_warning_pcts", [80, 95])
        for thresh in thresholds:
            if pct >= thresh and thresh not in _warned_thresholds:
                _warned_thresholds.add(thresh)
                _root_ref.after(0, lambda pct=pct, t=thresh:
                    messagebox.showwarning(
                        "Token usage warning",
                        f"You have used {pct:.0f}% of your token budget "
                        f"(threshold: {t}%).\n\nConsider rotating your key.",
                        parent=_root_ref,
                    ))
    except Exception as e:
        log(f"Warning check error: {e}", "ERROR")


def _check_auto_rotate(pct: float):
    """Auto-trigger key rotation once when usage exceeds auto_rotate_pct.
    Set to 0 to disable. Fires only once per session."""
    global _auto_rotated_this_session
    try:
        trigger_pct = config.get("auto_rotate_pct", 90)
        if trigger_pct <= 0:
            return
        if pct >= trigger_pct and not _auto_rotated_this_session:
            _auto_rotated_this_session = True
            log(f"Auto-rotation triggered at {pct:.1f}% (threshold: {trigger_pct}%)", "ROTATE")
            set_status("⚠ Auto-rotating key…", "orange")
            threading.Thread(target=_auto_rotate_worker, daemon=True).start()
    except Exception as e:
        log(f"Auto-rotate check error: {e}", "ERROR")


def _auto_rotate_worker():
    """Background worker for auto-rotation — reuses rotate_key logic
    and restarts services so the new key is picked up immediately."""
    try:
        result = subprocess.run(
            ["python", ROTATE_SCRIPT],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
        )
        if result.returncode == 0:
            log("Auto-rotation complete — restarting services to pick up new key…", "OK")
            set_status("Restarting services…", "blue")
            if result.stdout.strip():
                log(result.stdout.strip(), "INFO")
            # Brief pause to let the file system flush the new key
            time.sleep(1.5)
            # Restart Gateway + KeyBinder so the new active_key.txt is loaded
            threading.Thread(target=_restart_services_after_rotation, daemon=True).start()
        else:
            err = (result.stderr or result.stdout or "Unknown error").strip()
            log(f"Auto-rotation failed: {err}", "ERROR")
            set_status("✗ Auto-rotation failed", "red")
    except subprocess.TimeoutExpired:
        log("Auto-rotation timed out", "ERROR")
        set_status("✗ Auto-rotation timed out", "red")
    except Exception as e:
        log(f"Auto-rotation error: {e}", "ERROR")
    finally:
        rotating_event.clear()


def _restart_services_after_rotation():
    """Stop old services and start fresh so the new key is active immediately."""
    try:
        running_event.set()
    except Exception:
        pass

    # Kill old processes
    for proc, name in ((gateway_proc, "Gateway"), (keybinder_proc, "KeyBinder")):
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            except Exception:
                pass

    # Clear the rotation flag — rotation is done
    try:
        if os.path.exists(ROTATION_FLAG_FILE):
            os.remove(ROTATION_FLAG_FILE)
    except Exception:
        pass

    # Restart fresh with the new key loaded
    time.sleep(2)  # brief pause for the new gateway to be ready
    start_services()
    log("Services restarted after rotation — new key active", "OK")
    set_status("✓ Key rotated, services restarted", "green")


# ===================== GUI UPDATER =====================

def read_active_key():
    try:
        with open(ACTIVE_KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if not key:
            return "No key"
        if len(key) <= 12:
            return key
        return key[:12] + "…"
    except FileNotFoundError:
        return "No key file"
    except Exception as e:
        log(f"Error reading key file: {e}", "ERROR")
        return "Error"


def is_process_alive(proc):
    """Returns True if proc is alive (not None and not exited)."""
    return proc is not None and proc.poll() is None


def update_process_health_labels():
    """Update green/red service indicators."""
    gw_alive = is_process_alive(gateway_proc)
    kb_alive = is_process_alive(keybinder_proc)
    # Use ● glyph + color (colorblind users can read the word "running" / "stopped")
    gw_text = "● Gateway: running" if gw_alive else "● Gateway: stopped"
    kb_text = "● KeyBinder: running" if kb_alive else "● KeyBinder: stopped"
    gw_color = "#10B981" if gw_alive else "#F87171"
    kb_color = "#10B981" if kb_alive else "#F87171"
    try:
        gw_status_label.configure(text=gw_text, text_color=gw_color)
        kb_status_label.configure(text=kb_text, text_color=kb_color)
    except Exception:
        pass


def update_rotate_button_highlight():
    """Highlight Rotate button when KeyBinder has flagged rotation-needed."""
    needed = False
    try:
        if os.path.exists(ROTATION_FLAG_FILE):
            needed = True
    except Exception:
        pass
    try:
        if needed:
            rotate_btn.configure(
                fg_color="#F59E0B", hover_color="#D97706",
                text_color="#FFFFFF", text="⚠  Rotate Now!",
            )
        else:
            rotate_btn.configure(
                fg_color=ACCENT, hover_color="#2563EB",
                text_color="#FFFFFF", text="🔄  Rotate Now",
            )
    except Exception:
        pass


def _read_usage_file_safe():
    """Read token_usage.json with one retry. Returns parsed dict, or None if
    the file is genuinely unreadable / invalid. Reliably hides the
    write/read race that causes blinking."""
    for attempt in (1, 2):
        try:
            if not os.path.exists(USAGE_FILE):
                return None
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                raw = f.read()
            if not raw.strip():
                # File exists but empty — almost certainly mid-write race.
                # Brief sleep and retry, then give up gracefully if still empty.
                if attempt == 1:
                    time.sleep(0.05)
                    continue
                return None
            return json.loads(raw)
        except json.JSONDecodeError:
            # Same retry logic: transient partial write.
            if attempt == 1:
                time.sleep(0.05)
                continue
            raise
        except Exception:
            return None
    return None


# Activity-tag detection for log lines (content-based, supplements level tag).
# Each entry is a (tag, [substrings_to_match]) pair; first match wins.
_ACTIVITY_PATTERNS = [
    ("key_new",    ["new key", "key created", "created key", "key created"]),
    ("key_del",    ["deleted key", "delete key", "deleting key"]),
    ("gateway",    ["gateway", "upstream"]),
    ("keybinder",  ["keybinder", "rotation_needed", "rotation flag"]),
]
_last_warn_ts = {"corrupted": 0.0, "error": 0.0}
WARN_COOLDOWN_SEC = 30.0


def _log_throttled(level, msg, key):
    """Log only if cooldown has elapsed for this key."""
    now = time.time()
    if now - _last_warn_ts[key] >= WARN_COOLDOWN_SEC:
        _last_warn_ts[key] = now
        log(msg, level)


def _activity_tag_for(message: str) -> str:
    """Return an activity-level tag based on the message content."""
    lower = message.lower()
    for tag, keywords in _ACTIVITY_PATTERNS:
        if any(kw in lower for kw in keywords):
            return tag
    return ""


def update_display():
    """Refresh labels every REFRESH_MS milliseconds. Reads live config each tick."""
    if not running_event.is_set():
        return

    if _root_ref is None:
        return
    try:
        if not _root_ref.winfo_exists():
            return
    except Exception:
        return

    # Bug B-2 fix: read live config rather than frozen module-level constants
    total_limit = config.get("total_limit", 1_500_000)
    refresh_ms = config.get("refresh_ms", 2000)

    # Key
    try:
        key_label.configure(text=f"🔑  {read_active_key()}")
    except Exception:
        pass

    # Usage — read with race-safe retry so a mid-write doesn't blink the UI
    # or flood the log with "corrupted" warnings.
    data = None
    try:
        data = _read_usage_file_safe()
    except json.JSONDecodeError:
        _log_throttled("WARN", "Usage file corrupted (invalid JSON, retried)", "corrupted")
    except Exception as e:
        _log_throttled("ERROR", f"Error reading usage: {e}", "error")

    if data is None:
        if os.path.exists(USAGE_FILE):
            # File exists but couldn't be parsed even after retry.
            # DON'T overwrite the labels — keep showing last-known good data
            # so the UI doesn't blink. Just bump the status, no log spam.
            try:
                if "usage_label_text_on_error" not in update_display.__dict__:
                    usage_label.configure(
                        text="📊  Usage file unreadable (retrying)"
                    )
            except Exception:
                pass
        else:
            try:
                usage_label.configure(text="📊  No usage file yet")
                remaining_label.configure(
                    text="Waiting for first request…", text_color=TEXT_DIM,
                )
                _animate_progress(0)
            except Exception:
                pass
    else:
        total = data.get("total", 0)
        remaining = data.get("remaining", total_limit)
        pct = (total / total_limit * 100) if total_limit else 0

        try:
            usage_label.configure(
                text=f"📊  {total:,} / {total_limit:,} tokens  ({pct:.1f}%)"
            )
            _animate_progress(pct)
            remaining_label.configure(text=f"Remaining: {remaining:,}")

            # Color-shift the remaining label based on usage threshold.
            if pct < 50:
                remaining_color = "#10B981"
            elif pct < 80:
                remaining_color = "#FBBF24"
            else:
                remaining_color = "#F87171"
            remaining_label.configure(text_color=remaining_color)
        except Exception:
            pass

        # === Phase 3: token-limit warnings + auto-rotate ===
        _check_token_thresholds(pct)
        _check_auto_rotate(pct)

        # Pulse the bar at critical usage (>= 95%) to draw attention.
        global is_critical_threshold
        was_critical = is_critical_threshold
        is_critical_threshold = pct >= 95
        if is_critical_threshold and not was_critical:
            _pulse_progress(True)
        elif not is_critical_threshold and was_critical:
            _pulse_progress(False)

    # Health indicators
    update_process_health_labels()

    # Poller status (non-fatal — poller is optional).
    # PollerSnapshot is a dataclass — use attribute access, not .get().
    try:
        if key_poller_instance is not None:
            snap = key_poller_instance.snapshot()
            if not snap.enabled:
                key_poller_status_var.set("Idle (off)")
            elif snap.running:
                key_poller_status_var.set(
                    f"Pulling  {snap.tick_count} ticks · "
                    f"{snap.success_count} ok · {snap.error_count} err"
                )
            else:
                key_poller_status_var.set("Paused")
            # Surface last-error and last-key prefix so users see what's happening
            if snap.last_error:
                key_poller_status_var.set(
                    f"Last error: {snap.last_error[:80]}"
                )
    except Exception:
        pass

    # Rotate button highlight from KeyBinder flag
    update_rotate_button_highlight()

    # Auto-restart dead services (Feature F1)
    if config.get("auto_restart", True) and running_event.is_set():
        if gateway_proc is not None and gateway_proc.poll() is not None and _restart_counts["gateway"] <= MAX_RESTARTS_PER_MIN:
            log("Gateway process died — auto-restarting…", "WARN")
            threading.Thread(target=lambda: _restart_one("Gateway", "gateway"), daemon=True).start()
        if keybinder_proc is not None and keybinder_proc.poll() is not None and _restart_counts["keybinder"] <= MAX_RESTARTS_PER_MIN:
            log("KeyBinder process died — auto-restarting…", "WARN")
            threading.Thread(target=lambda: _restart_one("KeyBinder", "keybinder"), daemon=True).start()

    # Schedule next tick using live config
    if running_event.is_set() and _root_ref is not None:
        try:
            _root_ref.after(refresh_ms, update_display)
        except Exception:
            pass


# ===================== SETTINGS DIALOG =====================

# Feature F3: Start on Windows boot (no extra deps)
def _startup_shortcut_path():
    if sys.platform != "win32":
        return None
    startup = os.path.join(os.environ.get("APPDATA", ""),
                           r"Microsoft\Windows\Start Menu\Programs\Startup")
    return os.path.join(startup, f"{APP_NAME}.lnk")


def _set_launch_on_startup(enabled):
    """Create or remove the .lnk in the user's Startup folder."""
    if sys.platform != "win32":
        return False, "Startup feature is Windows-only"
    lnk_path = _startup_shortcut_path()
    if not lnk_path:
        return False, "Could not resolve Startup folder"
    try:
        if enabled:
            # Use PowerShell to create the shortcut — avoids pywin32 dep
            import subprocess as sp
            target = sys.executable
            script = os.path.abspath(__file__)
            # Work dir = script dir so relative paths in the script still resolve.
            workdir = os.path.dirname(script)
            ps = (
                f"$ws = New-Object -ComObject WScript.Shell; "
                f"$s = $ws.CreateShortcut('{lnk_path}'); "
                f"$s.TargetPath = '{target}'; "
                f"$s.Arguments = '\"{script}\"'; "
                f"$s.WorkingDirectory = '{workdir}'; "
                f"$s.WindowStyle = 7; "  # 7 = minimized
                f"$s.Save()"
            )
            sp.run(["powershell", "-NoProfile", "-Command", ps],
                   check=True, capture_output=True, timeout=10)
            return True, "Startup shortcut created"
        else:
            if os.path.exists(lnk_path):
                os.remove(lnk_path)
            return True, "Startup shortcut removed"
    except Exception as e:
        return False, str(e)


def open_settings():
    """Modal settings dialog with live-apply for theme + new fields."""
    win = ctk.CTkToplevel(_root_ref)
    win.title("Settings")
    win.geometry("520x660")
    win.transient(_root_ref)
    win.grab_set()
    win.resizable(False, False)

    PAD = {"padx": 14, "pady": 5}

    # ---- Card: Paths ----
    card_paths = ctk.CTkFrame(win, fg_color=CARD, border_width=1,
                               border_color=CARD_BORDER, corner_radius=10)
    card_paths.pack(fill="x", padx=14, pady=(14, 6))

    ctk.CTkLabel(card_paths, text="Paths", font=FONT_SECTION,
                 text_color=ACCENT).pack(anchor="w", padx=14, pady=(10, 2))

    row = ctk.CTkFrame(card_paths, fg_color="transparent")
    row.pack(fill="x", padx=10, pady=(0, 8))
    ctk.CTkLabel(row, text="Base directory:", font=FONT_SMALL,
                 text_color=TEXT_PRIMARY, anchor="w", width=140).pack(side=tk.LEFT)
    base_var = tk.StringVar(value=config["base_dir"])
    base_entry = ctk.CTkEntry(row, textvariable=base_var, width=280,
                              font=FONT_SMALL)
    base_entry.pack(side=tk.LEFT, padx=(8, 4))

    def browse():
        d = filedialog.askdirectory(initialdir=config["base_dir"], parent=win)
        if d:
            base_var.set(d)
    ctk.CTkButton(row, text="📂", command=browse, width=32, height=28,
                  fg_color="#374151", hover_color="#4B5563",
                  text_color=TEXT_PRIMARY, font=FONT_SMALL, corner_radius=6).pack(side=tk.LEFT)

    # ---- Card: Tokens ----
    card_tokens = ctk.CTkFrame(win, fg_color=CARD, border_width=1,
                                border_color=CARD_BORDER, corner_radius=10)
    card_tokens.pack(fill="x", padx=14, pady=(0, 6))
    ctk.CTkLabel(card_tokens, text="Tokens", font=FONT_SECTION,
                 text_color=ACCENT).pack(anchor="w", padx=14, pady=(10, 2))

    # Limit
    r1 = ctk.CTkFrame(card_tokens, fg_color="transparent")
    r1.pack(fill="x", padx=10, pady=(0, 4))
    ctk.CTkLabel(r1, text="Total token limit:", font=FONT_SMALL,
                 text_color=TEXT_PRIMARY, anchor="w", width=140).pack(side=tk.LEFT)
    limit_var = tk.StringVar(value=str(config["total_limit"]))
    ctk.CTkEntry(r1, textvariable=limit_var, width=120, font=FONT_SMALL).pack(side=tk.LEFT, padx=8)

    # Warning thresholds
    r1b = ctk.CTkFrame(card_tokens, fg_color="transparent")
    r1b.pack(fill="x", padx=10, pady=(0, 4))
    ctk.CTkLabel(r1b, text="Warning thresholds %:", font=FONT_SMALL,
                 text_color=TEXT_PRIMARY, anchor="w", width=140).pack(side=tk.LEFT)
    warn_thresh_var = tk.StringVar(
        value=", ".join(str(p) for p in config.get("token_warning_pcts", [80, 95]))
    )
    ctk.CTkEntry(r1b, textvariable=warn_thresh_var, width=120, font=FONT_SMALL).pack(side=tk.LEFT, padx=8)
    ctk.CTkLabel(r1b, text="e.g. 80, 95", font=ctk.CTkFont(size=9),
                 text_color=TEXT_DIM).pack(side=tk.LEFT, padx=(4, 0))

    # Auto-rotate at %
    r1c = ctk.CTkFrame(card_tokens, fg_color="transparent")
    r1c.pack(fill="x", padx=10, pady=(0, 8))
    ctk.CTkLabel(r1c, text="Auto-rotate at %:", font=FONT_SMALL,
                 text_color=TEXT_PRIMARY, anchor="w", width=140).pack(side=tk.LEFT)
    auto_rotate_var = tk.StringVar(value=str(config.get("auto_rotate_pct", 90)))
    ctk.CTkEntry(r1c, textvariable=auto_rotate_var, width=60, font=FONT_SMALL).pack(side=tk.LEFT, padx=8)
    ctk.CTkLabel(r1c, text="0 = disabled", font=ctk.CTkFont(size=9),
                 text_color=TEXT_DIM).pack(side=tk.LEFT, padx=(4, 0))

    # ---- Card: Behavior ----
    card_behavior = ctk.CTkFrame(win, fg_color=CARD, border_width=1,
                                  border_color=CARD_BORDER, corner_radius=10)
    card_behavior.pack(fill="x", padx=14, pady=(0, 6))
    ctk.CTkLabel(card_behavior, text="Behavior", font=FONT_SECTION,
                 text_color=ACCENT).pack(anchor="w", padx=14, pady=(10, 2))

    # Refresh
    r2 = ctk.CTkFrame(card_behavior, fg_color="transparent")
    r2.pack(fill="x", padx=10, pady=(0, 4))
    ctk.CTkLabel(r2, text="Refresh interval (ms):", font=FONT_SMALL,
                 text_color=TEXT_PRIMARY, anchor="w", width=140).pack(side=tk.LEFT)
    refresh_var = tk.StringVar(value=str(config["refresh_ms"]))
    ctk.CTkEntry(r2, textvariable=refresh_var, width=80, font=FONT_SMALL).pack(side=tk.LEFT, padx=8)

    # Startup delay
    r3 = ctk.CTkFrame(card_behavior, fg_color="transparent")
    r3.pack(fill="x", padx=10, pady=(0, 4))
    ctk.CTkLabel(r3, text="Startup delay (s):", font=FONT_SMALL,
                 text_color=TEXT_PRIMARY, anchor="w", width=140).pack(side=tk.LEFT)
    delay_var = tk.StringVar(value=str(config["startup_delay"]))
    ctk.CTkEntry(r3, textvariable=delay_var, width=80, font=FONT_SMALL).pack(side=tk.LEFT, padx=8)

    # Toggles
    autorestart_var = tk.BooleanVar(value=config.get("auto_restart", True))
    confirm_stop_var = tk.BooleanVar(value=config.get("confirm_stop", True))
    capture_logs_var = tk.BooleanVar(value=config.get("capture_subprocess_logs", True))
    startup_var = tk.BooleanVar(value=config.get("launch_on_startup", False))

    ctk.CTkCheckBox(card_behavior, text="Auto-restart crashed services",
                    variable=autorestart_var, font=FONT_SMALL,
                    checkbox_width=18, checkbox_height=18,
                    corner_radius=4).pack(anchor="w", padx=24, pady=2)
    ctk.CTkCheckBox(card_behavior, text="Confirm before Stop All",
                    variable=confirm_stop_var, font=FONT_SMALL,
                    checkbox_width=18, checkbox_height=18,
                    corner_radius=4).pack(anchor="w", padx=24, pady=2)
    ctk.CTkCheckBox(card_behavior, text="Capture subprocess logs",
                    variable=capture_logs_var, font=FONT_SMALL,
                    checkbox_width=18, checkbox_height=18,
                    corner_radius=4).pack(anchor="w", padx=24, pady=2)

    # Low-latency gateway toggle
    use_low_latency_var = tk.BooleanVar(
        value=config.get("use_low_latency_gateway", True)
    )
    ctk.CTkCheckBox(card_behavior,
                    text="Use low-latency gateway (persistent connections)",
                    variable=use_low_latency_var, font=FONT_SMALL,
                    checkbox_width=18, checkbox_height=18,
                    corner_radius=4).pack(anchor="w", padx=24, pady=2)
    if sys.platform == "win32":
        ctk.CTkCheckBox(card_behavior, text="Launch on Windows startup",
                        variable=startup_var, font=FONT_SMALL,
                        checkbox_width=18, checkbox_height=18,
                        corner_radius=4).pack(anchor="w", padx=24, pady=2)

    # Appearance mode
    r_appearance = ctk.CTkFrame(card_behavior, fg_color="transparent")
    r_appearance.pack(fill="x", padx=10, pady=(4, 10))
    ctk.CTkLabel(r_appearance, text="Appearance:", font=FONT_SMALL,
                 text_color=TEXT_PRIMARY, anchor="w", width=140).pack(side=tk.LEFT)
    appearance_var = tk.StringVar(value=config.get("appearance_mode", "Dark"))
    appearance_menu = ctk.CTkOptionMenu(
        r_appearance, values=["Dark", "Light", "System"],
        variable=appearance_var, width=120, height=26,
        font=FONT_SMALL, dropdown_font=FONT_SMALL,
        fg_color="#374151", button_color="#4B5563", button_hover_color="#6B7280",
    )
    appearance_menu.pack(side=tk.LEFT, padx=8)

    # ---- Buttons ----
    btn_frame = ctk.CTkFrame(win, fg_color="transparent")
    btn_frame.pack(pady=14)

    def save_and_close():
        # Validate numeric inputs
        try:
            new_limit = int(limit_var.get())
            new_refresh = int(refresh_var.get())
            new_delay = float(delay_var.get())
            new_auto_rotate = int(auto_rotate_var.get())
        except ValueError:
            messagebox.showerror("Invalid input",
                                 "Limit, refresh, delay, and auto-rotate must be numbers.",
                                 parent=win)
            return

        # Validate warning thresholds (comma-separated list of ints)
        try:
            new_thresholds = [int(p.strip()) for p in warn_thresh_var.get().split(",") if p.strip()]
        except ValueError:
            messagebox.showerror("Invalid input",
                                 "Warning thresholds must be comma-separated numbers (e.g. 80, 95).",
                                 parent=win)
            return

        # Track old values for restart-needed check
        old_base = config.get("base_dir", "")
        old_delay = config.get("startup_delay", 2)
        old_gateway_mode = config.get("use_low_latency_gateway", True)
        needs_restart = False

        # Validate base_dir
        new_base = base_var.get().strip()
        if not os.path.isdir(new_base):
            if not messagebox.askyesno(
                "Directory not found",
                f"'{new_base}' doesn't exist.\nSave anyway?",
                parent=win
            ):
                return

        # Bug B-10 fix: validate that required files exist in new base_dir
        required = ["OpusGateway.py", "Gateway_LowLatency.py", "KeyBinder.py", "rotate_now.py", "active_key.txt"]
        if os.path.isdir(new_base):
            missing = [f for f in required if not os.path.exists(os.path.join(new_base, f))]
            if missing:
                messagebox.showerror(
                    "Missing files",
                    f"The following files are missing in the new base directory:\n  " +
                    "\n  ".join(missing) +
                    "\n\nCannot save.",
                    parent=win
                )
                return

        # Apply all settings
        config["base_dir"] = new_base
        config["total_limit"] = new_limit
        config["refresh_ms"] = new_refresh
        config["startup_delay"] = new_delay
        config["auto_restart"] = autorestart_var.get()
        config["confirm_stop"] = confirm_stop_var.get()
        config["capture_subprocess_logs"] = capture_logs_var.get()
        config["launch_on_startup"] = startup_var.get()
        config["use_low_latency_gateway"] = use_low_latency_var.get()
        config["token_warning_pcts"] = new_thresholds
        config["auto_rotate_pct"] = new_auto_rotate
        new_appearance = appearance_var.get()
        # Reset warned thresholds if user changed the threshold list (so new
        # thresholds fire on next crossing) — but only reset ones that changed.
        global _warned_thresholds
        existing = set(config.get("token_warning_pcts", [80, 95]))
        _warned_thresholds = {t for t in _warned_thresholds if t in set(new_thresholds)}

        config["appearance_mode"] = new_appearance
        # Apply appearance mode immediately — no restart required
        try:
            ctk.set_appearance_mode(new_appearance)
        except Exception as e:
            log(f"Failed to apply appearance mode '{new_appearance}': {e}", "WARN")

        save_config(config)
        relaunch_dependent_scripts()
        log(f"Settings saved: base={new_base}, limit={new_limit:,}", "OK")

        # Check if settings that require restart were changed
        if new_base != old_base or new_delay != old_delay:
            needs_restart = True
        if use_low_latency_var.get() != old_gateway_mode:
            needs_restart = True

        # Apply startup shortcut toggle
        if config["launch_on_startup"]:
            ok, msg = _set_launch_on_startup(True)
            log(f"Startup shortcut: {msg}", "OK" if ok else "ERROR")
        else:
            ok, msg = _set_launch_on_startup(False)
            log(f"Startup shortcut: {msg}", "OK" if ok else "ERROR")

        if needs_restart:
            # Offer one-click restart
            restart_now = messagebox.askyesno(
                "Restart services?",
                "base_dir, startup_delay, or gateway mode changed — services need to restart.\n\n"
                "Restart Gateway and KeyBinder now?",
                parent=win,
            )
            if restart_now:
                win.destroy()
                threading.Thread(target=lambda: (time.sleep(0.3), restart_services()), daemon=True).start()
                return

        messagebox.showinfo(
            "Settings",
            "Settings saved.\n\n"
            "• Appearance applied immediately.\n"
            "• Token thresholds & auto-rotate apply on next refresh tick.",
            parent=win,
        )
        win.destroy()

    def reset_defaults():
        for k, v in DEFAULT_CONFIG.items():
            config[k] = v
        base_var.set(config["base_dir"])
        limit_var.set(str(config["total_limit"]))
        refresh_var.set(str(config["refresh_ms"]))
        delay_var.set(str(config["startup_delay"]))
        autorestart_var.set(config["auto_restart"])
        confirm_stop_var.set(config["confirm_stop"])
        capture_logs_var.set(config["capture_subprocess_logs"])
        startup_var.set(config["launch_on_startup"])
        use_low_latency_var.set(config["use_low_latency_gateway"])
        appearance_var.set(config["appearance_mode"])
        warn_thresh_var.set(", ".join(str(p) for p in config["token_warning_pcts"]))
        auto_rotate_var.set(str(config["auto_rotate_pct"]))

    def cancel():
        win.destroy()

    ctk.CTkButton(btn_frame, text="💾  Save", command=save_and_close,
                  fg_color=ACCENT, hover_color="#2563EB",
                  text_color="white", font=FONT_BODY,
                  corner_radius=8, height=32, width=100).pack(side=tk.LEFT, padx=4)
    ctk.CTkButton(btn_frame, text="↺  Reset", command=reset_defaults,
                  fg_color="#374151", hover_color="#4B5563",
                  text_color=TEXT_PRIMARY, font=FONT_BODY,
                  corner_radius=8, height=32, width=90).pack(side=tk.LEFT, padx=4)
    ctk.CTkButton(btn_frame, text="Cancel", command=cancel,
                  fg_color="transparent", hover_color=CARD_BORDER,
                  text_color=TEXT_DIM, font=FONT_BODY,
                  corner_radius=8, height=32, width=90).pack(side=tk.LEFT, padx=4)

    base_entry.focus_set()


# ===================== SYSTEM TRAY =====================

def create_tray_icon():
    """Create a pystray icon (runs in background thread)."""
    global tray_icon
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        log("pystray/Pillow not installed – tray disabled", "WARN")
        return

    def make_icon():
        # Clean abstract "O" mark with subtle vertical gradient — modern,
        # recognizable at small sizes, fits the app's blue accent theme.
        SIZE = 64
        img = Image.new("RGB", (SIZE, SIZE), color="#0E0E10")
        draw = ImageDraw.Draw(img)

        # Outer ring with gradient (top accent blue → bottom deeper blue)
        ring_color_top = (59, 130, 246)   # #3B82F6
        ring_color_bot = (29, 78, 216)    # #1D4ED8
        ring_thickness = 8

        for y in range(SIZE):
            # Linear interpolate between top and bottom colors
            t = y / (SIZE - 1)
            r = int(ring_color_top[0] * (1 - t) + ring_color_bot[0] * t)
            g = int(ring_color_top[1] * (1 - t) + ring_color_bot[1] * t)
            b = int(ring_color_top[2] * (1 - t) + ring_color_bot[2] * t)
            draw.line([(0, y), (SIZE, y)], fill=(r, g, b))

        # Cut out the center to leave a ring (subtract inner circle)
        mask = Image.new("L", (SIZE, SIZE), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse(
            [ring_thickness, ring_thickness,
             SIZE - ring_thickness - 1, SIZE - ring_thickness - 1],
            fill=255,
        )
        # Apply: paint solid ring with gradient color, then mask the center
        solid = Image.new("RGB", (SIZE, SIZE), ring_color_top)
        sol_draw = ImageDraw.Draw(solid)
        for y in range(SIZE):
            t = y / (SIZE - 1)
            r = int(ring_color_top[0] * (1 - t) + ring_color_bot[0] * t)
            g = int(ring_color_top[1] * (1 - t) + ring_color_bot[1] * t)
            b = int(ring_color_top[2] * (1 - t) + ring_color_bot[2] * t)
            sol_draw.line([(0, y), (SIZE, y)], fill=(r, g, b))

        img.paste(solid, (0, 0), mask=mask)

        # Small accent dot in center (rotation indicator — pulses subtly)
        dot_r = 4
        center = SIZE // 2
        draw.ellipse(
            [center - dot_r, center - dot_r, center + dot_r, center + dot_r],
            fill=(255, 255, 255),
        )
        return img

    def on_show(icon, item):
        # Bug B-5 fix: don't call root.after from tray thread directly —
        # use a thread-safe queue via icon.notify / icon.run, but simplest
        # is to just call deiconify directly. Tk is permissive in some
        # operations but to stay safe, use after via a one-shot helper.
        try:
            _root_ref.after(0, show_window)
        except Exception:
            show_window()

    def on_rotate(icon, item):
        try:
            _root_ref.after(0, rotate_key)
        except Exception:
            rotate_key()

    def on_quit(icon, item):
        # Use a thread with a small delay to avoid deadlock: icon.stop() must
        # return before _root_ref.after() runs, otherwise the tray thread
        # can hang if after() tries to pump the event loop while stop() blocks.
        def _delayed_quit():
            try:
                icon.stop()
            except Exception:
                pass
            time.sleep(0.15)
            try:
                _root_ref.after(0, lambda: stop_services(tray_too=False, exit_app=True))
            except Exception:
                stop_services(tray_too=False, exit_app=True)
        threading.Thread(target=_delayed_quit, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Show Window", on_show, default=True),
        pystray.MenuItem("Rotate Now", on_rotate),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    tray_icon = pystray.Icon("OpusControlPanel", make_icon(), "Opus Control Panel", menu)
    log("System tray icon ready", "OK")
    try:
        tray_icon.run()
    except Exception as e:
        log(f"Tray icon exited: {e}", "WARN")


def stop_tray():
    """Stop the tray icon if running."""
    global tray_icon
    if tray_icon:
        try:
            tray_icon.stop()
        except Exception:
            pass


# ===================== WINDOW VISIBILITY =====================

def hide_to_tray():
    window_hidden = True
    try:
        _root_ref.withdraw()
    except Exception:
        pass
    log("Window hidden to tray (services still running)", "INFO")


def show_window():
    window_hidden = False
    if _root_ref is None:
        return
    try:
        _root_ref.deiconify()
        _root_ref.lift()
        _root_ref.focus_force()
    except Exception:
        pass


# ===================== BUILD WINDOW =====================

# Configure CustomTkinter global appearance — Linear-style blue accent on dark.
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

root = ctk.CTk()
_root_ref = root
root.title("Opus Control Panel")

# Restore saved geometry if present and on-screen (validates against current
# screen bounds — multi-monitor disconnect protection).
_saved_geom = config.get("window_geometry", "")
if _saved_geom:
    applied = False
    try:
        # Parse "WxH+X+Y" or "WxH"; ignore anything malformed.
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        parts = _saved_geom.split("+")
        if "x" in parts[0]:
            w, h = parts[0].split("x")
            w, h = int(w), int(h)
            x = y = 0
            if len(parts) >= 3:
                x = int(parts[1])
                y = int(parts[2])
            # Validate: not absurdly small, and at least 100px on-screen
            if (w >= 400 and h >= 300 and
                -100 < x < screen_w - 100 and
                -100 < y < screen_h - 100):
                root.geometry(_saved_geom)
                applied = True
    except Exception:
        # Any parse / WinAPI hiccup -> fall back to default geometry below.
        applied = False
    if not applied:
        root.geometry("760x640")
    root.minsize(720, 580)

# ----- Font stack -----
FONT_TITLE = ctk.CTkFont(family="Segoe UI Variable", size=20, weight="bold")
FONT_SECTION = ctk.CTkFont(family="Segoe UI Variable", size=13, weight="bold")
FONT_BODY = ctk.CTkFont(family="Segoe UI Variable", size=12)
FONT_SMALL = ctk.CTkFont(family="Segoe UI Variable", size=11)
FONT_LOG = ctk.CTkFont(family="Cascadia Mono", size=10)

# ----- Color tokens (dark theme) -----
BG = "#0E0E10"           # near-black background
CARD = "#1A1A1D"          # card background
CARD_BORDER = "#2A2A2E"   # subtle border
ACCENT = "#3B82F6"        # Linear-style blue
TEXT_PRIMARY = "#E5E7EB"  # near-white
TEXT_DIM = "#9CA3AF"      # dim text

# ===================== TOGGLE SWITCH WIDGET =====================

class CTkToggleSwitch(ctk.CTkCanvas):
    """A pill-shaped animated toggle switch built on CTkCanvas.

    Styled to match the app's dark theme using the global color tokens
    (BG, CARD_BORDER, ACCENT, TEXT_PRIMARY, TEXT_DIM).

    Usage:
        tog = CTkToggleSwitch(parent, text="Label", command=callback)
        tog.set(True)   # or False
    """

    def __init__(
        self,
        master,
        width: int = 52,
        height: int = 26,
        text: str = "",
        command=None,
        default: bool = False,
        **kwargs,
    ):
        # Ensure CTkCanvas is initialized with the master and kwargs
        super().__init__(master, width=width, height=height,
                         highlightthickness=0, borderwidth=0, **kwargs)

        self._command = command
        self._on = default
        self._animating = False

        # Colors — pulled from the app's palette so the switch adapts
        # automatically if the user changes appearance mode.
        self._track_off = CARD_BORDER
        self._track_on  = ACCENT
        self._knob      = TEXT_PRIMARY
        self._text_on   = "On"
        self._text_off  = "Off"

        # Geometry
        self._tw = width
        self._th = height
        self._pad = 3                              # inset from track edge
        self._knob_r = (height - self._pad * 2) / 2
        self._cx_on  = width - self._pad - self._knob_r
        self._cx_off = self._pad + self._knob_r

        # Current knob center (start at default position)
        self._cx = self._cx_on if self._on else self._cx_off

        # Build static elements (track + text + knob — knob is re-drawn)
        self._draw_track()
        self._text_id = self._draw_text(text)
        self._knob_id = self._draw_knob()

        # Click / tap handler
        self.bind("<Button-1>", self._toggle)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> bool:
        """Return current on/off state."""
        return self._on

    def set(self, value: bool, _from_anim: bool = False):
        """Set the switch state without firing the command."""
        if self._on == value:
            return
        self._on = value
        target = self._cx_on if self._on else self._cx_off
        if _from_anim:
            # Already handled by animation — just redraw knob at final pos
            self._cx = target
            self._redraw_knob()
        else:
            self._animate_to(target)

    def configure(self, **kwargs):
        """Allow CTk-style configure calls (text, command, variable)."""
        if "command" in kwargs:
            self._command = kwargs["command"]
        if "text" in kwargs:
            self._text_id = self._draw_text(kwargs["text"])
        if "variable" in kwargs:
            var = kwargs["variable"]
            try:
                self.set(bool(var.get()))
            except Exception:
                pass
        super().configure(**kwargs)

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    def _draw_track(self):
        """Pill-shaped track — color reflects current state."""
        r = self._th / 2
        color = self._track_on if self._on else self._track_off
        # Build rounded-rect manually via polygon points
        pts = []
        for i in range(0, 360, 10):
            a = math.radians(i)
            pts.append(self._tw - r + r * math.cos(a))
            pts.append(r     + r * math.sin(a))
        for i in range(180, 360, 10):
            a = math.radians(i)
            pts.append(r + r * math.cos(a))
            pts.append(r + r * math.sin(a))
        # Simpler approach: draw rects + two arcs
        self.create_rectangle(r, 0, self._tw - r, self._th,
                              fill=color, outline="", tags="static")
        self.create_arc(0, 0, 2 * r, 2 * r, start=90, extent=180,
                        fill=color, outline="", tags="static")
        self.create_arc(self._tw - 2 * r, 0, self._tw, 2 * r,
                        start=-90, extent=180, fill=color, outline="", tags="static")

    def _draw_text(self, label: str):
        """Label centered in the knob area — white when on, dim when off."""
        cx = (self._cx_off + self._cx_on) / 2
        cy = self._th / 2
        color = TEXT_PRIMARY if self._on else TEXT_DIM
        return self.create_text(
            cx, cy, text=label, fill=color,
            font=("Segoe UI Variable", 9),
        )

    def _draw_knob(self):
        """Circle knob at current _cx position."""
        cy = self._th / 2
        return self.create_oval(
            self._cx - self._knob_r, cy - self._knob_r,
            self._cx + self._knob_r, cy + self._knob_r,
            fill=self._knob, outline="",
        )

    def _redraw_knob(self):
        """Erase and re-draw knob at self._cx; update track + text."""
        self.delete(self._knob_id)
        self._knob_id = self._draw_knob()
        self._redraw_static()

    def _redraw_static(self):
        """Re-draw track and text to match new _on state."""
        self.delete("static")
        self._draw_track()
        # Re-draw text
        self.delete(self._text_id)
        self._text_id = self._draw_text(self._get_label_text())

    def _get_label_text(self) -> str:
        """Try to retrieve the current text from the canvas text item."""
        try:
            return self.itemcget(self._text_id, "text")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def _animate_to(self, target_cx: float, steps: int = 8, interval: int = 14):
        """Smoothly slide the knob from self._cx to target_cx."""
        if self._animating:
            # Snap if already animating
            self._cx = target_cx
            self._redraw_knob()
            return
        self._animating = True
        start = self._cx
        delta = (target_cx - start) / steps

        def _step(i: int = 0):
            if i >= steps:
                self._cx = target_cx
                self._animating = False
                self._redraw_knob()
                return
            self._cx = start + delta * i
            self._redraw_knob()
            try:
                self.after(interval, lambda: _step(i + 1))
            except Exception:
                self._animating = False

        _step(0)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _toggle(self, event=None):
        """Flip state, animate, then fire command."""
        self.set(not self._on)
        if self._command:
            try:
                self._command()
            except Exception:
                pass

# ===================== UI BUILD =====================

# ----- SIDEBAR + VIEW ROUTER SKELETON -----

class SidebarNav(ctk.CTkFrame):
    """Vertical sidebar with nav buttons, not functional yet (Phase 1)."""
    def __init__(self, master, on_nav=None):
        super().__init__(master, width=72, fg_color=BG, corner_radius=0)
        self._on_nav = on_nav
        self._buttons = {}

        tabs = [
            ("Dashboard", "🏠"),
            ("Model Keys", "🔑"),
            ("Logs", "📋"),
            ("Extras", "⚙️"),
        ]
        for ix, (name, emoji) in enumerate(tabs):
            btn = ctk.CTkButton(
                self, text=f"  {emoji}  {name}",
                width=62, height=44, fg_color=BG, hover_color=CARD,
                text_color=TEXT_PRIMARY, anchor="w", corner_radius=12,
                font=("Segoe UI Variable", 13, "bold"),
                command=lambda n=name: self._fire(n),
            )
            btn.grid(row=ix, column=0, padx=6, pady=(10 if ix == 0 else 4, 0), sticky="ew")
            self._buttons[name] = btn
        self.grid_rowconfigure(len(tabs), weight=1)

    def _fire(self, name):
        if self._on_nav:
            self._on_nav(name)


class ViewRouter(ctk.CTkFrame):
    """Holds multiple frames and shows only the selected one."""
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self._views = {}
        self._shown = None
        # Add four placeholder frames labeled with their view name
        for name in ("Dashboard", "Model Keys", "Logs", "Extras"):
            frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=18)
            label = ctk.CTkLabel(frame, text=f"{name} View", font=("Segoe UI Variable", 20, "bold"), text_color=ACCENT)
            label.pack(expand=True)
            self._views[name] = frame
        # Show default
        self.show("Dashboard")

    def show(self, name):
        if self._shown:
            self._views[self._shown].pack_forget()
        self._views[name].pack(fill="both", expand=True, padx=10, pady=10)
        self._shown = name

# ----- Top bar -----
top_bar = ctk.CTkFrame(root, fg_color="transparent")
top_bar.pack(fill="x", padx=8, pady=(8, 4))

# Ghost-style icon buttons for Settings and Minimize
icon_btn_style = dict(
    width=36, height=36,
    fg_color="transparent",
    hover_color=CARD_BORDER,
    corner_radius=8,
    font=FONT_BODY,
    text_color=TEXT_PRIMARY,
)

ctk.CTkButton(top_bar, text="⚙", command=open_settings, **icon_btn_style).pack(side=tk.RIGHT)
ctk.CTkButton(top_bar, text="🗕", command=hide_to_tray, **icon_btn_style).pack(side=tk.RIGHT, padx=(4, 0))

# Title — inside a CTkLabel on a card for subtle grouping
title_label = ctk.CTkLabel(
    top_bar,
    text="Opus Control Panel",
    font=FONT_TITLE,
    text_color=TEXT_PRIMARY,
    anchor="w",
)
title_label.pack(side=tk.LEFT, padx=(4, 8))

# ----- Card: Key + Usage -----
info_card = ctk.CTkFrame(
    root, fg_color=CARD, border_width=1, border_color=CARD_BORDER, corner_radius=12,
)
info_card.pack(fill="x", padx=16, pady=(8, 6))

info_inner = ctk.CTkFrame(info_card, fg_color="transparent")
info_inner.pack(fill="x", padx=14, pady=12)

key_label = ctk.CTkLabel(info_inner, text="🔑 Loading…", font=FONT_BODY,
                         text_color=TEXT_PRIMARY, anchor="w")
key_label.pack(fill="x")

progress_var = tk.DoubleVar(value=0)
# Track the *displayed* progress so we can smooth-animate between ticks.
_displayed_pct = [0.0]
_progress_bar = ctk.CTkProgressBar(
    info_inner, variable=progress_var, height=10,
    progress_color="#10B981", fg_color=CARD_BORDER,
    corner_radius=5,
)
_progress_bar.pack(fill="x", pady=10)


def _hex_to_rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r, g, b):
    return f"#{int(r):02X}{int(g):02X}{int(b):02X}"


def _lerp_color(c1, c2, t):
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def _color_for_pct(pct):
    """Color shifts green → yellow → orange → red across 0..100%."""
    pct = max(0, min(100, pct))
    if pct < 50:
        return _lerp_color("#10B981", "#FBBF24", pct / 50.0)
    if pct < 80:
        return _lerp_color("#FBBF24", "#FB923C", (pct - 50) / 30.0)
    return _lerp_color("#FB923C", "#EF4444", (pct - 80) / 20.0)


def _animate_progress(target_pct, steps=8, interval=30):
    """Smoothly walk displayed_pct toward target_pct with color matching each step."""
    start = _displayed_pct[0]
    target = max(0.0, min(100.0, target_pct))
    if _root_ref is None:
        return
    try:
        if not _root_ref.winfo_exists():
            return
    except Exception:
        return

    if steps <= 0 or abs(target - start) < 0.1:
        _displayed_pct[0] = target
        try:
            progress_var.set(target)
            _progress_bar.configure(progress_color=_color_for_pct(target))
        except Exception:
            pass
        return

    delta = (target - start) / steps
    def _step(i=0):
        if i >= steps:
            _displayed_pct[0] = target
            try:
                progress_var.set(target)
                _progress_bar.configure(progress_color=_color_for_pct(target))
            except Exception:
                pass
            return
        _displayed_pct[0] = start + delta * i
        try:
            progress_var.set(_displayed_pct[0])
            _progress_bar.configure(progress_color=_color_for_pct(_displayed_pct[0]))
        except Exception:
            return
        if _root_ref is not None:
            try:
                _root_ref.after(interval, lambda: _step(i + 1))
            except Exception:
                pass
    _step(0)


def _pulse_progress(enabled):
    """Toggle a soft pulse animation: ramp 0.85..1.0 alpha on the bar's color.
    Only used when usage is at a critical level."""
    if not enabled:
        try:
            _progress_bar.configure(progress_color=_color_for_pct(_displayed_pct[0]))
        except Exception:
            pass
        return
    if _root_ref is None:
        return
    try:
        if not _root_ref.winfo_exists():
            return
    except Exception:
        return

    base = _color_for_pct(_displayed_pct[0])

    def _pulse_step(phase=0):
        if _root_ref is None:
            return
        try:
            if not _root_ref.winfo_exists():
                return
        except Exception:
            return
        if not is_critical_threshold:
            return
        # Phase 0..1..0 — fade bar slightly between full saturation and dimmed.
        t = (1 - abs(2 * phase - 1))
        dimmed = _lerp_color(base, "#7F1D1D", 1 - t)
        try:
            _progress_bar.configure(progress_color=dimmed)
        except Exception:
            return
        # Schedule next phase (0..1 over 800ms).
        if _root_ref is not None:
            try:
                _root_ref.after(40, lambda: _pulse_step((phase + 0.05) % 1.0))
            except Exception:
                pass
    _pulse_step(0)


# ===================== NETWORK MONITOR =====================

# Network monitor state — can be toggled off by the user
_net_monitor_enabled = True
_net_monitor = network_monitor.NetworkMonitor(interval=60)


def _on_network_update(snapshot):
    """Called from background thread with fresh network data."""
    def _update():
        if _root_ref is None:
            return
        try:
            if not _root_ref.winfo_exists():
                return
        except Exception:
            return
        try:
            render_network_card(snapshot)
        except Exception as e:
            print(f"[net] render error: {e}")
    try:
        _root_ref.after(0, _update)
    except Exception:
        pass

_net_monitor._callback = _on_network_update


def render_network_card(snapshot):
    """Refresh the network card widgets from a snapshot dict."""
    page = snapshot.get("page_latency", {})
    score = snapshot.get("quality_score", 0)
    improvement = snapshot.get("improvement_pct")

    try:
        page_ms = page.get("total_ms", 0)
        page_err = page.get("error")
        if page_err:
            page_latency_label.configure(
                text=f"🛰  Page Latency: {page_err}", text_color="#EF4444"
            )
        else:
            color = network_monitor.latency_color(page_ms)
            page_latency_label.configure(
                text=f"🛰  Page Latency: {page_ms} ms", text_color=color
            )
    except Exception:
        pass

    try:
        if score >= 80:
            emoji, lbl_color = "🟢", "#10B981"
        elif score >= 60:
            emoji, lbl_color = "🟡", "#FBBF24"
        elif score >= 35:
            emoji, lbl_color = "🟠", "#FB923C"
        else:
            emoji, lbl_color = "🔴", "#EF4444"
        score_label.configure(text=f"{emoji} Network Score: {score}/100", text_color=lbl_color)
    except Exception:
        pass

    try:
        if improvement is None:
            improvement_label.configure(text="⚡  Latency: not measured yet", text_color=TEXT_DIM)
        elif improvement > 0:
            improvement_label.configure(
                text=f"⚡  Latency improved {improvement}% after Fix Latency",
                text_color="#10B981",
            )
        else:
            improvement_label.configure(
                text="⚡  Latency: no improvement detected", text_color=TEXT_DIM
            )
    except Exception:
        pass


def open_dashboard_browser():
    log("Opening Dashboard in browser...", "INFO")
    try:
        _net_monitor.open_dashboard()
    except Exception as e:
        log(f"Could not open dashboard: {e}", "ERROR")


def open_status_browser():
    log("Opening Status page in browser...", "INFO")
    try:
        _net_monitor.open_status_page()
    except Exception as e:
        log(f"Could not open status page: {e}", "ERROR")


def test_speed_now():
    """Run a manual speed check on background thread, update UI."""
    log("Running speed test...", "INFO")
    set_status("Testing speed...", "blue")
    def _worker():
        try:
            _net_monitor.run_check()
            snap = _net_monitor._snapshot()
            ms = snap["page_latency"].get("total_ms", 0)
            err = snap["page_latency"].get("error")
            score = snap["quality_score"]
            if err:
                log(f"Speed test failed: {err}", "ERROR")
                set_status("✗ Speed test failed", "red")
            else:
                log(f"Speed test: {ms}ms (score {score}/100)", "OK")
                set_status(f"✓ Speed: {ms}ms (score {score})", "green")
        except Exception as e:
            log(f"Speed test error: {e}", "ERROR")
            set_status("✗ Speed test error", "red")
    threading.Thread(target=_worker, daemon=True).start()


def toggle_network_monitor():
    """Toggle the network monitor on/off. Button label reflects state."""
    global _net_monitor_enabled
    if _net_monitor_enabled:
        _net_monitor_enabled = False
        _net_monitor.stop()
        log("Network monitor disabled — activity log is the only output", "WARN")
        set_status("Network monitor OFF", "gray")
        try:
            net_toggle_btn.configure(text="Enable Net Monitor", fg_color="#10B981")
            # Reset network card labels to 'disabled'
            page_latency_label.configure(text="🛰  Page Latency: (disabled)", text_color=TEXT_DIM)
            score_label.configure(text="Network Score: —", text_color=TEXT_DIM)
            improvement_label.configure(text="⚡  Latency: monitor off", text_color=TEXT_DIM)
        except Exception:
            pass
    else:
        _net_monitor_enabled = True
        _net_monitor.start()
        log("Network monitor enabled", "OK")
        set_status("Network monitor ON", "green")
        try:
            net_toggle_btn.configure(text="Disable Net Monitor", fg_color="#374151")
        except Exception:
            pass


is_critical_threshold = False
# End of progress bar / animation helpers

usage_label = ctk.CTkLabel(info_inner, text="📊 …", font=FONT_SMALL,
                           text_color=TEXT_DIM, anchor="w")
usage_label.pack(fill="x")

remaining_label = ctk.CTkLabel(info_inner, text="🟢 Remaining: …",
                               font=FONT_SMALL, text_color="#10B981", anchor="w")
remaining_label.pack(fill="x", pady=(2, 0))

# Service health row inside the same card
health_row = ctk.CTkFrame(info_inner, fg_color="transparent")
health_row.pack(fill="x", pady=(10, 0))

gw_status_label = ctk.CTkLabel(health_row, text="● Gateway: …",
                               font=FONT_SMALL, text_color="#10B981", anchor="w")
gw_status_label.pack(side=tk.LEFT, padx=(0, 16))
kb_status_label = ctk.CTkLabel(health_row, text="● KeyBinder: …",
                               font=FONT_SMALL, text_color="#10B981", anchor="w")
kb_status_label.pack(side=tk.LEFT)

# ----- Buttons row -----
btn_frame = ctk.CTkFrame(root, fg_color="transparent")
btn_frame.pack(pady=10)

# ----- Phase 5: Key Poller sub-card -----
# Auto-pull the latest key from Hunter/coinbase/polygon on a 5-second cadence
# (runs pull_latest_key.py as a background subprocess). User can toggle
# auto-pulling on/off and trigger a one-shot pull from here.
poller_card = ctk.CTkFrame(
    root, fg_color=CARD, border_width=1, border_color=CARD_BORDER, corner_radius=10,
)
poller_card.pack(fill="x", padx=16, pady=(6, 4))

poller_inner = ctk.CTkFrame(poller_card, fg_color="transparent")
poller_inner.pack(fill="x", padx=14, pady=10)

# Row 1: title + status text (single line, fills width)
poller_title_row = ctk.CTkFrame(poller_inner, fg_color="transparent")
poller_title_row.pack(fill="x")
ctk.CTkLabel(
    poller_title_row, text="🔑  Auto Key Poller",
    font=FONT_SECTION, text_color=TEXT_PRIMARY, anchor="w",
).pack(side=tk.LEFT)

key_poller_polling_var = tk.BooleanVar(value=bool(config.get("auto_key_poll_enabled", False)))
key_poller_status_var = tk.StringVar(value="Idle (off)")

ctk.CTkLabel(
    poller_title_row, textvariable=key_poller_status_var,
    font=FONT_SMALL, text_color=TEXT_DIM, anchor="e",
).pack(side=tk.RIGHT)

# Row 2: checkbox + Pull Now + last-pull label
poller_controls_row = ctk.CTkFrame(poller_inner, fg_color="transparent")
poller_controls_row.pack(fill="x", pady=(8, 0))

def _on_auto_poll_toggle():
    """Persist the auto-poll toggle and tell the poller worker."""
    new_state = bool(key_poller_polling_var.get())
    config["auto_key_poll_enabled"] = new_state
    save_config(config)
    try:
        if new_state:
            key_poller_instance.start()
            log("Key poller started (5s cadence)", "OK")
        else:
            key_poller_instance.stop(join_timeout=2.0)
            log("Key poller stopped", "INFO")
    except Exception as e:
        log(f"Key poller toggle error: {e}", "ERROR")

# We need a custom checkbox row to put checkbox + buttons on one row.
# Use a grid: column 0 = checkbox, col 1 = Pull Now, col 2 = last info.
poller_controls_row.grid_columnconfigure(0, weight=0)
poller_controls_row.grid_columnconfigure(1, weight=0)
poller_controls_row.grid_columnconfigure(2, weight=1)

auto_poll_checkbox = ctk.CTkCheckBox(
    poller_controls_row, text="Auto-pull every 5s",
    variable=key_poller_polling_var,
    command=_on_auto_poll_toggle,
    font=FONT_SMALL, checkbox_width=18, checkbox_height=18, corner_radius=4,
)
auto_poll_checkbox.grid(row=0, column=0, sticky="w", padx=(0, 10))

def _pull_now():
    """Run a single pull on demand; show feedback in the status label.

    Tk is not thread-safe — only mutate key_poller_status_var / key_poller_last_var
    on the Tk thread, via root.after().
    """
    log("Manual key pull requested…", "INFO")
    key_poller_status_var.set("Pulling…")

    def _worker():
        ok = False
        err = None
        try:
            ok = key_poller_instance.run_once_now()
        except Exception as e:
            err = e
        # Marshal UI updates back to the Tk thread
        def _refresh_ui():
            snap = key_poller_instance.snapshot()
            if err is not None:
                key_poller_status_var.set(f"Error: {err}")
                log(f"Manual pull error: {err}", "ERROR")
            elif ok:
                key_poller_status_var.set("Idle (last pull OK)")
                log("Manual pull complete", "OK")
            else:
                key_poller_status_var.set("Idle (no change)")
                log("Manual pull: no change or failed", "WARN")
            # Update last-pull timestamp — PollerSnapshot has no time field,
            # so record wall-clock time here.
            key_poller_last_var.set(
                f"Last pull: {datetime.now().strftime('%H:%M:%S')}"
            )
        try:
            _root_ref.after(0, _refresh_ui)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()

pull_now_btn = ctk.CTkButton(
    poller_controls_row, text="🔄  Pull Now", command=_pull_now,
    fg_color="#374151", hover_color="#4B5563",
    text_color=TEXT_PRIMARY, font=FONT_SMALL,
    corner_radius=6, height=28, width=110,
)
pull_now_btn.grid(row=0, column=1, sticky="w", padx=(0, 8))

key_poller_last_var = tk.StringVar(value="Last pull: —")
ctk.CTkLabel(
    poller_controls_row, textvariable=key_poller_last_var,
    font=ctk.CTkFont(size=10), text_color=TEXT_DIM, anchor="w",
).grid(row=0, column=2, sticky="ew", padx=(6, 0))

# Reconcile poller state with the persisted config on startup
try:
    if key_poller_polling_var.get():
        key_poller_instance.start()
        log("Key poller auto-started (restored from config)", "OK")
except Exception as e:
    log(f"Key poller auto-start error: {e}", "WARN")


# ----- Quick-link buttons row -----
link_row = ctk.CTkFrame(root, fg_color="transparent")
link_row.pack(pady=(0, 2))

ctk.CTkButton(link_row, text="🌐  Dashboard", command=open_dashboard_browser,
              fg_color="#374151", hover_color="#4B5563",
              text_color=TEXT_PRIMARY, font=FONT_SMALL,
              corner_radius=6, height=26, width=140).pack(side=tk.LEFT, padx=4)
ctk.CTkButton(link_row, text="📡  Status Page", command=open_status_browser,
              fg_color="#374151", hover_color="#4B5563",
              text_color=TEXT_PRIMARY, font=FONT_SMALL,
              corner_radius=6, height=26, width=140).pack(side=tk.LEFT, padx=4)

# ----- Network card -----
net_card = ctk.CTkFrame(
    root, fg_color=CARD, border_width=1, border_color=CARD_BORDER, corner_radius=10,
)
net_card.pack(fill="x", padx=16, pady=(0, 6))

net_inner = ctk.CTkFrame(net_card, fg_color="transparent")
net_inner.pack(fill="x", padx=14, pady=10)

# Header row with score + test button
net_header = ctk.CTkFrame(net_inner, fg_color="transparent")
net_header.pack(fill="x", pady=(0, 6))

score_label = ctk.CTkLabel(net_header, text="🟢 Network Score: —",
                           font=FONT_SMALL, text_color=TEXT_DIM, anchor="w")
score_label.pack(side=tk.LEFT)

# Toggle button: start OFF, user enables when they want it
net_toggle_btn = ctk.CTkButton(net_header, text="Enable Net Monitor", command=toggle_network_monitor,
              fg_color="#10B981", hover_color="#059669",
              text_color="white", font=ctk.CTkFont(size=10),
              corner_radius=6, height=24, width=115)
net_toggle_btn.pack(side=tk.RIGHT, padx=(4, 0))

ctk.CTkButton(net_header, text="🧪 Test Speed", command=test_speed_now,
              fg_color="#374151", hover_color="#4B5563",
              text_color=TEXT_PRIMARY, font=FONT_SMALL,
              corner_radius=6, height=24, width=100).pack(side=tk.RIGHT)

# Page latency row
page_latency_label = ctk.CTkLabel(net_inner, text="🛰  Page Latency: —",
                                   font=FONT_SMALL, text_color=TEXT_DIM, anchor="w")
page_latency_label.pack(fill="x", pady=1)

# Improvement row
improvement_label = ctk.CTkLabel(net_inner, text="⚡  Latency: not measured yet",
                                  font=FONT_SMALL, text_color=TEXT_DIM, anchor="w")
improvement_label.pack(fill="x", pady=1)

# Model probes header + button
ctk.CTkLabel(net_inner, text="Model Health (click to probe available models)",
             font=ctk.CTkFont(size=10, weight="bold"),
             text_color=TEXT_DIM, anchor="w").pack(fill="x", pady=(6, 2))

model_probe_label = ctk.CTkLabel(
    net_inner,
    text="  No models probed yet — click '🧪 Test Models'",
    font=FONT_SMALL, text_color=TEXT_DIM, anchor="w",
)
model_probe_label.pack(fill="x", pady=(0, 2))

# (Copy Fastest button removed — replaced by Optimize Speed below)


def _test_models_worker():
    """Probe relevant models, rank by latency, show clean list.
    Excludes 'thinking' variants — they add 10-30s reasoning overhead."""
    log("Testing models...", "INFO")
    set_status("Testing models...", "blue")

    def _worker():
        try:
            model_ids = network_monitor.fetch_available_models()
            if not model_ids:
                model_probe_label.configure(
                    text="  Gateway offline — cannot list models",
                    text_color="#EF4444",
                )
                log("Model test: gateway offline", "WARN")
                set_status("Gateway offline", "red")
                return

            # Only Claude family (opus/sonnet/haiku/fable). Skip 'thinking' models
            # which have built-in reasoning overhead that inflates latency.
            keywords = ["claude-fable", "claude-opus", "claude-sonnet", "claude-haiku"]
            exclude = ["thinking", "agent"]
            relevant = []
            for m in model_ids:
                ml = m.lower()
                if any(k in ml for k in keywords) and not any(x in ml for x in exclude):
                    relevant.append(m)

            if not relevant:
                model_probe_label.configure(
                    text="  No Claude models found in gateway",
                    text_color="#EF4444",
                )
                log("Model test: no Claude models", "WARN")
                set_status("No Claude models", "orange")
                return

            log(f"Probing {len(relevant)} models: {', '.join(relevant)}", "INFO")

            results = []
            for mid in relevant:
                ms, status, code = network_monitor.probe_model(mid)
                results.append((mid, ms, status, code))

            # Sort by latency (fastest first), failed at bottom
            working = sorted(
                [(mid, ms, s, c) for mid, ms, s, c in results if s == "ok"],
                key=lambda x: x[1],
            )
            failed = [(mid, ms, s, c) for mid, ms, s, c in results if s != "ok"]

            # Clean display: just model name + ms, no bars
            lines = []
            for mid, ms, status, code in working:
                lines.append(f"  ✅ {mid:<30} {ms} ms")
            for mid, ms, status, code in failed:
                lines.append(f"  ❌ {mid:<30} {status}")

            fastest_ms = working[0][1] if working else 0
            fastest_name = working[0][0] if working else "none"

            def _update():
                try:
                    color = network_monitor.latency_color(fastest_ms) if working else "#EF4444"
                    model_probe_label.configure(text="\n".join(lines), text_color=color)
                except Exception:
                    pass
            try:
                _root_ref.after(0, _update)
            except Exception:
                pass

            if working:
                log(f"Fastest: {fastest_name} ({fastest_ms}ms)", "OK")
                set_status(f"Fastest: {fastest_name} ({fastest_ms}ms)", "green")
            else:
                log("All models failed", "ERROR")
                set_status("All models failed", "red")

        except Exception as e:
            log(f"Model test error: {e}", "ERROR")
            set_status("Model test failed", "red")
    threading.Thread(target=_worker, daemon=True).start()


# Button must come AFTER the function definition
test_models_btn = ctk.CTkButton(
    net_inner, text="🧪 Test Models", command=_test_models_worker,
    fg_color="#374151", hover_color="#4B5563",
    text_color=TEXT_PRIMARY, font=FONT_SMALL,
    corner_radius=6, height=24, width=140,
)
test_models_btn.pack(anchor="w", pady=(2, 0))

# === Speed Optimizer — what YOU can do to fix model latency ===
# Configure the gateway with settings that reduce latency:
# - Stream responses (first token arrives faster)
# - Lower default max_tokens (less to generate)
# - Drop slow models from rotation (if you've tested them)

def _apply_speed_optimization_worker():
    """Background worker for speed optimization. Top-level so 'global gateway_proc'
    works correctly (nested function would cause UnboundLocalError).
    Flips prompt caching on, writes the flag file, restarts the gateway."""
    global gateway_proc
    try:
        # Enable prompt caching in the gateway by writing the flag file it reads
        flag_path = os.path.join(BASE_DIR, "prompt_cache_enabled.txt")
        with open(flag_path, "w") as f:
            f.write("1")
        log("Prompt caching flag enabled", "OK")

        # Also write speed hint config
        hint = {
            "stream_preferred": True,
            "max_tokens_cap": 2048,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            with open(os.path.join(BASE_DIR, "speed_hint.json"), "w") as f:
                json.dump(hint, f, indent=2)
        except Exception as e:
            log(f"Could not write speed hint: {e}", "WARN")

        # Restart gateway so the prompt caching flag is picked up
        log("Restarting gateway with prompt caching...", "INFO")
        set_status("Restarting gateway...", "blue")
        if is_process_alive(gateway_proc):
            try:
                gateway_proc.terminate()
                gateway_proc.wait(timeout=5)
            except Exception:
                try: gateway_proc.kill()
                except Exception: pass
            gateway_proc = None

        time.sleep(1)
        start_services()
        time.sleep(3)

        # Verify with a fresh latency test
        _net_monitor.run_check()
        snap = _net_monitor.get_snapshot()
        new_ms = snap["page_latency"].get("total_ms", 0)
        err = snap["page_latency"].get("error")

        if err:
            log(f"Post-optimize measurement error: {err}", "WARN")
            set_status("Speed config applied", "green")
        else:
            log(f"✓ Speed optimization active — page latency: {new_ms}ms", "OK")
            set_status(f"✓ Optimized: {new_ms}ms", "green")

    except Exception as e:
        log(f"Speed optimization failed: {e}", "ERROR")
        set_status("Optimization failed", "red")


def apply_speed_optimization():
    """Launch speed optimization on a background thread."""
    threading.Thread(target=_apply_speed_optimization_worker, daemon=True).start()


optimize_btn = ctk.CTkButton(
    net_inner, text="⚡ Optimize Speed", command=apply_speed_optimization,
    fg_color="#F59E0B", hover_color="#D97706",
    text_color="#FFFFFF", font=FONT_SMALL,
    corner_radius=6, height=24, width=140,
)
optimize_btn.pack(anchor="w", pady=(2, 0))

# ===================== BUTTONS ROW =====================
def _fix_latency_worker():
    """Background worker for Fix Latency. Runs baseline → restart → post-fix."""
    global gateway_proc

    # Step 1: baseline measurement
    try:
        log("Fix Latency: measuring baseline latency...", "INFO")
        set_status("Measuring baseline...", "blue")
        _net_monitor.run_check()
        _net_monitor.set_baseline()
        before = _net_monitor.get_snapshot().get("before_fix_ms", 0)
        log(f"Baseline recorded: {before} ms", "OK")
    except Exception as e:
        log(f"Baseline measurement error: {e}", "WARN")

    # Step 2: switch to low-latency gateway
    config["use_low_latency_gateway"] = True
    save_config(config)
    relaunch_dependent_scripts()
    log("Fix Latency: switching to low-latency gateway...", "INFO")
    set_status("Fixing latency...", "blue")

    # Step 3: kill old gateway, start new
    if is_process_alive(gateway_proc):
        log("Fix Latency: terminating stale Gateway process...", "WARN")
        try:
            gateway_proc.terminate()
            gateway_proc.wait(timeout=5)
        except Exception:
            try:
                gateway_proc.kill()
            except Exception:
                pass
        gateway_proc = None

    time.sleep(2)
    start_services()
    time.sleep(3)

    # Step 4: post-fix measurement
    try:
        _net_monitor.run_check()
        _net_monitor.record_after_fix()
        snap = _net_monitor.get_snapshot()
        after = snap.get("after_fix_ms", 0)
        improvement = snap.get("improvement_pct")
        if improvement is not None and improvement > 0:
            log(f"⚡ Latency improved {improvement}% ({before} → {after} ms)", "OK")
            set_status(f"✓ Latency improved {improvement}%", "green")
        else:
            log(f"Post-fix latency: {after}ms (baseline {before}ms)", "INFO")
    except Exception as e:
        log(f"Post-fix measurement error: {e}", "WARN")


def fix_latency():
    """Force low-latency gateway mode and restart the Gateway process.
    Launches _fix_latency_worker on a background thread."""
    threading.Thread(target=_fix_latency_worker, daemon=True).start()


fix_latency_btn = ctk.CTkButton(
    btn_frame, text="⚡ Fix Latency", command=fix_latency,
    fg_color="#F59E0B", hover_color="#D97706",
    text_color="#FFFFFF", font=FONT_SMALL,
    corner_radius=6, height=28, width=125,
)
fix_latency_btn.pack(side=tk.LEFT, padx=4)

# Primary action button (rotate) — accent blue with subtle distinction.
rotate_btn = ctk.CTkButton(
    btn_frame, text="🔄 Rotate Now", command=rotate_key,
    fg_color=ACCENT, hover_color="#2563EB",
    text_color="white", font=FONT_SMALL,
    corner_radius=6, height=28, width=125,
)
rotate_btn.pack(side=tk.LEFT, padx=2)

refresh_btn = ctk.CTkButton(
    btn_frame, text="🔃 Refresh", command=refresh_token_usage,
    fg_color="#374151", hover_color="#4B5563",
    text_color=TEXT_PRIMARY, font=FONT_SMALL,
    corner_radius=6, height=28, width=95,
)
refresh_btn.pack(side=tk.LEFT, padx=2)

restart_btn = ctk.CTkButton(
    btn_frame, text="↻ Restart", command=restart_services,
    fg_color="#374151", hover_color="#4B5563",
    text_color=TEXT_PRIMARY, font=FONT_SMALL,
    corner_radius=6, height=28, width=95,
)
restart_btn.pack(side=tk.LEFT, padx=2)

# Stop handler with optional confirmation (Phase 1 fix).
def _stop_all_with_confirm():
    if config.get("confirm_stop", True):
        ok = messagebox.askyesno(
            "Stop all services?",
            "This will terminate the Gateway and KeyBinder.\n\nContinue?",
            parent=root,
        )
        if not ok:
            return
    stop_services(tray_too=False, exit_app=True)

# Destructive — muted red so it doesn't shout, but still distinct.
stop_btn = ctk.CTkButton(
    btn_frame, text="⏹ Stop All", command=_stop_all_with_confirm,
    fg_color="#7F1D1D", hover_color="#991B1B",
    text_color="#FCA5A5", font=FONT_SMALL,
    corner_radius=6, height=28, width=95,
)
stop_btn.pack(side=tk.LEFT, padx=2)

# ----- Status bar -----
status_label = ctk.CTkLabel(root, text="Starting services…",
                            font=FONT_SMALL, text_color=TEXT_DIM, anchor="w")
status_label.pack(fill="x", padx=20, pady=(0, 4))

# ----- Log card -----
log_card = ctk.CTkFrame(
    root, fg_color=CARD, border_width=1, border_color=CARD_BORDER, corner_radius=12,
)
log_card.pack(fill="both", expand=True, padx=16, pady=(0, 12))

# Log header inside the card
log_header = ctk.CTkFrame(log_card, fg_color="transparent")
log_header.pack(fill="x", padx=14, pady=(10, 4))
ctk.CTkLabel(log_header, text="📜  Activity Log", font=FONT_SECTION,
             text_color=TEXT_PRIMARY, anchor="w").pack(side=tk.LEFT)
ctk.CTkButton(log_header, text="Clear", command=lambda: clear_log(),
               width=60, height=24,
               fg_color="#374151", hover_color="#4B5563",
               text_color=TEXT_PRIMARY, font=FONT_SMALL,
               corner_radius=6).pack(side=tk.RIGHT)

# Text widget — wrapped in a CTkFrame so it adapts to dark theme.
# NOTE: tk.Text (not CTkTextbox) because we need tag_config for color-coded levels.
log_text_frame = ctk.CTkFrame(log_card, fg_color=CARD_BORDER, corner_radius=8)
log_text_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

log_text = tk.Text(
    log_text_frame, height=10, font=("Cascadia Mono", 10),
    state="disabled", bg="#0A0A0C", fg="#E5E7EB",
    insertbackground="#E5E7EB",
    wrap="word", relief="flat", padx=8, pady=8,
    borderwidth=0, highlightthickness=0,
)
log_scroll = ctk.CTkScrollbar(log_text_frame, orientation="vertical", command=log_text.yview,
                              button_color=CARD_BORDER, button_hover_color=ACCENT)
log_text.configure(yscrollcommand=log_scroll.set)
log_scroll.pack(side="right", fill="y", padx=(0, 4), pady=4)
log_text.pack(side="left", fill="both", expand=True, padx=4, pady=4)

log_tags_setup()

def clear_log():
    log_text.config(state="normal")
    log_text.delete("1.0", "end")
    log_text.config(state="disabled")
    log_buffer.clear()

# ----- Window close = hide to tray (services keep running) -----
def on_close():
    _save_geometry()
    hide_to_tray()

root.protocol("WM_DELETE_WINDOW", on_close)

# Save geometry whenever the user moves/resizes the window (debounced
# via a 1-second cooldown so we don't write to disk on every pixel).
_last_geom_save = [0.0]

def _save_geometry():
    try:
        geom = root.geometry()
        if geom and geom != config.get("window_geometry"):
            config["window_geometry"] = geom
            save_config(config)
    except Exception:
        pass

def _on_configure(event):
    if event.widget is not root:
        return
    now = time.time()
    if now - _last_geom_save[0] > 1.0:
        _last_geom_save[0] = now
        _save_geometry()

root.bind("<Configure>", _on_configure)

# ----- Start everything -----
log("Opus Control Panel starting…", "INFO")
log(f"Base directory: {BASE_DIR}", "INFO")
log(f"Token limit: {config['total_limit']:,}", "INFO")
log(f"Auto-restart: {config.get('auto_restart', True)}", "INFO")

threading.Thread(target=start_services, daemon=True).start()
threading.Thread(target=create_tray_icon, daemon=True).start()

# Network monitor does NOT auto-start — user enables it via the toggle button
# in the network card. This avoids any background requests on app launch and
# keeps the UI fully responsive.

root.after(500, update_display)

try:
    root.mainloop()
except KeyboardInterrupt:
    pass
finally:
    try:
        stop_services(tray_too=True, exit_app=False)
    except Exception:
        pass
    sys.exit(0)
