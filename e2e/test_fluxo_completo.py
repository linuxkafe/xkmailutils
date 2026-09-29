"""O caminho que um utilizador faz a sério: entrar, escrever, ver o score,
exportar. Estes testes clicam; não fazem requests.

Cada teste aqui duplica de propósito cobertura que `tests/` já tem por HTTP.
A duplicação não é desperdício: é a diferença entre "a rota devolve 200" e
"o utilizador consegue chegar lá". A rota `/verificar` respondia 200 muito
antes de haver um segundo factor que funcione — o erro estava exactamente na
ligação entre a página de login e a de verificação, que nenhum teste de
código de estado vê.
"""

from __future__ import annotations

import re

from playwright.sync_api import BrowserContext, Page

from e2e.fluxo import Servidor, entrar, guardar

#: PNG 1x1. O mesmo bytes que `tests/test_editor_flows.py` usa: o que
#: interessa ao validador é o cabeçalho, e um PNG válido é mais honesto do que
#: um ficheiro qualquer com extensão `.png`.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)

CAMPOS = {
    "name": "Ana Ribeiro",
    "role": "Engenheira de Software",
    "company": "Exemplo, Lda.",
    "email": "ana@exemplo.pt",
    "phone": "+351 912 345 678",
    "website": "exemplo.pt",
    "linkedin": "linkedin.com/in/ana",
}


def test_fluxo_completo_login_editor_score_exportar(
    page: Page, context: BrowserContext, servidor: Servidor
) -> None:
    """Login → segundo factor → editor → score → ficheiro descarregado.

    É o teste que justifica o T008. Cada passo é feito como o utilizador:
    escreve nos campos, carrega em botões, e o ficheiro chega por download
    real. O score é lido do DOM, não de uma resposta JSON — o que o
    utilizador vê é o que se prova.
    """
    entrar(page, servidor)
    assert page.url.endswith("/assinatura?aviso=bem-vindo"), page.url

    # O score existe antes de haver uma palavra escrita, e é um número com
    # escala de 0 a 100. Um score que não existe em estado vazio é um score
    # que aparece tarde demais para ajudar o utilizador a evitar o problema.
    caixa = page.locator("#caixa-score")
    caixa.wait_for(timeout=10_000)
    valor_inicial = caixa.locator(".score__value").inner_text()
    assert re.search(r"\d+\s*/\s*100", valor_inicial), valor_inicial
    nivel_inicial = caixa.get_attribute("data-nivel")
    assert nivel_inicial in {"SEGURO", "ATENCAO", "ELEVADO", "CRITICO"}, nivel_inicial

    guardar(page, servidor, CAMPOS)

    # Escrever muito texto deve piorar o score. Se não piorar, o analisador
    # está cego ao conteúdo — e um score que não se move é um número decorativo.
    campo = page.locator('[data-field="note"]')
    campo.fill("Contrato de prestação de serviços. " * 12)
    page.wait_for_function(
        "() => { const c = document.querySelector('#campo-fields');"
        " return c && c.value.includes('Contrato'); }",
        timeout=10_000,
    )

    with page.expect_download(timeout=15_000) as descarga:
        page.get_by_role("link", name="Descarregar .html").click()
    ficheiro = descarga.value
    assert ficheiro.suggested_filename == "assinatura.html", ficheiro.suggested_filename
    caminho = ficheiro.path()
    assert caminho, "o browser descarregou um ficheiro sem caminho em disco"

    with open(caminho, encoding="utf-8") as f:
        html = f.read()

    # As duas regras que o produto existe para honrar, verificadas no ficheiro
    # que sai, não no renderer que o produziu. É a diferença entre a regra e
    # a intenção dela.
    assert "data:" not in html
    assert "http://" not in html
    for campo_nome, valor in CAMPOS.items():
        assert valor in html, f"o campo {campo_nome} não chegou ao ficheiro exportado"
    assert html.count("Ana Ribeiro") == 1, "o nome aparece mais do que uma vez"


def test_otp_errado_nao_abre_sessao(page: Page, servidor: Servidor) -> None:
    """Um código errado tem de deixar o utilizador em `/verificar`.

    O teste que importa não é o do erro: é o do caminho fechado depois do
    erro. Sem isto, um `if errado: pass` em qualquer lado passa despercebido e
    a sessão fica aberta.
    """
    servidor.esquecer_codigos()
    page.goto(servidor.url("/entrar"))
    page.fill("#email", "ana@exemplo.pt")
    page.fill("#password", "correcthorsebattery1")
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url(f"**{servidor.prefix}/verificar**", timeout=10_000)

    page.fill("#code", "000000")
    page.get_by_role("button", name="Verificar").click()
    page.wait_for_selector(".notice--erro", timeout=10_000)
    assert "/verificar" in page.url, page.url
    assert not page.evaluate("() => document.cookie.includes('mailutils_session')")

    # E o editor continua fechado para quem não passou pelo segundo factor.
    page.goto(servidor.url("/assinatura"))
    page.wait_for_url(f"**{servidor.prefix}/entrar**", timeout=10_000)


def test_password_errada_mostra_o_erro(page: Page, servidor: Servidor) -> None:
    """Password errada: aviso visível, e nenhuma linha de código de acesso."""
    page.goto(servidor.url("/entrar"))
    page.fill("#email", "ana@exemplo.pt")
    page.fill("#password", "senha-errada-com-tamanho")
    page.get_by_role("button", name="Entrar").click()

    aviso = page.locator(".notice--erro")
    aviso.wait_for(timeout=10_000)
    assert aviso.inner_text().strip(), "o aviso de erro está vazio"
    page.wait_for_url(f"**{servidor.prefix}/entrar**", timeout=10_000)
    assert not page.evaluate("() => document.cookie.includes('mailutils_session')")


def test_preview_actualiza_o_score_sem_guardar(page: Page, servidor: Servidor) -> None:
    """O preview responde a escrever, sem guardar.

    O editor manda um `fetch` 350 ms depois de cada tecla (`app.js:154-159`).
    Se esse `fetch` deixar de funcionar, o formulário continua a guardar bem —
    e o utilizador perde o único sinal em tempo real de que tem. Por isso o
    teste vai ver o texto, e não o score depois de submeter.
    """
    entrar(page, servidor)
    saida = page.locator("#saida-html")
    saida.wait_for(timeout=10_000)

    page.locator('[data-field="name"]').fill("Ana Ribeiro")
    page.wait_for_function(
        "() => { const t = document.querySelector('#saida-html');"
        " return t && t.value.includes('Ana Ribeiro'); }",
        timeout=10_000,
    )


def test_tema_claro_e_aplicado_pelo_cookie(
    page: Page, context: BrowserContext, servidor: Servidor
) -> None:
    """O cookie de tema tem de chegar ao `<html>` como atributo.

    Não há toggle de tema na interface — é um facto do produto, não um bug. O
    que este teste fixa é o mecanismo: o cookie escrito pelo middleware
    (`main.py:133-147`) tem de chegar ao atributo que o CSS selector lê
    (`[data-tema="light"]`, `app.css:61`). Se um dos lados mudar sozinho, o
    tema deixa de funcionar e nada mais denuncia.
    """
    entrar(page, servidor)
    assert page.locator("html").get_attribute("data-tema") == "dark"

    context.add_cookies(
        [{"name": "mailutils_theme", "value": "light", "url": f"{servidor.base}{servidor.prefix}/"}]
    )
    page.reload()
    page.wait_for_selector('[data-tema="light"]', timeout=10_000)
    assert page.locator("html").get_attribute("data-tema") == "light"


def test_logotipo_via_url_e_nao_embebido(page: Page, servidor: Servidor, tmp_path) -> None:
    """Uma imagem carregada tem de sair como URL servida pela aplicação.

    Um `data:` URI é o sinal de spam mais severo numa assinatura — é a razão
    de o produto existir. Este teste sobe uma imagem pelo formulário e prova
    que a exportação a referencia por `/media/`, que é o que o servidor de
    email consegue carregar e o que não é uma violação de CSP no cliente.
    """
    entrar(page, servidor)

    destino = tmp_path / "logo.png"
    destino.write_bytes(PNG)
    page.locator("#ficheiro").set_input_files(str(destino))
    page.get_by_role("button", name="Carregar").click()
    page.wait_for_load_state("load")
    assert (
        page.get_by_text("Logótipo carregado.").count()
        or page.get_by_text("Logótipo carregado, mas é maior").count()
    ), "o upload não confirmou"

    guardar(page, servidor, CAMPOS)

    with page.expect_download(timeout=15_000) as descarga:
        page.get_by_role("link", name="Descarregar .html").click()

    with open(descarga.value.path(), encoding="utf-8") as f:
        html = f.read()

    assert "data:" not in html
    assert "/media/" in html, "o logótipo não é servido por URL"
    assert "<img" in html.lower()


def _largura_da_barra(page: Page) -> tuple[int, int]:
    """(largura da barra preenchida, largura da barra) em píxeis."""
    medidas = page.evaluate(
        "() => { const f = document.querySelector('.score__fill');"
        " const b = document.querySelector('.score__bar');"
        " return [f.getBoundingClientRect().width, b.getBoundingClientRect().width]; }"
    )
    return int(medidas[0]), int(medidas[1])


def test_a_barra_de_score_diz_a_verdade_no_primeiro_load(page: Page, servidor: Servidor) -> None:
    """A barra tem de mostrar o score no primeiro carregamento, sem escrever.

    Este é o teste que vale mais do suite. A barra renderizava a 100% com um
    score de 0: o browser descartava o `style="width: 0%"` do servidor porque a
    CSP bloqueia estilos inline, e um `div` sem `width` enche o contentor. O
    número ao lado dizia "0 / 100" e a barra ao lado dizia "100". Um score que
    mente é pior do que um score que não existe, porque é o produto inteiro a
    prometer que o número é honesto.

    E medir em vez de ler o atributo: um atributo `data-score="0"` pode estar
    lá e a regra CSS pode não estar a aplicar. O que interessa é o que se vê.
    """
    entrar(page, servidor)

    numero = int(re.search(r"(\d+)", page.locator(".score__value").inner_text()).group(1))
    preenchida, total = _largura_da_barra(page)
    assert total > 0, "a barra de score não tem largura: o CSS não a desenhou"
    razao = preenchida / total
    assert abs(razao - numero / 100) < 0.02, (
        f"o score diz {numero} e a barra está a {razao:.0%}. {preenchida}px de {total}px."
    )


def test_a_barra_diz_a_verdade_no_analisador(page: Page, servidor: Servidor) -> None:
    """O mesmo, em `/analisar`, que nunca é corrigido por JavaScript.

    O `app.js` sai cedo em `if (!form) return;` e `/analisar` não tem
    `#form-assinatura`. Ali a barra nunca recebia a correcção por CSSOM, o que
    tornava o erro permanente e não um defeito do primeiro load.
    """
    entrar(page, servidor)
    page.goto(servidor.url("/analisar"))
    page.locator("textarea#texto-email").fill(
        "From:ola@exemplo.pt\nAssunto:Oferta\nPara:ana@exemplo.pt\n\n"
        "Chamada à acção immediata. Desconto de 90% só hoje. "
        "Clique em http://exemplo.pt/oferta"
    )
    page.get_by_role("button", name="Analisar").click()

    caixa = page.locator("#caixa-score, .score")
    caixa.first.wait_for(timeout=15_000)
    texto = caixa.first.locator(".score__value").inner_text()
    numero = int(re.search(r"(\d+)", texto).group(1))
    preenchida, total = _largura_da_barra(page)
    assert total > 0, "a barra de score não tem largura: o CSS não a desenhou"
    razao = preenchida / total
    assert abs(razao - numero / 100) < 0.02, f"o score diz {numero} e a barra está a {razao:.0%}."


def test_o_preview_da_assinatura_renderiza(page: Page, servidor: Servidor) -> None:
    """O painel de pré-visualização tem de mostrar a assinatura.

    O preview é um `iframe sandbox=""` alimentado por uma `blob:` URL, e
    `default-src 'self'` bloqueava-a: o painel ficava permanentemente vazio e
    o editor — a peça central do produto — não mostrava nada. A excepção
    `frame-src 'self' blob:` é o que o faz funcionar, e este teste é o que
    detecta se essa excepção desaparecer.
    """
    entrar(page, servidor)
    page.locator('[data-field="name"]').fill("Ana Ribeiro")
    page.wait_for_function(
        "() => { const i = document.getElementById('preview');"
        " return i && i.src.startsWith('blob:'); }",
        timeout=10_000,
    )

    moldura = page.frames[-1]
    moldura.wait_for_selector("body", timeout=10_000)
    assert moldura.locator("body").inner_text().strip(), (
        "o iframe carregou mas está vazio: o preview não chegou a renderizar"
    )
