"""Construir a assinatura: o pipeline **único** que produz o que vai sair.

Este módulo nasceu no `T017-B` com uma pergunta: o compositor tem de anexar a
assinatura ao email, e como é que o faz sem duplicar o caminho?

A resposta é que não duplica: usa este. O preview, a exportação e agora o envio
passam todos por `construir()`. A alternativa era o compositor ter a sua própria
cópia de `build_signature_data` → `render_html` → `render_plain`, e as duas
divergirem no primeiro patch — que é o que acontece a qualquer cópia deste
ficheiro que não seja esta.

O `routes.py` de `signatures` importava isto como função local e agora importa
daqui. Nada mudou para ele: os testes do T003 passam sem alteração, e é esse o
teste de que uma extracção é uma refactorização e não uma reescrita.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..config import Settings
from . import renderer, spam

#: O nome da assinatura de que o produto faz uso. Há uma só, por decisão: um
#: operador com duas assinaturas não tem uma assinatura principal, tem duas
#: identidades, e escolher entre elas no envio seria um `select` que ninguém
#: pediu.
SIGNATURE_NAME = "assinatura principal"

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


def carregar_campos(raw: str) -> dict[str, str]:
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


def construir(
    settings: Settings,
    data: dict[str, str],
    theme: str,
    logo: dict[str, Any] | None,
    layout: str = renderer.DEFAULT_LAYOUT,
) -> dict[str, Any]:
    """Pipeline único de construção: dados → HTML → texto → score.

    Preview, página inicial, exportação e envio passam todos por aqui. Um único
    caminho é a única forma de a assinatura mostrada ser a assinatura entregue.
    (FR-3.8)
    """
    payload = dict(data)
    payload["logo_url"] = (logo or {}).get("url", "")
    payload["theme"] = theme
    payload["layout"] = layout
    built = renderer.build_signature_data(payload, settings)
    html = renderer.render_html(built, settings)
    plain = renderer.render_plain(built, settings)
    return {
        "html": html,
        "plain": plain,
        "score": spam.score_signature(html, plain),
        "dados": built,
    }


def carregar_assinatura(conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM signatures WHERE user_id = ? AND name = ?", (user_id, SIGNATURE_NAME)
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "theme": row["theme"],
        "layout": row["layout"],
        "logo_id": row["logo_id"],
        "fields": carregar_campos(row["fields_json"]),
    }


def carregar_logo(
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
