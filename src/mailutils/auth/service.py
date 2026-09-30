"""Regras de negócio da autenticação e do segundo factor.

Vive em `service.py` e não nas rotas para que cada decisão — quem pode entrar,
quando se pede um código, o que acontece à 6.ª tentativa — seja testável sem
passar por HTTP. As rotas ficam com parsing e templates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import security
from ..config import Settings
from ..db import transaction
from ..mailer import send_invite, send_otp

#: Motivo de bloqueio devolvido ao login. Exposto como constante porque a rota o
#: mostra ao utilizador e o teste precisa de o referenciar.
LOCKED_OUT = "demasiadas tentativas"
INVALID_CREDENTIALS = "email ou palavra-passe inválidos"
ACCOUNT_INACTIVE = "conta desactivada"
CHALLENGE_SENT = "código enviado"
#: O pedido caiu dentro da janela de espera, e portanto **não** saiu código
#: nenhum. Antes partilhava a razão de `CHALLENGE_SENT`, e a interface dizia
#: «Enviámos um código para o seu email» sem ter enviado nada — que é como uma
#: pessoa passa meia hora à procura de um email que não existe. (F-16)
CHALLENGE_ENVIADO_HA_POUCO = "código já enviado há pouco"
INVALID_CODE = "código inválido ou expirado"
CHALLENGE_EXHAUSTED = "demasiadas tentativas — peça um novo código"
EMAIL_SEND_FAILED = "não foi possível enviar o email"


@dataclass
class LoginOutcome:
    """Resultado de `login`. `challenge` distingue o caminho de 2F do caminho
    normal; a rota nunca decide isso sozinha."""

    ok: bool
    reason: str = ""
    user_id: int = 0
    email: str = ""
    must_verify: bool = False
    challenge_id: int = 0
    retry_after: int = 0


# --------------------------------------------------------------------------
# Utilizadores
# --------------------------------------------------------------------------


def create_user(
    conn: sqlite3.Connection,
    email: str,
    password: str,
    *,
    is_admin: bool = False,
    must_change_password: bool = False,
) -> int:
    """Cria um utilizador. Levanta `ValueError` com mensagem em pt-PT."""
    normalised = security.normalise_email(email)
    if not security.is_valid_email(normalised):
        raise ValueError("Email inválido.")
    problem = security.password_problem(password)
    if problem:
        raise ValueError(problem)
    now = security.iso(security.utcnow())
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash, is_admin, is_active,"
            " must_change_password, created_at, password_changed_at)"
            " VALUES (?, ?, ?, 1, ?, ?, ?)",
            (
                normalised,
                security.hash_password(password),
                1 if is_admin else 0,
                1 if must_change_password else 0,
                now,
                None if must_change_password else now,
            ),
        )
    return int(cursor.lastrowid or 0)


def get_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE email = ?", (security.normalise_email(email),)
    ).fetchone()


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def set_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    problem = security.password_problem(password)
    if problem:
        raise ValueError(problem)
    with transaction(conn):
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0,"
            " password_changed_at = ? WHERE id = ?",
            (security.hash_password(password), security.iso(security.utcnow()), user_id),
        )


def set_active(conn: sqlite3.Connection, user_id: int, active: bool) -> None:
    """Desactivar uma conta tem de matar as sessões, não só bloquear o login
    seguinte. Caso contrário, um cookie válido continua a valer."""
    with transaction(conn):
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
        if not active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def bootstrap_admin(conn: sqlite3.Connection, settings: Settings) -> str:
    """Cria o primeiro utilizador a partir do `.env`. Idempotente. (FR-1.1)

    Devolve o que fez, para o operador ver no arranque sem ter de abrir a BD.
    Não loga a palavra-passe, nunca.
    """
    if not settings.admin_email or not settings.admin_password:
        return "MAILUTILS_ADMIN_EMAIL/PASSWORD por definir — nenhum admin criado."

    existing = get_user_by_email(conn, settings.admin_email)
    if existing is not None:
        # O `.env` é a fonte do primeiro arranque, não um override silencioso
        # de palavras já mudadas. Reescrever a password a cada arranque seria
        # uma porta aberta que ninguém notaria.
        if not existing["is_admin"]:
            with transaction(conn):
                conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (existing["id"],))
        return f"Admin {settings.admin_email} já existe."

    problem = security.password_problem(settings.admin_password)
    if not security.is_valid_email(settings.admin_email):
        return "MAILUTILS_ADMIN_EMAIL inválido — nenhum admin criado."
    if problem:
        return f"{problem} — nenhum admin criado."

    user_id = create_user(conn, settings.admin_email, settings.admin_password, is_admin=True)
    return f"Admin {settings.admin_email} criado (id {user_id})."


# --------------------------------------------------------------------------
# Limitação de tentativas de login
# --------------------------------------------------------------------------


def login_identifier(email: str, ip: str) -> str:
    return f"{security.normalise_email(email)}|{ip or 'unknown'}"


def is_locked_out(conn: sqlite3.Connection, identifier: str, settings: Settings) -> bool:
    cutoff = security.iso(security.in_minutes(-settings.login_window_minutes))
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM login_attempts"
        " WHERE identifier = ? AND success = 0 AND created_at > ?",
        (identifier, cutoff),
    ).fetchone()
    return bool(row and row["n"] >= settings.login_max_attempts)


def record_login_attempt(
    conn: sqlite3.Connection,
    settings: Settings,
    identifier: str,
    ip: str,
    success: bool,
) -> None:
    cutoff = security.iso(security.in_minutes(-settings.login_window_minutes))
    with transaction(conn):
        conn.execute(
            "INSERT INTO login_attempts (identifier, ip, success, created_at) VALUES (?, ?, ?, ?)",
            (identifier, ip or "", 1 if success else 0, security.iso(security.utcnow())),
        )
        # Limpar fora da janela é o que impede a tabela de crescer sem limite
        # num servidor que vive anos.
        conn.execute("DELETE FROM login_attempts WHERE created_at < ?", (cutoff,))


# --------------------------------------------------------------------------
# Dispositivos
# --------------------------------------------------------------------------


def known_device(conn: sqlite3.Connection, user_id: int, fingerprint: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM devices WHERE user_id = ? AND fingerprint = ?",
        (user_id, fingerprint),
    ).fetchone()


def register_device(
    conn: sqlite3.Connection,
    settings: Settings,
    user_id: int,
    fingerprint: str,
    user_agent: str,
    label: str,
) -> int:
    """Regista o dispositivo e poda os antigos.

    A poda é por `created_at` e não por `last_seen_at`: o limite existe para
    não deixar uma conta com 4000 dispositivos registados, não para premiar
    quem entra muitas vezes.
    """
    now = security.utcnow()
    with transaction(conn):
        conn.execute(
            "INSERT INTO devices"
            " (user_id, fingerprint, user_agent, label, created_at, last_seen_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id, fingerprint)"
            " DO UPDATE SET last_seen_at = excluded.last_seen_at",
            (
                user_id,
                fingerprint,
                user_agent[:200],
                label,
                security.iso(now),
                security.iso(now),
            ),
        )
        keep = conn.execute(
            "SELECT id FROM devices WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, settings.max_devices),
        ).fetchall()
        keep_ids = {row["id"] for row in keep}
        stale = conn.execute("SELECT id FROM devices WHERE user_id = ?", (user_id,)).fetchall()
        for row in stale:
            if row["id"] not in keep_ids:
                conn.execute("DELETE FROM devices WHERE id = ?", (row["id"],))
    row = known_device(conn, user_id, fingerprint)
    return int(row["id"]) if row else 0


def list_devices(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM devices WHERE user_id = ? ORDER BY last_seen_at DESC", (user_id,)
    ).fetchall()


def revoke_device(conn: sqlite3.Connection, user_id: int, device_id: int) -> bool:
    """Revogar um dispositivo tem de invalidar as suas sessões. Uma sessão que
    sobrevive à revogação do dispositivo é uma sessão que não se pode auditar."""
    with transaction(conn):
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND device_id = ?", (user_id, device_id)
        )
        cursor = conn.execute(
            "DELETE FROM devices WHERE id = ? AND user_id = ?", (device_id, user_id)
        )
    return bool(cursor.rowcount)


# --------------------------------------------------------------------------
# Códigos OTP
# --------------------------------------------------------------------------


def cooldown_remaining(conn: sqlite3.Connection, email: str, settings: Settings) -> int:
    """Segundos que faltam para poder pedir outro código.

    O cooldown é por *email* e não por dispositivo: o abuso a evitar é
    inundar a caixa de correio de alguém, e isso não depende de onde o pedido
    veio.
    """
    row = conn.execute(
        "SELECT created_at FROM otp_codes WHERE purpose = 'login' AND consumed_at IS NULL"
        " AND user_id IN (SELECT id FROM users WHERE email = ?)"
        " ORDER BY created_at DESC LIMIT 1",
        (security.normalise_email(email),),
    ).fetchone()
    if row is None:
        return 0
    try:
        created = security.parse_iso(row["created_at"])
    except (ValueError, TypeError):
        return 0
    elapsed = (security.utcnow() - created).total_seconds()
    remaining = settings.otp_cooldown_seconds - elapsed
    return int(remaining) + 1 if remaining > 0 else 0


def issue_challenge(
    conn: sqlite3.Connection, settings: Settings, user_id: int, device_fp: str
) -> tuple[int, str]:
    """Cria um OTP e envia-o. Devolve (id do desafio, código).

    O código volta para a função porque o único consumidor legítimo é o
    `send_otp` logo a seguir. A rota nunca o vê: `login()` devolve só um
    resultado sem segredo.
    """
    code = security.new_otp()
    now = security.utcnow()
    expires = security.otp_expiry(now, settings.otp_ttl_minutes)
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO otp_codes (user_id, device_fp, code_hash, purpose, created_at, expires_at)"
            " VALUES (?, ?, ?, 'login', ?, ?)",
            (user_id, device_fp, security.hash_otp(code), security.iso(now), security.iso(expires)),
        )
    return int(cursor.lastrowid or 0), code


def get_challenge(conn: sqlite3.Connection, challenge_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM otp_codes WHERE id = ?", (challenge_id,)).fetchone()


def consume_challenge(conn: sqlite3.Connection, challenge_id: int) -> None:
    """Marca como usado. Invalidar mesmo numa tentativa falhada é deliberado:
    manter o código vivo depois de uma tentativa errada dá ao atacante mais
    tentativas de graça e o utilizador nem repara."""
    with transaction(conn):
        conn.execute(
            "UPDATE otp_codes SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
            (security.iso(security.utcnow()), challenge_id),
        )


def _validate_challenge(
    conn: sqlite3.Connection, settings: Settings, challenge_id: int, code: str
) -> tuple[bool, str]:
    row = get_challenge(conn, challenge_id)
    if row is None:
        return False, INVALID_CODE
    if row["consumed_at"] is not None or security.is_expired(row["expires_at"]):
        return False, INVALID_CODE
    if row["attempts"] >= settings.otp_max_attempts:
        return False, CHALLENGE_EXHAUSTED

    with transaction(conn):
        conn.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = ?", (challenge_id,))
    consume_challenge(conn, challenge_id)
    if not security.otp_matches(code, row["code_hash"]):
        return False, INVALID_CODE
    return True, ""


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


def login(
    conn: sqlite3.Connection,
    settings: Settings,
    email: str,
    password: str,
    fingerprint: str,
    user_agent: str,
    ip: str,
) -> LoginOutcome:
    """Autentica. Decide entre login directo e desafio de segundo factor.

    O caminho de 2F é o mesmo para palavras-passe correctas e incorrectas: só
    o resultado difere. Dizer a um atacante "a password está certa mas é um
    dispositivo novo" é confirmar metade do segredo.
    """
    identifier = login_identifier(email, ip)
    if is_locked_out(conn, identifier, settings):
        return LoginOutcome(ok=False, reason=LOCKED_OUT)

    user = get_user_by_email(conn, email)
    valid = user is not None and security.verify_password(password, user["password_hash"])
    if not valid:
        record_login_attempt(conn, settings, identifier, ip, success=False)
        return LoginOutcome(ok=False, reason=INVALID_CREDENTIALS)
    if not user["is_active"]:
        record_login_attempt(conn, settings, identifier, ip, success=False)
        return LoginOutcome(ok=False, reason=ACCOUNT_INACTIVE)

    record_login_attempt(conn, settings, identifier, ip, success=True)
    known = known_device(conn, user["id"], fingerprint)
    if known is not None:
        register_device(conn, settings, user["id"], fingerprint, user_agent, known["label"])
        return LoginOutcome(ok=True, user_id=user["id"], email=user["email"])

    remaining = cooldown_remaining(conn, email, settings)
    if remaining > 0:
        # Não se envia código. A resposta é a mesma que um dispositivo novo
        # receberia, mas o texto **não** é: a interface diz que há um código a
        # caminho e diz quanto falta. Um ecrã que promete um email e não o
        # envia é pior do que um ecrã que não promete nada.
        return LoginOutcome(
            ok=False,
            must_verify=True,
            reason=CHALLENGE_ENVIADO_HA_POUCO,
            retry_after=remaining,
        )

    challenge_id, code = issue_challenge(conn, settings, user["id"], fingerprint)
    try:
        send_otp(settings, user["email"], code)
    except Exception:
        # O código foi gravado mas nunca saiu. Inutilizá-lo evita que alguém
        # com acesso à base de dados o use.
        consume_challenge(conn, challenge_id)
        return LoginOutcome(ok=False, reason=EMAIL_SEND_FAILED)
    return LoginOutcome(
        ok=False, must_verify=True, reason=CHALLENGE_SENT, challenge_id=challenge_id
    )


def verify_challenge(
    conn: sqlite3.Connection,
    settings: Settings,
    challenge_id: int,
    code: str,
    fingerprint: str,
    user_agent: str,
) -> LoginOutcome:
    """Segunda etapa: valida o código e, se certo, regista o dispositivo."""
    row = get_challenge(conn, challenge_id)
    if row is None:
        return LoginOutcome(ok=False, reason=INVALID_CODE)
    user = get_user(conn, row["user_id"])
    if user is None or not user["is_active"]:
        return LoginOutcome(ok=False, reason=INVALID_CODE)

    accepted, reason = _validate_challenge(conn, settings, challenge_id, code)
    if not accepted:
        return LoginOutcome(ok=False, must_verify=True, reason=reason)

    register_device(
        conn,
        settings,
        user["id"],
        fingerprint,
        user_agent,
        security.describe_device(user_agent),
    )
    return LoginOutcome(ok=True, user_id=user["id"], email=user["email"])


# --------------------------------------------------------------------------
# Convites
# --------------------------------------------------------------------------


def create_invite(
    conn: sqlite3.Connection, settings: Settings, email: str, created_by: int
) -> tuple[str, str]:
    """Cria um convite. Devolve (token em claro, url). O token em claro é a
    única vez que existe; a base de dados guarda o digest.

    Um convite pendente anterior para o mesmo email é **revogado**. Sem isso,
    "criar convite" duas vezes deixa dois tokens vivos, e revogar um não
    revoga o outro — a pessoa continua com um convite válido que o admin
    acredita ter cancelado. É também o que faz deste acto o caminho de
    reenvio: o segundo convite substitui o primeiro.
    """
    normalised = security.normalise_email(email)
    if not security.is_valid_email(normalised):
        raise ValueError("Email inválido.")
    user = get_user_by_email(conn, normalised)
    if user is not None:
        raise ValueError("Já existe um utilizador com esse email.")

    token = security.new_token()
    now = security.utcnow()
    with transaction(conn):
        conn.execute(
            "UPDATE invites SET revoked_at = ? WHERE email = ?"
            " AND accepted_at IS NULL AND revoked_at IS NULL",
            (security.iso(now), normalised),
        )
        conn.execute(
            "INSERT INTO invites (email, token_hash, created_by, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                normalised,
                security.hash_token(token),
                created_by,
                security.iso(now),
                security.iso(security.in_hours(settings.invite_ttl_hours, now)),
            ),
        )
    # O link vai por email para um browser que não sabe nada desta
    # instalação. Sem o prefixo, é um 404 — e o único sintoma é um convite que
    # "não chega".
    url = f"{settings.public_url('/convite')}/{token}"
    return token, url


def send_invitation(
    conn: sqlite3.Connection, settings: Settings, email: str, created_by: int
) -> str:
    token, url = create_invite(conn, settings, email, created_by)
    send_invite(settings, security.normalise_email(email), url)
    return url


def get_invite_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM invites WHERE token_hash = ?", (security.hash_token(token),)
    ).fetchone()


def accept_invite(conn: sqlite3.Connection, token: str, password: str) -> tuple[bool, str, int]:
    """Aceita um convite e define a palavra-passe.

    Devolve (sucesso, razão, user_id). O utilizador define a própria
    palavra-passe: o admin nunca a vê depois de criar o convite. (FR-1.3)
    """
    invite = get_invite_by_token(conn, token)
    if invite is None:
        return False, "Convite inválido.", 0
    if invite["revoked_at"] is not None:
        return False, "Convite revogado.", 0
    if invite["accepted_at"] is not None:
        return False, "Convite já usado.", 0
    if security.is_expired(invite["expires_at"]):
        return False, "Convite expirado.", 0
    if get_user_by_email(conn, invite["email"]) is not None:
        return False, "Já existe um utilizador com esse email.", 0

    problem = security.password_problem(password)
    if problem:
        return False, problem, 0

    user_id = create_user(
        conn, invite["email"], password, is_admin=False, must_change_password=False
    )
    with transaction(conn):
        conn.execute(
            "UPDATE invites SET accepted_at = ? WHERE id = ? AND accepted_at IS NULL",
            (security.iso(security.utcnow()), invite["id"]),
        )
    return True, "", user_id


def list_invites(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM invites ORDER BY created_at DESC").fetchall()


def revoke_invite(conn: sqlite3.Connection, invite_id: int) -> bool:
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE invites SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (security.iso(security.utcnow()), invite_id),
        )
    return bool(cursor.rowcount)


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, email, is_admin, is_active, created_at FROM users ORDER BY email"
    ).fetchall()


__all__ = [
    "ACCOUNT_INACTIVE",
    "CHALLENGE_EXHAUSTED",
    "CHALLENGE_SENT",
    "EMAIL_SEND_FAILED",
    "CHALLENGE_ENVIADO_HA_POUCO",
    "INVALID_CODE",
    "INVALID_CREDENTIALS",
    "LOCKED_OUT",
    "LoginOutcome",
    "accept_invite",
    "bootstrap_admin",
    "cooldown_remaining",
    "consume_challenge",
    "create_invite",
    "create_user",
    "get_challenge",
    "get_invite_by_token",
    "get_user",
    "get_user_by_email",
    "issue_challenge",
    "is_locked_out",
    "known_device",
    "list_devices",
    "list_invites",
    "list_users",
    "login",
    "login_identifier",
    "record_login_attempt",
    "register_device",
    "revoke_device",
    "revoke_invite",
    "send_invitation",
    "set_active",
    "set_password",
    "verify_challenge",
]
