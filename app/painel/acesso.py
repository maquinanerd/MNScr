"""Acesso ao painel: um administrador, senha com scrypt, sessão assinada e CSRF.

Portado do painel do MN-Prime (app/prime/web/auth.py), que já roda no mesmo Coolify.
O administrador é criado no primeiro acesso (`/setup`), pelo próprio operador, no
navegador: nenhuma senha passa por variável de ambiente, painel de deploy ou log. O
primeiro acesso exige o código que o painel imprime no log ao subir — o domínio novo
aparece nos logs públicos de certificados, e o primeiro robô a achá-lo não pode virar
administrador.

O cadastro mora em `painel.db`, e não no banco do robô: o painel só lê aquele.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, Optional

from ..sqlite_utils import connect_sqlite

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 64}
MIN_PASSWORD = 10

#: Quantos scrypt rodam ao mesmo tempo (cada um custa 16 MiB). Uma rajada de muitos IPs
#: fica mais lenta, mas não esgota a memória — e não tranca o administrador do lado de
#: fora, como faria um teto global de tentativas.
_SCRYPT_SLOTS = threading.BoundedSemaphore(4)
_SCRYPT_WAIT_S = 10.0


class AuthBusy(RuntimeError):
    """Scrypt demais ao mesmo tempo: a senha não foi conferida (e a tentativa é devolvida)."""


class PainelStore:
    """O que o painel grava: administrador, código de primeiro acesso e época da sessão."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS painel_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _conn(self) -> sqlite3.Connection:
        return connect_sqlite(self.db_path, row_factory=sqlite3.Row)

    def setting(self, key: str, default: str = "") -> str:
        conn = self._conn()
        try:
            row = conn.execute("SELECT value FROM painel_settings WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
        return row["value"] if row else default

    def session_epoch(self) -> str:
        """Muda a cada logout: um cookie emitido antes deixa de valer, mesmo roubado."""
        return self.setting("session_epoch", "0") or "0"

    def bump_session_epoch(self) -> None:
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO painel_settings(key, value) VALUES ('session_epoch', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)"
                )
        finally:
            conn.close()

    def admin_exists(self) -> bool:
        return bool(self.setting("admin_user")) and bool(self.setting("admin_password_hash"))

    def setup_code(self) -> Optional[str]:
        """O código que o primeiro acesso exige enquanto não há administrador."""
        if self.setting("admin_user"):
            return None
        return self.setting("setup_code") or None

    def ensure_setup_code(self) -> Optional[str]:
        if self.setting("admin_user"):
            return None
        conn = self._conn()
        try:
            with conn:
                # 64 bits: vale só enquanto não há administrador, mas o domínio novo
                # aparece nos logs públicos de certificados no minuto em que nasce.
                conn.execute(
                    "INSERT OR IGNORE INTO painel_settings(key, value) VALUES ('setup_code', ?)",
                    (secrets.token_hex(8).upper(),),
                )
        finally:
            conn.close()
        return self.setup_code()

    def create_admin(self, user: str, password_hash: str) -> bool:
        """Grava usuário e senha numa transação só; False se já existe administrador."""
        conn = self._conn()
        try:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM painel_settings WHERE key = 'admin_user' AND value <> ''").fetchone():
                conn.execute("ROLLBACK")
                return False
            for key, value in (("admin_user", user), ("admin_password_hash", password_hash)):
                conn.execute(
                    "INSERT INTO painel_settings(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            conn.execute("DELETE FROM painel_settings WHERE key = 'setup_code'")
            conn.execute("COMMIT")
            return True
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def reset_admin(self) -> str:
        """Apaga o administrador, derruba as sessões abertas e devolve um código novo.

        É o caminho para uma senha esquecida: `python -m app.painel --novo-acesso` no
        terminal do contêiner do painel.
        """
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM painel_settings WHERE key IN ('admin_user', 'admin_password_hash', 'setup_code')"
                )
        finally:
            conn.close()
        self.bump_session_epoch()
        return self.ensure_setup_code() or ""


def session_secret(store: PainelStore) -> str:
    """Assina o cookie. Vem de `MNSCR_PAINEL_SECRET_KEY` ou de um arquivo 0600 ao lado do
    `painel.db`, criado uma vez; nunca do banco, que um backup leva junto."""
    from_env = (os.getenv("MNSCR_PAINEL_SECRET_KEY") or "").strip()
    if from_env:
        return from_env
    path = Path(store.db_path).with_name("painel.secret")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(32))
    secret = path.read_text(encoding="ascii").strip()
    if len(secret) < 32:
        raise RuntimeError(f"{path} está vazio ou curto demais; apague-o e reinicie o painel")
    return secret


def _scrypt(password: str, salt: bytes) -> bytes:
    if not _SCRYPT_SLOTS.acquire(timeout=_SCRYPT_WAIT_S):
        raise AuthBusy("servidor ocupado; tente de novo em instantes")
    try:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    finally:
        _SCRYPT_SLOTS.release()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    digest = _scrypt(password, salt)
    return hmac.compare_digest(digest, expected)


def create_admin(store: PainelStore, user: str, password: str, *, code: str = "") -> None:
    expected = store.setup_code()
    if not expected or not hmac.compare_digest(expected.encode(), (code or "").strip().upper().encode()):
        raise ValueError("código de primeiro acesso incorreto (ele está no log do contêiner do painel)")
    user = (user or "").strip()
    if not user or len(user) > 64:
        raise ValueError("informe um usuário")
    if len(password or "") < MIN_PASSWORD:
        raise ValueError(f"a senha precisa de pelo menos {MIN_PASSWORD} caracteres")
    if not store.create_admin(user, hash_password(password)):
        raise ValueError("o administrador já foi criado")


def check_login(store: PainelStore, user: str, password: str) -> bool:
    stored_user = store.setting("admin_user")
    stored_hash = store.setting("admin_password_hash")
    if not stored_user or not stored_hash:
        return False
    same_user = hmac.compare_digest((user or "").strip().encode(), stored_user.encode())
    return verify_password(password or "", stored_hash) and same_user


def client_key(host: str) -> str:
    """A chave do limite de tentativas: o IPv4, ou a rede /64 de um IPv6.

    Um único cliente IPv6 costuma ter um /64 inteiro; contar por endereço daria a ele
    tentativas sem fim.
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class LoginThrottle:
    """Tentativas de login por cliente, contadas ANTES de conferir a senha.

    Contar antes é o que segura uma rajada em paralelo do mesmo cliente. Contra muitos
    clientes ao mesmo tempo, o freio é o limite de scrypt simultâneos — não um teto
    global, que qualquer um usaria para trancar o administrador do lado de fora.
    """

    def __init__(self, limit: int = 5, window: float = 300.0, *, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> Deque[float]:
        q = self._hits.get(key)
        if q is None:
            return deque()
        while q and now - q[0] > self.window:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
        return q

    def _prune(self, now: float) -> None:
        for stale in list(self._hits):
            self._trim(stale, now)
        overflow = len(self._hits) - self.max_keys
        if overflow > 0:
            for stale in sorted(self._hits, key=lambda k: self._hits[k][-1])[:overflow]:
                self._hits.pop(stale, None)

    def attempt(self, host: str) -> bool:
        """Registra uma tentativa; False se o cliente já está no limite."""
        key = client_key(host)
        now = time.monotonic()
        with self._lock:
            if len(self._hits) >= self.max_keys:
                self._prune(now)
            if len(self._trim(key, now)) >= self.limit:
                return False
            self._hits.setdefault(key, deque()).append(now)
            return True

    def refund(self, host: str) -> None:
        """Devolve a última tentativa: a senha não chegou a ser conferida (`AuthBusy`)."""
        key = client_key(host)
        with self._lock:
            q = self._hits.get(key)
            if q:
                q.pop()
                if not q:
                    self._hits.pop(key, None)

    def reset(self, host: str) -> None:
        with self._lock:
            self._hits.pop(client_key(host), None)


def csrf_token(session: dict) -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def csrf_ok(session: dict, submitted: str) -> bool:
    expected = session.get("csrf") or ""
    return bool(expected) and hmac.compare_digest(expected.encode(), (submitted or "").encode())
