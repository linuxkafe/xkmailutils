"""Servidor real + browser real para o suite E2E (T008).

As fixtures vivem aqui porque é onde o pytest as vai buscar. A lógica de
conduzir o utilizador — entrar, preencher o editor, guardar — vive em
`fluxo.py`, e a razão é concreta: `conftest.py` é um nome reservado pelo
pytest, a suite de `tests/` tem um com o mesmo nome, e dois ficheiros com o
mesmo nome no mesmo `sys.path` fazem o segundo sombrear o primeiro. O erro
que daí sai (`cannot import name 'csrf_from' from 'conftest'`) não tem nada a
ver com o que se está a medir.

O que este suite compra e o que não compra — vale a pena ler antes de
confiar num verde daqui.

**Compra:** que o fluxo login → segundo factor → editor → score → exportação
funciona num browser a sério, com formulários, cookies com `path`, redirects
303, `fetch` de preview, themed CSS aplicado e uma descarga de ficheiro real.
Os testes de `tests/` não provam nada disto: o `SyncASGIClient` não tem
`window.location`, não executa `app.js`, e um `POST` de formulário não
demonstra que o botão certo está ligado ao campo certo.

**Não compra:** compatibilidade com Gmail, Outlook ou Thunderbird. O que se
exporta é o ficheiro; o que o cliente de email faz com ele continua a ser
caixa-preta e está declarado como tal em `docs/VISION.md`.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from itertools import count
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from e2e.fluxo import EMAIL_ADMIN, PASSWORD_ADMIN, Servidor

SERVER = Path(__file__).resolve().parent / "_server.py"
START_TIMEOUT = 30.0

#: Numera os dispositivos fictícios. Ver a fixture `context` para o porque.
DISPOSITIVOS = count(1)


def _free_port() -> int:
    """Porta livre neste instante.

    Reserva-a e solta-a. Há uma janela entre o `close()` e o `bind()` do
    servidor filho; é a técnica padrão, e a alternativa — fixa a 8000, como
    `main()` faz — é pior, porque passa a falhar sempre que alguém tem o
    `make run` aberto.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _esperar_saude(servidor: Servidor) -> None:
    """Espera que a aplicação responda a `/saude`.

    `/saude` é a única rota que não precisa de sessão nem de base de dados em
    condições normais — é o readiness probe correcto.
    """
    deadline = time.monotonic() + START_TIMEOUT
    ultimo_erro: Exception | None = None
    while time.monotonic() < deadline:
        if servidor.process.poll() is not None:
            with servidor._lock:  # noqa: SLF001
                saida = "".join(servidor.linhas)
            raise AssertionError(
                f"o servidor E2E morreu com código {servidor.process.returncode}.\n{saida}"
            )
        try:
            # `S310` audita o esquema do URL, e aqui o esquema não é uma
            # entrada: `base` é sempre `http://127.0.0.1` e a porta sai de um
            # `bind` efémero. Não há `file:` nem esquema customizado a auditar.
            with urllib.request.urlopen(  # noqa: S310
                f"{servidor.base}{servidor.prefix}/saude", timeout=1
            ) as resposta:
                if resposta.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            ultimo_erro = exc
        time.sleep(0.1)
    raise AssertionError(
        f"o servidor E2E não respondeu a /saude em {START_TIMEOUT}s: {ultimo_erro}"
    )


@pytest.fixture(scope="session")
def servidor(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Servidor]:
    """Um servidor real, com a base de dados e os logótipos num temporário.

    Âmbito de sessão: arrancar o uvicorn e esperar pelo `/saude` custa alguns
    segundos, e repeti-lo por teste não compra cobertura — só compra flake. O
    isolamento vem de a base de dados ser efémera, não de haver um servidor por
    teste.
    """
    raiz = tmp_path_factory.mktemp("mailutils-e2e")
    porta = _free_port()
    prefixo = "/xkmailutils"

    ambiente = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src"),
        # O `print` do mailer vai para um pipe, e stdout canalizado é
        # block-buffered. Sem isto o código fica no buffer e o teste passa a
        # depender de um flush acidental.
        "PYTHONUNBUFFERED": "1",
        "MAILUTILS_ENV": "development",
        "MAILUTILS_DB_PATH": str(raiz / "e2e.db"),
        "MAILUTILS_MEDIA_DIR": str(raiz / "media"),
        "MAILUTILS_ADMIN_EMAIL": EMAIL_ADMIN,
        "MAILUTILS_ADMIN_PASSWORD": PASSWORD_ADMIN,
        "MAILUTILS_MAIL_BACKEND": "console",
        # 60 s de cooldown fariam o segundo login do suite não emitir código
        # nenhum, e o teste falharia por um relógio, não por um bug.
        "MAILUTILS_OTP_COOLDOWN_SECONDS": "0",
        "MAILUTILS_PUBLIC_BASE_URL": f"http://127.0.0.1:{porta}",
        "MAILUTILS_PATH_PREFIX": prefixo,
    }

    processo = subprocess.Popen(  # noqa: S603
        [sys.executable, str(SERVER), str(porta)],
        env=ambiente,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    inst = Servidor(process=processo, port=porta, prefix=prefixo)

    def _ler() -> None:
        assert processo.stdout is not None
        for linha in processo.stdout:
            with inst._lock:  # noqa: SLF001
                inst.linhas.append(linha)

    threading.Thread(target=_ler, daemon=True).start()

    _esperar_saude(inst)
    try:
        yield inst
    finally:
        processo.terminate()
        try:
            processo.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - só num travado
            processo.kill()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    """Chromium headless, um por sessão de testes.

    Sem `pytest-playwright`: essa biblioteca traria a sua própria fixture de
    `page` e uma dependência a mais, e o que este suite precisa de controlar
    é o servidor, não o browser.
    """
    with sync_playwright() as p:
        navegador = p.chromium.launch()
        try:
            yield navegador
        finally:
            navegador.close()


@pytest.fixture
def context(browser: Browser) -> Iterator[BrowserContext]:
    """Um contexto novo por teste: cookies limpos, cache limpo, zero estado partilhado.

    O `Accept-Language` é único por teste, e não por acaso. O fingerprint de
    dispositivo é o SHA-256 de User-Agent + Accept-Language (`web.py:306`), e o
    browser é partilhado por todos os testes — sem esta variação, o primeiro
    teste regista o dispositivo e todos os seguintes entram sem segundo factor.
    O suite passaria inteiro a provar o caminho errado, sem uma única falha.
    Dar a cada teste um dispositivo diferente é o que torna cada teste um
    dispositivo novo, que é o caso que o segundo factor existe para cobrir.
    """
    ctx = browser.new_context(
        accept_downloads=True,
        extra_http_headers={"Accept-Language": f"pt-PT,pt;q=0.9,e2e-{next(DISPOSITIVOS)}"},
    )
    try:
        yield ctx
    finally:
        ctx.close()


@pytest.fixture
def page(context: BrowserContext) -> Iterator[Page]:
    pg = context.new_page()
    try:
        yield pg
    finally:
        pg.close()
