"""Opt-in semantic response cache backed by an OpenAI-compatible embedder."""

from __future__ import annotations

import asyncio
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from nonoka.core.llm import LLMResponse


class OpenAICompatibleEmbedder:
  def __init__(self, *, api_base: str, model: str, api_key_env: str, dimensions: int | None = None) -> None:
    self.api_base = api_base.rstrip("/")
    self.model = model
    self.api_key_env = api_key_env
    self.dimensions = dimensions

  async def embed(self, text: str) -> list[float]:
    key = os.getenv(self.api_key_env)
    if not key:
      raise RuntimeError(f"embedding API key environment variable {self.api_key_env} is not set")
    body: dict[str, Any] = {"model": self.model, "input": text}
    if self.dimensions is not None:
      body["dimensions"] = self.dimensions

    def request() -> list[float]:
      payload = json.dumps(body).encode("utf-8")
      req = Request(
        f"{self.api_base}/embeddings", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST",
      )
      with urlopen(req, timeout=20) as response:  # nosec B310 - user-configured provider endpoint
        data = json.loads(response.read())
      vector = data["data"][0]["embedding"]
      if not isinstance(vector, list) or not vector:
        raise RuntimeError("embedding response contained no vector")
      return [float(value) for value in vector]
    return await asyncio.to_thread(request)


class SQLiteSemanticResponseCache:
  """Bounded cosine-similarity cache for deterministic, tool-free completions."""

  def __init__(self, path: str | Path, *, max_candidates: int = 128) -> None:
    self.path = Path(path).expanduser()
    self.max_candidates = max_candidates
    self._initialized = False
    self._lock = asyncio.Lock()

  async def _init(self) -> None:
    if self._initialized:
      return
    async with self._lock:
      if self._initialized:
        return
      self.path.parent.mkdir(parents=True, exist_ok=True)
      with sqlite3.connect(self.path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS llm_semantic_cache (id INTEGER PRIMARY KEY, model TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '', variant TEXT NOT NULL DEFAULT '', embedding TEXT NOT NULL, response TEXT NOT NULL, expires_at REAL NOT NULL, created_at REAL NOT NULL)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(llm_semantic_cache)")}
        if "scope" not in columns:
          db.execute("ALTER TABLE llm_semantic_cache ADD COLUMN scope TEXT NOT NULL DEFAULT ''")
        if "variant" not in columns:
          db.execute("ALTER TABLE llm_semantic_cache ADD COLUMN variant TEXT NOT NULL DEFAULT ''")
        db.execute("CREATE INDEX IF NOT EXISTS llm_semantic_cache_model_scope_variant_expiry ON llm_semantic_cache(model, scope, variant, expires_at)")
      self._initialized = True

  @staticmethod
  def _similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
      return -1.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else -1.0

  async def get(self, vector: list[float], *, model: str, scope: str, variant: str, threshold: float) -> LLMResponse | None:
    await self._init()
    def lookup() -> LLMResponse | None:
      now = time.time()
      with sqlite3.connect(self.path) as db:
        db.execute("DELETE FROM llm_semantic_cache WHERE expires_at <= ?", (now,))
        rows = db.execute("SELECT embedding, response FROM llm_semantic_cache WHERE model = ? AND scope = ? AND variant = ? AND expires_at > ? ORDER BY created_at DESC LIMIT ?", (model, scope, variant, now, self.max_candidates)).fetchall()
      best: tuple[float, str] | None = None
      for stored, response in rows:
        score = self._similarity(vector, json.loads(stored))
        if score >= threshold and (best is None or score > best[0]):
          best = (score, response)
      if best is None:
        return None
      response = LLMResponse.model_validate_json(best[1])
      response.usage["_semantic_similarity_score"] = round(best[0], 6)
      return response
    return await asyncio.to_thread(lookup)

  async def put(self, vector: list[float], response: LLMResponse, *, model: str, scope: str, variant: str, ttl_seconds: int) -> None:
    await self._init()
    expires_at, now = time.time() + ttl_seconds, time.time()
    payload, embedding = response.model_dump_json(), json.dumps(vector, separators=(",", ":"))
    def insert() -> None:
      with sqlite3.connect(self.path) as db:
        db.execute("INSERT INTO llm_semantic_cache(model, scope, variant, embedding, response, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (model, scope, variant, embedding, payload, expires_at, now))
    await asyncio.to_thread(insert)
