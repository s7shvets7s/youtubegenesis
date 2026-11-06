from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from ru_en_dubber.cli import project_root
from ru_en_dubber.processes import stop_process
from ru_en_dubber.web.catalog import details, media_path
from ru_en_dubber.web.progress import write_json
from ru_en_dubber.web.settings import build_argv, schema
from ru_en_dubber.workspace import WorkspaceCatalog

LAUNCH_MODES = {"new", "script", "script_import", "resume", "translate", "tts", "export"}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return (
                not kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                or code.value == 259
            )
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Service:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.workspace = WorkspaceCatalog(self.root)
        self.runs = self.workspace.runtime
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, object] = {}

    def snapshots(self) -> list[dict]:
        result = []
        for directory in self.runs.iterdir():
            if not directory.is_dir():
                continue
            try:
                record = json.loads(
                    (directory / "request.json").read_text(encoding="utf-8")
                )
                progress_path = directory / "progress.json"
                state = (
                    json.loads(progress_path.read_text(encoding="utf-8"))
                    if progress_path.exists()
                    else {"status": "starting", "stages": [], "overall": 0}
                )
                process = self.processes.get(directory.name)
                if process is not None and process.poll() is not None:
                    self.processes.pop(directory.name, None)
                    stream = self.logs.pop(directory.name, None)
                    if stream:
                        stream.close()
                    if state["status"] in {"starting", "running"}:
                        state.update(
                            status="failed",
                            error=f"Process exited with code {process.returncode}",
                        )
                elif process is None and state["status"] in {"starting", "running"}:
                    pid_path = directory / "worker.pid"
                    if pid_path.exists() and not process_alive(
                        int(pid_path.read_text(encoding="ascii"))
                    ):
                        state.update(
                            status="failed",
                            error=(
                                "Processing was interrupted. You can re-run "
                                "from the saved video."
                            ),
                        )
                    else:
                        state["status"] = "untracked"
                        state["error"] = (
                            "The server restarted. Wait for the process to finish "
                            "before a new run."
                        )
                result.append(
                    {
                        "id": directory.name,
                        "channel_id": record["channel_id"],
                        "channel_name": record["channel_name"],
                        "video_id": record["video_id"],
                        "video_title": record["video_title"],
                        "run_id": record["run_id"],
                        "created_at": record["created_at"],
                        "mode": record["mode"],
                        "progress": state,
                    }
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return sorted(result, key=lambda item: item["created_at"], reverse=True)

    def close(self) -> None:
        """Stop owned workers and release their process/log handles."""
        with self.lock:
            processes = list(self.processes.values())
            streams = list(self.logs.values())
            self.processes.clear()
            self.logs.clear()
        try:
            for process in processes:
                stop_process(process)
        finally:
            for stream in streams:
                stream.close()

    def launch(self, payload: dict) -> dict:
        with self.lock:
            if any(
                run["progress"]["status"] in {"starting", "running", "untracked"}
                for run in self.snapshots()
            ):
                raise ValueError(
                    "A pipeline is already running. Wait for it to finish "
                    "to avoid overloading the GPU."
                )
            if not isinstance(payload, dict):
                raise ValueError("A settings object is expected")
            channel_id = payload.get("channel_id", "")
            channel = self.workspace.channel_summary(
                self.workspace.channel_directory(channel_id)
            )
            mode = payload.get("mode", "new")
            if mode not in LAUNCH_MODES:
                raise ValueError("Unknown launch mode")
            created_video: Path | None = None
            script_path: Path | None = None
            script_content: bytes | None = None
            if mode in {"new", "script_import"}:
                video = self.workspace.create_video(
                    channel_id, payload.get("video_title", "")
                )
                created_video = self.workspace.video_directory(channel_id, video["id"])
                run_id = self.workspace.new_run_id()
            elif mode == "script":
                video_id = payload.get("video_id", "")
                video = self.workspace.video_summary(
                    self.workspace.video_directory(channel_id, video_id)
                )
                script_content = self.workspace.read_ready_script(channel_id, video_id)
                if payload.get("output_mode") == "capcut":
                    video_directory = self.workspace.video_directory(channel_id, video_id)
                    beat_sheet = video_directory / "CAPCUT_BEATS.srt"
                    if (
                        not beat_sheet.resolve().is_relative_to(video_directory.resolve())
                        or not beat_sheet.is_file()
                    ):
                        raise ValueError("This video has no CAPCUT_BEATS.srt")
                    script_content = beat_sheet.read_bytes()
                run_id = self.workspace.new_run_id()
            else:
                video_id = payload.get("video_id", "")
                video = self.workspace.video_summary(
                    self.workspace.video_directory(channel_id, video_id)
                )
                run_id = payload.get("run_id", "")
                self.workspace.run_directory(channel_id, video_id, run_id)
            video_id = video["id"]
            jobs = self.workspace.runs_directory(channel_id, video_id)
            operation_id = uuid4().hex
            directory = self.runs / operation_id
            directory.mkdir()
            try:
                cli_mode = "script" if mode in {"script", "script_import"} else mode
                cli_payload = {**payload, "mode": cli_mode, "job_id": run_id}
                if mode == "script_import":
                    script_text = payload.get("script_text")
                    if (
                        not isinstance(script_text, str)
                        or not script_text.strip()
                        or "\0" in script_text
                    ):
                        raise ValueError(
                            "Paste an English script or upload a .txt/.md"
                        )
                    script_bytes = script_text.encode("utf-8")
                    if len(script_bytes) > 128_000:
                        raise ValueError("The script is too large: maximum 128 KB")
                    video = self.workspace.save_script(
                        channel_id, video_id, script_text, status="ready"
                    )
                    script_content = script_bytes
                if script_content is not None:
                    suffix = ".srt" if payload.get("output_mode") == "capcut" else ".md"
                    script_path = directory / f"script-snapshot{suffix}"
                    script_path.write_bytes(script_content)
                argv = build_argv(
                    cli_payload,
                    self.root,
                    jobs,
                    run_id,
                    script_path=script_path,
                    script_root=directory if script_path is not None else None,
                )
                if mode == "script" and payload.get("options", {}).get(
                    "tts_provider", "chatterbox"
                ) == "cosyvoice3":
                    video_directory = self.workspace.video_directory(channel_id, video_id)
                    direction_name = (
                        "CAPCUT_VOICE_DIRECTION.json"
                        if payload.get("output_mode") == "capcut"
                        else "VOICE_DIRECTION.json"
                    )
                    direction_source = video_directory / direction_name
                    if direction_source.is_file():
                        if not direction_source.resolve().is_relative_to(
                            video_directory.resolve()
                        ) or direction_source.stat().st_size > 128_000:
                            raise ValueError("The voice-direction file must be inside the video and up to 128 KB")
                        direction_snapshot = directory / "direction-snapshot.json"
                        shutil.copyfile(direction_source, direction_snapshot)
                        argv += ["--tts-direction-file", str(direction_snapshot)]
            except (ValueError, TypeError, OSError):
                shutil.rmtree(directory)
                if created_video is not None:
                    shutil.rmtree(created_video)
                raise
            record = {
                "argv": argv,
                "channel_id": channel_id,
                "channel_name": channel["display_name"],
                "video_id": video_id,
                "video_title": video["working_title"],
                "run_id": run_id,
                "mode": mode,
                "created_at": time.time(),
                "options": payload.get("options", {}),
            }
            write_json(directory / "request.json", record)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = (
                str(self.root / "src")
                + os.pathsep
                + environment.get("PYTHONPATH", "")
            )
            environment["PYTHONIOENCODING"] = "utf-8"
            stream = (directory / "worker.log").open("wb")
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "ru_en_dubber.web.worker",
                        str(directory / "request.json"),
                    ],
                    cwd=self.root,
                    env=environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except OSError as error:
                stream.close()
                write_json(
                    directory / "progress.json",
                    {"status": "failed", "error": str(error), "stages": [], "overall": 0},
                )
                raise
            self.processes[operation_id] = process
            self.logs[operation_id] = stream
            (directory / "worker.pid").write_text(str(process.pid), encoding="ascii")
            self.workspace.touch_video(channel_id, video_id)
            return {
                "id": operation_id,
                "channel_id": channel_id,
                "video_id": video_id,
                "video_title": video["working_title"],
                "run_id": run_id,
            }


def handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _host_ok(self) -> bool:
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def _headers(self, status: int, kind: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "media-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )

        def _json(self, data, status=200) -> None:
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self._host_ok():
                self._json({"error": "Invalid Host"}, 403)
                return
            try:
                path = unquote(urlsplit(self.path).path)
                if path == "/api/v1/bootstrap":
                    self._json(
                        {"token": service.token, "settings": schema(), "root": str(service.root)}
                    )
                elif path == "/api/v1/channels":
                    self._json(service.workspace.list_channels())
                elif path == "/api/v1/runs":
                    with service.lock:
                        self._json({"runs": service.snapshots()})
                elif path.startswith("/api/v1/channels/"):
                    self._channel_route(path)
                else:
                    files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                             "/i18n-en.json": "i18n-en.json", "/i18n-ru.json": "i18n-ru.json"}
                    if path not in files:
                        raise FileNotFoundError("Page not found")
                    target = Path(__file__).parent / "static" / files[path]
                    body = target.read_bytes()
                    kind = mimetypes.guess_type(target.name)[0] or "text/plain"
                    self._headers(200, kind + "; charset=utf-8", len(body))
                    self.end_headers()
                    self.wfile.write(body)
            except (FileNotFoundError, KeyError) as error:
                self._json({"error": str(error)}, 404)
            except (ValueError, TypeError) as error:
                self._json({"error": str(error)}, 400)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except OSError:
                self._json({"error": "Could not read workspace data"}, 500)

        def _channel_route(self, path: str) -> None:
            parts = path[len("/api/v1/") :].split("/")
            if len(parts) == 2 and parts[0] == "channels":
                self._json(service.workspace.channel_details(parts[1]))
                return
            if len(parts) == 4 and parts[0] == "channels" and parts[2] == "videos":
                self._json(service.workspace.video_details(parts[1], parts[3]))
                return
            if (
                len(parts) == 6
                and parts[0] == "channels"
                and parts[2] == "videos"
                and parts[4] == "runs"
            ):
                self._json(details(service.workspace, parts[1], parts[3], parts[5]))
                return
            if (
                len(parts) >= 8
                and parts[0] == "channels"
                and parts[2] == "videos"
                and parts[4] == "runs"
                and parts[6] == "media"
            ):
                target = media_path(
                    service.workspace,
                    parts[1],
                    parts[3],
                    parts[5],
                    "/".join(parts[7:]),
                )
                self._media(target)
                return
            raise FileNotFoundError("Workspace object not found")

        def _media(self, target: Path) -> None:
            size = target.stat().st_size
            start, end = 0, size - 1
            range_header = self.headers.get("Range")
            if range_header:
                import re

                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                if not match or not any(match.groups()):
                    self._range_error(size)
                    return
                left, right = match.groups()
                start = int(left) if left else max(0, size - int(right))
                end = min(int(right), size - 1) if left and right else size - 1
                if start > end or start >= size:
                    self._range_error(size)
                    return
            length = max(0, end - start + 1)
            kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._headers(206 if range_header else 200, kind, length)
            self.send_header("Accept-Ranges", "bytes")
            if range_header:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with target.open("rb") as stream:
                stream.seek(start)
                while length:
                    block = stream.read(min(length, 256 * 1024))
                    if not block:
                        break
                    self.wfile.write(block)
                    length -= len(block)

        def _range_error(self, size: int) -> None:
            self._headers(416, "text/plain", 0)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            allowed = {
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }
            if (
                not self._host_ok()
                or origin not in allowed
                or not secrets.compare_digest(
                    self.headers.get("X-CSRF-Token", ""), service.token
                )
            ):
                self._json(
                    {"error": "Refresh the page to confirm the local session"},
                    403,
                )
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 300_000:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("A settings object is expected")
                if self.path == "/api/v1/runs":
                    self._json(service.launch(payload), 201)
                elif self.path == "/api/v1/channels":
                    self._json(service.workspace.create_channel(payload.get("display_name", "")), 201)
                else:
                    raise FileNotFoundError("Method not found")
            except FileNotFoundError as error:
                self._json({"error": str(error)}, 404)
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._json({"error": str(error)}, 400)
            except OSError:
                self._json({"error": "Could not complete the operation"}, 500)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Local pipeline web UI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    service = Service(project_root())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(service))
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Pipeline UI: {url}", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
