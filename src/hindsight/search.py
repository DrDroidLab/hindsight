"""Disposable, atomic FTS5 projection; scope filtering precedes ranking."""

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path

from .models import KnowledgeError
from .redaction import Redactor


class SearchIndex:
    def __init__(self, index_dir: Path, redactor=None, embedding_provider=None):
        self.directory = Path(index_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "search.sqlite"
        self.redactor = redactor or Redactor()
        self.embedding_provider = embedding_provider
        self._configured_dimension = getattr(embedding_provider, "dimension", None)
        self._lock = threading.RLock()

    @property
    def revision(self):
        return self._revision()

    def _model_key(self):
        provider = self.embedding_provider
        if not provider:
            return ""
        return json.dumps(
            [
                getattr(provider, "model", ""),
                getattr(provider, "endpoint", ""),
                self._configured_dimension,
                getattr(self.redactor, "version", "1"),
            ]
        )

    @staticmethod
    def _valid_vector(vector):
        return (
            isinstance(vector, list)
            and 0 < len(vector) <= 16384
            and all(
                not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)
                for x in vector
            )
        )

    def _vectors(self, records):
        vectors: list[list[float] | None] = [None] * len(records)
        if not self.embedding_provider:
            return vectors, {}
        cache = {}
        if self.path.exists():
            try:
                with sqlite3.connect(self.path) as previous:
                    cache = dict(previous.execute("SELECT cache_key,vector FROM embedding_cache"))
            except sqlite3.Error:
                pass
        model = self._model_key()
        texts = [self.redactor.redact(r.meta["title"] + "\n" + r.body)[:32000] for r in records]
        keys = [
            hashlib.sha256(json.dumps([r.scope, model, text]).encode()).hexdigest()
            for r, text in zip(records, texts, strict=False)
        ]
        missing = []
        for i, key in enumerate(keys):
            try:
                candidate = json.loads(cache.get(key, "null"))
            except (ValueError, TypeError):
                candidate = None
            if self._valid_vector(candidate):
                vectors[i] = candidate
            else:
                missing.append(i)
        for offset in range(0, len(missing), 32):
            indices = missing[offset : offset + 32]
            try:
                batch = self.embedding_provider.embed([texts[i] for i in indices])
                if len(batch) != len(indices) or not all(self._valid_vector(v) for v in batch):
                    continue
                for i, vector in zip(indices, batch, strict=False):
                    vectors[i] = vector
            except Exception:  # noqa: BLE001,S112 -- optional provider failures must not log payloads
                continue
        if len({len(v) for v in vectors if v}) > 1:
            # Same-named models changing dimensions cannot mix old and new vectors.
            return [None] * len(records), {}
        return vectors, {
            key: json.dumps(vector) for key, vector in zip(keys, vectors, strict=False) if vector
        }

    def rebuild(self, records, revision):
        with self._lock:
            fd, name = tempfile.mkstemp(prefix="search-", suffix=".sqlite", dir=self.directory)
            os.close(fd)
            try:
                db = sqlite3.connect(name)
                try:
                    db.executescript("""
                    CREATE TABLE objects(id TEXT PRIMARY KEY,scope TEXT,path TEXT,kind TEXT,status TEXT,title TEXT,body TEXT,services_json TEXT,alerts_json TEXT,revision TEXT,updated_at TEXT,meta TEXT,vector TEXT);
                    CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,title,body,aliases);
                    CREATE TABLE index_metadata(git_revision TEXT,schema_version INTEGER,embedding_revision TEXT);
                    CREATE TABLE embedding_cache(cache_key TEXT PRIMARY KEY,vector TEXT);
                    """)
                    vectors, cache = self._vectors(records)
                    for record, vector in zip(records, vectors, strict=False):
                        meta = self.redactor.clean(record.meta)
                        body = self.redactor.redact(record.body)
                        db.execute(
                            "INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                record.id,
                                record.scope,
                                record.path,
                                record.kind,
                                meta.get("status", ""),
                                meta["title"],
                                body,
                                json.dumps(meta.get("service_ids", [])),
                                json.dumps(meta.get("alerts", [])),
                                record.revision,
                                meta.get("updated_at", ""),
                                json.dumps(meta),
                                json.dumps(vector),
                            ),
                        )
                        db.execute(
                            "INSERT INTO search VALUES (?,?,?,?)",
                            (record.id, meta["title"], body, " ".join(meta.get("alerts", []))),
                        )
                    db.executemany("INSERT INTO embedding_cache VALUES (?,?)", cache.items())
                    db.execute(
                        "INSERT INTO index_metadata VALUES (?,?,?)",
                        (revision, 1, self._model_key()),
                    )
                    db.commit()
                finally:
                    db.close()
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)

    @staticmethod
    def _cosine(a, b):
        if not a or not b or len(a) != len(b):
            return 0
        try:
            norm = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b, strict=False)) / norm if norm else 0
        except (TypeError, ValueError, OverflowError):
            return 0

    def recall(self, identity, query="", service=None, alert=None, scope=None, limit=10):
        if not isinstance(query, str) or len(query) > 2048:
            raise KnowledgeError("Search query exceeds limit")
        limit = max(1, min(int(limit), 50))
        scopes = tuple(sorted(set(identity.read_scopes) | {"shared"}))
        if scope:
            if not identity.can_read(scope):
                return {"results": [], "index_revision": self._revision(), "truncated": False}
            scopes = (scope,)
        if not self.path.exists():
            return {"results": [], "index_revision": "", "truncated": False}
        query = self.redactor.redact(query)
        terms = re.findall(r"\w+", query, re.UNICODE)[:32]
        fts = " OR ".join('"' + word.replace('"', '""') + '"' for word in terms)
        with self._lock, sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            revision = db.execute("SELECT git_revision FROM index_metadata").fetchone()[0]
            clauses = ["scope IN (" + ",".join("?" for _ in scopes) + ")", "status != 'rejected'"]
            parameters = list(scopes)
            if service:
                clauses.append(
                    "(id=? OR EXISTS (SELECT 1 FROM json_each(services_json) WHERE value=?))"
                )
                parameters += [service, service]
            if alert:
                clauses.append("EXISTS (SELECT 1 FROM json_each(alerts_json) WHERE value=?)")
                parameters.append(alert)
            # Fetch only allowed records before any lexical or semantic score is computed.
            rows = db.execute(
                "SELECT * FROM objects WHERE " + " AND ".join(clauses) + " ORDER BY id LIMIT 10001",
                parameters,
            ).fetchall()
            scores = {}
            if fts:
                # FTS is evaluated only on a temporary authorized corpus, avoiding global IDF leakage.
                db.execute(
                    "CREATE VIRTUAL TABLE temp.authorized USING fts5(id UNINDEXED,title,body,aliases)"
                )
                db.executemany(
                    "INSERT INTO authorized VALUES (?,?,?,?)",
                    [
                        (r["id"], r["title"], r["body"], " ".join(json.loads(r["alerts_json"])))
                        for r in rows[:10000]
                    ],
                )
                scores = {
                    r[0]: -r[1]
                    for r in db.execute(
                        "SELECT id,bm25(authorized,0,4,1,2) FROM authorized WHERE authorized MATCH ?",
                        (fts,),
                    )
                }
            vector = None
            indexed_model = db.execute("SELECT embedding_revision FROM index_metadata").fetchone()[
                0
            ]
            if query and self.embedding_provider and indexed_model == self._model_key():
                try:
                    vector = self.embedding_provider.embed([query])[0]
                except Exception:  # noqa: BLE001,S110 -- optional provider failures must not log payloads
                    pass
            ranked = []
            for row in rows[:10000]:
                semantic = self._cosine(vector, json.loads(row["vector"])) if vector else 0
                if terms and row["id"] not in scores and semantic <= 0.15:
                    continue
                meta = json.loads(row["meta"])
                score = scores.get(row["id"], 0) + max(0, semantic)
                # Comparable approved evidence precedes unreviewed/stale evidence.
                score *= {"approved": 1.3, "stale": 0.6, "proposed": 0.8}.get(row["status"], 1)
                if not terms:
                    score = {"approved": 3, "stale": 1, "proposed": 0.5}.get(row["status"], 2)
                ranked.append(
                    {
                        "id": row["id"],
                        "kind": row["kind"],
                        "title": row["title"],
                        "scope": row["scope"],
                        "status": row["status"],
                        "unreviewed": row["status"] == "proposed",
                        "stale": row["status"] == "stale",
                        "score": score,
                        "content": row["body"][:8000],
                        "source_revision": row["revision"],
                        "meta": meta,
                        "related_ids": [],
                    }
                )
            ranked.sort(key=lambda r: (-r["score"], r["id"]))
            return {
                "results": ranked[:limit],
                "index_revision": revision,
                "truncated": len(ranked) > limit or len(rows) > 10000,
            }

    def _revision(self):
        if not self.path.exists():
            return ""
        try:
            with sqlite3.connect(self.path) as db:
                return db.execute("SELECT git_revision FROM index_metadata").fetchone()[0]
        except sqlite3.Error:
            return ""
