"""Reproduce the 'hangs with base64' report: spawn the MCP server in a
subprocess, send a real fill_and_save JSON-RPC call with template_b64,
measure how long each phase takes, surface where it stalls."""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

P = Path(__file__).parent
PROJECT_ROOT = P.parent
HWPX = next((PROJECT_ROOT / "output" / "templates").glob("*.hwpx"))


def main() -> int:
    raw = HWPX.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    print(f"template: {HWPX.name}  {len(raw):,} bytes -> {len(b64):,} base64 chars")

    py = sys.executable
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [py, "-m", "report_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, bufsize=0,
    )

    out_q: "queue.Queue[bytes]" = queue.Queue()

    def reader():
        while True:
            line = proc.stdout.readline()
            if not line:
                return
            out_q.put(line)

    threading.Thread(target=reader, daemon=True).start()

    def send(obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        t0 = time.perf_counter()
        proc.stdin.write(data)
        proc.stdin.flush()
        elapsed = time.perf_counter() - t0
        return elapsed, len(data)

    def recv(timeout: float):
        t0 = time.perf_counter()
        try:
            line = out_q.get(timeout=timeout)
        except queue.Empty:
            return None, time.perf_counter() - t0
        return json.loads(line.decode("utf-8")), time.perf_counter() - t0

    try:
        # initialize
        e, n = send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "probe-stdio-bytes", "version": "0"}},
        })
        print(f"[init] sent {n}B in {e*1000:.1f}ms")
        r, dt = recv(timeout=10)
        print(f"[init] recv in {dt*1000:.1f}ms  serverInfo={r and r.get('result', {}).get('serverInfo')}")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        time.sleep(0.05)

        # 1) list_template_targets via bytes (small response)
        e, n = send({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "list_template_targets",
                "arguments": {
                    "template_b64": b64,
                    "template_filename": HWPX.name,
                    "target_kinds": ["paragraph"],
                    "limit": 5,
                },
            },
        })
        print(f"[list_b64] sent {n:,}B (b64 payload {len(b64):,} chars) in {e*1000:.1f}ms")
        r, dt = recv(timeout=60)
        if r is None:
            print(f"[list_b64] TIMEOUT after {dt:.1f}s — server stuck reading large stdin")
            return 1
        print(f"[list_b64] recv in {dt*1000:.0f}ms  status={r.get('result', {}).get('structuredContent', {}).get('status')}")

        # 2) fill_and_save: bytes in + bytes out (largest payload both ways)
        targets_resp = r["result"]["structuredContent"]
        targets = targets_resp["targets"]
        # Find a real target by current_text so we get a real text_hash
        # First fetch full list
        e, n = send({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "list_template_targets",
                "arguments": {
                    "template_b64": b64, "template_filename": HWPX.name,
                    "target_kinds": ["paragraph"], "limit": 400,
                },
            },
        })
        full, dt = recv(timeout=60)
        if full is None:
            print(f"[list_full] TIMEOUT after {dt:.1f}s")
            return 1
        all_targets = full["result"]["structuredContent"]["targets"]
        date_t = next(t for t in all_targets if (t.get("current_text") or "").strip() == "2026. 03. 23.")
        print(f"[list_full] recv in {dt*1000:.0f}ms  total {len(all_targets)} targets, date target id={date_t['target_id']}")

        edits = [{
            "edit_type": "text", "target_kind": "paragraph",
            "target_id": date_t["target_id"],
            "expected_text_hash": date_t["text_hash"],
            "new_text": "2026. 05. 19.",
        }]
        t_send = time.perf_counter()
        e, n = send({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "fill_and_save",
                "arguments": {
                    "template_b64": b64,
                    "template_filename": HWPX.name,
                    "edits": edits,
                    "return_output_bytes": True,
                },
            },
        })
        print(f"[fill_b64] sent {n:,}B in {e*1000:.1f}ms (large stdin write)")
        r, dt = recv(timeout=120)
        if r is None:
            print(f"[fill_b64] TIMEOUT after {dt:.1f}s — this is the user's symptom")
            return 1
        sc = r["result"].get("structuredContent") or {}
        out_size = sc.get("output_size_bytes")
        b64_len = len(sc.get("output_b64") or "")
        print(f"[fill_b64] recv in {dt*1000:.0f}ms  status={sc.get('status')} "
              f"output={out_size:,}B (b64 {b64_len:,} chars)")
        print(f"[total roundtrip] send+process+recv = {(time.perf_counter() - t_send)*1000:.0f}ms")

    finally:
        stderr_data = b""
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            stderr_data = proc.stderr.read()
        except Exception:
            pass
        if stderr_data:
            print("\n--- server stderr (last 20 lines) ---")
            for line in stderr_data.decode("utf-8", errors="replace").splitlines()[-20:]:
                print("  " + line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
