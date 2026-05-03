"""
execution/sandbox.py

Sandboxed code execution layer.  All execution uses subprocess + timeout.
Never eval() or exec() inside this process.

Security model:
- Python: isolated subprocess, no network access flag not enforced at OS level
  (rely on timeout + output capping instead; network isolation requires a container).
- Shell: whitelist-only; the first token of the command must be in SHELL_WHITELIST.
- Swift / Java: require compiler presence; checked before running.
- All stdout/stderr are captured and truncated to MAX_OUTPUT_BYTES.
- All runs are logged to execution_logs table.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Optional

from config import EXECUTION_TIMEOUT_DEFAULT, RENDERS_DIR, SHELL_WHITELIST
from db.queries import insert_execution_log

MAX_OUTPUT_BYTES = 32_768  # 32 KB hard cap on captured output


def _run(
    cmd: list[str],
    *,
    stdin_data: Optional[str] = None,
    timeout: int = EXECUTION_TIMEOUT_DEFAULT,
    cwd: Optional[str] = None,
) -> dict:
    """
    Internal runner.  Returns a normalized result dict.
    Never raises — all errors are surfaced in the returned dict.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        duration_ms = (time.monotonic() - start) * 1000
        stdout = proc.stdout[:MAX_OUTPUT_BYTES] if proc.stdout else ""
        stderr = proc.stderr[:MAX_OUTPUT_BYTES] if proc.stderr else ""
        return {
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": round(duration_ms, 1),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        duration_ms = (time.monotonic() - start) * 1000
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"[Timed out after {timeout}s]",
            "duration_ms": round(duration_ms, 1),
            "timed_out": True,
        }
    except FileNotFoundError as e:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"[Command not found: {e}]",
            "duration_ms": 0.0,
            "timed_out": False,
        }
    except Exception as e:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"[Execution error: {e}]",
            "duration_ms": 0.0,
            "timed_out": False,
        }


# ── Python ────────────────────────────────────────────────────────────────────

def run_python(
    code: str,
    timeout: int = EXECUTION_TIMEOUT_DEFAULT,
    project_id: Optional[str] = None,
) -> dict:
    """Execute Python code in a subprocess.  Returns stdout/stderr/exit_code."""
    result = _run(
        [shutil.which("python3") or "python3", "-c", code],
        timeout=timeout,
    )
    insert_execution_log(
        language="python",
        code_input=code[:4096],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        duration_ms=result["duration_ms"],
        project_id=project_id,
    )
    return result


def run_python_file(
    code: str,
    timeout: int = EXECUTION_TIMEOUT_DEFAULT,
    project_id: Optional[str] = None,
) -> dict:
    """Write code to a temp file and execute it (avoids shell-escaping issues)."""
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = f.name
    try:
        result = _run(
            [shutil.which("python3") or "python3", tmp_path],
            timeout=timeout,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    insert_execution_log(
        language="python",
        code_input=code[:4096],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        duration_ms=result["duration_ms"],
        project_id=project_id,
    )
    return result


# ── Shell ─────────────────────────────────────────────────────────────────────

def run_shell(
    command: str,
    timeout: int = EXECUTION_TIMEOUT_DEFAULT,
    project_id: Optional[str] = None,
) -> dict:
    """
    Execute a shell command.  Only the FIRST token is whitelisted.
    Arguments after the first token are passed through but the base command
    must be in SHELL_WHITELIST.
    """
    tokens = command.strip().split()
    if not tokens:
        return {"exit_code": -1, "stdout": "", "stderr": "[Empty command]", "duration_ms": 0.0, "timed_out": False}

    base_cmd = os.path.basename(tokens[0])
    if base_cmd not in SHELL_WHITELIST:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"[Command '{base_cmd}' is not in the shell whitelist. Allowed: {sorted(SHELL_WHITELIST)}]",
            "duration_ms": 0.0,
            "timed_out": False,
        }

    result = _run(tokens, timeout=timeout)
    insert_execution_log(
        language="shell",
        code_input=command[:4096],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        duration_ms=result["duration_ms"],
        project_id=project_id,
    )
    return result


# ── Swift ─────────────────────────────────────────────────────────────────────

def run_swift(
    code: str,
    timeout: int = EXECUTION_TIMEOUT_DEFAULT * 3,
    project_id: Optional[str] = None,
) -> dict:
    """Compile and run a Swift snippet.  Requires swift compiler in PATH."""
    swift_bin = shutil.which("swift")
    if not swift_bin:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "[swift not found in PATH]",
            "duration_ms": 0.0,
            "timed_out": False,
        }

    with tempfile.NamedTemporaryFile(
        suffix=".swift", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = f.name
    try:
        result = _run([swift_bin, tmp_path], timeout=timeout)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    insert_execution_log(
        language="swift",
        code_input=code[:4096],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        duration_ms=result["duration_ms"],
        project_id=project_id,
    )
    return result


# ── Java ──────────────────────────────────────────────────────────────────────

def run_java(
    code: str,
    class_name: str = "Main",
    timeout: int = EXECUTION_TIMEOUT_DEFAULT * 3,
    project_id: Optional[str] = None,
) -> dict:
    """
    Compile and run a Java class.
    The public class in `code` must match `class_name`.
    Requires javac + java in PATH.
    """
    javac_bin = shutil.which("javac")
    java_bin = shutil.which("java")
    if not javac_bin or not java_bin:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "[javac or java not found in PATH]",
            "duration_ms": 0.0,
            "timed_out": False,
        }

    tmpdir = tempfile.mkdtemp()
    src_path = os.path.join(tmpdir, f"{class_name}.java")
    try:
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(code)

        compile_result = _run([javac_bin, src_path], timeout=30, cwd=tmpdir)
        if compile_result["exit_code"] != 0:
            insert_execution_log(
                language="java",
                code_input=code[:4096],
                stdout=compile_result["stdout"],
                stderr=compile_result["stderr"],
                exit_code=compile_result["exit_code"],
                duration_ms=compile_result["duration_ms"],
                project_id=project_id,
            )
            return compile_result

        run_result = _run([java_bin, class_name], timeout=timeout, cwd=tmpdir)
    finally:
        import shutil as _shutil
        _shutil.rmtree(tmpdir, ignore_errors=True)

    insert_execution_log(
        language="java",
        code_input=code[:4096],
        stdout=run_result["stdout"],
        stderr=run_result["stderr"],
        exit_code=run_result["exit_code"],
        duration_ms=run_result["duration_ms"],
        project_id=project_id,
    )
    return run_result


# ── HTML / CSS / JS render ────────────────────────────────────────────────────

def render_html(
    html: str,
    filename: Optional[str] = None,
) -> dict:
    """
    Write HTML (may include embedded CSS and JS) to a file in the renders dir.
    Returns the absolute path and a file:// URL for local preview.
    """
    os.makedirs(RENDERS_DIR, exist_ok=True)
    fname = (filename or f"render_{uuid.uuid4().hex[:8]}") 
    if not fname.endswith(".html"):
        fname += ".html"
    # Prevent path traversal
    fname = os.path.basename(fname)
    out_path = os.path.join(RENDERS_DIR, fname)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        return {
            "written": True,
            "path": out_path,
            "url": f"file://{out_path}",
            "size_bytes": len(html.encode("utf-8")),
        }
    except OSError as e:
        return {"written": False, "error": str(e)}


# ── SQLite read-only query ────────────────────────────────────────────────────

def query_db(sql: str, params: Optional[list] = None) -> dict:
    """
    Execute a read-only SQL SELECT against holograim.db.
    Rejects any statement that is not a SELECT to protect the database.
    """
    cleaned = sql.strip().upper()
    if not cleaned.startswith("SELECT"):
        return {
            "error": "Only SELECT statements are permitted in query_db.",
            "rows": [],
        }
    from db.schema import get_connection
    try:
        with get_connection() as conn:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(sql, params or []).fetchall()
        return {"rows": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"error": str(e), "rows": []}


# ── Conda ─────────────────────────────────────────────────────────────────────

def conda_list_envs() -> dict:
    """List all Conda environments.  Returns names and paths."""
    conda_bin = shutil.which("conda")
    if not conda_bin:
        return {"error": "conda not found in PATH", "envs": []}
    result = _run([conda_bin, "env", "list", "--json"], timeout=20)
    if result["exit_code"] != 0:
        return {"error": result["stderr"], "envs": []}
    try:
        import json
        data = json.loads(result["stdout"])
        envs = [{"path": p, "name": os.path.basename(p)} for p in data.get("envs", [])]
        return {"envs": envs}
    except Exception as e:
        return {"error": f"Failed to parse conda output: {e}", "raw": result["stdout"]}


def conda_run_python(
    env_name: str,
    code: str,
    timeout: int = EXECUTION_TIMEOUT_DEFAULT,
    project_id: Optional[str] = None,
) -> dict:
    """
    Run Python code inside a specific Conda environment.
    `env_name` is the name (not path) of the environment.
    """
    conda_bin = shutil.which("conda")
    if not conda_bin:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "[conda not found in PATH]",
            "duration_ms": 0.0,
            "timed_out": False,
        }
    result = _run(
        [conda_bin, "run", "-n", env_name, "python3", "-c", code],
        timeout=timeout,
    )
    insert_execution_log(
        language=f"python@conda:{env_name}",
        code_input=code[:4096],
        stdout=result["stdout"],
        stderr=result["stderr"],
        exit_code=result["exit_code"],
        duration_ms=result["duration_ms"],
        project_id=project_id,
    )
    return result


# ── File I/O ──────────────────────────────────────────────────────────────────

def write_file(
    content: str,
    filename: str,
    subdirectory: str = "outputs",
) -> dict:
    """
    Write arbitrary text content to data/<subdirectory>/<filename>.
    Path traversal is prevented — the filename is stripped of any directory
    components.
    """
    from config import DATA_DIR
    safe_filename = os.path.basename(filename)
    if not safe_filename:
        return {"written": False, "error": "Invalid filename."}
    # Only allow safe subdirectory names
    safe_sub = "".join(c for c in subdirectory if c.isalnum() or c in ("_", "-"))
    out_dir = os.path.join(DATA_DIR, safe_sub or "outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, safe_filename)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "written": True,
            "path": out_path,
            "size_bytes": len(content.encode("utf-8")),
        }
    except OSError as e:
        return {"written": False, "error": str(e)}


def read_file_safe(filename: str, subdirectory: str = "outputs") -> dict:
    """
    Read a file from data/<subdirectory>/<filename>.
    Path traversal is prevented.
    """
    from config import DATA_DIR
    safe_filename = os.path.basename(filename)
    safe_sub = "".join(c for c in subdirectory if c.isalnum() or c in ("_", "-"))
    in_path = os.path.join(DATA_DIR, safe_sub or "outputs", safe_filename)
    if not os.path.exists(in_path):
        return {"found": False, "error": f"File not found: {in_path}"}
    try:
        with open(in_path, "r", encoding="utf-8") as f:
            content = f.read(MAX_OUTPUT_BYTES)
        return {"found": True, "content": content, "path": in_path}
    except OSError as e:
        return {"found": False, "error": str(e)}
