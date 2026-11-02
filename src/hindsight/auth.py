"""Private per-user HMAC credentials. Knowledge git is not identity authority."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from .models import SLUG, Identity, KnowledgeError


class AuthStore:
    def __init__(self, private_dir: Path):
        self.directory = Path(private_dir)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink():
            raise KnowledgeError("Unsafe private directory")
        os.chmod(self.directory, 0o700)
        with FileLock(str(self.directory / "auth-init.lock"), timeout=15):
            self._initialize()

    def _initialize(self):
        pepper_path = self.directory / "pepper"
        try:
            fd = os.open(pepper_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as out:
                out.write(secrets.token_bytes(32))
        fd = os.open(pepper_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as src:
            self.pepper = src.read(33)
        if len(self.pepper) != 32:
            raise KnowledgeError("Invalid private pepper")
        os.chmod(pepper_path, 0o600, follow_symlinks=False)
        self.path = self.directory / "auth.sqlite"
        if self.path.is_symlink():
            raise KnowledgeError("Unsafe private database")
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT,email TEXT,active INTEGER,created_at REAL);
            CREATE TABLE IF NOT EXISTS keys(id TEXT PRIMARY KEY,user_id TEXT,secret_hash TEXT UNIQUE,prefix TEXT,created_at REAL,revoked_at REAL);
            CREATE TABLE IF NOT EXISTS memberships(user_id TEXT,team TEXT,can_read INTEGER,can_write INTEGER,PRIMARY KEY(user_id,team));
            CREATE TABLE IF NOT EXISTS sessions(id_hash TEXT PRIMARY KEY,user_id TEXT,key_id TEXT,expires_at REAL,csrf_hash TEXT);
            CREATE TABLE IF NOT EXISTS bootstrap(secret_hash TEXT PRIMARY KEY,expires_at REAL);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _hash(self, value):
        if not isinstance(value, str) or len(value) > 1024:
            raise KnowledgeError("Invalid credential", 401)
        return hmac.new(self.pepper, value.encode(), hashlib.sha256).hexdigest()

    def has_users(self):
        with self._db() as db:
            return bool(db.execute("SELECT 1 FROM users LIMIT 1").fetchone())

    def bootstrap(self):
        token = secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise KnowledgeError("Bootstrap is disabled", 409)
            db.execute("DELETE FROM bootstrap")
            db.execute("INSERT INTO bootstrap VALUES (?,?)", (self._hash(token), time.time() + 900))
        return token

    @staticmethod
    def _validate(name, email, scopes):
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 120
            or any(ord(c) < 32 or c in "<>\x7f" for c in name)
        ):
            raise KnowledgeError("Invalid verified author name")
        if (
            not isinstance(email, str)
            or len(email) > 254
            or "@" not in email
            or any(c.isspace() or c in "<>\x00" for c in email)
        ):
            raise KnowledgeError("Invalid verified author email")
        if any(not isinstance(s, str) or not SLUG.fullmatch(s) for s in scopes):
            raise KnowledgeError("Invalid scope")

    def _new_user(self, db, name, email, reads, writes):
        reads, writes = (
            tuple(sorted(set(reads) | set(writes) | {"shared"})),
            tuple(sorted(set(writes))),
        )
        self._validate(name, email, reads + writes)
        user_id = str(uuid.uuid4())
        db.execute("INSERT INTO users VALUES (?,?,?,?,?)", (user_id, name, email, 1, time.time()))
        for scope in reads:
            db.execute(
                "INSERT INTO memberships VALUES (?,?,?,?)",
                (user_id, scope, 1, int(scope in writes)),
            )
        key = self._new_key(db, user_id)
        return Identity(user_id, name, email, reads, writes), key

    def enroll(self, token, name, email, team):
        self._validate(name, email, (team,))
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM bootstrap WHERE secret_hash=?", (self._hash(token),)
            ).fetchone()
            if (
                db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
                or not row
                or row["expires_at"] < time.time()
            ):
                raise KnowledgeError("Invalid or expired enrollment token", 401)
            result = self._new_user(db, name, email, ("shared", team), ("shared", team))
            db.execute("DELETE FROM bootstrap")
            return result

    def create_user(self, name, email, read_scopes, write_scopes):
        with self._db() as db:
            return self._new_user(db, name, email, read_scopes, write_scopes)

    def _new_key(self, db, user_id):
        key = "hsk_" + secrets.token_urlsafe(36)
        db.execute(
            "INSERT INTO keys VALUES (?,?,?,?,?,NULL)",
            (str(uuid.uuid4()), user_id, self._hash(key), key[:12], time.time()),
        )
        return key

    def issue_key(self, user_id):
        self.get_user(user_id)
        with self._db() as db:
            return self._new_key(db, user_id)

    def revoke_key(self, prefix):
        with self._db() as db:
            db.execute(
                "UPDATE keys SET revoked_at=? WHERE prefix=? OR id=?", (time.time(), prefix, prefix)
            )

    def get_user(self, user_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
            if not row:
                raise KnowledgeError("Invalid credential", 401)
            scopes = db.execute(
                "SELECT * FROM memberships WHERE user_id=? ORDER BY team", (user_id,)
            ).fetchall()
            return Identity(
                row["id"],
                row["name"],
                row["email"],
                tuple(s["team"] for s in scopes if s["can_read"]),
                tuple(s["team"] for s in scopes if s["can_write"]),
            )

    def _key(self, key):
        digest = self._hash(key)
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM keys WHERE secret_hash=? AND revoked_at IS NULL", (digest,)
            ).fetchone()
        if not row or not hmac.compare_digest(row["secret_hash"], digest):
            raise KnowledgeError("Invalid credential", 401)
        return row

    def authenticate(self, key):
        return self.get_user(self._key(key)["user_id"])

    def create_session(self, key):
        row = self._key(key)
        identity = self.get_user(row["user_id"])
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?)",
                (self._hash(token), identity.id, row["id"], time.time() + 28800, self._hash(csrf)),
            )
        return token, csrf, identity

    def _session(self, token):
        with self._db() as db:
            row = db.execute(
                "SELECT s.* FROM sessions s JOIN keys k ON k.id=s.key_id WHERE s.id_hash=? AND s.expires_at>? AND k.revoked_at IS NULL",
                (self._hash(token), time.time()),
            ).fetchone()
        if not row:
            raise KnowledgeError("Invalid session", 401)
        return row

    def authenticate_session(self, token):
        return self.get_user(self._session(token)["user_id"])

    def check_csrf(self, session_token, csrf_token):
        try:
            return hmac.compare_digest(
                self._session(session_token)["csrf_hash"], self._hash(csrf_token)
            )
        except KnowledgeError:
            return False

    def delete_session(self, token):
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE id_hash=?", (self._hash(token),))
