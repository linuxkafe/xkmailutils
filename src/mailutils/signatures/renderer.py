"""Geração do HTML da assinatura.

Porquê table-based com estilos inline: o motor de renderização do Outlook
(Word) não implementa CSS moderno, e o Gmail removes `<style>` do cabeçalho em
muitos clientes. `<table>` + `style="..."` na célula é a única forma que sobrevive
a ambos. (FR-4.1)

Porquê zero `data:` URI: um `data:` URI numa assinatura é o sinal de spam mais
severo que existe. A imagem vem por URL absoluta. (FR-4.2)

Este módulo é *critical file*: o output entra em emails de clientes reais.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape

from ..config import Settings

#: Marcador no fim do HTML. Ajuda o cliente a distinguir assinatura de conteúdo,
#: e dá a um filtro uma fronteira_lexical_em_vez_de_limite_de_tag.
SIGNATURE_MARKER = "<!-- mailutils-signature -->"


#: Toda a saída de texto passa por aqui. Duplo escape em atributos (aspas e
#: apostrofos) evita partir um atributo com o input do utilizador.
def _t(value: str | None) -> str:
    return escape((value or "").strip(), quote=True)


def _plain(value: str | None) -> str:
    """Escapa para texto simples, sem entidades HTML."""
    return (value or "").strip()


@dataclass(frozen=True)
class Theme:
    """Cores de uma assinatura. Alinhado com `docs/DESIGN.md`."""

    name: str
    background: str
    text: str
    muted: str
    accent: str
    border: str


#: Temas de assinatura. Deliberadamente poucos: cada tema é uma superfície de
#: suporte a sério (cliente de email, impressão, modo escuro do OS), e cada
#: combinação nova é uma que alguém vai ter de depurar.
#:
#: As cores não foram escolhidas a olho. `tests/test_renderer.py` parametriza o
#: gate de contraste sobre `sorted(THEMES)`, portanto **qualquer tema novo é
#: verificado automaticamente** contra 4.5:1 para `text` e `muted`, no fundo
#: que sai no HTML — não contra o fundo declarado. Um tema escuro tem de levar o
#: seu fundo; um claro é transparente de propósito (ver `_precisa_de_fundo`).
THEMES: dict[str, Theme] = {
    "dark": Theme(
        name="Escuro",
        background="#1a1a1a",
        text="#f0f0f0",
        muted="#b0b0b0",
        accent="#F8B400",
        border="#3a3a3a",
    ),
    "light": Theme(
        name="Claro",
        background="#ffffff",
        text="#212121",
        muted="#555555",
        accent="#0056b3",
        border="#e0e0e0",
    ),
    "graphite": Theme(
        name="Grafite",
        background="#101317",
        text="#f2f4f7",
        muted="#b3bcc9",
        accent="#7cb8ff",
        border="#2a3038",
    ),
    "navy": Theme(
        name="Azul escuro",
        background="#0d1b2a",
        text="#eef3f8",
        muted="#aec4d9",
        accent="#ffd166",
        border="#1b3348",
    ),
    "forest": Theme(
        name="Verde escuro",
        background="#0e1f17",
        text="#eaf4ee",
        muted="#a9c6b6",
        accent="#8fd694",
        border="#1d3a2b",
    ),
    "paper": Theme(
        name="Papel",
        background="#ffffff",
        text="#2b2620",
        muted="#5f574c",
        accent="#9a3412",
        border="#ddd6c9",
    ),
    "slate": Theme(
        name="Ardósia",
        background="#ffffff",
        text="#1f2933",
        muted="#52606d",
        accent="#0b5c8a",
        border="#d5dde3",
    ),
}

DEFAULT_THEME = "dark"


@dataclass(frozen=True)
class Layout:
    """Uma variante de estrutura da assinatura."""

    name: str
    description: str


#: Estruturas possíveis. `stack` é a original e não muda: assinaturas já
#: guardadas em `signatures` não têm coluna de layout, e o default tem de
#: continuar a produzir o mesmo HTML, byte a byte, ou o T013 passa a mudar o
#: email de quem já tinha uma assinatura.
LAYOUTS: dict[str, Layout] = {
    "stack": Layout(
        name="Vertical",
        description="Identidade e contactos um abaixo do outro, ao lado do logótipo.",
    ),
    "compact": Layout(
        name="Compacto",
        description="Duas linhas, o mais baixo possível. O logótipo fica pequeno e ao lado.",
    ),
    "columns": Layout(
        name="Duas colunas",
        description="Identidade em cima e contactos repartidos por duas colunas.",
    ),
    "boxed": Layout(
        name="Com moldura",
        description="Vertical, dentro de uma moldura com borda. Visível nos temas claros.",
    ),
}

DEFAULT_LAYOUT = "stack"

#: Campos que são ligações visíveis. O limite existe porque 15 hyperlinks numa
#: assinatura é, para vários filtros, mais links do que texto. (FR-4.4)
LINK_KEYS = ("website", "linkedin", "github", "mastodon", "custom")


@dataclass
class SignatureData:
    """Conteúdo de uma assinatura, já validado e normalizado."""

    name: str = ""
    role: str = ""
    company: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    address: str = ""
    note: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    logo_url: str = ""
    logo_alt: str = ""
    theme: str = DEFAULT_THEME
    layout: str = DEFAULT_LAYOUT
    #: Preenchido pelo renderer quando o limite de ligações foi excedido.
    truncated_links: int = 0
    warnings: list[str] = field(default_factory=list)

    def links_for_output(self, limit: int) -> list[dict[str, str]]:
        return [link for link in self.links[:limit] if link.get("url")]


def _normalise_url(url: str) -> str:
    """Garante esquema explícito.

    `javascript:`, `data:` e `vbscript:` num atributo `href` são execução, não
    navegação. Rejeitados aqui, antes de chegarem ao HTML.
    """
    candidate = (url or "").strip()
    if not candidate:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", candidate):
        candidate = "https://" + candidate
    scheme = candidate.split(":", 1)[0].lower()
    if scheme not in {"http", "https", "mailto", "tel"}:
        return ""
    return candidate


def build_signature_data(
    raw: dict,
    settings: Settings,
    theme: str | None = None,
    layout: str | None = None,
) -> SignatureData:
    """Normaliza o input cru do formulário num `SignatureData`.

    Concentra aqui toda a validação de ligações, temas e layouts para que o
    renderer possa assumir dados já limpos. (Princípio 2: simplicidade)
    """
    data = SignatureData(
        name=_plain(raw.get("name")),
        role=_plain(raw.get("role")),
        company=_plain(raw.get("company")),
        email=_plain(raw.get("email")),
        phone=_plain(raw.get("phone")),
        website=_normalise_url(raw.get("website", "")),
        address=_plain(raw.get("address")),
        note=_plain(raw.get("note")),
        logo_url=(raw.get("logo_url") or "").strip(),
        logo_alt=_plain(raw.get("logo_alt")) or "Logótipo",
        theme=theme or raw.get("theme") or DEFAULT_THEME,
        layout=layout or raw.get("layout") or DEFAULT_LAYOUT,
    )

    if data.theme not in THEMES:
        data.warnings.append(f"Tema {data.theme!r} desconhecido. A usar '{DEFAULT_THEME}'.")
        data.theme = DEFAULT_THEME

    # A mesma política do tema: um layout desconhecido **não** cai para o
    # default em silêncio. Uma assinatura guardada com `layout='box'` que
    # deixasse de existir renderizaria vertical sem ninguém saber porque é que
    # a assinatura mudou de aspecto depois de uma actualização.
    if data.layout not in LAYOUTS:
        data.warnings.append(f"Estrutura {data.layout!r} desconhecida. A usar '{DEFAULT_LAYOUT}'.")
        data.layout = DEFAULT_LAYOUT

    links: list[dict[str, str]] = []
    if data.website:
        label = data.website.removeprefix("https://").rstrip("/")
        links.append({"label": label, "url": data.website})

    for key in LINK_KEYS[1:]:
        value = raw.get(key) or (raw.get("links") or {}).get(key)
        url = _normalise_url(value if isinstance(value, str) else "")
        if url:
            label = {"linkedin": "LinkedIn", "github": "GitHub", "mastodon": "Mastodon"}.get(
                key, key.capitalize()
            )
            links.append({"label": label, "url": url})

    raw_links = raw.get("link_list") or []
    if isinstance(raw_links, list):
        for item in raw_links:
            if not isinstance(item, dict):
                continue
            url = _normalise_url(item.get("url", ""))
            if url:
                links.append({"label": _plain(item.get("label")) or url, "url": url})

    data.links = links
    data.truncated_links = max(0, len(links) - settings.max_visible_links)
    return data


def _luminance(colour: str) -> float:
    """Luminância relativa de um `#rrggbb`. WCAG 2.1."""
    hexa = colour.lstrip("#")
    canais = [int(hexa[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lineares = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * lineares[0] + 0.7152 * lineares[1] + 0.0722 * lineares[2]


def _contraste(a: str, b: str) -> float:
    """Razão de contraste entre duas cores, de 1.0 a 21.0."""
    la, lb = _luminance(a), _luminance(b)
    claro, escuro = max(la, lb), min(la, lb)
    return (claro + 0.05) / (escuro + 0.05)


def _precisa_de_fundo(theme: Theme) -> bool:
    """Se um tema tem de levar o seu fundo ou pode ficar transparente.

    Uma assinatura transparente é o que se quer num cliente de email claro: o
    texto adota a cor do fundo do leitor. Mas um tema escuro sem fundo é
    **ilegível** — foi o que aconteceu: o tema `dark` tinha `text="#f0f0f0"` e
    o `background="#1a1a1a"` declarado nunca era emitido, contra o branco de um
    Thunderbird ou um Outlook dava 1.14:1, e a aplicação dizia «0 / 100
    SEGURO» por cima de uma assinatura que o destinatário não via.

    A regra é por luminância, não por nome: se o texto do tema for mais claro
    que o fundo declarado, o fundo tem de ir junto. Um tema claro fica
    transparente de propósito — pôr `#ffffff` num leitor com fundo colorido
    faria a assinatura aparecer como um rectângulo branco, que é pior.
    """
    return _luminance(theme.text) > _luminance(theme.background)


class _Blocos:
    """As peças de HTML que as quatro estruturas partilh.

    Existe por uma razão concreta e não por estilo: o `stack` é o layout
    original e tem de continuar a produzir **byte a byte** o mesmo HTML, porque
    é o que já está guardado na base de dados e o que os testes fixam. Se cada
    estrutura construísse os seus `<span>` e os seus `<a>`, uma correcção de
    escaping num deles não chegaria aos outros, e a garantia de que texto de
    utilizador não injecta HTML passaria a valer para três dos quatro.

    Tudo o que é comum — escaping, cor, tamanho, o `<a>` — vive aqui. Cada
    estrutura decide só a **arrumação**.
    """

    def __init__(self, data: SignatureData, settings: Settings) -> None:
        self.data = data
        self.settings = settings
        self.theme = THEMES[data.theme]
        self.links = data.links_for_output(settings.max_visible_links)

    # --- peças pequenas -------------------------------------------------

    def span(self, content: str, *, size: int, colour: str, weight: str = "") -> str:
        weight_css = f"font-weight:{weight};" if weight else ""
        return f'<span style="font-size:{size}px;{weight_css}color:{colour};">{content}</span>'

    def anchor(self, href: str, label: str) -> str:
        return (
            f'<a href="{_t(href)}" style="color:{self.theme.accent};text-decoration:none;">'
            f"{_t(label)}</a>"
        )

    def linha(self, item: str) -> str:
        """Uma linha da assinatura é a sua própria tabela. Ver `render_stack`."""
        return (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
            ' style="margin:0 0 4px 0;"><tr><td style="padding:0;margin:0;">'
            f"{item}</td></tr></table>"
        )

    def pilha(self, items: list[str]) -> str:
        return "".join(self.linha(item) for item in items)

    def celula(self, inner: str) -> str:
        if not inner:
            return ""
        return f'<td style="vertical-align:top;">{inner}</td>'

    # --- conteúdo -------------------------------------------------------

    def identity(self) -> list[str]:
        out: list[str] = []
        if self.data.name:
            out.append(self.span(_t(self.data.name), size=15, colour=self.theme.text, weight="600"))
        if self.data.role:
            out.append(self.span(_t(self.data.role), size=13, colour=self.theme.muted))
        if self.data.company:
            out.append(self.span(_t(self.data.company), size=13, colour=self.theme.muted))
        return out

    def contactos(self) -> list[str]:
        """Email, telefone, morada e ligações, por esta ordem e nesta fonte."""
        out: list[str] = []
        if self.data.email:
            out.append(
                self.span(
                    self.anchor(f"mailto:{self.data.email}", self.data.email),
                    size=13,
                    colour=self.theme.text,
                )
            )
        if self.data.phone:
            tel = re.sub(r"[^0-9+]", "", self.data.phone) or self.data.phone
            out.append(
                self.span(
                    self.anchor(f"tel:{tel}", self.data.phone),
                    size=13,
                    colour=self.theme.text,
                )
            )
        if self.data.address:
            out.append(self.span(_t(self.data.address), size=12, colour=self.theme.muted))
        for link in self.links:
            out.append(
                self.span(
                    self.anchor(link["url"], link["label"]),
                    size=13,
                    colour=self.theme.text,
                )
            )
        return out

    def telefone(self) -> str:
        return re.sub(r"[^0-9+]", "", self.data.phone or "") or (self.data.phone or "")

    def nota(self) -> str:
        if not self.data.note:
            return ""
        return (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
            ' style="margin:8px 0 0 0;"><tr><td style="padding:0;margin:0;'
            f"border-top:1px solid {self.theme.border};font-size:11px;color:{self.theme.muted};"
            f'line-height:16px;">{_t(self.data.note).replace(chr(10), "<br>")}</td></tr></table>'
        )

    def logo(self, *, largura: int = 96, align: str = "top") -> str:
        """A célula do logótipo só existe se houver logótipo.

        Uma célula vazia com `width` fixo é sinal de HTML de spam e não serve
        para nada.
        """
        if not self.data.logo_url:
            return ""
        return (
            f'<td style="padding:0 16px 0 0;vertical-align:{align};">'
            f'<img src="{_t(self.data.logo_url)}" width="{largura}" '
            f'alt="{_t(self.data.logo_alt)}" '
            f'style="display:block;width:{largura}px;height:auto;border:0;outline:none;'
            'text-decoration:none;-ms-interpolation-mode:bicubic;"></td>'
        )

    # --- superfície -----------------------------------------------------

    def superficie(self) -> tuple[str, str, str]:
        """`(fundo, padding, bgcolor)` — o mesmo que `fundo`/`padding`/`cor_tabela`.

        O fundo do tema, quando o tema é escuro, vai em dois sítios de propósito:
        `background` no `<div>` para os clientes modernos, e `bgcolor` no
        `<table>` porque o Word engine do Outlook ignora `background` num `<div>`
        e só honra o atributo. Sem os dois, o tema escuro sai com texto claro
        sobre o branco do cliente — 1.14:1, invisível. (F-02)
        """
        opaco = _precisa_de_fundo(self.theme)
        return (
            f"background:{self.theme.background};" if opaco else "",
            "padding:10px 12px;" if opaco else "",
            f' bgcolor="{self.theme.background}"' if opaco else "",
        )

    def embrulho(self, inner: str, *, padding_interno: bool = False) -> str:
        fundo, padding, _ = self.superficie()
        # `padding_interno`: quando a estrutura já traz o seu próprio padding
        # (a moldura), o do embrulho seria um segundo anel de espaço em volta.
        return (
            '<div style="font-family:Arial,Helvetica,sans-serif;'
            f"color:{self.theme.text};font-size:13px;line-height:18px;{fundo}"
            f"{'' if padding_interno else padding}"
            f'display:inline-block;">'
            f"{inner}{SIGNATURE_MARKER}</div>"
        )

    def tabela(self, *, estilo: str, cor_tabela: str) -> str:
        return (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
            f'{cor_tabela} style="{estilo}">'
        )


def _render_stack(b: _Blocos) -> str:
    """A estrutura original: identidade e contactos empilhados, ao lado do logótipo.

    Não muda. É o layout das assinaturas que já estão guardadas.
    """
    body = b.pilha(b.identity()) + b.pilha(b.contactos()) + b.nota()

    fundo, _, cor_tabela = b.superficie()
    html = "".join(
        [
            b.tabela(
                estilo=(f"border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;{fundo}"),
                cor_tabela=cor_tabela,
            ),
            "<tr>",
            b.logo(),
            b.celula(body),
            "</tr>",
            "</table>",
        ]
    )
    return b.embrulho(html)


def _render_compact(b: _Blocos) -> str:
    """Tudo em duas linhas, o mais baixo possível.

    As ligações não descem de linha: a assinatura ocupa a altura de um parágrafo
    em vez de seis. É o layout para quem manda muito e não quer que o rodapé
    empurre o conteúdo para baixo da dobra.
    """
    theme = b.theme
    sep = f'<span style="color:{theme.border};"> · </span>'

    primeira: list[str] = []
    if b.data.name:
        primeira.append(b.span(_t(b.data.name), size=15, colour=theme.text, weight="600"))
    for value in (b.data.role, b.data.company):
        if value:
            primeira.append(b.span(_t(value), size=13, colour=theme.muted))
    if b.data.email:
        primeira.append(
            b.span(
                b.anchor(f"mailto:{b.data.email}", b.data.email),
                size=13,
                colour=theme.text,
            )
        )
    if b.data.phone:
        primeira.append(
            b.span(
                b.anchor(f"tel:{b.telefone()}", b.data.phone),
                size=13,
                colour=theme.text,
            )
        )

    segunda: list[str] = []
    if b.data.address:
        segunda.append(b.span(_t(b.data.address), size=12, colour=theme.muted))
    for link in b.links:
        segunda.append(b.span(b.anchor(link["url"], link["label"]), size=13, colour=theme.text))

    corpo = sep.join(primeira)
    if segunda:
        corpo += f"<br>{sep.join(segunda)}"
    if b.data.note:
        corpo += b.nota()

    fundo, _, cor_tabela = b.superficie()
    celula = b.celula(corpo).replace("vertical-align:top", "vertical-align:middle")
    html = "".join(
        [
            b.tabela(
                estilo=(f"border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;{fundo}"),
                cor_tabela=cor_tabela,
            ),
            "<tr>",
            b.logo(largura=40, align="middle"),
            celula,
            "</tr>",
            "</table>",
        ]
    )
    return b.embrulho(html)


def _render_columns(b: _Blocos) -> str:
    """Identidade em cima, contactos repartidos por duas colunas.

    A segunda coluna só existe se sobrar conteúdo para ela. Uma coluna vazia com
    `width` fixo é um buraco visível na assinatura e um sinal de HTML de spam.
    """
    corpo = b.pilha(b.identity())

    contactos = b.contactos()
    if contactos:
        meio = (len(contactos) + 1) // 2
        esquerda = contactos[:meio]
        direita = contactos[meio:]
        celulas = [f'<td style="padding:0 24px 0 0;vertical-align:top;">{b.pilha(esquerda)}</td>']
        if direita:
            celulas.append(f'<td style="vertical-align:top;">{b.pilha(direita)}</td>')
        corpo += (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
            ' style="margin:0;"><tr>' + "".join(celulas) + "</tr></table>"
        )

    corpo += b.nota()

    fundo, _, cor_tabela = b.superficie()
    html = "".join(
        [
            b.tabela(
                estilo=(f"border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;{fundo}"),
                cor_tabela=cor_tabela,
            ),
            "<tr>",
            b.logo(),
            b.celula(corpo),
            "</tr>",
            "</table>",
        ]
    )
    return b.embrulho(html)


def _render_boxed(b: _Blocos) -> str:
    """Vertical, dentro de uma moldura.

    A diferença para `stack` é a moldura, que **existe mesmo nos temas claros**.
    Nos escuros a moldura quase não se vê porque o bloco já é opaco; nos claros
    é o que dá à assinatura uma silhueta em vez de texto solto no fundo do
    leitor.
    """
    theme = b.theme
    body = b.pilha(b.identity()) + b.pilha(b.contactos()) + b.nota()

    fundo, _, cor_tabela = b.superficie()
    html = "".join(
        [
            b.tabela(
                estilo=(
                    "border-collapse:separate;font-family:Arial,Helvetica,sans-serif;"
                    f"border:1px solid {theme.border};border-radius:8px;"
                    f"padding:12px 14px;{fundo}"
                ),
                cor_tabela=cor_tabela,
            ),
            "<tr>",
            b.logo(),
            b.celula(body),
            "</tr>",
            "</table>",
        ]
    )
    return b.embrulho(html, padding_interno=True)


#: Índice de ``data.layout`` para a função que desenha. Preenchido depois das
#: funções, por isso `_Blocos` tem de estar definida antes.
_RENDERERS = {
    "stack": _render_stack,
    "compact": _render_compact,
    "columns": _render_columns,
    "boxed": _render_boxed,
}


def render_html(data: SignatureData, settings: Settings) -> str:
    """Devolve o HTML da assinatura. É isto, byte a byte, que vai para o email."""
    render = _RENDERERS.get(data.layout, _render_stack)
    return render(_Blocos(data, settings))


def render_plain(data: SignatureData, settings: Settings) -> str:
    """Equivalente em texto simples.

    Existe por duas razões: o Thunderbird e o Outlook deixam escolher texto
    simples; e Bayes (o filtro do Gmail) dá pontuação muito mais baixa a texto
    plano. (FR-3.9, FR-4.5)
    """
    lines: list[str] = []
    if data.name:
        lines.append(data.name)
    if data.role or data.company:
        lines.append(" · ".join(part for part in (data.role, data.company) if part))
    if data.email:
        lines.append(data.email)
    if data.phone:
        lines.append(data.phone)
    if data.address:
        lines.append(data.address)
    for link in data.links_for_output(settings.max_visible_links):
        lines.append(link["url"])
    if data.note:
        lines.append("")
        lines.extend(data.note.splitlines())
    # O `-- ` final é a convenção de assinatura em texto simples (RFC 3676 §4.3).
    lines.append("-- ")
    return "\n".join(lines)


def client_instructions(client: str) -> str:
    """Instruções de instalação por cliente de email. (FR-3.10)"""
    instructions = {
        "thunderbird": (
            "Definições → Composição de mensagens → Assinatura de texto → "
            "desmarque «Usar HTML» e cole o bloco. Se a sua versão tiver "
            "«HTML», cole lá o ficheiro descarregado."
        ),
        "outlook": (
            "Ficheiro → Opções → Correio → Compor e responder. No Outlook "
            "Windows, a assinatura tem de ser um ficheiro .htm — descarregue o "
            ".html e aponte para ele. No Outlook Web: Definições → Correio → "
            "Compor e responder."
        ),
        "gmail": (
            "Definições → Geral → Assinatura → Criar nova → cole o HTML. Se a "
            "assinatura aparecer sem formatação, o Gmail removeu o estilo — "
            "exporte também a versão .txt."
        ),
        "applemail": (
            "Mail → Definições → Assinaturas → + → editar, e cole o HTML. "
            "Requer «Always match default font» desligado."
        ),
    }
    return instructions.get(client, "")


__all__ = [
    "DEFAULT_LAYOUT",
    "DEFAULT_THEME",
    "LAYOUTS",
    "LINK_KEYS",
    "SIGNATURE_MARKER",
    "THEMES",
    "SignatureData",
    "Theme",
    "build_signature_data",
    "client_instructions",
    "render_html",
    "render_plain",
]
