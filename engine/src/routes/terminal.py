"""Terminal route: WebSocket-based PTY terminal."""

import asyncio
import json
import os
import platform
import threading
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.engine_state import state

router = APIRouter()


@router.websocket("/terminal")
async def terminal_ws(websocket: WebSocket, cwd: Optional[str] = None):
    """
    WebSocket terminal using ConPTY (pywinpty) on Windows, pty module on Linux/Mac.
    Full PTY support: backspace, arrow keys, tab completion, colours all work.
    Text messages: raw terminal input bytes (str).
    JSON messages: {"type":"resize","cols":N,"rows":N} to resize the PTY.
    """
    await websocket.accept()

    # Use explicitly passed cwd, fall back to workspace, then home dir
    if cwd and os.path.isdir(cwd):
        cwd = os.path.realpath(cwd)
    else:
        cwd = state.workspace_path or os.path.expanduser("~")
    loop = asyncio.get_event_loop()

    if platform.system() == "Windows":
        # ── Windows: use pywinpty ConPTY ──────────────────────────────────────
        try:
            from winpty import PtyProcess
        except ImportError:
            await websocket.send_text(
                "\r\n\x1b[31m[pywinpty not installed — run: pip install pywinpty]\x1b[0m\r\n"
            )
            await websocket.close()
            return

        try:
            pty_proc = PtyProcess.spawn("powershell.exe", dimensions=(24, 220), cwd=cwd)
        except Exception as e:
            await websocket.send_text(f"\r\n\x1b[31m[PTY failed to start: {e}]\x1b[0m\r\n")
            await websocket.close()
            return

        stop_event = asyncio.Event()

        def _read_pty():
            """Blocking PTY read — runs in a thread."""
            while not stop_event.is_set():
                try:
                    data = pty_proc.read(4096)
                    if data:
                        asyncio.run_coroutine_threadsafe(
                            websocket.send_text(data), loop
                        )
                except EOFError:
                    break
                except Exception:
                    break

        read_thread = threading.Thread(target=_read_pty, daemon=True)
        read_thread.start()

        try:
            while True:
                msg = await websocket.receive_text()
                # JSON → resize control message
                try:
                    ctrl = json.loads(msg)
                    if ctrl.get("type") == "resize":
                        pty_proc.setwinsize(
                            int(ctrl.get("rows", 24)),
                            int(ctrl.get("cols", 220)),
                        )
                    continue
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
                # Regular keystrokes → PTY stdin
                pty_proc.write(msg)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            stop_event.set()
            try:
                pty_proc.terminate(force=True)
            except Exception:
                pass

    else:
        # ── Linux / Mac: use pty module ───────────────────────────────────────
        import fcntl
        import pty as pty_mod
        import struct
        import termios

        master_fd, slave_fd = pty_mod.openpty()
        try:
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                cwd=cwd, close_fds=True,
            )
        except Exception as e:
            os.close(master_fd)
            os.close(slave_fd)
            await websocket.send_text(f"\r\n\x1b[31m[Shell failed: {e}]\x1b[0m\r\n")
            await websocket.close()
            return
        os.close(slave_fd)

        async def _read():
            while True:
                try:
                    data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                except Exception:
                    break

        async def _write():
            try:
                while True:
                    msg = await websocket.receive_text()
                    try:
                        ctrl = json.loads(msg)
                        if ctrl.get("type") == "resize":
                            rows = int(ctrl.get("rows", 24))
                            cols = int(ctrl.get("cols", 220))
                            fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                                        struct.pack("HHHH", rows, cols, 0, 0))
                        continue
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                    os.write(master_fd, msg.encode("utf-8", errors="replace"))
            except (WebSocketDisconnect, Exception):
                pass

        try:
            await asyncio.gather(_read(), _write())
        finally:
            try:
                os.close(master_fd)
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except Exception:
                pass
