"""Palavras-passe, sessões, CSRF, códigos OTP e impressão digital de dispositivo.

Este é o ficheiro onde um erro é uma vulnerabilidade. Três regras que não se
negociam aqui:

1. Nenhum segredo é escrito em log. Nem em debug. `grep -ri otp src/ | grep
   print` tem de dar vazio — há um teste que o garante.
2. Nenhuma comparação de segredo usa `==`. Usa `hmac.compare_digest`.
3. Nenhum código de email é devolvido ao cliente. O cliente recebe "enviámos um
   código", nunca o código. (CLAUDE.md, Never Do)
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

#: Custo de scrypt. 2^15 ≈ 100 ms por hash em hardware de 2020. É o ponto de
#: equilíbrio: mais alto estrangula o login em máquinas fracas — o número de
#: tentativas já está limitado a 5 por 15 minutos — e mais baixo torna o ataque
#: offline barato. Nunca descer sem rever NFR-4.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

#: O email do utilizador nunca entra no hash de OTP, só o código, para que o
#: hash seja verificável sem lookup.
_OTP_ALPHABET = "0123456789"
OTP_DIGITS = 6

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
MIN_PASSWORD_LENGTH = 12


# --------------------------------------------------------------------------
# Tempo
# --------------------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_iso(raw: str) -> datetime:
    """Lê timestamps gravados. Tolera o `Z` que versões antigas do SQLite dão.

    Levanta `ValueError` em vez de devolver um sentinel: quem chama precisa de
    distinguir "data válida" de "data inválida", e um `None` silencioso
    transformar-se-ia numa data que nunca expira.
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"timestamp inválido: {raw!r}")
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# --------------------------------------------------------------------------
# Validação
# --------------------------------------------------------------------------


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(normalise_email(email)))


def password_problem(password: str) -> str | None:
    """Devolve a razão pela qual a palavra-passe é unacceptable, ou `None`.

    A regra é comprimento, não composição. forcing símbolos à mixture produz
    `Passw0rd!` em todo o lado e não aumenta a entropia real.
    """
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"A palavra-passe precisa de pelo menos {MIN_PASSWORD_LENGTH} caracteres."
    if not password.strip():
        return "A palavra-passe não pode ser só espaços."
    return None


# --------------------------------------------------------------------------
# Palavras-passe
# --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$hash` — todos os parâmetros no próprio campo.

    Guardá-los permite subir o custo mais tarde sem invalidar as palavras-passe
    existentes. É a razão de não ser só um blob.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=_scrypt_maxmem(SCRYPT_N, SCRYPT_R),
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verifica em tempo constante. Nunca levanta em entrada malformada.

    Um `stored` corrompido devolve `False`, não uma excepção: o login tem de
    falhar, não o servidor.
    """
    try:
        algorithm, n_s, r_s, p_s, salt_hex, digest_hex = (stored or "").split("$")
        if algorithm != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
            maxmem=_scrypt_maxmem(int(n_s), int(r_s)),
        )
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------
# Tokens opacos (sessões, convites) e segredos de uso único (OTP)
# --------------------------------------------------------------------------


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Digest de um token de portador. A base de dados guarda o digest, nunca o
    token — um dump da base de dados não dá sessões activas."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_otp(now: datetime | None = None) -> str:
    """`secrets.choice` e não `random`: o OTP é material de autenticação."""
    return "".join(secrets.choice(_OTP_ALPHABET) for _ in range(OTP_DIGITS))


def hash_otp(code: str) -> str:
    """SHA-256 simples: o espaço é de 10^6, não resiste a brute force offline,
    mas a tabela tem TTL de minutos e 5 tentativas. Argon2 aqui custaria ~100 ms
    por tentativa de *ataque*, não por tentativa legítima — desvantagem sem
    vantagem."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def otp_matches(candidate: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_otp((candidate or "").strip()), stored_hash or "")


def otp_expiry(now: datetime | None = None, ttl_minutes: int = 10) -> datetime:
    return (now or utcnow()) + timedelta(minutes=ttl_minutes)


# --------------------------------------------------------------------------
# Dispositivos
# --------------------------------------------------------------------------


def device_fingerprint(user_agent: str, accept_language: str) -> str:
    """Identidade estável e opaca do navegador.

    Não é segurança — é UX. Um `User-Agent` completo no fingerprint seria
    reversível para o dono do site; o SHA-256 trunca o identificador e o
    User-Agent alheio não é legível a partir dele. Um browser diferente dá
    outro hash, e é exactamente isso que queremos ver no segundo factor.
    """
    raw = f"{user_agent or ''}\n{accept_language or ''}".strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def describe_device(user_agent: str) -> str:
    """Rótulo legível para a lista de dispositivos.

    Puramente informativo. Se não soubermos o browser, diz `Desconhecido` em vez
    de despejar a string crua na UI.
    """
    ua = user_agent or ""
    for needle, name in (
        ("Edg/", "Edge"),
        ("OPR/", "Opera"),
        ("Firefox/", "Firefox"),
        ("Chrome/", "Chrome"),
        ("Thunderbird", "Thunderbird"),
        ("Safari/", "Safari"),
    ):
        if needle in ua:
            return name
    if ua:
        return "Outro cliente"
    return "Desconhecido"


# --------------------------------------------------------------------------
# Datas
# --------------------------------------------------------------------------


def in_minutes(minutes: int, now: datetime | None = None) -> datetime:
    return (now or utcnow()) + timedelta(minutes=minutes)


def in_hours(hours: int, now: datetime | None = None) -> datetime:
    return (now or utcnow()) + timedelta(hours=hours)


def in_days(days: int, now: datetime | None = None) -> datetime:
    return (now or utcnow()) + timedelta(days=days)


def is_expired(expires_at: str, now: datetime | None = None) -> bool:
    try:
        return parse_iso(expires_at) <= (now or utcnow())
    except (ValueError, TypeError, OverflowError):
        # Um timestamp ilegível é tratado como expirado. Falhar fechado é
        # correcto para uma data de expiração.
        return True


def _scrypt_maxmem(n: int, r: int) -> int:
    """Memória máxima para `hashlib.scrypt`.

    scrypt precisa de ~`128 * n * r` bytes. A stdlib aplica um limite por
    omissão de 32 MiB, que a 2^15 × 8 dá por exceeded — daí o cálculo
    explícito, com 2× de folga para o motor ter margem.
    """
    return 128 * n * r * 2


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "SCRYPT_N",
    "describe_device",
    "device_fingerprint",
    "hash_otp",
    "hash_password",
    "hash_token",
    "in_days",
    "in_hours",
    "in_minutes",
    "is_expired",
    "is_valid_email",
    "iso",
    "new_otp",
    "new_token",
    "normalise_email",
    "otp_expiry",
    "otp_matches",
    "parse_iso",
    "password_problem",
    "utcnow",
    "verify_password",
]
