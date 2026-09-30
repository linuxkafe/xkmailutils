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

from e2e.fluxo import Servidor, entrar, guardar, limpar

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

    # O cartão de score existe antes de haver uma palavra escrita, e diz que
    # ainda não há nada para medir. Não diz «0 / 100 SEGURO»: um formulário
    # em branco não é uma assinatura segura, é nenhuma assinatura, e um selo
    # verde aqui treina o utilizador a ignorar o selo. (F-12)
    caixa = page.locator("#caixa-score")
    caixa.wait_for(timeout=10_000)
    assert caixa.get_attribute("data-nivel") == "VAZIO", caixa.get_attribute("data-nivel")
    assert "POR PREENCHER" in caixa.inner_text()
    assert not re.search(r"\d+\s*/\s*100", caixa.locator(".score__value").inner_text())

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


def test_o_score_actualiza_enquanto_se_escreve(page: Page, servidor: Servidor) -> None:
    """O `renderScore` do `app.js` tem de mudar o número E a barra.

    O teste anterior afirmava apenas sobre a textarea de HTML — nunca sobre o
    score, apesar de a docstring falar do score. Uma revisão mutou
    `app.js:88` para `if (scoreBox) return;`, que deixa o `renderScore`
    inoperante, e os 9 E2E e os 499 testes por HTTP continuaram verdes. (F-05)

    **E o score tem de ser um que se mexa.** A primeira versão deste teste
    escrevia só o nome, que pontua 0 — e portanto a mutação de congelar a barra
    em zero passava, porque 0 era o valor certo. Era o mesmo erro do F-07 numa
    forma diferente, e a mutação M-04 apanhou-o. Aqui o score sobe a 20, e os
    três sintomas são afirmados de uma vez: o número, o nível, e o atributo
    que a barra CSS lê.
    """
    entrar(page, servidor)
    limpar(page, servidor)
    score = page.locator("#caixa-score")

    # Estado de partida: vazio, sem score para mostrar. Garantido por `limpar`,
    # não por sorte — a base de dados é partilhada por toda a sessão.
    assert score.get_attribute("data-nivel") == "VAZIO", (
        f"o editor não começou no estado neutro, ficou em {score.get_attribute('data-nivel')!r}"
    )

    page.locator('[data-field="note"]').fill(
        "Documentos em http://exemplo1.pt e http://exemplo2.pt"
    )
    page.wait_for_function(
        "() => { const s = document.querySelector('#caixa-score .score__value');"
        " return s && parseInt(s.textContent, 10) >= 15; }",
        timeout=10_000,
    )

    numero = int(re.search(r"(\d+)", score.locator(".score__value").inner_text()).group(1))
    assert numero >= 15, f"o score ficou em {numero}: o teste não prova que ele muda"

    atributo = score.locator(".score__fill").get_attribute("data-score")
    assert atributo == str(numero), (
        f"a barra diz data-score={atributo!r} e o número diz {numero}: congelar a "
        f"barra em zero passaria este teste, e foi o que aconteceu"
    )

    preenchida, total = _largura_da_barra(page)
    assert total > 0
    assert abs(preenchida / total - int(atributo) / 100) < 0.02, (
        f"a barra está a {preenchida}/{total}px para um score de {atributo}"
    )


def test_a_barra_e_discriminante_com_um_score_nao_zero(page: Page, servidor: Servidor) -> None:
    """A barra tem de ser medida com um score que não seja zero.

    A assinatura vazia e a preenchida pontuam ambas 0, e `width: 0` na regra
    base produz exactamente o mesmo pixel que `[data-score="0"] { width: 0% }`.
    Um teste que só mede o caso do zero passa com a barra **toda** apagada —
    uma revisão verificou que apagar as 101 regras deixava o teste verde. (F-07)

    Aqui o score é empurrado deliberadamente acima de 15% para que remover
    qualquer regra da tabela, ou remover o `width: 0` da base, faça falhar.
    """
    entrar(page, servidor)
    limpar(page, servidor)
    # URLs escritas pelo utilizador na nota. `_normalise_url` só normaliza os
    # campos de ligação; texto livre vai tal e qual, e `INSECURE_URL` dispara a
    # 20 pontos. Escolhi uma regra de conteúdo e não `data:` ou `<script>`,
    # porque aquelas fariam o score ir a CRÍTICO e a exportação a ficar
    # bloqueada — o teste passaria, mas por um caminho que não é o do editor.
    page.locator('[data-field="note"]').fill(
        "Documentos em http://exemplo1.pt e http://exemplo2.pt"
    )
    page.wait_for_function(
        "() => { const s = document.querySelector('#caixa-score .score__value');"
        " return s && parseInt(s.textContent, 10) >= 15; }",
        timeout=10_000,
    )

    numero = int(re.search(r"(\d+)", page.locator(".score__value").inner_text()).group(1))
    assert numero >= 15, f"o score não chegou a 15 (ficou em {numero}): o teste não prova nada"

    preenchida, total = _largura_da_barra(page)
    assert total > 0
    razao = preenchida / total
    assert razao > 0.10, (
        f"o score diz {numero} e a barra está a {razao:.0%} — a barra não está a "
        f"responder. Apagar a tabela de `data-score` faria este teste passar."
    )
    assert abs(razao - numero / 100) < 0.02, (
        f"{preenchida}px de {total}px para um score de {numero}"
    )


def test_tema_claro_por_clique(page: Page, context: BrowserContext, servidor: Servidor) -> None:
    """O tema muda-se com um clique, sem fabricar o cookie.

    Era o F-01, um BLOCKER: o mecanismo estava certo e testado — o cookie era
    lido pelo middleware e reescrito em cada resposta — mas nada na interface
    o escrevia. A única forma de ter a aplicação em claro era injectar o
    cookie por código, e o teste anterior fazia exactamente isso, pelo que
    media um caminho que nenhum humano executa.

    Este teste clica. É a diferença entre a feature existir e a feature ser
    alcançável, e só ele prova que é a segunda.
    """
    entrar(page, servidor)
    assert page.locator("html").get_attribute("data-tema") == "dark"

    page.get_by_role("button", name="Claro").first.click()

    page.wait_for_selector('html[data-tema="light"]', timeout=10_000)
    assert page.locator("html").get_attribute("data-tema") == "light"

    # O cookie tem de ter mudado, e não só o atributo: a recarga é o que
    # prova que a escolha sobreviveu ao servidor em vez de ser um efeito
    # passageiro do DOM.
    valor = next(c["value"] for c in context.cookies() if c["name"] == "mailutils_theme")
    assert valor == "light", (
        f"o cookie ficou em {valor!r} e o HTML em light — ou a resposta é inconsistente"
    )
    page.reload()
    page.wait_for_selector('html[data-tema="light"]', timeout=10_000)

    # E voltar ao escuro também.
    page.get_by_role("button", name="Escuro").first.click()
    page.wait_for_selector('html[data-tema="dark"]', timeout=10_000)


def test_tema_antes_de_entrar(page: Page, servidor: Servidor) -> None:
    """O botão também existe na página de login, onde não há sessão.

    O interruptor está no cabeçalho de todas as páginas, e a página de login é
    justamente onde uma pessoa escolhe se quer a aplicação clara ou escura
    antes de ter conta. Foi por isso que o token de CSRF do tema é de
    pré-sessão: sem ele, o botão teria de desaparecer aí.
    """
    page.goto(servidor.url("/entrar"))
    page.get_by_role("button", name="Claro").first.click()
    page.wait_for_selector('html[data-tema="light"]', timeout=10_000)
    assert "entrar" in page.url, "a página de login não devia sair ao mudar o tema"


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
    """(largura da barra preenchida, largura da barra) em píxeis, em repouso.

    `.score__fill` tem `transition: width 250ms`, pelo que medir durante a
    animação dá um valor a meio. Duas tentativas erraram neste teste:

    1. Ler uma vez, sem esperar. Deu 6% para um score de 20.
    2. Esperar no browser comparando a largura com a do frame anterior, que é
       a mesma medição 16 ms depois — ainda dentro da transição.

    A versão que funciona são duas leituras separadas por uma pausa maior do
    que a transição, aceites só quando coincidem.
    """
    medir = (
        "() => { const f = document.querySelector('.score__fill');"
        " const b = document.querySelector('.score__bar');"
        " return [f.getBoundingClientRect().width, b.getBoundingClientRect().width]; }"
    )
    ultima = page.evaluate(medir)
    for _ in range(10):
        page.wait_for_timeout(120)
        actual = page.evaluate(medir)
        if abs(actual[0] - ultima[0]) < 0.5:
            return int(actual[0]), int(actual[1])
        ultima = actual
    return int(ultima[0]), int(ultima[1])


def test_a_barra_de_score_diz_a_verdade_no_primeiro_load(page: Page, servidor: Servidor) -> None:
    """A barra tem de mostrar o score no primeiro carregamento, sem escrever.

    Este é o teste que vale mais do suite. A barra renderizava a 100% com um
    score de 0: o browser descartava o `style="width: 0%"` do servidor porque a
    CSP bloqueia estilos inline, e um `div` sem `width` enche o contentor. O
    número ao lado dizia "0 / 100" e a barra ao lado dizia "100". Um score que
    mente é pior do que um score que não existe, porque é o produto inteiro a
    prometer que o número é honesto.

    E medir em vez de ler o atributo: um `data-score="0"` pode estar lá e a
    regra CSS pode não estar a aplicar. O que interessa é o que se vê.
    """
    entrar(page, servidor)
    page.locator('[data-field="name"]').fill("Ana Ribeiro")
    page.wait_for_function(
        "() => { const s = document.querySelector('#caixa-score');"
        " return s && s.getAttribute('data-nivel') !== 'VAZIO'; }",
        timeout=10_000,
    )
    score = page.locator("#caixa-score")
    numero = int(re.search(r"(\d+)", score.locator(".score__value").inner_text()).group(1))
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
        "Chamada à acção imediata. Desconto de 90% só hoje. "
        "Clique em http://exemplo.pt/oferta"
    )
    page.get_by_role("button", name="Analisar").click()

    score = page.locator(".score").first
    score.wait_for(timeout=15_000)
    numero = int(re.search(r"(\d+)", score.locator(".score__value").inner_text()).group(1))
    preenchida, total = _largura_da_barra(page)
    assert total > 0, "a barra de score não tem largura: o CSS não a desenhou"
    razao = preenchida / total
    assert abs(razao - numero / 100) < 0.02, f"o score diz {numero} e a barra está a {razao:.0%}."


def test_o_preview_mostra_a_assinatura_como_ela_sai(page: Page, servidor: Servidor) -> None:
    """O preview tem de mostrar a assinatura: com estilos e com acentos.

    Este teste já falhou uma vez, e a forma como falhou é o ponto. A primeira
    versão afirmava apenas que o texto dentro do `iframe` não estava vazio, e
    passou com o preview a mostrar **Times New Roman a preto, sem uma cor
    sequer, e com os acentos como `TÃ©cnica`**. Um teste que passa sem o que
    queria verificar é pior do que nenhum teste: dá confiança onde não há.

    Agora afirma as três coisas que o utilizador vê: a família de letra, a cor,
    e os acentos. E compara com o HTML exportado, que é a mesma assinatura — a
    preview e a entrega não podem divergir, que é o que o `FR-3.8` pede.
    (F-04)
    """
    entrar(page, servidor)
    page.locator('[data-field="name"]').fill("Ana")
    page.locator('[data-field="role"]').fill("Técnica de TI")
    page.wait_for_function(
        "() => { const i = document.getElementById('preview');"
        " return i && i.src.includes('preview-documento'); }",
        timeout=10_000,
    )
    # O `iframe` recarrega a cada alteração; esperar que assente.
    page.wait_for_timeout(800)

    moldura = page.frames[-1]
    moldura.wait_for_selector("body", timeout=10_000)

    # 1) A família de letra, lida do HTML exportado e comparada com a calculada.
    exportado = page.locator("#saida-html").input_value()
    fonte = re.search(r"font-family:([^;\"]+)", exportado)
    assert fonte, f"o HTML exportado não declara font-family: {exportado[:200]}"
    esperada = fonte.group(1).split(",")[0].strip("\"'")
    calculada = moldura.evaluate(
        "() => getComputedStyle(document.body.firstElementChild).fontFamily"
    )
    assert esperada in calculada, (
        f"o preview mostra {calculada!r} e a assinatura declara {esperada!r}: "
        f"o preview não está a mostrar a assinatura"
    )

    # 2) A cor. A assinatura é `color:`, e o preview tem de a aplicar.
    cor = re.search(r"color:(#[0-9a-fA-F]{3,6})", exportado)
    assert cor, f"o HTML exportado não declara cor: {exportado[:200]}"
    aplicada = moldura.evaluate(
        "() => { const els = document.querySelectorAll('span');"
        " for (const e of els) { const c = getComputedStyle(e).color;"
        " if (c) return c; } return ''; }"
    )
    esperada_cor = _rgb(cor.group(1))
    assert aplicada == esperada_cor, (
        f"o preview mostra {aplicada} e a assinatura declara {cor.group(1)} ({esperada_cor})"
    )

    # 3) Os acentos. Um `Blob` sem charset dava `TÃ©cnica`; a rota tem
    #    `<meta charset="utf-8">` e o header diz `text/html; charset=utf-8`.
    texto = moldura.locator("body").inner_text()
    assert "Técnica de TI" in texto, f"os acentos saíram partidos: {texto!r}"
    assert "T\u00c3" not in texto


def _rgb(hexadecimal: str) -> str:
    """`#rrggbb` na forma que `getComputedStyle` devolve."""
    h = hexadecimal.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"
