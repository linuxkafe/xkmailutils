"""Cliente ASGI mínimo para os testes.

**Porquê não `starlette.testclient.TestClient`:** a starlette 0.31.1 instalada
passa `app=` ao constructor do `httpx.Client`, e o httpx 0.28 removeu esse
parâmetro. Rebaixar o httpx do sistema para resolver um problema de testes
seria um efeito colateral no computador do utilizador por causa de um bug nosso.
Este cliente faz o mesmo trabalho com a `ASGITransport` que já está
instalada, em dependências do projecto: zero.

Duas capacidades que o `TestClient` dá e que este reproduz:

1. **Lifespan.** A `ASGITransport` do httpx não corre o lifespan, e é no
   lifespan que o mailutils aplica migrações e cria o primeiro admin. Sem isto
   os testes não teriam tabelas.
2. **Persistência de cookies entre pedidos**, porque é assim que uma sessão
   atravessa o fluxo de login.

A API é a do `TestClient` (`.get`, `.post`, `.cookies`) para que os testes se
leiam como se usassem a stdlib.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from typing import Any

import httpx


class SyncASGIClient:
    """Cliente HTTP síncrono sobre uma aplicação ASGI.

    Corre a aplicação num event loop dedicado, numa thread, e despacha cada
    pedido para lá com `run_coroutine_threadsafe`. É o que permite manter um
    único `AsyncClient` — e portanto um único `Cookies` — vivo durante toda a
    sessão de testes.
    """

    def __init__(self, app: Any, base_url: str = "http://testserver") -> None:
        self.app = app
        self.base_url = base_url
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._client = self._submit(
            lambda: httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=base_url,
                follow_redirects=True,
            )
        )
        self._lifespan_ctx = app.router.lifespan_context(app)
        self._lifespan_cm = self._submit(self._lifespan_ctx.__aenter__)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, factory):
        """Corre uma corrotina/fábrica no loop da aplicação e devolve o
        resultado ao thread de teste, bloqueando."""

        async def _call() -> Any:
            result = factory()
            if asyncio.iscoroutine(result):
                return await result
            return result

        return asyncio.run_coroutine_threadsafe(_call(), self._loop).result()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        kwargs.setdefault("follow_redirects", True)
        return self._submit(lambda: self._client.request(method, url, **kwargs))

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def cookies(self) -> httpx.Cookies:
        return self._client.cookies

    def cookie_jar(self):
        """O `Cookies` subjacente. `jar` deixou de ser público no httpx 0.28,
        e um teste que precisa de ver o `path` de um cookie não tem outro
        caminho."""
        return self._client.cookies

    def get_cookie(self, name: str) -> str:
        """Valor de um cookie do jar. O `jar` do httpx deixou de ser público na
        0.28, daí o método em vez de acesso directo nos testes."""
        return self._client.cookies.get(name, default="") or ""

    def set_cookie(self, name: str, value: str) -> None:
        """Coloca um cookie como se o servidor o tivesse feito.

        O domínio vem do `base_url` do cliente, nunca de uma constante. Com o
        `base_url` a ser o endereço real da aplicação, um domínio fixo faria o
        cookie ser enviado para um sítio onde a aplicação não está — e o
        teste passaria a medir o cookie, não a aplicação.

        Usado pelos testes que autenticam por atalho (registo directo do
        dispositivo) em vez de percorrer o fluxo de OTP. É um atalho de
        *setup*, não de asserção: o fluxo de OTP tem os seus próprios testes.
        """
        self._client.cookies.set(name, value, domain=self._client.base_url.host)

    def clear_cookies(self) -> None:
        """Esquece a sessão, simulando um browser novo."""
        self._client.cookies.clear()

    def close(self) -> None:
        try:
            self._submit(lambda: self._lifespan_ctx.__aexit__(None, None, None))
            self._submit(self._client.aclose)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            self._loop.close()

    def __enter__(self) -> SyncASGIClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def live_client(app: Any) -> Iterator[SyncASGIClient]:
    """`with live_client(app) as client:` — entra e sai do lifespan."""
    client = SyncASGIClient(app)
    try:
        yield client
    finally:
        client.close()
