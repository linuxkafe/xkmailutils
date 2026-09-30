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
}

DEFAULT_THEME = "dark"

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


def build_signature_data(raw: dict, settings: Settings, theme: str | None = None) -> SignatureData:
    """Normaliza o input cru do formulário num `SignatureData`.

    Concentra aqui toda a validação de ligações e temas para que o renderer
    possa assumir dados já limpos. (Princípio 2: simplicidade)
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
    )

    if data.theme not in THEMES:
        data.warnings.append(f"Tema {data.theme!r} desconhecido. A usar '{DEFAULT_THEME}'.")
        data.theme = DEFAULT_THEME

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


def render_html(data: SignatureData, settings: Settings) -> str:
    """Devolve o HTML da assinatura. É isto, byte a byte, que vai para o email."""
    theme = THEMES[data.theme]
    links = data.links_for_output(settings.max_visible_links)

    # Cada linha da assinatura é a sua própria tabela. O Word engine do Outlook
    # não respeita `<div>` dentro de `<td>` com margens consistentes, e a
    # correção é não usar `<div>` para estrutura. (Princípio 1: simplicidade
    # rui por compatibilidade, não por estilo.)
    def stack(items: list[str]) -> str:
        return "".join(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
            f' style="margin:0 0 4px 0;"><tr><td style="padding:0;margin:0;">'
            f"{item}</td></tr></table>"
            for item in items
        )

    def span(content: str, *, size: int, colour: str, weight: str = "") -> str:
        weight_css = f"font-weight:{weight};" if weight else ""
        return f'<span style="font-size:{size}px;{weight_css}color:{colour};">{content}</span>'

    def anchor(href: str, label: str) -> str:
        return (
            f'<a href="{_t(href)}" style="color:{theme.accent};text-decoration:none;">'
            f"{_t(label)}</a>"
        )

    identity: list[str] = []
    if data.name:
        identity.append(span(_t(data.name), size=15, colour=theme.text, weight="600"))
    if data.role:
        identity.append(span(_t(data.role), size=13, colour=theme.muted))
    if data.company:
        identity.append(span(_t(data.company), size=13, colour=theme.muted))

    contact_rows: list[str] = []
    if data.email:
        contact_rows.append(
            span(anchor(f"mailto:{data.email}", data.email), size=13, colour=theme.text)
        )
    if data.phone:
        tel = re.sub(r"[^0-9+]", "", data.phone) or data.phone
        contact_rows.append(span(anchor(f"tel:{tel}", data.phone), size=13, colour=theme.text))
    if data.address:
        contact_rows.append(span(_t(data.address), size=12, colour=theme.muted))
    for link in links:
        contact_rows.append(span(anchor(link["url"], link["label"]), size=13, colour=theme.text))

    # A coluna do logótipo só existe se houver logótipo. Uma célula vazia com
    # width fixo é sinal de HTML de spam e não serve para nada.
    logo_cell = ""
    if data.logo_url:
        logo_cell = (
            '<td style="padding:0 16px 0 0;vertical-align:top;">'
            f'<img src="{_t(data.logo_url)}" width="96" alt="{_t(data.logo_alt)}" '
            'style="display:block;width:96px;height:auto;border:0;outline:none;'
            'text-decoration:none;-ms-interpolation-mode:bicubic;"></td>'
        )

    def cell(inner: str) -> str:
        if not inner:
            return ""
        return f'<td style="vertical-align:top;">{inner}</td>'

    note = ""
    if data.note:
        note = (
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
            ' style="margin:8px 0 0 0;"><tr><td style="padding:0;margin:0;'
            f"border-top:1px solid {theme.border};font-size:11px;color:{theme.muted};"
            f'line-height:16px;">{_t(data.note).replace(chr(10), "<br>")}</td></tr></table>'
        )

    body = stack(identity) + stack(contact_rows) + note

    # O fundo do tema, quando o tema é escuro. Vai em dois sítios de propósito:
    # `background` no `<div>` para os clientes modernos, e `bgcolor` no
    # `<table>` porque o Word engine do Outlook ignora `background` num `<div>`
    # e só honra o atributo. Sem os dois, o tema escuro sai com texto claro
    # sobre o branco do cliente — 1.14:1, invisível. (F-02)
    opaco = _precisa_de_fundo(theme)
    fundo = f"background:{theme.background};" if opaco else ""
    # O padding só existe quando há fundo: texto colado à borda de um bloco
    # escuro parece uma caixa mal feita, e texto sem fundo não precisa dele.
    padding = "padding:10px 12px;" if opaco else ""
    cor_tabela = f' bgcolor="{theme.background}"' if opaco else ""

    parts = [
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
        f"{cor_tabela} "
        'style="border-collapse:collapse;font-family:Arial,Helvetica,sans-serif;'
        f'{fundo}">',
        "<tr>",
        logo_cell,
        cell(body),
        "</tr>",
        "</table>",
    ]
    html = "".join(parts)

    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;'
        f"color:{theme.text};font-size:13px;line-height:18px;{fundo}{padding}"
        f'display:inline-block;">'
        f"{html}{SIGNATURE_MARKER}</div>"
    )


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
    "DEFAULT_THEME",
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
