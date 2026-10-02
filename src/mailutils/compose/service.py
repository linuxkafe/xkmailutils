"""Composição e envio de emails com score de spam.

Este módulo é o coração do T015/T017-B: compor, pontuar, bloquear, enviar.
A regra de ouro (FR-7.3): **o email que sai passa pelo mesmo `spam.py` que
avalia a assinatura e é bloqueado pelo mesmo critério**. Não há caminho
alternativo, não há "mais tolerante". Se a assinatura bloqueia, o email
bloqueia.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mailutils import config, db, mailer, security
from mailutils.signatures import spam, renderer
from mailutils.lists import service as lists_service
from mailutils.db import loads_fields


SIGNATURE_NAME = "assinatura principal"


def _load_signature(
    conn: db.sqlite3.Connection, user_id: int
) -> dict[str, Any] | None:
    """Carrega a assinatura do utilizador (nome por defeito)."""
    row = conn.execute(
        "SELECT * FROM signatures WHERE user_id = ? AND name = ?",
        (user_id, SIGNATURE_NAME),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "theme": row["theme"],
        "layout": row["layout"],
        "logo_id": row["logo_id"],
        "fields": loads_fields(row["fields_json"]),
    }


def _load_logo(
    settings: config.Settings, conn: db.sqlite3.Connection, signature: dict[str, Any] | None
) -> dict[str, Any] | None:
    if not signature or not signature.get("logo_id"):
        return None
    row = conn.execute("SELECT * FROM logos WHERE id = ?", (signature["logo_id"],)).fetchone()
    if row is None:
        return None
    return {
        "url": settings.public_media_url(row["filename"]),
        "width": row["width"],
        "height": row["height"],
        "bytes": row["byte_size"],
    }


def _build_signature_data(
    conn: db.sqlite3.Connection, user_id: int, settings: config.Settings
) -> dict[str, Any] | None:
    """Constrói o dicionário completo para o renderer."""
    signature = _load_signature(conn, user_id)
    if not signature:
        return None
    logo = _load_logo(settings, conn, signature)
    return {
        "theme": signature["theme"],
        "layout": signature["layout"],
        "fields": signature["fields"],
        "logo": logo,
    }


def _render_signature(
    conn: db.sqlite3.Connection, user_id: int, settings: config.Settings
) -> tuple[str | None, str | None]:
    """Renderiza a assinatura do utilizador (HTML e texto)."""
    data = _build_signature_data(conn, user_id, settings)
    if not data:
        return None, None
    html = renderer.render_html(renderer.SignatureData(**data), settings)
    plain = renderer.render_plain(renderer.SignatureData(**data), settings)
    return html, plain


def _render_email_body(
    body_text: str,
    signature_html: str | None,
    signature_text: str | None,
) -> tuple[str, str]:
    """Constrói o HTML e texto simples do email completo.

    A assinatura é anexada ao corpo. Se não houver assinatura, usa só o corpo.
    """
    if signature_html:
        html = f"""
<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#212121;line-height:1.6;max-width:600px;">
    {body_text.replace(chr(10), "<br>")}
    <hr style="border:none;border-top:1px solid #ddd;margin:24px 0;">
    {signature_html}
</div>
"""
    else:
        html = f"""
<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#212121;line-height:1.6;max-width:600px;">
    {body_text.replace(chr(10), "<br>")}
</div>
"""

    if signature_text:
        text = f"{body_text}\n\n-- \n{signature_text}"
    else:
        text = body_text

    return html, text


@dataclass(frozen=True)
class ComposeResult:
    """Resultado da composição/validação antes de enviar."""
    score: int
    categoria: str
    categoria_acentuada: str
    regras: list[dict]
    bloqueado: bool
    aviso: str


@dataclass(frozen=True)
class SendResult:
    """Resultado do envio/agendamento."""
    enviado: int
    falhado: int
    omitido: int
    envio_id: int | None = None
    agendado: bool = False


def compose_and_score(
    conn: db.sqlite3.Connection,
    user_id: int,
    subject: str,
    body_text: str,
    settings: config.Settings,
    attach_signature: bool = True,
) -> ComposeResult:
    """Compõe o email completo (corpo + assinatura) e calcula o score de spam.

    Esta é a função que o editor chama via AJAX a cada alteração —
    por isso tem de ser rápida e determinística.
    """
    # 1. Renderiza assinatura (se houver e se solicitado)
    signature_html = None
    signature_text = None
    if attach_signature:
        signature_html, signature_text = _render_signature(conn, user_id, settings)

    # 2. Renderiza email completo (corpo + assinatura)
    html, text = _render_email_body(body_text, signature_html, signature_text)

    # 3. Adiciona assunto ao HTML para score completo (o spam.py espera HTML completo)
    full_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{subject}</title></head>
<body>{html}</body>
</html>"""

    # 4. Calcula score com o MESMO motor das assinaturas (FR-7.2)
    score_result = spam.score_signature(full_html, text)

    # 5. Política de bloqueio: mesma da assinatura (FR-7.3, FR-4.9)
    bloqueado = score_result["exportacao_bloqueada"]

    return ComposeResult(
        score=score_result["score"],
        categoria=score_result["categoria"],
        categoria_acentuada=score_result["categoria_acentuada"],
        regras=score_result["regras"],
        bloqueado=bloqueado,
        aviso=score_result["aviso"],
    )


def enviar_agora(
    conn: db.sqlite3.Connection,
    user_id: int,
    list_id: int,
    subject: str,
    body_text: str,
    settings: config.Settings,
    attach_signature: bool = True,
) -> SendResult:
    """Envia o email para todos os destinatários confirmados da lista.

    Valida o score ANTES de enviar (FR-7.3). Se bloqueado, não envia nada
    e devolve resultado com `bloqueado=True`.
    """
    # 1. Valida score ANTES de qualquer envio (FR-7.3)
    compose_result = compose_and_score(
        conn, user_id, subject, body_text, settings, attach_signature=True
    )
    if compose_result.bloqueado:
        return SendResult(
            enviado=0,
            falhado=0,
            omitido=0,
            envio_id=None,
            agendado=False,
        )

    # 2. Busca destinatários válidos (confirmados + não descadenciados)
    destinatarios = lists_service.destinatarios(conn, list_id)
    if not destinatarios:
        return SendResult(enviado=0, falhado=0, omitido=0, envio_id=None, agendado=False)

    # 2. Prepara assinatura (uma vez só)
    signature_html = None
    signature_text = None
    if attach_signature:
        signature_html, signature_text = _render_signature(conn, user_id, settings)

    # 3. Envia em bloco, conta resultados
    enviados = 0
    falhados = 0
    for dest in destinatarios:
        try:
            html, text = _render_email_body(body_text, signature_html, signature_text)
            mailer.send(
                settings,
                dest["email"],
                subject,
                text,
                html,
            )
            enviados += 1
        except Exception:
            # FR-7.7: falha num não aborta os outros; razão = classe da excepção
            falhados += 1

    return SendResult(enviado=enviados, falhado=falhados, omitido=0, envio_id=None, agendado=False)


def agendar_envio(
    conn: db.sqlite3.Connection,
    user_id: int,
    list_id: int,
    subject: str,
    body_text: str,
    scheduled_at: str,
    settings: config.Settings,
    attach_signature: bool = True,
) -> SendResult:
    """Agenda um envio para depois. Valida score AGORA (não depois).

    Se o score bloqueia, não agenda — devolve erro imediato.
    """
    # Valida score AGORA (FR-7.3: mesma política, avaliação antecipada)
    compose_result = compose_and_score(
        conn, user_id, subject, body_text, settings, attach_signature=True
    )
    if compose_result.bloqueado:
        return SendResult(enviado=0, falhado=0, omitido=0, envio_id=None, agendado=False)

    # Insere na fila de envios agendados
    agora = security.iso(security.utcnow())
    with db.transaction(conn):
        cursor = conn.execute(
            """INSERT INTO list_envios
               (list_id, subject, body, state, scheduled_at, created_at)
               VALUES (?, ?, ?, 'agendado', ?, ?)""",
            (list_id, subject, body_text, scheduled_at, security.iso(security.utcnow())),
        )
        envio_id = cursor.lastrowid

    return SendResult(enviado=0, falhado=0, omitido=0, envio_id=envio_id, agendado=True)