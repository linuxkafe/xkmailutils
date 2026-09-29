"""Testes da invariante visual: cabeçalho e rodapé partilham o mesmo fundo.

`FR-5.1` é um requisito explícito do dono do projecto: em darkmode, o fundo do
cabeçalho e o do rodapé são idênticos. A razão de isto ter um ficheiro de teste
próprio é que é a única regra do `DESIGN.md` que é uma *igualdade* entre dois
elementos — e igualdades não se vêem num revisão visual, só se verificam.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "src" / "mailutils" / "static"
TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "mailutils" / "templates"

CSS = (STATIC / "app.css").read_text(encoding="utf-8")


def _all_rules(css: str) -> list[tuple[list[str], str]]:
    """Todos os blocos de nível de topo, com os seus selectors.

    Os comentários são removidos primeiro: um `{` dentro de um comentário
    desalinha o emparelhamento de todo o resto do ficheiro, e o parser passa a
    devolver regras que não existem. Foi o primeiro bug deste ficheiro.

    O `:root,` seguido de `[data-tema="light"]` num só bloco tem de contar
    como as duas coisas — é a razão de isto existir em vez de partir o
    ficheiro por linhas.
    """
    clean = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules: list[tuple[list[str], str]] = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", clean):
        selectors = [part.strip() for part in match.group(1).split(",") if part.strip()]
        rules.append((selectors, match.group(2)))
    return rules


def _shared_rule(css: str, selector: str) -> str:
    """Corpo da primeira regra cuja lista de selectors inclui `selector`."""
    for selectors, body in _all_rules(css):
        if selector in selectors:
            return body
    raise AssertionError(f"não encontrei regra para {selector!r} em app.css")


def _own_rule(css: str, selector: str) -> str:
    """Corpo de uma regra que menciona `selector` **sozinho**.

    Distingue isto da regra partilhada é que permite perguntar "o cabeçalho
    tem um fundo próprio?". Sem a distinção, a pergunta recebe a regra
    partilhada como resposta e passa a provar o contrário do que diz.
    """
    for selectors, body in _all_rules(css):
        if selectors == [selector]:
            return body
    return ""


def _declarations(css: str, selector: str) -> dict[str, str]:
    """Declarações de uma regra, por nome de propriedade."""
    body = _shared_rule(css, selector)
    return dict(re.findall(r"([a-z-]+)\s*:\s*([^;]+);", body))


def _theme_tokens(css: str, theme: str) -> dict[str, str]:
    """Tokens de um tema, já resolvidos.

    Um tema é `:root` (os valores por omissão) mais o bloco que o sobrescreve.
    Ler só um dos dois dá metade dos tokens — e a metade errada, porque é a
    que não tem o que se está a testar. A ordem do ficheiro importa: mais
    tarde ganha, como em CSS.
    """
    tokens: dict[str, str] = {}
    for selectors, body in _all_rules(css):
        applies = ":root" in selectors or f'[data-tema="{theme}"]' in selectors
        if not applies:
            continue
        tokens.update(re.findall(r"(--[a-z-]+)\s*:\s*([^;]+);", body))
    return tokens


class TestHeaderFooterEquality:
    def test_both_appear_in_the_same_rule(self) -> None:
        """Uma regra partilhada, não duas regras iguais. Duas regras podem
        divergir na edição seguinte; uma não."""
        assert re.search(r"\.site-header\s*,\s*\.site-footer\s*\{", CSS), (
            "cabeçalho e rodapé têm de partilhar uma única regra"
        )

    def test_both_use_the_same_background_token(self) -> None:
        shared = _declarations(CSS, ".site-header")
        assert shared["background"] == "var(--chrome)"
        assert _declarations(CSS, ".site-footer")["background"] == "var(--chrome)"

    def test_no_individual_background_overrides(self) -> None:
        """Se `.site-header` ou `.site-footer` tiverem a sua própria regra de
        fundo, a igualdade passa a depender de qual vence. É exactamente aí
        que este requisito se parte — e só na revisão visual ninguém nota."""
        for selector in (".site-header", ".site-footer"):
            body = _own_rule(CSS, selector)
            for prop in ("background", "background-color", "background-image"):
                assert f"{prop}:" not in body, f"{selector} tem um {prop} próprio"

    def test_no_gradient_anywhere(self) -> None:
        """Um gradiente é a forma mais fácil de os fundos divergirem sem
        ninguém se aperceber: os dois lados ficam "quase" iguais."""
        clean = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)
        assert "gradient" not in clean, "há um gradiente no CSS do mailutils"

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_chrome_token_is_defined_in_both_themes(self, theme: str) -> None:
        assert "--chrome" in _theme_tokens(CSS, theme), f"o tema {theme} não define --chrome"

    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_functional_text_colours_meet_contrast(self, theme: str) -> None:
        """As cores funcionais têm dois papéis: `--*-fill` para barras e
        bordas, `--*-text` para texto. Os de texto têm de passar 4.5:1 sobre o
        fundo do próprio tema — o hexe da marca não passa em light mode, e foi
        esse o defeito que o DESIGN.md proibia e o CSS cometia."""
        tokens = _theme_tokens(CSS, theme)
        backgrounds = [tokens["--bg"], tokens["--bg-alt"], tokens["--surface"]]
        for name in ("ok", "warn", "high", "bad", "info"):
            token = f"--{name}-text"
            assert token in tokens, f"falta {token} no tema {theme}"
            for background in backgrounds:
                assert _contrast(tokens[token], background) >= 4.5, (
                    f"{token} ({tokens[token]}) não passa 4.5:1 sobre {background} no tema {theme}"
                )

    def test_every_fill_token_has_a_text_twin(self) -> None:
        """Um par sem o gémeo texto é um par a meio de ser corrigido."""
        for theme in ("light", "dark"):
            tokens = _theme_tokens(CSS, theme)
            for name in ("ok", "warn", "high", "bad", "info"):
                assert f"--{name}-fill" in tokens
                assert f"--{name}-text" in tokens

    def test_border_colour_is_visible_in_both_themes(self) -> None:
        """`#444444` sobre fundo escuro é um campo de formulário que desaparece
        dentro do cartão. O token `--border-strong` é o que desenha o
        contorno dos inputs."""
        for theme in ("light", "dark"):
            tokens = _theme_tokens(CSS, theme)
            for background in (tokens["--bg"], tokens["--surface"]):
                assert _contrast(tokens["--border-strong"], background) >= 1.5, (
                    f"--border-strong não se distingue no tema {theme}"
                )

    @pytest.mark.parametrize("selector", [".site-header", ".site-footer"])
    def test_only_the_header_is_positioned(self, selector: str) -> None:
        """O cabeçalho pode ser `sticky`; o rodapé não. Isso é posição, não
        cor, e não quebra a invariante."""
        body = _own_rule(CSS, selector)
        if "position" in body:
            assert selector == ".site-header", "só o cabeçalho pode ser posicionado"


class TestDarkModeValues:
    def test_dark_chrome_is_dark(self) -> None:
        assert _is_dark(_theme_tokens(CSS, "dark")["--chrome"])

    def test_light_and_dark_differ(self) -> None:
        """Um tema escuro onde nada muda de cor é um tema que não existe."""
        dark = _theme_tokens(CSS, "dark")
        light = _theme_tokens(CSS, "light")
        assert dark["--bg"] != light["--bg"]
        assert dark["--surface"] != light["--surface"]
        assert dark["--text"] != light["--text"]

    def test_no_flash_of_wrong_theme(self) -> None:
        """O tema tem de vir do servidor, escrito no `<html>`. Um script que o
        troca depois do primeiro paint produz um flash branco em darkmode."""
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert 'data-tema="{{ tema }}"' in base
        head = base.split("</head>")[0]
        assert "<script" not in head, "há um script no <head>: o tema chega tarde"


class TestTokens:
    def test_linuxkafe_palette_is_present(self) -> None:
        """A paleta vem do tema LINUXKAFÉ. Se alguém a mudar, tem de mudar em
        `docs/DESIGN.md` primeiro — e este teste avisa."""
        for token in ("#f8b400", "#e0e0e0", "#0056b3"):
            assert token in CSS.lower(), f"falta o token {token} do LINUXKAFÉ"

    def test_brand_green_is_unusable_as_text(self) -> None:
        """Documenta *porque* existem dois papéis para as cores funcionais.

        Se este teste deixar de passar, alguém escureceu o verde da marca e a
        razão de existir `--ok-text` desapareceu. Isso é uma decisão
        consciente, e tem de ser tomada com este teste à vista.
        """
        assert _contrast("#22c55e", "#ffffff") < 4.5
        assert _contrast("#f8b400", "#ffffff") < 4.5

    def test_text_on_yellow_passes_contrast(self) -> None:
        """O botão primário é amarelo com texto escuro. É a acção principal de
        cada ecrã, e é onde a legibilidade não pode falhar."""
        assert _contrast("#212121", "#f8b400") >= 4.5

    def test_text_on_dark_background_passes_contrast(self) -> None:
        assert _contrast("#e8e8e8", "#121212") >= 4.5

    def test_text_on_light_background_passes_contrast(self) -> None:
        assert _contrast("#212121", "#ffffff") >= 4.5


class TestNoForbiddenReferences:
    """Requisito explícito do dono: zero menções à Universidade do Porto, em
    lado nenhum. Este teste varre tudo o que entra no produto."""

    @pytest.mark.parametrize(
        "forbidden", ["up.pt", "u.porto", "universidade", "university of porto"]
    )
    def test_absent_from_shipped_assets(self, forbidden: str) -> None:
        needle = forbidden.lower()
        targets = list(STATIC.rglob("*")) + list(TEMPLATES.rglob("*"))
        targets = [p for p in targets if p.is_file()]
        assert targets, "não encontrei ficheiros para varrer"
        offenders = [
            str(p.relative_to(STATIC.parent))
            for p in targets
            if needle in p.read_text(encoding="utf-8", errors="ignore").lower()
        ]
        assert not offenders, f"menções encontradas: {offenders}"


class TestNoEmojiInCode:
    """`FR-D1` do design system. Emoji em código é sinal de por terminar."""

    EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff]")

    def test_absent_from_stylesheet_and_templates(self) -> None:
        targets = [p for p in list(STATIC.rglob("*")) + list(TEMPLATES.rglob("*")) if p.is_file()]
        offenders = [
            str(p.name)
            for p in targets
            if self.EMOJI.search(p.read_text(encoding="utf-8", errors="ignore"))
        ]
        assert not offenders, f"emoji em {offenders}"


class TestAccessibility:
    def test_reduced_motion_is_respected(self) -> None:
        assert "prefers-reduced-motion" in CSS

    def test_focus_is_visible(self) -> None:
        """Sem contorno de foco, a navegação por teclado é inutilizável — e o
        ficheiro de score anima a cada alteração."""
        assert "focus-visible" in CSS
        assert "outline:" in CSS

    def test_colour_is_never_the_only_signal(self) -> None:
        """Cada nível de score tem também um marcador textual. Um utilizador
        daltónico tem de conseguir ler o mesmo que os outros."""
        from mailutils.signatures import spam

        for score in (5, 30, 55, 80):
            label = spam.label_for_score(score)
            assert label and label != "SEGURO" or score < 20
        editor = (TEMPLATES / "editor.html").read_text(encoding="utf-8")
        for marker in ("[OK]", "[!]", "[!!]", "[X]"):
            assert marker in editor

    def test_visually_hidden_class_exists(self) -> None:
        """Precisa de existir para os `aria-label` em cabeçalhos de tabela onde
        a coluna é só acções."""
        assert ".visually-hidden" in CSS

    def test_skip_link_is_present(self) -> None:
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        assert "Saltar para o conteúdo" in base


def _is_dark(hex_colour: str) -> bool:
    value = hex_colour.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def _relative_luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        raw = int(value[i : i + 2], 16) / 255
        channels.append(raw / 12.92 if raw <= 0.03928 else ((raw + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(foreground: str, background: str) -> float:
    """Rácio de contraste WCAG 2.1. Calculado aqui, e não numa dependência,
    porque a regra tem de ser verificável sem instalar nada."""
    light, dark = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (light + 0.05) / (dark + 0.05)
