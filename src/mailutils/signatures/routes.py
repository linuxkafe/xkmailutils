"""Rotas do gerador de assinaturas: edição, preview, score e exportação.

O preview e a exportação usam **o mesmo** `render_html`. Não há uma segunda
implementação "para o preview": seria o ponto onde a assinatura mostrada e a
assinatura entregue divergem, e o utilizador só descobriria isso ao enviar
email. (FR-3.8)
"""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from .. import security
from ..config import Settings
from ..db import dumps_fields, loads_fields, transaction
from ..templates import page
from ..web import Session, csrf_is_valid, get_db, ir, require_session
from . import images, renderer, spam

router = APIRouter(prefix="/assinatura")

Db = Annotated[sqlite3.Connection, Depends(get_db)]
Active = Annotated[Session, Depends(require_session)]

SIGNATURE_NAME = "assinatura principal"

#: Campos de texto aceite no formulário. Lista explícita: aceitar um dict
#: arbitrário faria com que uma chave `logo_url` pudesse apontar para um host
#: escolhido pelo utilizador — ou seja, um beacon.
FIELD_KEYS = (
    "name",
    "role",
    "company",
    "email",
    "phone",
    "website",
    "linkedin",
    "github",
    "mastodon",
    "address",
    "note",
)

_CLIENTS = ("thunderbird", "outlook", "gmail", "applemail")


# --------------------------------------------------------------------------
# Editor
# --------------------------------------------------------------------------


@router.get("")
def editor(request: Request, conn: Db, session: Active) -> Response:
    settings = request.app.state.settings
    signature = _load_signature(conn, session.user_id)
    context: dict[str, Any] = {
        "fields": signature["fields"] if signature else {},
        "logo": _load_logo(settings, conn, signature),
        "temas": renderer.THEMES,
        "clientes": _CLIENTS,
        "instrucoes": {c: renderer.client_instructions(c) for c in _CLIENTS},
        "saved": signature is not None,
        "tema_assinatura": (signature["theme"] if signature else renderer.DEFAULT_THEME),
    }
    # Mesmo sem assinatura guardada, o preview e o score são calculados: o
    # utilizador vê de imediato o que o score faz com um HTML vazio, e isso
    # ensina-o a ler o número antes de o ver subir.
    built = _build(settings, context["fields"], context["tema_assinatura"], context["logo"])
    context.update(built)
    return page(request, "editor.html", context)


@router.post("/preview")
async def preview(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
    fields: Annotated[str, Form()] = "",
    theme: Annotated[str, Form()] = "dark",
) -> Response:
    """Recalcula o HTML e o score sem recarregar a página.

    Chama exactamente o mesmo `_build` que a exportação usa. Um segundo
    caminho de render seria o sítio onde a assinatura mostrada e a entregue
    divergem — e o utilizador só o descobriria ao enviar email. (FR-3.8)
    """
    if not csrf_is_valid(session, csrf_token):
        return Response(
            content='{"erro": "sessão expirada, recarregue a página"}',
            media_type="application/json",
            status_code=403,
        )
    settings = request.app.state.settings
    signature = _load_signature(conn, session.user_id)
    built = _build(
        settings,
        _form_fields(fields),
        theme,
        _load_logo(settings, conn, signature),
    )
    return Response(
        content=_json(built),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/guardar")
async def save(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
    fields: Annotated[str, Form()] = "",
    theme: Annotated[str, Form()] = "dark",
    logo_id: Annotated[int, Form()] = 0,
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/assinatura?erro=csrf")
    data = _form_fields(fields)
    now = security.iso(security.utcnow())
    with transaction(conn):
        # `COALESCE` e não `excluded.logo_id`: o formulário de edição não leva
        # um campo de logótipo, e um `logo_id` vazio apagaria a imagem que o
        # upload acabou de associar. Remover o logótipo é uma acção separada,
        # explícita, com a sua própria rota.
        conn.execute(
            "INSERT INTO signatures"
            " (user_id, name, theme, fields_json, logo_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id, name) DO UPDATE SET"
            " theme = excluded.theme,"
            " fields_json = excluded.fields_json,"
            " logo_id = COALESCE(excluded.logo_id, signatures.logo_id),"
            " updated_at = excluded.updated_at",
            (session.user_id, SIGNATURE_NAME, theme, dumps_fields(data), logo_id or None, now, now),
        )
    return ir(request, "/assinatura?aviso=guardada")


# --------------------------------------------------------------------------
# Logótipo
# --------------------------------------------------------------------------


@router.post("/logotipo")
async def upload_logo(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/assinatura?erro=csrf")
    if file is None:
        return ir(request, "/assinatura?erro=logotipo")

    settings = request.app.state.settings
    payload = file.file.read(settings.max_upload_bytes + 1)
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO logos (user_id, filename, content_type, byte_size, width,"
            " height, created_at) VALUES (?, 'pendente', '', 0, 0, 0, ?)",
            (session.user_id, security.iso(security.utcnow())),
        )
        logo_id = int(cursor.lastrowid or 0)

    try:
        stored = images.store_upload(
            payload,
            logo_id,
            settings.media_dir,
            settings.max_upload_bytes,
            settings.logo_max_px,
        )
    except images.ImageRejected as exc:
        with transaction(conn):
            conn.execute("DELETE FROM logos WHERE id = ?", (logo_id,))
        return ir(request, f"/assinatura?erro={quote(str(exc)[:80])}")

    with transaction(conn):
        conn.execute(
            "UPDATE logos SET filename = ?, content_type = ?, byte_size = ?,"
            " width = ?, height = ? WHERE id = ?",
            (
                stored.filename,
                stored.content_type,
                stored.byte_size,
                stored.width,
                stored.height,
                logo_id,
            ),
        )
    notice = "logotipo-guardado"
    if stored.note:
        notice = "logotipo-guardado-pequeno"
    now = security.iso(security.utcnow())
    with transaction(conn):
        # UPSERT, não UPDATE: o utilizador pode carregar o logótipo *antes* de
        # guardar o resto dos campos, e nesse momento ainda não existe linha
        # na tabela. Um `UPDATE` seria silenciosamente um no-op e o logótipo
        # ficaria órfão — guardado no disco, visível no editor, ausente do
        # HTML exportado.
        conn.execute(
            "INSERT INTO signatures"
            " (user_id, name, theme, fields_json, logo_id, created_at, updated_at)"
            " VALUES (?, ?, ?, '{}', ?, ?, ?)"
            " ON CONFLICT (user_id, name) DO UPDATE SET"
            " logo_id = excluded.logo_id, updated_at = excluded.updated_at",
            (session.user_id, SIGNATURE_NAME, renderer.DEFAULT_THEME, logo_id, now, now),
        )
    return ir(request, f"/assinatura?aviso={notice}")


@router.post("/logotipo/remover")
def remove_logo(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
) -> Response:
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/assinatura?erro=csrf")
    signature = _load_signature(conn, session.user_id)
    if signature and signature["logo_id"]:
        row = conn.execute(
            "SELECT filename FROM logos WHERE id = ? AND user_id = ?",
            (signature["logo_id"], session.user_id),
        ).fetchone()
        if row:
            images.delete_image(request.app.state.settings.media_dir, row["filename"])
        with transaction(conn):
            conn.execute("DELETE FROM logos WHERE id = ?", (signature["logo_id"],))
            conn.execute(
                "UPDATE signatures SET logo_id = NULL, updated_at = ? WHERE user_id = ?",
                (security.iso(security.utcnow()), session.user_id),
            )
    return ir(request, "/assinatura?aviso=logotipo-removido")


# --------------------------------------------------------------------------
# Exportação
# --------------------------------------------------------------------------


@router.get("/exportar.html")
def export_html(
    request: Request,
    conn: Db,
    session: Active,
) -> Response:
    return _export(request, conn, session, "html")


@router.get("/exportar.txt")
def export_txt(
    request: Request,
    conn: Db,
    session: Active,
) -> Response:
    return _export(request, conn, session, "txt")


def _export(request: Request, conn: sqlite3.Connection, session: Session, kind: str) -> Response:
    """Exporta a assinatura guardada.

    O score `CRÍTICO` bloqueia a exportação. Um botão que permite gerar
    exatamente o HTML que o produto diz ser lixo é uma decisão de produto
    contrária ao objectivo — o utilizador que quiser o ficheiro pode sempre
    gerar HTML à mão, mas a ferramenta não o ajuda a falhar. (FR-4.9)
    """
    settings = request.app.state.settings
    signature = _load_signature(conn, session.user_id)
    if signature is None:
        return ir(request, "/assinatura?erro=vazio")
    built = _build(
        settings,
        signature["fields"],
        signature["theme"],
        _load_logo(settings, conn, signature),
    )
    html, plain, report = built["html"], built["plain"], built["score"]
    if report["exportacao_bloqueada"]:
        return ir(request, "/assinatura?erro=bloqueado")

    if kind == "txt":
        return Response(
            content=plain,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="assinatura.txt"',
                "Cache-Control": "no-store",
            },
        )
    return Response(
        content=_standalone_document(html),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="assinatura.html"',
            "Cache-Control": "no-store",
        },
    )


def _standalone_document(fragment: str) -> str:
    """Envolve o fragmento num documento completo para o ficheiro `.html`.

    O fragmento em si não tem `<html>` porque vai para dentro de um email. Para
    o ficheiro descarregado ser abrível, precisa de um documento. O `<meta
    http-equiv="Content-Security-Policy">` é o mesmo princípio: um ficheiro
    de assinatura não deve carregar scripts nem fazer pedidos a terceiros.
    """
    return (
        '<!doctype html>\n<html lang="pt-PT">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta http-equiv="Content-Security-Policy"'
        " content=\"default-src 'none'; img-src https: http:\">\n"
        "<title>Assinatura de email</title>\n</head>\n"
        '<body style="margin:0;padding:24px;background:#ffffff;">\n'
        f"{fragment}\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------
# Interno
# --------------------------------------------------------------------------


def _form_fields(raw: str) -> dict[str, str]:
    """Lê os campos do formulário a partir de JSON.

    JSON e não campos soltos porque o editor manda links dinamicamente, e uma
    lista de `link_url_1`, `link_url_2`… é uma superfície de input que ninguém
    consegue validar a olho.
    """
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: str(data.get(key, "")) for key in FIELD_KEYS if key in data}


def _build(
    settings: Settings,
    data: dict[str, str],
    theme: str,
    logo: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pipeline único de construção: dados → HTML → texto → score.

    Preview, página inicial e exportação passam todos por aqui. Um único
    caminho é a única forma de a assinatura mostrada ser a assinatura
    entregue. (FR-3.8)
    """
    payload = dict(data)
    payload["logo_url"] = (logo or {}).get("url", "")
    payload["theme"] = theme
    built = renderer.build_signature_data(payload, settings)
    html = renderer.render_html(built, settings)
    plain = renderer.render_plain(built, settings)
    return {
        "html": html,
        "plain": plain,
        "score": spam.score_signature(html, plain),
        "dados": built,
    }


def _load_signature(conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM signatures WHERE user_id = ? AND name = ?", (user_id, SIGNATURE_NAME)
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "theme": row["theme"],
        "logo_id": row["logo_id"],
        "fields": loads_fields(row["fields_json"]),
    }


def _load_logo(
    settings: Settings, conn: sqlite3.Connection, signature: dict[str, Any] | None
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


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(
        {
            "html": payload["html"],
            "plain": payload["plain"],
            "score": payload["score"],
        },
        ensure_ascii=False,
    )
