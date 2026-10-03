"""Score de risco de spam sobre o HTML final da assinatura.

Este módulo é *critical file*. A regra do CLAUDE.md: se enviesar para baixo, o
produto mente ao utilizador. Daí três decisões estruturais:

1. Cada regra tem **peso** e **explicação em pt-PT**, nunca um número solto.
2. As regras negativas (o que NÃO deve aparecer) **baixam** o score. Sem isto,
   um HTML impecável nunca chega a 0 e o utilizador aprende a ignorar o número.
3. O score é explicitamente rotulado como heurístico em toda a parte em que
   aparece. (FR-4.8)

O que isto **não** é: o algoritmo do Gmail. Não existe forma pública de o
replicar. Isto é um inventário dos padrões documentados e observados.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Categorias de resultado. Os limites são Requirements FR-4.7.
CATEGORIES: tuple[tuple[str, int, str], ...] = (
    ("SEGURO", 0, "Não encontrámos padrões de spam conhecidos."),
    ("ATENCAO", 20, "Há sinais fracos. Alguns filtros podem penalizar."),
    ("ELEVADO", 45, "Padrões conhecidos de spam presentes. Convém corrigir."),
    ("CRITICO", 70, "Sinais graves. A exportação está bloqueada de propósito."),
)

_SEVERITY = {"info": 0, "baixo": 5, "medio": 15, "alto": 30, "critico": 50}

#: Tags cujo appearance não pertence a uma assinatura. A presença de qualquer
#: uma é motivo suficiente para bloqueio de exportação: são vectores de execução
#: ou de recolha, não de apresentação.
_FORBIDDEN_TAGS = ("script", "iframe", "object", "embed", "form", "input", "video", "audio")


@dataclass(frozen=True)
class Finding:
    """Uma regra disparada. `regra` é estável — é o que a UI e os testes
    referenciam. `mensagem` é o que o utilizador lê."""

    regra: str
    pontos: int
    gravidade: str
    mensagem: str
    remediacao: str = ""

    def as_dict(self) -> dict:
        return {
            "regra": self.regra,
            "pontos": self.pontos,
            "gravidade": self.gravidade,
            "mensagem": self.mensagem,
            "remediacao": self.remediacao,
        }


def bloqueado(score: int, findings) -> bool:
    """A política de bloqueio da `FR-4.9`. **Um único sítio.**

    Bloqueia se o score for `CRÍTICO` **ou** se *qualquer* regra for de gravidade
    `crítica`. O score mede risco agregado; o bloqueio é política sobre o pior
    sinal individual. Decidir só pelo total daria ao utilizador forma de
    contornar o bloqueio com mais texto — que é o que a `FR-4.9` diz na
    primeira frase.

    **Esta função é partilhada por dois motores.** A assinatura é pontuada por
    `spam.py` e o email completo por `analyzer/scoring.py`, e os dois têm de
    decidir o bloqueio com a **mesma** política (`FR-7.3`): uma política mais
    tolerante no compositor seria um caminho de envio que reprova o que a
    aplicação reprovaria noutro sítio, que é exactamente a quebra da unifying
    invariant. Por isso a decisão vive aqui e não em cada motor.

    Aceita as **duas** representações de um finding, e a necessidade de o fazer
    é o que torna a partilha honesta em vez de nominal: `spam.py` produz
    objectos `Finding` (com `.gravidade`) e `scoring.analyse()` devolve
    dicionários (com `["gravidade"]`), porque um vai para o template Jinja e o
    outro é o que a API devolve.

    Duas representações do mesmo conceito é uma hexadecimalidade do domínio que
    vale a pena levar a sério: se esta função aceitasse só uma, o outro motor
    teria de converter, e a conversão é onde uma política começa a divergir sem
    ninguém dar por isso. Aceitar as duas e documentar é mais barato do que
    unificar os dicionários com `Finding.as_dict()` num caminho novo.
    """
    for f in findings:
        gravidade = f["gravidade"] if isinstance(f, dict) else f.gravidade
        if gravidade == "critico":
            return True
    return score >= CATEGORIES[3][1]


def categorise(score: int) -> tuple[str, str]:
    """Devolve (rótulo, descrição) para um score já clampado a 0..100."""
    label = "SEGURO"
    description = CATEGORIES[0][2]
    for name, threshold, text in CATEGORIES:
        if score >= threshold:
            label, description = name, text
    return label, description


#: Rótulos com acento na UI; a constante interna não leva acento para comparar
#: com o que está em `docs/REQUIREMENTS.md` de forma literal.
_LABELS_UTF8 = {
    "SEGURO": "SEGURO",
    "ATENCAO": "ATENÇÃO",
    "ELEVADO": "ELEVADO",
    "CRITICO": "CRÍTICO",
}


def label_for_score(score: int) -> str:
    return _LABELS_UTF8[categorise(score)[0]]


def _strip_tags(html: str) -> str:
    """Texto visível aproximado, para a razão texto/imagem."""
    without_comments = re.sub(r"<!--.*?-->", " ", html, flags=re.DOTALL)
    return re.sub(r"<[^>]+>", " ", without_comments)


def _hrefs(html: str) -> list[str]:
    return re.findall(r"href\s*=\s*[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE)


def _srcs(html: str) -> list[str]:
    return re.findall(r"src\s*=\s*[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE)


def score_signature(html: str, plain_text: str = "") -> dict:
    """Analisa o HTML final e devolve score, categoria e as regras disparadas.

    Devolve sempre um relatório completo, mesmo sem findings — a UI precisa do
    score para mostrar a barra e a lista de regras para dizer o que *não* foi
    encontrado.
    """
    findings: list[Finding] = []
    lowered = html.lower()
    visible_text = _strip_tags(html).strip()
    hrefs = _hrefs(html)
    srcs = _srcs(html)

    # --- Regras de sinal grave -------------------------------------------
    if "data:" in lowered and re.search(r"(src|background)\s*=\s*[\"']\s*data:", lowered):
        findings.append(
            Finding(
                "IMG_DATA_URI",
                55,
                "critico",
                "A imagem está embebida como data: URI (texto base64 dentro do HTML).",
                "Carregue o logótipo pelo botão de upload. O mailutils serve a imagem "
                "por URL, que é o que os filtros esperam numa assinatura.",
            )
        )

    for tag in _FORBIDDEN_TAGS:
        if re.search(rf"<{tag}\b", lowered):
            findings.append(
                Finding(
                    f"FORBIDDEN_TAG_{tag.upper()}",
                    50,
                    "critico",
                    f"A assinatura contém um elemento <{tag}>.",
                    "Remove o elemento. Num email, <script> e <iframe> são bloqueados "
                    "pela maioria dos clientes e contam como sinal de spam.",
                )
            )

    if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all", lowered):
        findings.append(
            Finding(
                "HIDDEN_CONTENT",
                30,
                "alto",
                "Há conteúdo escondido (display:none ou equivalente).",
                "Texto escondido com ligações é a técnica clássica de spam. Elimine-o.",
            )
        )

    if re.search(r"http://", lowered) and "https://" not in lowered:
        findings.append(
            Finding(
                "INSECURE_URL",
                20,
                "medio",
                "Todas as ligações são http://.",
                "Os clientes marcam imagens http:// como não carregadas e isso "
                "implica penalização. Defina MAILUTILS_PUBLIC_BASE_URL com https://.",
            )
        )

    # --- Regras de densidade ---------------------------------------------
    link_text_hits = [h for h in hrefs if h.lower().startswith(("http://", "https://"))]
    if len(link_text_hits) >= 8:
        findings.append(
            Finding(
                "LINK_DENSITY",
                18,
                "medio",
                f"A assinatura tem {len(link_text_hits)} ligações HTTP.",
                "Menos de 6. Mais ligações do que texto é um dos sinais mais consistentes de spam.",
            )
        )
    elif len(link_text_hits) >= 5:
        findings.append(
            Finding(
                "LINK_COUNT_MODERATE",
                6,
                "baixo",
                f"A assinatura tem {len(link_text_hits)} ligações HTTP.",
                "Funciona, mas 3 a 4 é o que a maioria dos clientes pessoais precisa.",
            )
        )

    if len(visible_text) < 20 and srcs:
        findings.append(
            Finding(
                "IMAGE_WITHOUT_TEXT",
                25,
                "alto",
                "A assinatura é quase só imagem e tem pouco texto.",
                "Um email com ratio imagem/texto alto é classificado como "
                "promocional. Acrescente nome e contactos em texto.",
            )
        )

    # Ou uma dimensão de 1×1, ou um nome de beacon. As duas condições juntas
    # (E em vez de OU) deixavam passar `/beacon.gif` sem `1x1` no caminho —
    # exactamente o caso mais comum.
    for src in srcs:
        tiny = re.search(r"(^|[/_-])(1x1|px)([/_.-]|$)", src, re.I)
        beacon = re.search(r"pixel|spacer|beacon|track|openx|impression", src, re.I)
        if tiny or beacon:
            findings.append(
                Finding(
                    "TRACKING_PIXEL",
                    40,
                    "critico",
                    "A imagem parece um tracking pixel.",
                    "Remova. Mailutils não gera pixels de rastreio, e nenhum "
                    "cliente de email precisa de um.",
                )
            )

    # --- Regras de estrutura ---------------------------------------------
    if re.search(r"url\s*\(", lowered) and re.search(r"background(-image)?\s*:", lowered):
        findings.append(
            Finding(
                "CSS_BACKGROUND_IMAGE",
                20,
                "medio",
                "Há uma imagem de fundo via CSS.",
                "Muitos clientes de email bloqueiam imagens de fundo. Use <img>.",
            )
        )

    if len(html) > 30_000:
        findings.append(
            Finding(
                "OVERSIZED",
                15,
                "medio",
                f"O HTML tem {len(html) // 1024} KiB.",
                "Reduza o logótipo. Acima de ~30 KiB muitos clientes truncam a "
                "assinagem ou a marcam.",
            )
        )

    if "style" in lowered and re.search(r"position\s*:\s*fixed", lowered):
        findings.append(
            Finding(
                "POSITION_FIXED",
                10,
                "baixo",
                "Há posicionamento fixo no HTML.",
                "Não é suportado por clientes de email. Afecta a apresentação.",
            )
        )

    # --- Bonificações -----------------------------------------------------
    # Um HTML limpo tem de conseguir chegar a 0. Sem isto o utilizador vê
    # sempre um número > 0 e aprende a desvalorizar a escala.
    bonuses: list[Finding] = []
    if not findings:
        if len(html) < 3_000:
            bonuses.append(
                Finding(
                    "COMPACT",
                    -4,
                    "baixo",
                    "HTML compacto: menos de 3 KiB.",
                )
            )
        if visible_text and len(visible_text) > 40 and not link_text_hits:
            bonuses.append(
                Finding(
                    "TEXT_DOMINANT",
                    -3,
                    "baixo",
                    "Assinatura sobretudo textual, sem ligações.",
                )
            )
        if plain_text.strip():
            bonuses.append(
                Finding(
                    "PLAIN_FALLBACK",
                    -3,
                    "baixo",
                    "Existe versão de texto simples disponível para exportação.",
                )
            )

    total = sum(f.pontos for f in findings) + sum(f.pontos for f in bonuses)
    score = max(0, min(100, total))
    label, description = categorise(score)

    # O score e o bloqueio são duas perguntas diferentes.
    #
    #   O score mede risco **agregado**: 40 pontos de tracking pixel + 25 de
    #   pouco texto dão 65, que é "ELEVADO" e parece razoável ao olhar.
    #   O bloqueio é política sobre o **pior sinal individual**: uma assinatura
    #   com `<script>` ou um beacon de rastreio não deve ser exportável por
    #   nenhum total, porque nenhuma soma de pontos menores muda a natureza
    #   do que está no HTML.
    #
    # Decidir só pelo total dava ao utilizador a forma de contornar o bloqueio
    # com mais texto. (FR-4.9)
    bloqueada = bloqueado(score, findings)

    return {
        "score": score,
        "categoria": label,
        "categoria_acentuada": label_for_score(score),
        "descricao": description,
        # Uma assinatura vazia pontua 0 e merece 0, mas mostrar «0 / 100
        # SEGURO» com um selo verde e dois créditos de bónus é ensinar ao
        # utilizador a ignorar o selo antes de ele ter escrito uma letra. Um
        # score honesto para um formulário em branco é «ainda não há nada para
        # medir». O score não muda; o que muda é haver um estado neutro para
        # mostrar. (F-12)
        "vazio": not visible_text and not srcs and not hrefs,
        "regras": [f.as_dict() for f in findings],
        "creditos": [f.as_dict() for f in bonuses],
        "estatisticas": {
            "ligacoes_http": len(link_text_hits),
            "imagens": len(srcs),
            "caracteres_visiveis": len(visible_text),
            "bytes_html": len(html),
            "tamanho_legivel": f"{len(html) / 1024:.1f} KiB",
        },
        "exportacao_bloqueada": bloqueada,
        "aviso": (
            "Este score é uma heurística local baseada em padrões de spam "
            "documentados. Não é o algoritmo do Gmail, do Outlook ou de qualquer "
            "outro fornecedor, e não substitui um teste real de envio."
        ),
    }


__all__ = ["CATEGORIES", "Finding", "categorise", "label_for_score", "score_signature"]
