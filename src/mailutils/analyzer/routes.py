"""Rotas do analisador de email.

Uma página, um endpoint, e **nada guardado**. A análise acontece dentro do
pedido e o resultado é devolvido ao browser. Não há tabela, não há histórico,
não há ficheiro em disco.

A razão não é falta de tempo para um histórico: é que um email colado aqui é
quase sempre spam *recebido*, e spam recebido contém o que o remetente queria
que alguém lesse. Arquivar isso no servidor, sem ninguém pedir, seria guardar
material alheio. A decisão está em `reader.py` e está aqui repetida porque é o
que um revisor precisa de ver primeiro.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from ..templates import page
from ..web import Session, csrf_is_valid, get_db, ir, require_session
from . import reader, scoring

router = APIRouter(prefix="/analisar")

Db = Annotated[sqlite3.Connection, Depends(get_db)]
Active = Annotated[Session, Depends(require_session)]

#: Só o primeiro de vários ficheiros é lido. Um `.eml` é um ficheiro; aceitar
#: três e analisar o terceiro em silêncio seria uma surpresa.
EXAMPLE = (
    "From: Ana Silva <ana@exemplo.pt>\n"
    "To: destinatario@exemplo.pt\n"
    "Subject: Relatório trimestral\n"
    "Date: Mon, 15 Sep 2026 09:30:00 +0100\n"
    "Message-ID: <20260915093000.1@exemplo.pt>\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "\n"
    "Olá,\n\n"
    "Segue o relatório do trimestre. Qualquer dúvida, diz-me.\n\n"
    "-- \n"
    "Ana Silva\n"
)


@router.get("")
def form(request: Request, conn: Db, session: Active) -> Response:
    return page(
        request,
        "analisar.html",
        {
            "exemplo": EXAMPLE,
            "limite_kib": reader.MAX_INPUT_BYTES // 1024,
        },
    )


@router.post("/")
async def analisar(
    request: Request,
    conn: Db,
    session: Active,
    csrf_token: Annotated[str, Form()] = "",
    conteudo: Annotated[str, Form()] = "",
    ficheiro: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """Analisa o email colado ou o ficheiro carregado.

    `GET` e `POST` na mesma rota em vez de duas rotas: o utilizador cola, vê o
    resultado, corrige e reenvia. Com um endpoint separado, o botão de voltar
    perderia o texto colado, e perder texto colado é irritante.
    """
    if not csrf_is_valid(session, csrf_token):
        return ir(request, "/assinatura?erro=csrf")

    bruto, erro = _entrada(ficheiro, conteudo)
    if erro:
        return ir(request, f"/analisar?erro={erro}")

    try:
        email = reader.parse(bruto)
    except reader.InputTooLarge:
        return ir(request, "/analisar?erro=grande")
    if not email.html_body and not email.text_body:
        return ir(request, "/analisar?erro=vazio")

    relatorio = scoring.analyse(email)
    return page(
        request,
        "analisar.html",
        {
            "exemplo": EXAMPLE,
            "limite_kib": reader.MAX_INPUT_BYTES // 1024,
            "relatorio": relatorio,
            "tem_resultado": True,
            "texto": bruto if len(bruto) <= 20_000 else bruto[:20_000] + "\n…",
            "reconheceu_cabecalhos": reader.looks_like_email(bruto),
        },
    )


def _entrada(ficheiro: UploadFile | None, conteudo: str) -> tuple[str, str]:
    """Decide de onde vem o email, e devolve (conteúdo, chave-de-erro).

    O ficheiro tem prioridade sobre a caixa de texto. Não por ser "melhor" —
    por não haver razão para o utilizador preencher as duas coisas e ficar à
    espera de que o servidor adivinhe qual vale.
    """
    if ficheiro is not None and ficheiro.filename:
        payload = ficheiro.file.read(reader.MAX_INPUT_BYTES + 1)
        if len(payload) > reader.MAX_INPUT_BYTES:
            return "", "grande"
        return payload.decode("utf-8", errors="replace"), ""

    if conteudo.strip():
        return conteudo, ""
    return "", "vazio"


__all__ = ["router"]
