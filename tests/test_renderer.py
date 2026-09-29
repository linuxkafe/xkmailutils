"""Testes de `signatures/renderer.py`.

Este é o HTML que entra em emails de clientes reais. Os testes não verificam
"sai HTML": verificam as propriedades que, se quebrarem, põem a assinatura no
spam ou partem o Outlook.
"""

from __future__ import annotations

import re

import pytest

from mailutils import config
from mailutils.signatures import renderer


@pytest.fixture
def settings() -> config.Settings:
    base = config.load_settings(env="development")
    return config.Settings(
        **{
            **{field: getattr(base, field) for field in base.__dataclass_fields__},
            "public_base_url": "https://mailutils.exemplo.pt",
            "max_visible_links": 6,
        }
    )


def build(settings: config.Settings, **fields) -> renderer.SignatureData:
    return renderer.build_signature_data(fields, settings)


def render(settings: config.Settings, **fields) -> str:
    return renderer.render_html(build(settings, **fields), settings)


FULL = {
    "name": "Ana Silva",
    "role": "Responsável de Sistemas",
    "company": "Exemplo, Lda.",
    "email": "ana@exemplo.pt",
    "phone": "+351 912 345 678",
    "website": "exemplo.pt",
    "linkedin": "linkedin.com/in/ana",
    "github": "github.com/ana",
    "address": "Rua Exemplo 1, Porto",
    "note": "Informacao confidencial.",
}


class TestForbiddenConstructs:
    """Nenhum destes pode aparecer.

    São as regras que o produto existe para impor. Pôr um utilizador a sermão
    por uma coisa que o mailutils nunca gera seria um defeito do mailutils, não
    do utilizador.
    """

    @pytest.mark.parametrize(
        "tag", ["script", "style", "iframe", "form", "object", "embed", "link", "base"]
    )
    def test_tag_is_absent(self, settings: config.Settings, tag: str) -> None:
        html = render(settings, **FULL).lower()
        assert f"<{tag}" not in html, f"<{tag}> apareceu na assinatura"

    def test_never_emits_data_uri(self, settings: config.Settings) -> None:
        """A regra que dá nome ao produto. Um `data:` URI aqui é uma
        regressão de definição."""
        html = render(settings, **FULL)
        assert "data:" not in html.lower()
        assert "base64" not in html.lower()

    def test_no_hidden_content(self, settings: config.Settings) -> None:
        html = render(settings, **FULL).lower()
        assert "display:none" not in html.replace(" ", "")
        assert "visibility:hidden" not in html.replace(" ", "")

    def test_styles_are_inline_only(self, settings: config.Settings) -> None:
        """`<style>` é removido pelo Gmail em muitos clientes. Tudo tem de estar
        no atributo `style=`."""
        html = render(settings, **FULL)
        assert not re.search(r"<style", html, re.I)
        assert html.count("style=") > 0


class TestStructure:
    def test_is_table_based(self, settings: config.Settings) -> None:
        """O motor de renderização do Outlook não implementa CSS moderno.
        `<table>` + inline é a única estrutura que sobrevive."""
        html = render(settings, **FULL)
        assert "<table" in html
        assert "cellpadding=" in html and "cellspacing=" in html
        assert 'role="presentation"' in html

    def test_carries_the_marker_as_the_last_content(self, settings: config.Settings) -> None:
        """O marcador dá ao cliente uma fronteira entre assinatura e conteúdo,
        e reduz falsos positivos de Bayes. Fica dentro do `<div>` de
        abertura, que é onde o cliente espera que a assinatura termine."""
        html = render(settings, **FULL)
        assert renderer.SIGNATURE_MARKER in html
        assert html.index(renderer.SIGNATURE_MARKER) > html.index("Ana Silva")
        assert html[html.index(renderer.SIGNATURE_MARKER) :].strip() == (
            renderer.SIGNATURE_MARKER + "</div>"
        )

    def test_is_small(self, settings: config.Settings) -> None:
        """Acima de ~30 KiB muitos clientes truncam a assinatura."""
        assert len(render(settings, **FULL)) < 5000

    def test_empty_signature_still_produces_valid_html(self, settings: config.Settings) -> None:
        html = render(settings)
        assert html.startswith("<div")
        assert renderer.SIGNATURE_MARKER in html
        assert html.count("<table") == html.count("</table>")
        assert html.count("<td") == html.count("</td>")

    def test_logo_adds_an_image(self, settings: config.Settings) -> None:
        html = render(settings, logo_url="https://mailutils.exemplo.pt/media/logo-1.png")
        assert '<img src="https://mailutils.exemplo.pt/media/logo-1.png"' in html
        assert "alt=" in html

    def test_logo_image_is_https(self, settings: config.Settings) -> None:
        """A imagem entra no email de um destinatário. `http://` é conteúdo
        inseguro e o cliente não a carrega."""
        assert "http://" not in render(settings, logo_url="https://x.pt/l.png")

    def test_no_logo_means_no_image_tag(self, settings: config.Settings) -> None:
        assert "<img" not in render(settings, **FULL)

    def test_tables_are_balanced(self, settings: config.Settings) -> None:
        html = render(settings, **FULL)
        assert html.count("<table") == html.count("</table>")
        assert html.count("<tr>") == html.count("</tr>")
        assert html.count("<td") == html.count("</td>")


class TestEscaping:
    def test_html_in_text_is_escaped(self, settings: config.Settings) -> None:
        """Se `<script>` do utilizador passar em claro, o email leva código."""
        html = render(settings, name="<script>alert(1)</script>")
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html

    def test_attribute_injection_is_blocked(self, settings: config.Settings) -> None:
        """Um nome com `\"` podia fechar o atributo e injectar outro. É o
        ataque clássico contra atributos sem escape."""
        html = render(settings, name='Ana" onmouseover="alert(1)')
        assert 'onmouseover="alert' not in html
        assert "&quot;" in html

    def test_single_quote_is_escaped_too(self, settings: config.Settings) -> None:
        html = render(settings, name="Ana' onload='x")
        assert "onload='x" not in html

    def test_ampersand_is_escaped(self, settings: config.Settings) -> None:
        assert "&amp;" in render(settings, name="Ana & Parceiros")

    def test_note_newlines_become_breaks(self, settings: config.Settings) -> None:
        html = render(settings, note="linha 1\nlinha 2")
        assert "<br>" in html
        assert "linha 1<br>linha 2" in html.replace(">linha 1", ">linha 1")


class TestUrls:
    def test_scheme_is_added_when_missing(self, settings: config.Settings) -> None:
        data = build(settings, website="exemplo.pt")
        assert data.website == "https://exemplo.pt"

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ],
    )
    def test_dangerous_schemes_are_dropped(self, settings: config.Settings, url: str) -> None:
        """`javascript:` num `href` é execução, não navegação. Mesmo que o
        mailutils não gere URLs, o utilizador escreve-as à mão."""
        data = build(settings, website=url)
        assert data.website == ""
        assert "javascript" not in render(settings, website=url).lower()
        assert "vbscript" not in render(settings, website=url).lower()

    def test_mailto_and_tel_are_allowed(self, settings: config.Settings) -> None:
        html = render(settings, email="ana@exemplo.pt", phone="+351912345678")
        assert "mailto:ana@exemplo.pt" in html
        assert "tel:+351912345678" in html

    def test_phone_link_strips_formatting(self, settings: config.Settings) -> None:
        """O `tel:` tem de ser só dígitos, senão o cliente não o reconhece."""
        html = render(settings, phone="+351 (912) 345-678")
        assert 'href="tel:+351912345678"' in html
        assert "+351 (912) 345-678" in html  # o texto mantém a formatação


class TestLinkLimit:
    def test_website_and_profiles_become_links(self, settings: config.Settings) -> None:
        data = build(settings, **FULL)
        urls = [link["url"] for link in data.links]
        assert "https://exemplo.pt" in urls
        assert "https://linkedin.com/in/ana" in urls
        assert "https://github.com/ana" in urls

    def test_extra_links_are_truncated_and_counted(self, settings: config.Settings) -> None:
        data = build(
            settings,
            website="a.pt",
            linkedin="b.pt",
            github="c.pt",
            mastodon="d.pt",
            link_list=[{"label": f"L{i}", "url": f"https://x{i}.pt"} for i in range(5)],
        )
        assert len(data.links_for_output(6)) == 6
        assert data.truncated_links == 3

    def test_truncation_is_reported_in_the_html(self, settings: config.Settings) -> None:
        """O utilizador tem de saber que há ligações que não aparecem. Cortar
        em silêncio é pior do que não mostrar."""
        data = build(
            settings,
            link_list=[{"label": f"L{i}", "url": f"https://x{i}.pt"} for i in range(9)],
        )
        assert data.truncated_links >= 1
        assert len(data.links_for_output(6)) == 6

    def test_malformed_link_entries_are_ignored(self, settings: config.Settings) -> None:
        data = build(settings, link_list=[None, "texto", 42, {"url": ""}, {"url": "x.pt"}])
        assert len(data.links) == 1
        assert data.links[0]["url"] == "https://x.pt"


class TestThemes:
    def test_default_is_dark(self, settings: config.Settings) -> None:
        assert build(settings).theme == renderer.DEFAULT_THEME
        assert renderer.DEFAULT_THEME == "dark"

    def test_unknown_theme_falls_back_with_a_warning(self, settings: config.Settings) -> None:
        data = build(settings, theme="arco-iris-neon")
        assert data.theme == renderer.DEFAULT_THEME
        assert any("arco-iris-neon" in w for w in data.warnings)

    def test_theme_colours_reach_the_html(self, settings: config.Settings) -> None:
        light = render(settings, theme="light", name="Ana")
        dark = render(settings, theme="dark", name="Ana")
        assert renderer.THEMES["light"].text in light
        assert renderer.THEMES["dark"].text in dark
        assert light != dark


class TestPlainText:
    def test_ends_with_the_rfc_marker(self, settings: config.Settings) -> None:
        """`-- ` no fim é a convenção de assinatura (RFC 3676). Reduz falsos
        positivos em classificadores de Bayes."""
        plain = render_plain(settings, **FULL)
        assert plain.endswith("-- ")
        assert plain.rstrip().endswith("--")

    def test_contains_the_essentials(self, settings: config.Settings) -> None:
        text = render_plain(settings, **FULL)
        for expected in ("Ana Silva", "ana@exemplo.pt", "912 345 678", "exemplo.pt"):
            assert expected in text

    def test_has_no_markup(self, settings: config.Settings) -> None:
        text = render_plain(settings, **FULL)
        assert "<" not in text
        assert "&" not in text

    def test_respects_the_link_limit(self, settings: config.Settings) -> None:
        data = build(
            settings,
            link_list=[{"label": f"L{i}", "url": f"https://x{i}.pt"} for i in range(9)],
        )
        text = renderer.render_plain(data, settings)
        assert text.count("https://x") == 6

    def test_empty_produces_only_the_marker(self, settings: config.Settings) -> None:
        assert render_plain(settings).rstrip() == "--"


class TestClientInstructions:
    @pytest.mark.parametrize("client", ["thunderbird", "outlook", "gmail", "applemail"])
    def test_every_client_has_instructions(self, client: str) -> None:
        assert renderer.client_instructions(client).strip()

    def test_unknown_client_returns_empty(self) -> None:
        assert renderer.client_instructions("pineapple") == ""

    def test_instructions_are_in_portuguese(self) -> None:
        assert "Definições" in renderer.client_instructions("thunderbird")


def render_plain(settings: config.Settings, **fields) -> str:
    return renderer.render_plain(build(settings, **fields), settings)
