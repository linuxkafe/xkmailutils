"""Arranca a aplicação num processo próprio, para os testes E2E.

Porque um processo e não `create_app()` dentro do pytest: o objectivo do T008
é clicar a sério. O `SyncASGIClient` de `tests/asgi_client.py` e um cliente
in-process — não abre porta, não tem window.location, não executa
`fetch`, não descarrega ficheiros. Um browser só fala com HTTP real.

Porque `uvicorn.run()` e não `mailutils.main.main()`: `main()` fixa a porta
8000 (main.py:231) e isso obriga o suite E2E a depender de a porta estar
livre. Aqui escolhe-se uma porta efémera. `main()` acrescenta ao produto
apenas o `mkdir` dos directórios — o lifespan, as migrações e o
`bootstrap_admin` são os mesmos porque vêm do `create_app()`.
"""

from __future__ import annotations

import sys

from mailutils.config import load_settings
from mailutils.main import create_app


def serve(port: int) -> None:
    """Corre a aplicação em `port` até o processo ser terminado.

    O ambiente — base de dados, directório de media, administrador inicial,
    cooldown de OTP — chega por variáveis de ambiente e é montado pela
    fixture `servidor` em `conftest.py`. Nada é semeado por código: o
    primeiro utilizador tem de nascer pelo mesmo caminho que nasce em
    produção, senão o E2E testa um caminho que ninguém usa.
    """
    import uvicorn

    settings = load_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    serve(int(sys.argv[1]))
