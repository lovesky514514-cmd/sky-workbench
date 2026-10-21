"""Optional persistent Qwen semantic inference worker.

This process is launched with the project-local semantic Python environment.
It communicates over newline-delimited JSON on stdio so the lightweight core
runtime never imports or bundles PyTorch or Transformers.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from pathlib import Path
from typing import Any


DEFAULT_INSTRUCTION = "Given a COMSOL scientific modeling query, retrieve passages that answer the query."


def clean_text(value: Any) -> str:
    """Replace malformed source-code surrogates before the Rust tokenizer."""

    return str(value).encode("utf-8", errors="replace").decode("utf-8")


class QwenSemanticRuntime:
    def __init__(self, embedding_path: Path, reranker_path: Path) -> None:
        self.embedding_path = embedding_path
        self.reranker_path = reranker_path
        self.embedding_tokenizer: Any = None
        self.embedding_model: Any = None
        self.reranker_tokenizer: Any = None
        self.reranker_model: Any = None
        self.device = "cpu"

    @staticmethod
    def _libraries() -> tuple[Any, Any, Any]:
        import torch
        import torch.nn.functional as functional
        from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

        return torch, functional, (AutoModel, AutoModelForCausalLM, AutoTokenizer)

    def _load_embedding(self) -> None:
        if self.embedding_model is not None:
            return
        torch, _, classes = self._libraries()
        auto_model, _, auto_tokenizer = classes
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.embedding_tokenizer = auto_tokenizer.from_pretrained(
            self.embedding_path, local_files_only=True, trust_remote_code=False, padding_side="left"
        )
        self.embedding_model = auto_model.from_pretrained(
            self.embedding_path,
            local_files_only=True,
            trust_remote_code=False,
            torch_dtype=torch.float16 if self.device == "cuda" else "auto",
            low_cpu_mem_usage=True,
        ).to(self.device).eval()
        torch.set_grad_enabled(False)

    def _load_reranker(self) -> None:
        if self.reranker_model is not None:
            return
        torch, _, classes = self._libraries()
        _, auto_causal, auto_tokenizer = classes
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.reranker_tokenizer = auto_tokenizer.from_pretrained(
            self.reranker_path, local_files_only=True, trust_remote_code=False, padding_side="left"
        )
        self.reranker_model = auto_causal.from_pretrained(
            self.reranker_path,
            local_files_only=True,
            trust_remote_code=False,
            torch_dtype=torch.float16 if self.device == "cuda" else "auto",
            low_cpu_mem_usage=True,
        ).to(self.device).eval()
        torch.set_grad_enabled(False)

    @staticmethod
    def _last_token_pool(hidden: Any, attention_mask: Any) -> Any:
        if bool(attention_mask[:, -1].sum() == attention_mask.shape[0]):
            return hidden[:, -1]
        sequence_lengths = attention_mask.sum(dim=1) - 1
        return hidden[range(hidden.shape[0]), sequence_lengths]

    def embed(self, texts: list[str], *, query: bool, instruction: str, max_length: int) -> list[list[float]]:
        self._load_embedding()
        torch, functional, _ = self._libraries()
        prepared = [f"Instruct: {instruction}\nQuery:{text}" for text in texts] if query else texts
        encoded = self.embedding_tokenizer(
            prepared,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        )
        encoded = {name: value.to(self.device) for name, value in encoded.items()}
        with torch.inference_mode():
            output = self.embedding_model(**encoded)
            vectors = self._last_token_pool(output.last_hidden_state, encoded["attention_mask"])
            vectors = functional.normalize(vectors.float(), p=2, dim=1)
        return vectors.cpu().numpy().tolist()

    def rerank(self, query: str, documents: list[str], *, instruction: str, max_length: int) -> list[float]:
        self._load_reranker()
        torch, _, _ = self._libraries()
        prefix = (
            '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the '
            'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
            '<|im_start|>user\n'
        )
        suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
        prompts = [
            f"{prefix}<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}{suffix}"
            for document in documents
        ]
        encoded = self.reranker_tokenizer(
            prompts,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        )
        encoded = {name: value.to(self.device) for name, value in encoded.items()}
        true_id = self.reranker_tokenizer.convert_tokens_to_ids("yes")
        false_id = self.reranker_tokenizer.convert_tokens_to_ids("no")
        with torch.inference_mode():
            logits = self.reranker_model(**encoded).logits[:, -1, :]
            pair_logits = torch.stack((logits[:, false_id], logits[:, true_id]), dim=1).float()
            scores = torch.softmax(pair_logits, dim=1)[:, 1]
        return [float(value) for value in scores.cpu().tolist()]

    def verify(self) -> dict[str, Any]:
        started = time.perf_counter()
        samples = ["COMSOL stationary heat transfer boundary condition", "稳态传热边界条件"]
        first = self.embed(samples, query=True, instruction=DEFAULT_INSTRUCTION, max_length=256)
        second = self.embed(samples, query=True, instruction=DEFAULT_INSTRUCTION, max_length=256)
        dimension = len(first[0]) if first else 0
        finite = bool(first) and all(math.isfinite(value) for vector in first for value in vector)
        repeatable = first == second
        documents = [
            "Heat Transfer in Solids supports prescribed temperature boundary conditions.",
            "A calendar describes dates and holidays.",
        ]
        scores = self.rerank(samples[0], documents, instruction=DEFAULT_INSTRUCTION, max_length=384)
        reranker_finite = len(scores) == 2 and all(math.isfinite(value) for value in scores)
        return {
            "passed": dimension == 1024 and finite and repeatable and reranker_finite and scores[0] > scores[1],
            "embedding": {
                "model": "Qwen/Qwen3-Embedding-0.6B",
                "dimensions": dimension,
                "finite": finite,
                "repeatable": repeatable,
                "query_and_document_supported": True,
                "device": self.device,
            },
            "reranker": {
                "model": "Qwen/Qwen3-Reranker-0.6B",
                "finite": reranker_finite,
                "batch_size": len(scores),
                "scores": scores,
                "relevant_ranked_first": bool(len(scores) == 2 and scores[0] > scores[1]),
                "device": self.device,
            },
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = str(request.get("operation", "ping"))
        if operation == "ping":
            return {
                "ready": True,
                "embedding_files": (self.embedding_path / "model.safetensors").is_file(),
                "reranker_files": (self.reranker_path / "model.safetensors").is_file(),
            }
        if operation == "embed":
            texts = [clean_text(value) for value in request.get("texts", [])]
            return {"vectors": self.embed(
                texts,
                query=bool(request.get("query", False)),
                instruction=str(request.get("instruction") or DEFAULT_INSTRUCTION),
                max_length=max(64, min(int(request.get("max_length", 512)), 4096)),
            )}
        if operation == "rerank":
            documents = [clean_text(value) for value in request.get("documents", [])]
            return {"scores": self.rerank(
                str(request.get("query", "")),
                documents,
                instruction=str(request.get("instruction") or DEFAULT_INSTRUCTION),
                max_length=max(64, min(int(request.get("max_length", 768)), 4096)),
            )}
        if operation == "verify":
            return self.verify()
        if operation == "shutdown":
            return {"shutdown": True}
        raise ValueError(f"Unknown semantic operation: {operation}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedding", type=Path, required=True)
    parser.add_argument("--reranker", type=Path, required=True)
    arguments = parser.parse_args()
    runtime = QwenSemanticRuntime(arguments.embedding.resolve(), arguments.reranker.resolve())
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            payload = {"ok": True, "result": runtime.handle(request)}
        except Exception as exc:
            payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-4000:]}
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
        if payload.get("result", {}).get("shutdown"):
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
