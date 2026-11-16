"""Thin client for the optional project-local Qwen semantic runtime."""

from __future__ import annotations

import json
import os
import subprocess
from collections import OrderedDict
from pathlib import Path
from queue import Empty, Queue
from threading import RLock, Thread
from typing import Any
from uuid import uuid4

import numpy as np

from app.app_paths import app_paths
from app.execution.scientific_transfer import decode_object


EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
RERANKER_MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"
EMBEDDING_NAMESPACE = "qwen3-embedding-0.6b-1024"
REQUEST_LIMIT = 512_000
RESPONSE_LIMIT = 1_000_000
BATCH_LIMIT = 32


def _request_frame(payload: dict) -> bytes:
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    frame = bytearray()
    for piece in encoder.iterencode(payload):
        if len(piece) >= REQUEST_LIMIT:
            raise ValueError('semantic_request_limit')
        data = piece.encode('utf-8')
        if len(frame) + len(data) >= REQUEST_LIMIT:
            raise ValueError('semantic_request_limit')
        frame.extend(data)
    return bytes(frame) + b'\n'


def _read_frame(pipe) -> dict:
    # 限制字节数并要求换行 / Bound bytes and require a newline terminator.
    raw = pipe.readline(RESPONSE_LIMIT + 1)
    if not raw:
        raise ValueError('semantic_worker_exited_without_response')
    if len(raw) > RESPONSE_LIMIT:
        raise ValueError('semantic_response_limit')
    if not raw.endswith(b'\n'):
        raise ValueError('semantic_response_truncated')
    value = decode_object(raw)
    if type(value.get('ok')) is not bool or (value['ok'] and not isinstance(value.get('result'), dict)):
        raise ValueError('semantic_response_invalid')
    return value


class SemanticRuntimeClient:
    def __init__(self) -> None:
        self._lock = RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._timeout = 240
        self._embedding_state = "Unavailable"
        self._reranker_state = "Unavailable"
        self._error: str | None = None
        self._verified: dict[str, Any] | None = None
        self._embedding_cache: OrderedDict[tuple[str, bool, int], np.ndarray] = OrderedDict()

    @property
    def semantic_dir(self) -> Path:
        return app_paths.models_dir / "semantic"

    @property
    def embedding_path(self) -> Path:
        return self.semantic_dir / "Qwen3-Embedding-0.6B"

    @property
    def reranker_path(self) -> Path:
        return self.semantic_dir / "Qwen3-Reranker-0.6B"

    def _python_path(self) -> Path:
        override = os.getenv("COMSOL_WORKBENCH_SEMANTIC_PYTHON", "")
        if override:
            return Path(override).expanduser().resolve()
        return app_paths.workspace_dir / "semantic_runtime" / ".venv" / "Scripts" / "python.exe"

    def _worker_path(self) -> Path:
        override = os.getenv("COMSOL_WORKBENCH_SEMANTIC_WORKER", "")
        if override:
            return Path(override).expanduser().resolve()
        development = app_paths.project_root / "scripts" / "semantic_runtime_worker.py"
        if development.is_file():
            return development
        return app_paths.resource_dir / "resources" / "semantic_runtime" / "semantic_runtime_worker.py"

    def status(self) -> dict[str, Any]:
        python_path = self._python_path()
        worker_path = self._worker_path()
        embedding_files = (self.embedding_path / "model.safetensors").is_file()
        reranker_files = (self.reranker_path / "model.safetensors").is_file()
        runtime_available = python_path.is_file() and worker_path.is_file()
        if self._verified and self._verified.get("passed"):
            self._embedding_state = "Ready"
            self._reranker_state = "Ready"
        elif not runtime_available or not embedding_files:
            self._embedding_state = "Unavailable"
        elif self._embedding_state == "Unavailable":
            self._embedding_state = "Loading"
        if not runtime_available or not reranker_files:
            self._reranker_state = "Unavailable"
        elif self._reranker_state == "Unavailable":
            self._reranker_state = "Loading"
        return {
            "runtime_available": runtime_available,
            "python": str(python_path),
            "worker": str(worker_path),
            "model_directory": str(self.semantic_dir),
            "embedding": {
                "model": EMBEDDING_MODEL_ID,
                "path": str(self.embedding_path),
                "files_present": embedding_files,
                "status": self._embedding_state,
                "dimensions": 1024 if self._embedding_state == "Ready" else None,
            },
            "reranker": {
                "model": RERANKER_MODEL_ID,
                "path": str(self.reranker_path),
                "files_present": reranker_files,
                "status": self._reranker_state,
            },
            "verified": self._verified,
            "error": self._error,
        }

    def _start(self) -> subprocess.Popen[bytes]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._process
            if self._process is not None:
                self._discard(self._process)
            status = self.status()
            if not status["runtime_available"]:
                raise RuntimeError("Optional semantic Python runtime or worker is unavailable")
            if not status["embedding"]["files_present"] or not status["reranker"]["files_present"]:
                raise RuntimeError("Official Qwen semantic model files are incomplete")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self._process = subprocess.Popen(
                [
                    str(self._python_path()), "-u", str(self._worker_path()),
                    "--embedding", str(self.embedding_path),
                    "--reranker", str(self.reranker_path),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            return self._process

    def _call(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = uuid4().hex
        frame = _request_frame({**payload, 'request_id': request_id})
        with self._lock:
            process = self._start()
            assert process.stdin is not None and process.stdout is not None
            result = Queue(maxsize=1)

            def exchange():
                try:
                    process.stdin.write(frame)
                    process.stdin.flush()
                    response = _read_frame(process.stdout)
                    if response.get('request_id') != request_id:
                        raise ValueError('semantic_response_identity_mismatch')
                    result.put(response)
                except Exception as error:
                    result.put(error)

            thread = Thread(target=exchange, daemon=True, name='qwen-bounded-stdio')
            thread.start()
            try:
                response = result.get(timeout=self._timeout)
            except Empty:
                response = TimeoutError('semantic_worker_response_timeout')
            if isinstance(response, Exception):
                self._discard(process, thread)
                raise RuntimeError(str(response)) from response
            if not response['ok']:
                raise RuntimeError('Semantic runtime rejected the request')
            return response['result']

    def _discard(self, process, thread=None):
        self._process = None
        self._verified = None
        self._embedding_state = self._reranker_state = 'Unavailable'
        self._embedding_cache.clear()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if thread is not None:
            thread.join(timeout=2)
        if thread is None or not thread.is_alive():
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass

    def verify(self) -> dict[str, Any]:
        self._embedding_state = "Loading"
        self._reranker_state = "Loading"
        try:
            result = self._call({"operation": "verify"})
            self._verified = result
            if not result.get("passed"):
                raise RuntimeError(f"Semantic verification failed: {result}")
            self._embedding_state = "Ready"
            self._reranker_state = "Ready"
            self._error = None
            return result
        except Exception as exc:
            self._embedding_state = "Error"
            self._reranker_state = "Error"
            self._error = f"{type(exc).__name__}: {exc}"
            raise

    def embed(self, texts: list[str], *, query: bool = False, max_length: int = 512) -> np.ndarray:
        if not 1 <= len(texts) <= BATCH_LIMIT or any(len(text) > 65_536 for text in texts):
            raise ValueError('semantic_embedding_batch_limit')
        if len(texts) == 1:
            key = (texts[0], query, max_length)
            cached = self._embedding_cache.get(key)
            if cached is not None:
                self._embedding_cache.move_to_end(key)
                return cached.copy()
        self._embedding_state = "Loading"
        try:
            result = self._call({"operation": "embed", "texts": texts, "query": query, "max_length": max_length})
            vectors = np.asarray(result.get("vectors", []), dtype=np.float32)
            if vectors.shape != (len(texts), 1024) or not np.all(np.isfinite(vectors)):
                raise RuntimeError(f"Invalid embedding output shape or values: {vectors.shape}")
            self._embedding_state = "Ready"
            self._error = None
            if len(texts) == 1:
                self._embedding_cache[key] = vectors.copy()
                while len(self._embedding_cache) > 128:
                    self._embedding_cache.popitem(last=False)
            return vectors
        except Exception as exc:
            self._embedding_state = "Error"
            self._error = f"{type(exc).__name__}: {exc}"
            raise

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        if len(documents) > BATCH_LIMIT or len(query) > 65_536 or any(len(text) > 65_536 for text in documents):
            raise ValueError('semantic_rerank_batch_limit')
        self._reranker_state = "Loading"
        try:
            result = self._call({"operation": "rerank", "query": query, "documents": documents})
            scores = [float(value) for value in result.get("scores", [])]
            if len(scores) != len(documents) or not all(np.isfinite(value) for value in scores):
                raise RuntimeError("Invalid reranker output")
            self._reranker_state = "Ready"
            self._error = None
            return scores
        except Exception as exc:
            self._reranker_state = "Error"
            self._error = f"{type(exc).__name__}: {exc}"
            raise

    def shutdown(self) -> None:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._process = None
                return
            try:
                self._call({"operation": "shutdown"})
                self._process.wait(timeout=10)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                pass
            finally:
                if self._process is not None:
                    self._discard(self._process)


semantic_runtime = SemanticRuntimeClient()
