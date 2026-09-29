"""Arranque da aplicação: app factory, ligação à base de dados, media e saúde.

`create_app()` e não um objecto global. Um import de `mailutils.main` não
deve abrir uma base de dados nem correr migrações — é o que permite aos
testes criar dez aplicações em memória sem efeitos de bordo.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db
from .admin.routes import router as admin_router
from .analyzer.routes import router as analyzer_router
from .auth import service
from .auth.routes import router as auth_router
from .signatures.routes import router as signature_router
from .templates import setup_templates
from .web import THEME_COOKIE, _Redirect

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Tipos servidos por `/media`. Lista explícita: servir o que está no disco com
#: um `Content-Type` adivinhado é como um `.html` com `<script>` passa a ser
#: servido como imagem.
#: Sufixos de rotas onde nem o tema nem a sessão interessam. `/media` e
#: `/static` são servidas por handlers que não passam por `get_db`, e `/saude`
#: tem de responder mesmo que a base de dados esteja inacessível.
#:
#: São comparados como *sufixos* e não como caminhos absolutos, porque a
#: aplicação vive sob um prefixo e o caminho que chega ao middleware o inclui.
_CONTEXTLESS_SUFFIXES = ("/media", "/static", "/saude")

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def create_app(settings: config.Settings | None = None) -> FastAPI:
    """Cria a aplicação. Aceita `settings` para os testes; sem ele lê o `.env`."""
    resolved = settings or config.load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # As migrações correm no arranque, não no import: um `python -c "import
        # mailutils"` não deve tocar na base de dados de produção.
        connection = app.state.db_factory()
        try:
            db.migrate(connection)
        finally:
            connection.close()
        boot_connection = app.state.db_factory()
        try:
            app.state.boot_message = service.bootstrap_admin(boot_connection, resolved)
        finally:
            boot_connection.close()
        yield

    prefix = resolved.path_prefix
    app = FastAPI(
        title="mailutils",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.trust_proxy = resolved.trust_proxy
    app.state.db_factory = lambda: db.connect(resolved.db_path)

    setup_templates(app)
    # O prefixo entra nas *rotas*, não em `root_path`.
    #
    # `root_path` serve para `url_for` gerar URLs correctas quando alguém
    # põe a app atrás de um proxy que faz stripping do prefixo. Aqui a app é
    # servida directamente, e o caminho que chega ao router é o caminho
    # completo. Se as duas coisas fossem usadas juntas, o prefixo apareceria
    # duas vezes em cada URL. Por isso: prefixo nas rotas, e zero uso de
    # `url_for` — os templates recebem `raiz` e escrevem-no à mão.
    app.include_router(auth_router, prefix=prefix)
    app.include_router(signature_router, prefix=prefix)
    app.include_router(analyzer_router, prefix=prefix)
    app.include_router(admin_router, prefix=prefix)
    app.mount(f"{prefix}/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            # `frame-src 'self' blob:` — o preview da assinatura é um iframe
            # `sandbox=""` alimentado por uma `blob:` URL, e `default-src 'self'`
            # bloqueava-o. Sem isto o painel de pré-visualização nunca renderiza
            # nada. É a única excepção e é restrita a esta_directiva: o resto
            # continua a ser `self`, e o `sandbox` sem `allow-scripts` nem
            # `allow-same-origin` mantém o conteúdo opaco.
            #
            # `style-src 'self'` fica sem excepções. `style=""` inline é
            # bloqueado de propósito: nenhuma folha de estilo da aplicação
            # depende de estilo inline, e depende-la seria abrir um sink de
            # injecção de CSS exactamente no ficheiro onde o utilizador escreve
            # texto. Os valores dinâmicos que o CSS não consegue expressar
            # (a largura da barra de score) estão em `app.css`, indexados por
            # `data-score`.
            "default-src 'self'; img-src 'self' https: data:; style-src 'self'; "
            "script-src 'self'; form-action 'self'; frame-src 'self' blob:; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        if resolved.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        """Prepara `request.state` para os templates e converte `_Redirect`.

        Três coisas acontecem aqui, todas uma vez só, e nenhuma delas pode
        ficar a cargo de um handler:

        - O tema é lido do cookie e reescrito. A sessão é resolvida em
          `web.get_db`, que corre depois deste middleware.
        - `_Redirect` — a excepção interna das dependências — vira um 303.
        - O cookie do tema é reescrito, para a página seguinte ter um valor
          válido mesmo que o cliente não guarde o primeiro.

        A leitura do tema acontece **antes** de `call_next`, e isso não é
        estilo. O `request.state` é lido pelos templates durante o render
        (`templates.py:121`), que corre dentro de `call_next`: definir o tema
        depois de a resposta estar feita produzia uma página com o tema por
        omissão e um cookie com o tema pedido — e o utilizador nunca via
        diferença nenhuma. Era exactamente o que acontecia.
        """
        cookie_theme = request.cookies.get(THEME_COOKIE, "")
        theme = cookie_theme if cookie_theme in {"dark", "light"} else "dark"
        request.state.theme = theme

        try:
            response = await call_next(request)
        except _Redirect as jump:
            response = RedirectResponse(url=jump.url, status_code=303)

        if not request.url.path.endswith(_CONTEXTLESS_SUFFIXES):
            # Path com o prefixo: com `path="/"`, o cookie de tema seria
            # enviado a todos os serviços do mesmo host, e o `path` do
            # `delete_cookie` teria de bater certo para o logout funcionar.
            response.set_cookie(
                THEME_COOKIE,
                theme,
                max_age=365 * 86400,
                samesite="lax",
                path=f"{prefix}/",
            )
        return response

    @app.get(prefix, include_in_schema=False)
    @app.get(prefix + "/", include_in_schema=False)
    def root() -> RedirectResponse:
        """Raiz do prefixo. `/` (o domínio inteiro) responde 404 de propósito:
        quem chega sem o prefixo não está a usar esta aplicação, e redirecê-lo
        seria assumir o site inteiro."""
        return RedirectResponse(url=f"{prefix}/assinatura", status_code=302)

    @app.get(f"{prefix}/saude", include_in_schema=False)
    def health(request: Request) -> Response:
        """Sonda de saúde.

        Devolve sempre a mesma forma e nunca diz se uma conta existe — esta
        rota é pública e um "utilizador não existe" é enumeração de
        utilizadores de graça. (FR-2.9)
        """
        connection = app.state.db_factory()
        try:
            db.migrate(connection)
            row = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        except sqlite3.Error:
            return Response("indisponivel", status_code=503, media_type="text/plain")
        finally:
            connection.close()
        return Response(
            f"ok {row['n'] if row else 0}",
            status_code=200,
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )

    @app.get(f"{prefix}/media/{{filename}}")
    def media(request: Request, filename: str) -> Response:
        """Serve o logótipo.

        Checks explícitos e um `..` rejeitado: o nome vem do URL, e o único
        nome de ficheiro que existe é `logo-<int>.<ext>`. A resistência à
        travessia de directório é o que impede que `/media/../../.env` devolva
        um segredo. (NFR-7)
        """
        if "/" in filename or "\\" in filename or ".." in filename:
            return Response("não encontrado", status_code=404, media_type="text/plain")
        if not filename.startswith("logo-"):
            return Response("não encontrado", status_code=404, media_type="text/plain")
        suffix = Path(filename).suffix.lower()
        if suffix not in _MEDIA_TYPES:
            return Response("não encontrado", status_code=404, media_type="text/plain")

        target = resolved.media_dir / filename
        if not target.is_file():
            return Response("não encontrado", status_code=404, media_type="text/plain")

        return FileResponse(
            target,
            media_type=_MEDIA_TYPES[suffix],
            headers={
                "X-Content-Type-Options": "nosniff",
                # Um logótipo não muda: um ano de cache é o correcto e evita
                # que o servidor de email re-faca o pedido a cada envio.
                "Cache-Control": "public, max-age=31536000, immutable",
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    @app.exception_handler(500)
    async def server_error(request: Request, exc: Exception) -> Response:
        return Response(
            "erro interno do servidor",
            status_code=500,
            media_type="text/plain; charset=utf-8",
        )

    return app


def main() -> None:  # pragma: no cover - ponto de entrada do `make run`
    import uvicorn

    settings = config.load_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8000)


app = None  # criado explicitamente por `main()`; ver `make run`
