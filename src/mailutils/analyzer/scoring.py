"""Score de risco de spam sobre um **email completo**.

## Porque é um módulo separado de `signatures/spam.py`

Porque os dois medem coisas diferentes, e um número de pontos não transfere
entre elas.

Uma assinatura com 6 ligações é um sinal. Um email com 6 ligações é um email
normal. Uma assinatura com uma imagem é um logótipo; um email com 90% de imagem
é uma campanha. As mesmas regras com os mesmos pesos dariam resultados que não
são só errados — são enganadores, que é pior.

Reutilizar o motor de `signatures/spam.py` seria tentador e seria o erro
correcto: `spam.py` está calibrado para "isto vai para o email de UMA pessoa, a
partir de um cliente de email", e afinar os limiares para emails marketing
rebenta o score das assinaturas, que já está provado.

O que se partilha é só o que é genuinamente o mesmo: a escala de categorias e o
formato de um `Finding`. Vêm de `spam.py` por importação, não por cópia — uma
cópia divergiria no primeiro patch.

## Enquadramento

Este módulo serve para **auditar um email que o utilizador recebeu**. É o caso
de uso que a persona da Alexandra tem: recebe spam, quer saber porque. Não é
uma ferramenta para construir spam, e a interface diz isso.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from ..signatures.spam import CATEGORIES, Finding, categorise, label_for_score
from .reader import ParsedEmail

#: Categorias de resultado, com os mesmos limiares da assinatura. Partilhado
#: para que a escala seja a mesma nos dois ecrãs e o utilizador não aprenda
#: dois sistemas de leitura.
ESCALA = CATEGORIES

#: Pesos das Categories. Não é o de `spam.py`: um sinal grave tem de ser grave
#: *aqui* também, e não é por isso que este módulo existe.
SEVERITY = {"info": 0, "baixo": 5, "medio": 12, "alto": 22, "critico": 45}

#: Palavras que aparecem em assunto de phishing. Lista curta e genérica de
#: propósito: uma lista longa vira um lexicon que se contorna com sinónimos, e
#: deixa de ser um sinal para ser ruído.
#: Radicais, não formas completas.
#:
#: Escrever `verific(?:a|e|ue)` parece correcto e não é: o imperativo é
#: "verifiq-ue" (de *verificar*), não "verific+ue". Com a forma errada, o
#: phishing mais comum em português — "Verifique a sua conta" — passava limpo.
#: `verifi(?:c|q)\w*` cobre verificar, verifica, verifique, verificou, e
#: qualquer forma que apareça amanhã.
_GOLPE_ASSUNTO = re.compile(
    r"\b("
    r"verifi(?:c|q)\w*|confirm\w*|verifica(cao|ção)|confirma(cao|ção)|"
    r"urgente|urgentemente|urg[eê]ncia|"
    r"ultima\s+chance|última\s+chance|"
    r"pr[ée]mio|ganhou|ganha|sortead[oa]|"
    r"suspens[ao]|bloquead[oa]|expir\w*|"
    r"activ\w*|ativ\w*|actualiz\w*|atualiz\w*|desbloqueie|"
    r"clique\s+(aqui|agora|no\s+link)|pressione\s+(aqui|no\s+bot[aã]o)|"
    r"invoice|factura|fatura|western\s+union|wire\s+transfer|"
    r"seed\s+phrase|mnemonic|recovery\s+phrase|frase\s+de\s+recuperacao|"
    r"seu\s+cart[aã]o\s+(foi|ser[aá])|sua\s+conta\s+(foi|ser[aá])"
    r")",
    re.IGNORECASE,
)


def _domain(endereco: str) -> str:
    """Domínio de um endereço de email, ou host de um URL.

    Serve as duas coisas e por isso tem de lidar com as duas formas: um
    endereço tem `@`, um URL não tem nenhum. Uma versão que exigisse o `@`
    devolveva `""` para todos os URLs — e a regra "ligação para um IP em vez de
    um nome" nunca disparava, que é exactamente o caso que ela existe para
    apanhar.
    """
    texto = (endereco or "").strip()
    if "@" in texto:
        # `Name <a@b.pt>` ou `a@b.pt`
        return texto.rsplit("@", 1)[-1].strip("> \t").lower()
    try:
        host = urlparse(texto).hostname
    except ValueError:
        return ""
    return host.lower() if host else ""


#: Endereços de resposta que não são do mesmo domínio de quem envia. Uma
#: newsletter com `From: noreply@servico.com` e `Reply-To: alguem@outro.pt` é
#: uma das poucas coisas que se confirma sem abrir o email.
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

#: Encurtadores. Um link curto é a forma mais barata de esconder o destino.
_SHORTENERS = re.compile(
    r"https?://(?:bit\.ly|tinyurl\.com|t\.co|goo\.gl|ow\.ly|is\.gd|buff\.ly|"
    r"cutt\.ly|rebrand\.ly|shorturl\.at|linktr\.ee)/\S+",
    re.IGNORECASE,
)

#: Chamadas a acção em linguagem de urgência, dentro do corpo.
#: As duas línguas, porque o destinatário lê português e o spam vem de todo o
#: lado. Um filtro que só reconhece inglês é um filtro que falha em pt-PT.
_URGENCIA = re.compile(
    r"\b("
    r"urgent|immediately|within\s+24\s+hours|act\s+now|limited\s+time|"
    r"click\s+here|verify\s+your\s+account|update\s+your\s+information|"
    r"do\s+not\s+delay|final\s+notice|"
    r"urgente|imediatamente|clique\s+aqui|pressione\s+aqui|"
    r"[uú]ltima\s+chance|prazo|"
    r"sua\s+conta\s+ser[aá]|a\s+sua\s+conta\s+ser[aá]|"
    r"actualiz[ae]e|atualize\s+os\s+seus\s+dados|"
    r"n[aã]o\s+perca\s+o\s+prazo"
    r")",
    re.IGNORECASE,
)

#: Texto que só se vê se a cor do fundo for igual à cor do texto. Clássico de
#: spam que "passa" a leitura por texto.
_texto_oculto = re.compile(
    r"(?:font-size\s*:\s*0|display\s*:\s*none|visibility\s*:\s*hidden|"
    r"color\s*:\s*(?:#fff(?:fff)?\b|white\b))"
    r"[^>]*>\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 ,.'\"!?-]{3,})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Categoria:
    """Um grupo de regras. A separação existe para a UI poder dizer *onde* está
    o problema, e não só *que* há um problema."""

    nome: str
    descricao: str


CATEGORIAS: tuple[Categoria, ...] = (
    Categoria("cabecalhos", "Cabeçalhos: quem diz ter enviado, e para quem."),
    Categoria("texto", "Texto: assunto, urgência e calls-to-action."),
    Categoria("html", "HTML: scripts, estilos, conteúdo escondido."),
    Categoria("ligacoes", "Ligações: quantas, para onde, e como."),
    Categoria("imagens", "Imagens: origem, peso e rácio com o texto."),
    Categoria("estrutura", "Estrutura: tamanho, anexos, partes."),
)


def _finding(
    regra: str, pontos: int, gravidade: str, mensagem: str, remediacao: str = ""
) -> Finding:
    """Constrói um `Finding`.

    Reutiliza a estrutura de `signatures/spam.py` em vez de inventar outra, para
    que a UI do editor e a do analisador não tenham de tratar dois formatos
    diferentes. A categoria de cada regra vem do agrupamento em `por_categoria`,
    não de um campo no `Finding`.
    """
    return Finding(regra, pontos, gravidade, mensagem, remediacao)


def _regras_cabecalhos(email: ParsedEmail) -> list[Finding]:
    achados: list[Finding] = []
    remetente = email.sender
    reply_to = email.header("reply-to")
    return_path = email.header("return-path")
    auth_results = " ".join(
        email.all_headers("authentication-results") + email.all_headers("received-spf")
    ).lower()

    if not remetente:
        achados.append(
            _finding(
                "SEM_FROM",
                20,
                "alto",
                "O email não tem cabeçalho From.",
                "Um email sem remetente declarado é bloqueado pela maioria dos "
                "clientes. Confirme com quem lhe enviaram o endereço real.",
            )
        )

    if not email.header("date"):
        achados.append(
            _finding(
                "SEM_DATA",
                8,
                "medio",
                "O email não tem cabeçalho Date.",
                "A data é usada para detecção de replay e para ordenação. A sua "
                "ausência é rara em email legítimo.",
            )
        )

    if not email.header("message-id"):
        achados.append(
            _finding(
                "SEM_MESSAGE_ID",
                5,
                "baixo",
                "O email não tem Message-ID.",
                "Identificador único do email. A falta é comum em email gerado por "
                "scripts, e rara em clientes de email.",
            )
        )

    dominio_remetente = _domain(remetente)
    dominio_reply = _domain(reply_to)
    if dominio_remetente and dominio_reply and dominio_reply != dominio_remetente:
        achados.append(
            _finding(
                "REPLY_TO_OUTRO_DOMINIO",
                25,
                "alto",
                f"Reply-To aponta para {dominio_reply}, mas From diz {dominio_remetente}.",
                "Um remetente legítimo e um endereço de resposta em domínios "
                "diferentes é uma das falhas mais comuns em phishing. É o sinal mais "
                "fácil de verificar sem técnico.",
            )
        )

    dominio_return = _domain(return_path)
    if dominio_remetente and dominio_return and dominio_return != dominio_remetente:
        achados.append(
            _finding(
                "RETURN_PATH_OUTRO_DOMINIO",
                15,
                "medio",
                f"Return-Path aponta para {dominio_return}, diferente de From.",
                "Normal em email marketing com bounce handling. Vale a pena "
                "confirmar se o serviço que lhe mandou é mesmo esse.",
            )
        )

    if remetente and not re.search(r"@", remetente):
        achados.append(
            _finding(
                "FROM_MALFORMADO",
                25,
                "alto",
                "O cabeçalho From não contém um endereço de email válido.",
                "Normalmente é código usado para imitar o nome de um remetente "
                "legítimo. Verifique o endereço entre < e >.",
            )
        )

    nome_completo = re.match(r"\s*\"?([^\"<]*)\"?\s*<", remetente or "")
    if nome_completo and _GOLPE_ASSUNTO.search(nome_completo.group(1)):
        achados.append(
            _finding(
                "NOME_SUSPEITO",
                12,
                "medio",
                "O nome de exibição do remetente usa linguagem de golpe.",
                "Muitos clientes mostram só o nome, e escondem o endereço. "
                "Ler o endereço completo muda a decisão.",
            )
        )

    if auth_results:
        if "fail" in auth_results or "softfail" in auth_results:
            achados.append(
                _finding(
                    "SPF_FALHA",
                    30,
                    "alto",
                    "O servidor de origem não passou a verificação SPF.",
                    "SPF diz que este domínio não autorizou o servidor que enviou o "
                    "email. Pode ser uma configuração errada, ou um remetente falso.",
                )
            )
        if "dkim=fail" in auth_results:
            achados.append(
                _finding(
                    "DKIM_FALHA",
                    30,
                    "alto",
                    "A assinatura DKIM não validou.",
                    "DKIM garante que o conteúdo não foi alterado em trânsito. Uma "
                    "falha é EXPECTÍVEL num email que passou por um gateway que "
                    "reescreve as mensagens.",
                )
            )

    if email.to_addrs and len(email.to_addrs) > 30:
        achados.append(
            _finding(
                "MUITOS_DESTINATARIOS",
                10,
                "medio",
                f"O email declara {len(email.to_addrs)} destinatários.",
                "Um email para uma pessoa não tem trinta destinatários. Confirme que "
                "não foi reencaminhado a partir de uma lista.",
            )
        )

    return achados


def _regras_texto(email: ParsedEmail) -> list[Finding]:
    achados: list[Finding] = []
    assunto = email.subject
    corpo = email.text_body or email.visible_text

    if not assunto:
        achados.append(
            _finding(
                "SEM_ASSUNTO",
                8,
                "medio",
                "O email não tem assunto.",
                "A maioria dos filtros trata a ausência de assunto como sinal. Não é "
                "proibido, mas é raro em email legítimo.",
            )
        )
    elif _GOLPE_ASSUNTO.search(assunto):
        achados.append(
            _finding(
                "ASSUNTO_GOLPE",
                20,
                "alto",
                "O assunto usa linguagem de golpe ou urgência.",
                "Vale a pena abrir o email e ver a remetente real antes de responder.",
            )
        )

    if _URGENCIA.search(corpo):
        achados.append(
            _finding(
                "LINGUAGEM_URGENTE",
                15,
                "alto",
                "O corpo contém linguagem de urgência ou de pressão.",
                "Pressão para agir depressa é o mecanismo da maioria das burlas. "
                "Um remetente legítimo não precisa de apressar o destinatário.",
            )
        )

    maiusculas = sum(1 for c in assunto if c.isupper())
    if len(assunto) > 8 and maiusculas / len(assunto) > 0.6:
        achados.append(
            _finding(
                "ASSUNTO_GRITADO",
                10,
                "medio",
                "O assunto está maioritariamente em maiúsculas.",
                "MAIÚSCULAS GRITADAS é o sinal mais visível de spam, e o mais fácil de falsificar.",
            )
        )

    if email.html_body and not email.text_body:
        achados.append(
            _finding(
                "SEM_ALTERNATIVA_TEXTO",
                12,
                "medio",
                "O email só tem parte HTML, sem alternativa em texto simples.",
                "Um email legítimo quase sempre envia as duas partes. Só-HTML é mais "
                "difícil de indexar e de arquivar, e é um sinal conhecido.",
            )
        )

    return achados


def _regras_html(email: ParsedEmail) -> list[Finding]:
    achados: list[Finding] = []
    html = email.html_body
    if not html:
        return achados
    baixa = html.lower()

    for tag in ("script", "iframe", "form", "object", "embed", "base"):
        if re.search(rf"<{tag}\b", baixa):
            achados.append(
                _finding(
                    f"HTML_TAG_{tag.upper()}",
                    SEVERITY["critico"],
                    "critico",
                    f"O email contém um elemento <{tag}>.",
                    "Num email, <script> e <iframe> são bloqueados pela maioria dos "
                    "clientes e contam como sinal de spam. Nenhum email legítimo "
                    "precisa deles.",
                )
            )

    if "javascript:" in baixa:
        achados.append(
            _finding(
                "HTML_JAVASCRIPT_LINK",
                SEVERITY["critico"],
                "critico",
                "Há uma ligação `javascript:` no HTML.",
                "Não é uma ligação, é execução de código no cliente. Não aparece em "
                "email legítimo.",
            )
        )

    for match in _texto_oculto.finditer(html):
        achados.append(
            _finding(
                "TEXTO_OCULTO",
                30,
                "alto",
                f"Há texto invisível no HTML: «{match.group(1)[:60].strip()}».",
                "Texto escondido com ligações é a técnica mais antiga e mais "
                "eficaz de spam. Se não é visível para si, também não é para o "
                "filtro — e para o cliente, o que resta é pouco.",
            )
        )

    if re.search(r"position\s*:\s*fixed|position\s*:\s*absolute\s*;\s*(top|left)\s*:\s*-\d", baixa):
        achados.append(
            _finding(
                "CSS_POSICAO_ABSOLUTA",
                8,
                "baixo",
                "O HTML usa posicionamento absoluto ou fixo.",
                "Serve para pôr texto fora da vista. Não é motivo para recusar um "
                "email sozinho, mas é o que permite os dois problemas anteriores.",
            )
        )

    if re.search(r"expression\s*\(|@import|url\s*\(\s*[\"']?javascript", baixa):
        achados.append(
            _finding(
                "CSS_EXPRESSAO",
                20,
                "alto",
                "O HTML usa expressões CSS ou importações.",
                "Foram usadas por ataques antigos contra clientes de email. A "
                "presença, hoje, é só de remetente mal intencionado.",
            )
        )

    if re.search(r"<!--\[if|<v:|mso-|MSO\s", html, re.IGNORECASE):
        # Não é um problema. Fica registado como observação para não parecer que
        # a análise o esqueceu.
        achados.append(
            _finding(
                "HTML_CONDICIONAL_OUTLOOK",
                -3,
                "info",
                "O HTML contém código condicional para o Outlook.",
                "Isto é normal e esperado em newsletters bem feitas. Não penaliza.",
            )
        )

    if len(html) > 200_000:
        achados.append(
            _finding(
                "HTML_ENORME",
                15,
                "alto",
                f"A parte HTML tem {len(html) // 1024} KiB.",
                "Passar de ~200 KiB é o limite a partir do qual muitos clientes "
                "truncam a mensagem ou a marcam.",
            )
        )

    return achados


def _regras_ligacoes(email: ParsedEmail) -> list[Finding]:
    achados: list[Finding] = []
    ligacoes = email.html_links
    externas = [href for href in ligacoes if href.lower().startswith(("http://", "https://"))]
    visibles = email.visible_text

    if len(externas) >= 40:
        achados.append(
            _finding(
                "MUITAS_LIGACOES",
                18,
                "alto",
                f"O email tem {len(externas)} ligações.",
                "Um email com quarenta ligações é uma campanha, não uma mensagem. "
                "Em email pessoal, três a sete é o normal.",
            )
        )
    elif len(externas) >= 15:
        achados.append(
            _finding(
                "LIGACOES_DENSAS",
                8,
                "baixo",
                f"O email tem {len(externas)} ligações.",
                "É o que uma newsletter tem. Não é suspeito por si só — mas vale a "
                "pena saber quem são os remetentes.",
            )
        )

    if _SHORTENERS.search(" ".join(externas)):
        achados.append(
            _finding(
                "LINK_ENCURTADO",
                20,
                "alto",
                "Há ligações de um encurtador (bit.ly, t.co, e outros).",
                "Um encurtador esconde o destino até ao clique. É a forma mais "
                "barata de levar alguém a uma página que não announce.",
            )
        )

    if any(href.lower().startswith("http://") for href in externas) and not any(
        href.lower().startswith("https://") for href in externas
    ):
        achados.append(
            _finding(
                "LIGACOES_SEM_TLS",
                10,
                "medio",
                "Todas as ligações são http://.",
                "Sem TLS, o conteúdo pode ser alterado em trânsito. Num email, "
                "http:// também é sinal de configuração antiga ou descuidada.",
            )
        )

    for href in externas:
        dominio = _domain(href)
        if not dominio:
            continue
        if _IPV4.search(dominio):
            achados.append(
                _finding(
                    "LIGACAO_POR_IP",
                    20,
                    "alto",
                    f"Uma ligação aponta para um endereço IP, não para um nome: {href[:80]}",
                    "Um site legítimo tem nome de domínio. Um link para um IP é a "
                    "forma mais básica de esconder para onde vai.",
                )
            )
            break
        etiquetas = dominio.split(".")
        # O sinal é a *profundidade* com uma etiqueta descritiva pelo meio:
        # `login.secure-account-verification.exemplo.pt` tem cinco etiquetas e
        # uma delas descreve o que o site "faz". Medir só o comprimento da
        # primeira não apanha nada — `login` tem cinco caracteres.
        interm = etiquetas[:-2]
        if len(etiquetas) >= 4 and any(len(e) >= 12 for e in interm):
            achados.append(
                _finding(
                    "SUBDOMINIO_SUSPEITO",
                    12,
                    "medio",
                    f"Uma ligação usa um subdomínio com {len(etiquetas)} etiquetas: {dominio[:70]}",
                    "Nomes como `login.secure-account-verification.exemplo.pt` "
                    "existem para parecer oficiais. O domínio verdadeiro é o "
                    "PENÚLTIMO e ÚLTIMO par de etiquetas — leia-os, não o nome "
                    "que aparece no topo.",
                )
            )
            break

    # Só dispara se o texto **contiver mesmo** uma URL escrita por extenso.
    # Um email com zero ligações *e* zero URLs não é suspeito — é um email. A
    # versão anterior disparava em qualquer newsletter sem botões, e falsos
    # positivos são a maneira mais rápida de um analisador perder credibilidade.
    url_no_texto = re.search(r"(?:https?://|www\.)\S+", visibles, re.IGNORECASE)
    if not externas and email.html_body and url_no_texto:
        achados.append(
            _finding(
                "LIGACAO_NAO_CLICAVEL",
                8,
                "medio",
                "O texto escreve um URL em vez de o tornar clicável.",
                "Um email que escreve o endereço em vez de o ligar quer que o "
                "destinatário o leia, o copie e o cole. Quem copia um endereço "
                "não sabe para onde vai antes de lá ir.",
            )
        )

    return achados


def _regras_imagens(email: ParsedEmail) -> list[Finding]:
    achados: list[Finding] = []
    imagens = email.html_images
    if not imagens:
        return achados

    texto_visivel = len(email.visible_text)
    if texto_visivel < 120 and len(imagens) >= 3:
        achados.append(
            _finding(
                "IMAGENS_SEM_TEXTO",
                22,
                "alto",
                f"O email tem {len(imagens)} imagens e quase nenhum texto "
                f"({texto_visivel} caracteres visíveis).",
                "Email que é quase só imagem é o formato preferido de quem não "
                "consegue ser bloqueado por um filtro de texto. E raramente "
                "sobrevive à versão texto.",
            )
        )

    remotas = [src for src in imagens if src.lower().startswith(("http://", "https://"))]
    if len(remotas) == len(imagens) and len(imagens) >= 3:
        achados.append(
            _finding(
                "IMAGENS_TODAS_EXTERNAS",
                6,
                "baixo",
                "Todas as imagens vêm de servidores externos.",
                "É assim que o tracking de abertura funciona: quem vê a imagem é "
                "registado. Com 40% de taxa de abertura num email pessoal, vale a "
                "pena perguntar porquê.",
            )
        )

    # Uma imagem `data:` é base64 dentro do HTML. É o sinal mais severo que
    # existe numa assinatura e o `CLAUDE.md` proíbe gerá-lo em qualquer
    # contexto. `spam.py` sempre teve esta regra — é a razão de o compositor
    # não poder dar-se por satisfeito com um motor só. Esta lacuna apareceu
    # porque o `T017-B` encontrou, ao escrever os testes, que um `data:` URI
    # num email composto **não** era pontuado: o email saía com a mesma
    # pontuação que um email limpo.
    #
    # A gravidade é `crítica` e não `alta` porque o `T017-A` do `CLAUDE.md` diz
    # que uma imagem embebida é o que distingue um logótipo servido por URL de
    # um email que carrega o ficheiro inteiro do outro lado do Atlantico.
    for src in imagens:
        if src.lower().startswith("data:"):
            achados.append(
                _finding(
                    "IMG_DATA_URI",
                    55,
                    "critico",
                    "Uma imagem está embebida como data: URI (texto base64 dentro do HTML).",
                    "Sirva o logótipo por URL. Uma imagem embebida aumenta o "
                    "email em dezenas de KB, não pode ser bloqueada nem "
                    "redimensionada pelo cliente, e é um sinal que os filtros "
                    "contam como spam.",
                )
            )
            break

    for src in imagens:
        assinatura = r"(^|[/_.-])(1x1|px|spacer|beacon|track|open|impression)([/_.-]|$)"
        if re.search(assinatura, src, re.I):
            achados.append(
                _finding(
                    "PIXEL_DE_RASTREIO",
                    25,
                    "alto",
                    "Há uma imagem que parece pixel de rastreio.",
                    "Um pixel invisível serve só para avisar o remetente que o email "
                    "foi aberto. Se o email que recebeu tem um, o remetente sabe "
                    "quando o leu — e se o reenviar.",
                )
            )
            break

    return achados


def _regras_estrutura(email: ParsedEmail) -> list[Finding]:
    achados: list[Finding] = []

    if email.attachments:
        executaveis = [
            nome
            for nome in email.attachments
            if re.search(r"\.(exe|scr|bat|cmd|vbs|js|jar|msi|apk|ps1|iso|img)$", nome or "", re.I)
        ]
        if executaveis:
            achados.append(
                _finding(
                    "ANEXO_EXECUTAVEL",
                    SEVERITY["critico"],
                    "critico",
                    f"O email traz um anexo executável: {', '.join(executaveis[:3])}",
                    "Não abra. Nem para ver. Se for legítimo, o remetente tem outra "
                    "forma de lhe enviar o ficheiro.",
                )
            )
        else:
            achados.append(
                _finding(
                    "TEM_ANEXOS",
                    6,
                    "baixo",
                    f"O email traz {len(email.attachments)} anexo(s).",
                    "Normal em email com documentos. Só é suspeito combinado com "
                    "pedido de acção imediata.",
                )
            )

    tamanho = email.size_bytes
    if tamanho > 2_000_000:
        achados.append(
            _finding(
                "EMAIL_ENORME",
                12,
                "alto",
                f"O email tem {tamanho // 1024} KiB.",
                "Passar de ~2 MiB é onde muitos gateways começam a cortar. Num email "
                "com texto, é sempre conteúdo desnecessário.",
            )
        )

    if email.multipart and not (email.has_text or email.has_html):
        achados.append(
            _finding(
                "MULTIPART_SEM_CONTEUDO",
                15,
                "alto",
                "O email é multipart mas nenhuma parte tem conteúdo legível.",
                "Pode ser um anexo de tipo errado, ou uma tentativa de meter "
                "conteúdo onde o filtro não o vê.",
            )
        )

    return achados


#: Ordem de avaliação. A ordem é visível na UI pela ordem das categorias, e por
#: isso vai do que se lê sem abrir o email para o que exige abrir.
_REGRAS: tuple[tuple[str, Callable[[ParsedEmail], list[Finding]]], ...] = (
    ("cabecalhos", _regras_cabecalhos),
    ("texto", _regras_texto),
    ("html", _regras_html),
    ("ligacoes", _regras_ligacoes),
    ("imagens", _regras_imagens),
    ("estrutura", _regras_estrutura),
)


def analyse(email: ParsedEmail) -> dict:
    """Analisa o email e devolve o relatório completo.

    Devolve sempre a estrutura inteira, mesmo sem achados: a UI precisa de
    mostrar "nada foi encontrado", e um relatório vazio não é isso.
    """
    por_categoria: dict[str, list[Finding]] = {}
    todos: list[Finding] = []
    for nome, regra in _REGRAS:
        achados = regra(email)
        por_categoria[nome] = achados
        todos.extend(achados)

    penalizadores = [f for f in todos if f.pontos > 0]
    creditos = [f for f in todos if f.pontos < 0]
    total = sum(f.pontos for f in todos)
    score = max(0, min(100, total))
    label, descricao = categorise(score)

    resumo = [
        {
            "categoria": c.nome,
            "descricao": c.descricao,
            "pontos": sum(f.pontos for f in por_categoria.get(c.nome, [])),
            "regras": [f.as_dict() for f in por_categoria.get(c.nome, [])],
        }
        for c in CATEGORIAS
    ]

    return {
        "score": score,
        "categoria": label,
        "categoria_acentuada": label_for_score(score),
        "descricao": descricao,
        "categorias": resumo,
        "regras": [f.as_dict() for f in penalizadores],
        "creditos": [f.as_dict() for f in creditos],
        "resumo_email": {
            "de": email.sender or "(sem From)",
            "assunto": email.subject or "(sem assunto)",
            "para": ", ".join(email.to_addrs[:6]) or "(sem To)",
            "tem_html": email.has_html,
            "tem_texto": email.has_text,
            "ligacoes": len(email.html_links),
            "imagens": len(email.html_images),
            "anexos": email.attachments,
            "caracteres_visiveis": len(email.visible_text),
            "bytes": email.size_bytes,
        },
        "notas_leitura": email.parse_errors,
        # Um email com `From:` e `Reply-To:` em domínios diferentes, `SPF fail`
        # e 40 ligações não é uma conta ao lado de zero. O score é a média de
        # gravidade; a categoria é o que o utilizador age sobre.
        "veredito": _veredict(score, len(penalizadores), email),
        "aviso": (
            "Este score é uma heurística local, baseada em padrões de spam "
            "documentados e em sinais que são visíveis numa mensagem recebida. "
            "Não é o algoritmo do Gmail, do Outlook ou de qualquer outro "
            "fornecedor, e não substitui o julgamento de quem conhece o "
            "remetente."
        ),
        "como_usar": (
            "A forma segura de usar isto é defensiva: cole um email de que "
            "suspeite, veja o que disparou, e decida. Não é uma forma de "
            "fazer email entrar onde não deve."
        ),
    }


def _veredict(score: int, n_achados: int, email: ParsedEmail) -> str:
    if n_achados == 0:
        return (
            "Não foi encontrado nenhum sinal conhecido. Isso não prova que o "
            "email seja legítimo — só que não há nada de errado *nele*."
        )
    if score >= 70:
        return (
            "Vários sinais Independentes apontam na mesma direcção. Trate como "
            "suspeito: não clique em ligações, não responda, e confirme o "
            "remetente por outro caminho."
        )
    if score >= 45:
        return (
            "Há sinais suficientes para justificar atenção. Leia as regras "
            "abaixo: um email pode ser legítimo e disparar uma delas só por "
            "configuração."
        )
    if n_achados == 1:
        return (
            "Um sinal isolado, que é o que acontece com email legítimo com uma "
            "configuração estranha. Vale a pena olhar; não vale a pena entrar "
            "em pânico."
        )
    return (
        "Alguns sinais, nenhum decisivo. Para os julgados juntos, leia as "
        "regras — a combinação é que diz alguma coisa."
    )
