"""Compositor: o texto que sai. `T017-B`.

`compose/` é **irmão** de `analyzer/`, não extensão, e a razão está escrita nos
dois lados porque é o tipo de decisão que um revisor tem de ver primeiro.

`analyzer/` é stateless por decisão (`analyzer/routes.py:8`): o email colado ali
é quase sempre spam *recebido*, e spam recebido contém o que o remetente queria
que alguém lesse. Arquivá-lo sem ninguém pedir seria guardar material alheio.

O compositor é o oposto em todos os sentidos: é texto do dono, guardado por
utilizador, e a sua utilidade é sair. Persistir um não implica persistir o outro,
e um módulo único com dois comportamentos faria com que cada regra tivesse de
perguntar em que contexto está — que é a forma mais cara de um bug.

---

## A unifying invariant, e onde ela vive

O `CLAUDE.md` diz: *a aplicação nunca envia algo que ela própria reprovaria*.

Isto não é uma regra de interface. Um botão que mostra o score e um caminho de
envio que não o consulta são duas coisas diferentes, e a segunda é a que decide.
Por isso `enviar()` **não tem** como ser chamado sem `avaliar()` correr primeiro:
não é uma disciplina, é a estrutura das funções. `avaliar()` é chamado dentro de
`enviar()`, e o bloqueio sai antes do primeiro `mailer.send`.

**O motor é `analyzer/scoring.py`, não `signatures/spam.py`.** Um número de
pontos não transfere entre uma assinatura e um email marketing: uma assinatura
com seis ligações é um sinal, um email com seis ligações é um email normal.
`spam.py` está calibrado para o email de UMA pessoa, a partir de um cliente de
email; usá-lo aqui afina o motor no sentido errado e degrada o score das
assinaturas, que já está provado. A `FR-7.2` foi corrigida para dizer isto, e o
erro estava escrito no plano do T017 antes de haver uma linha de código.

O que os dois motores **partilham** é a política de bloqueio: `spam.bloqueado()`.
Uma política mais tolerante aqui seria um caminho de envio que reprova o que a
aplicação reprovaria noutro sítio, e é a mesma quebra com outro nome (`FR-7.3`).
"""

from __future__ import annotations

import html as htmllib
import sqlite3
from typing import Any

from .. import mailer, security
from ..analyzer import reader, scoring
from ..config import Settings
from ..db import transaction
from ..lists import service as listas
from ..signatures import build as assinatura_build
from ..signatures import spam

#: Quantos destinatários vão num único envio. Um envio para 5000 pessoas com um
#: `SMTP.send_message` para cada um é um envio que demora minutos e que falha a
#: meio sem deixar rasto do que já foi. O número é configuração e não constante
#: (NFR-18); este é o valor por omissão que a `FR-8.5` nomeia.
MAX_DESTINATARIOS = 200


# ------------------------------------------------------------------ rascunho


def guardar_rascunho(conn: sqlite3.Connection, user_id: int, assunto: str, corpo: str) -> None:
    """Guarda o rascunho. **Um por utilizador** (FR-7.1), sobrescrito.

    `INSERT ... ON CONFLICT` e não `UPDATE` seguido de `INSERT`: são duas
    consultas com uma janela entre elas, e o que acontece nessa janela é um
    erro de integridade que o utilizador vê como "não guardou".
    """
    agora = security.iso(security.utcnow())
    with transaction(conn):
        conn.execute(
            "INSERT INTO compose_drafts (user_id, assunto, corpo, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET"
            "   assunto = excluded.assunto,"
            "   corpo = excluded.corpo,"
            "   updated_at = excluded.updated_at",
            (user_id, assunto or "", corpo or "", agora),
        )


def rascunho(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    """O rascunho deste utilizador. Vazio se não houver.

    `user_id` é a chave primária da tabela, o que faz do `WHERE` um teste de
    dono trivial — e é o que torna impossível ler o rascunho de outro.
    """
    linha = conn.execute(
        "SELECT assunto, corpo, updated_at FROM compose_drafts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if linha is None:
        return {"assunto": "", "corpo": "", "updated_at": ""}
    return {
        "assunto": linha["assunto"],
        "corpo": linha["corpo"],
        "updated_at": linha["updated_at"],
    }


def contar_rascunhos(conn: sqlite3.Connection, user_id: int) -> int:
    """Quantos rascunhos tem. Tem de ser 0 ou 1.

    Existe para os testes, e existe porque um esquema que promete um rascunho só
    por utilizador é uma promessa que nenhuma restrição de esquema garante — a
    chave primária garante, mas um teste que a exercita vale mais do que uma
    afirmação no docstring.
    """
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM compose_drafts WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
    )


# --------------------------------------------------------------- construção


def _html_do_email(assunto: str, corpo: str, assinatura_html: str) -> str:
    """O HTML que vai no email: corpo escapado + assinatura.

    **O corpo é escapado e isto não é um detalhe.** O rascunho é do operador e
    o email vai para a caixa de outra pessoa. Um `<script>` colado no rascunho
    é um `<script>` no email de um destinatário, e `spam.py` dava 50 pontos a
    essa assinatura — mas só depois de o HTML já estar montado e de o `<script>`
    já estar lá. Escapar não é hygiene, é o que impede que o rascunho seja um
    vector de injecção para os destinatários.

    `htmllib.escape` com `quote=True` (o default) escapa também `"` e `'`, que
    é o que impede breakout de atributo se este HTML alguma vez for montado por
    concatenação em vez de escapado.
    """
    partes = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'color:#212121;line-height:22px;">',
        "<p>" + htmllib.escape(corpo).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>",
    ]
    if assinatura_html:
        partes.append(assinatura_html)
    partes.append("</div>")
    return "".join(partes)


def _assinatura_do_utilizador(
    conn: sqlite3.Connection, settings: Settings, user_id: int
) -> tuple[str, str]:
    """`(html, texto)` da assinatura deste utilizador. `("", "")` se não houver.

    Passa pelo pipeline partilhado (`signatures/build.py`), nunca por uma cópia:
    o que o compositor anexa tem de ser a mesma coisa que o preview mostra e que
    a exportação produz, ou a assinatura entregue não é a assinatura escolhida.

    A assinatura é **anexa sempre** neste ticket. A `FR-7.4` previa uma opção de
    não anexar, e o dono decidiu que ela não entra agora: sem checkbox, o score
    que se vê é o score do email que sai, e não há duas combinações para a
    interface divergence entre elas.
    """
    guardada = assinatura_build.carregar_assinatura(conn, user_id)
    if guardada is None:
        return "", ""
    logo = assinatura_build.carregar_logo(settings, conn, guardada)
    constroida = assinatura_build.construir(
        settings,
        guardada["fields"],
        guardada["theme"],
        logo,
        guardada["layout"],
    )
    return constroida["html"], constroida["plain"]


def _email_para_analise(
    assunto: str,
    corpo: str,
    html: str,
    remetente: str,
    n_destinatarios: int,
) -> reader.ParsedEmail:
    """Monta o `ParsedEmail` que vai ser pontuado.

    É aqui que a pergunta merece atenção: **o que é pontuado?** A resposta é o
    que vai sair — assunto, corpo, HTML com a assinatura já anexada, e os
    cabeçalhos que decidem se o email é parasita.

    O `From` e o `Reply-To` entram porque são um sinal: um `From` do domínio do
    operador com `Reply-To` para outro é uma das formas mais antigas de spam, e
    `scoring.py` olha para os dois. Construir o `ParsedEmail` só com o texto
    seria pontuar um email que ninguém vai receber.

    **Não** se faz aqui um round-trip por `str(email)` e `reader.parse()`. Seria
    mais fiel ao que sai, e é uma serialize-parse desnecessária por email: o
    que estas regras olham (cabeçalhos, texto, HTML, ligações, imagens) está
    todo aqui, e a alternativa introduz uma fonte de parse-errors no caminho do
    portão — que é o pior sítio para ter uma.
    """
    email = reader.ParsedEmail()
    email.headers = {
        "from": [f"mailutils <{remetente}>"],
        "to": [", ".join(f"destinatario{i}@exemplo.pt" for i in range(n_destinatarios))],
        "subject": [assunto or ""],
        "reply-to": [remetente],
    }
    email.text_body = corpo
    email.html_body = html
    return email


# ------------------------------------------------------------------- score


def decisao_de_bloqueio(score: int, gravedades: list[str]) -> bool:
    """A política da `FR-4.9`, pelo caminho do compositor.

    É uma delegação a `spam.bloqueado()` e existe para que os testes possam
    comparar os dois motores sem depender de como cada umrepresenta um finding.
    Uma cópia desta expressão aqui seria uma segunda política, e a `FR-7.3` diz
    que há uma.
    """
    return spam.bloqueado(score, [spam.Finding("X", 0, g, "m") for g in gravedades])


def avaliar(
    conn: sqlite3.Connection,
    settings: Settings,
    user_id: int,
    assunto: str,
    corpo: str,
    list_id: int,
) -> dict[str, Any]:
    """Pontua o email que sairia, e diz se pode sair.

    Não envia. É a função que a interface chama para mostrar a barra, e é a
    mesma que `enviar()` chama para decidir — as duas vezes o mesmo código, que
    é o que faz o score mostrado e o score aplicado serem o mesmo número.
    """
    remetente = _remetente_da_lista(conn, user_id, list_id)
    destinatarios = listas.destinatarios(conn, list_id)
    assinatura_html, assinatura_plain = _assinatura_do_utilizador(conn, settings, user_id)
    html = _html_do_email(assunto, corpo, assinatura_html)
    texto = corpo + ("\n" + assinatura_plain if assinatura_plain else "")

    email = _email_para_analise(
        assunto, texto, html, remetente or "", len(destinatarios["destinatarios"])
    )
    relatorio = scoring.analyse(email)
    regras = relatorio["regras"] + relatorio["creditos"]
    criticas = [r for r in regras if r["gravidade"] == "critico"]

    return {
        "score": relatorio["score"],
        "categoria": relatorio["categoria_acentuada"],
        "descricao": relatorio["descricao"],
        "bloqueado": spam.bloqueado(relatorio["score"], regras),
        "regras_criticas": criticas,
        "regras": relatorio["regras"],
        "creditos": relatorio["creditos"],
        "html": html,
        "texto": texto,
        "assinatura": assinatura_html,
        "n_destinatarios": len(destinatarios["destinatarios"]),
    }


def _remetente_da_lista(conn: sqlite3.Connection, user_id: int, list_id: int) -> str:
    """O endereço `from` confirmado desta lista, ou `""`.

    `""` e não um email inventado: sem remetente o email não sai, e pontuar um
    `From` falso seria medir uma coisa que ninguém vai receber.
    """
    linha = conn.execute(
        "SELECT s.email AS email FROM recipient_lists l"
        " JOIN senders s ON s.id = l.sender_id"
        " WHERE l.id = ? AND l.user_id = ?",
        (list_id, user_id),
    ).fetchone()
    return linha["email"] if linha else ""


# -------------------------------------------------------------------- envio


def enviar(
    conn: sqlite3.Connection,
    settings: Settings,
    user_id: int,
    assunto: str,
    corpo: str,
    list_id: int,
) -> dict[str, Any]:
    """Envia o email composto para a lista, e diz o que aconteceu.

    **A ordem das operações é a regra.** Pontuar, depois decidir, e só depois
    enviar. Não há caminho nesta função que chegue ao `mailer.send` sem passar
    por `avaliar()`, e `avaliar()` nunca envia. É isto que é a unifying
    invariant: não uma convenção sobre onde escrever o código, uma estrutura em
    que o envio depende do score.

    Devolve sempre um relatório com `enviados`, `falhados`, `omitidos` e
    `bloqueado` — mesmo quando recusa. Um relatório que só existe no caminho
    feliz é um relatório que a interface não sabe mostrar no caminho que importa.
    """
    if listas.lista_do_utilizador(conn, user_id, list_id) is None:
        return _relatorio_vazio("A lista não existe.")

    resultado = listas.destinatarios(conn, list_id)
    if not resultado["enviavel"]:
        # O portão do `from` (o `T017-A`) e o portão do score são dois portões
        # diferentes e ambos são obrigatórios. Um score perfeito não substitui
        # um `from` por confirmar, e um `from` confirmado não substitui o score.
        return _relatorio_vazio(
            "A lista não tem remetente confirmado. Confirma o remetente antes de enviar.",
            extra={"bloqueado": True, "motivo": "sem remetente confirmado"},
        )

    avaliacao = avaliar(conn, settings, user_id, assunto, corpo, list_id)
    if avaliacao["bloqueado"]:
        # O motivo vai no relatório. Um bloqueio sem motivo é um bloqueio que o
        # utilizador contorna a adivinhar, e a interface não tem como dizer qual
        # regra disparou se o serviço não a nomear.
        return _relatorio_vazio(
            "O email foi bloqueado pelo score. Vê as regras que dispararam.",
            extra={
                "bloqueado": True,
                "score": avaliacao["score"],
                "categoria": avaliacao["categoria"],
                "regras_criticas": avaliacao["regras_criticas"],
                "regras": avaliacao["regras"],
                "creditos": avaliacao["creditos"],
            },
        )

    destinatarios = resultado["destinatarios"]
    if not destinatarios:
        return _relatorio_vazio("A lista não tem ninguém a quem enviar.")

    enviados = 0
    falhados = 0
    omitidos = 0
    #: Classe da excepção → quantas vezes aconteceu. (FR-7.7)
    #:
    #: A **classe** e nunca a mensagem: um `SMTPAuthenticationError` traz o
    #: utilizador e a palavra-passe no texto, e mostrar isso ao operador ou
    #: escrevê-lo num log é vazar o que autentica o servidor. A classe basta
    #: para o operador saber se é `SMTPAuthenticationError` (credenciais) ou
    #: `SMTPRecipientsRefused` (um destinatário mau), que são problemas
    #: diferentes com acções diferentes.
    erros: dict[str, int] = {}
    for linha in destinatarios[:MAX_DESTINATARIOS]:
        # A descadência é lida aqui, e não antes do laço, para que quem se
        # descadencia entre a decisão e o envio não receba. A janela é pequena,
        # mas a janela existe — e um link de descadência é uma acção de quem lê
        # o email, não uma migração de base de dados.
        if _descadenciado_agora(conn, list_id, linha):
            omitidos += 1
            continue
        cabecalhos = _cabecalhos_de_descadencia(settings, list_id, linha)
        try:
            mailer.send(
                settings,
                linha["email"],
                assunto,
                avaliacao["texto"],
                avaliacao["html"],
                cabecalhos,
            )
        except Exception as erro:  # noqa: BLE001
            # **Toda** a excepção conta como falha e nenhuma sai daqui. Ver a
            # nota de `erros` acima para o porque de ser a classe.
            falhados += 1
            nome = type(erro).__name__
            erros[nome] = erros.get(nome, 0) + 1
            _registar(f"envio falhado: {nome}")
            continue
        enviados += 1

    return {
        "enviados": enviados,
        "falhados": falhados,
        "omitidos": omitidos,
        "bloqueado": False,
        "motivo": "",
        "score": avaliacao["score"],
        "categoria": avaliacao["categoria"],
        "regras_criticas": [],
        "erros": erros,
    }


def _relatorio_vazio(motivo: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """O relatório de um envio que não aconteceu.

    Tem as mesmas chaves de um envio que aconteceu, todas a zero. A razão está
    em `motivo` e é legível por uma pessoa — a interface não tem de adivinhar se
    um relatório vazio é um sucesso sem destinatários ou um bloqueio.
    """
    base = {
        "enviados": 0,
        "falhados": 0,
        "omitidos": 0,
        "bloqueado": False,
        "motivo": motivo,
        "score": 0,
        "categoria": "",
        "regras_criticas": [],
    }
    base.update(extra or {})
    return base


def _descadenciado_agora(conn: sqlite3.Connection, list_id: int, linha: Any) -> bool:
    """O `unsubscribed_at` foi preenchido depois da decisão de enviar?"""
    achado = conn.execute(
        "SELECT unsubscribed_at FROM list_addresses WHERE list_id = ? AND email = ?",
        (list_id, linha["email"]),
    ).fetchone()
    return achado is not None and achado["unsubscribed_at"] is not None


def _cabecalhos_de_descadencia(settings: Settings, list_id: int, linha: Any) -> dict[str, str]:
    """`List-Unsubscribe` e `List-Unsubscribe-Post` (FR-6.8).

    Um URL e um `mailto:`. O botão de "cancelar subscrição" do Gmail só aparece
    com o `One-Click`, e um cliente que não conhece o Gmail não mostra nada —
    por isso o link é o que garante que sair é possível para quem lê o email num
    cliente que não conhece nenhum dos dois.

    O token é o mesmo de sempre: assinado, com prazo longo, e com o
    `address_id` dentro. Sem ele, `address_id` é um inteiro enumerável e
    enumerá-lo dá a lista inteira.
    """
    return {
        "List-Unsubscribe": f"<{listas.link_descadenciar(settings, list_id, linha['id'])}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def _registar(mensagem: str) -> None:
    """Um log de envio que **não** pode vazar endereços nem códigos.

    Sem destinatário e sem corpo de email. Um log de envio que escreve o
    endereço de quem recebeu é uma lista de contactos em texto plano num ficheiro
    que sobrevive à rotação, e é a mesma coisa que o produto passou o `T017-A` a
    não fazer.
    """
    import logging

    logging.getLogger("mailutils.compose").info(mensagem)


__all__ = [
    "MAX_DESTINATARIOS",
    "avaliar",
    "contar_rascunhos",
    "decisao_de_bloqueio",
    "enviar",
    "guardar_rascunho",
    "rascunho",
]
