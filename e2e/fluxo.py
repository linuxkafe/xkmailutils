"""Conduzir o produto como um utilizador: entrar, escrever, guardar, exportar.

Separado do `conftest.py` por um motivo concreto, não por estilo — ver a
docstring de `conftest.py`.

O segundo factor é lido do **stdout do servidor**, com o backend `console`.
É a única via honesta: a base de dados guarda só o SHA-256 do código
(`service.py:298`), portanto semear um código conhecido seria atalhar por
baixo do `mailer` — e um teste que atalha por baixo deixa de estar a provar
que o utilizador consegue entrar. Se `wait_for_code` deixar de encontrar o
código porque o `print` mudou de sítio, o teste tem de falhar, nunca a ser
ignorado.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from threading import Lock

from playwright.sync_api import Page

#: Senha do administrador do servidor de teste. Vive no repositório de propósito:
#: o problema de ela estar ali nunca é a senha, é um `.env` no git.
PASSWORD_ADMIN = "correcthorsebattery1"
EMAIL_ADMIN = "ana@exemplo.pt"

#: O `user-agent` de um browser Playwright serve de rótulo nos registos de
#: dispositivo do utilizador, e é o que o teste usa para os distinguir.
PREVIEW = "signature-preview"

CODE_TIMEOUT = 15.0

#: O assunto que o `mailer` escreve em modo console: `assunto=123456 é o seu
#: código de acesso` (mailer.py:35). Extrair do assunto e não do corpo, porque
#: o corpo depende de indentação e o assunto não.
CODE_IN_SUBJECT = re.compile(r"assunto=(\d{6})\b")


@dataclass
class Servidor:
    """O servidor E2E, visto do lado do teste."""

    process: subprocess.Popen
    port: int
    prefix: str
    linhas: list[str] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)
    _lidas: int = 0

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.base}{self.prefix}{path}"

    def esquecer_codigos(self) -> None:
        """Marca os códigos já vistos como consumidos.

        Sem isto, um segundo teste que espera pelo OTP pode apanhar o do
        primeiro — e passar com um código morto, que é a pior forma de um
        teste de autenticação estar verde.
        """
        with self._lock:
            self._lidas = len(self.linhas)

    def wait_for_code(self, timeout: float = CODE_TIMEOUT) -> str:
        """Espera pelo próximo código de acesso e devolve-o.

        Levanta `AssertionError` se não chegar. Um E2E de segundo factor que
        passa sem ver o código não testou o segundo factor.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                novas = self.linhas[self._lidas :]
                self._lidas = len(self.linhas)
            for linha in novas:
                achado = CODE_IN_SUBJECT.search(linha)
                if achado:
                    return achado.group(1)
            time.sleep(0.05)
        with self._lock:
            ultimas = "".join(self.linhas[-25:])
        raise AssertionError(
            f"nenhum código de acesso chegou ao stdout do servidor em {timeout}s. "
            f"O backend de email é 'console' e escreve em stdout (mailer.py:135); "
            f"se mudou, este teste tem de mudar também.\n"
            f"--- últimas linhas do servidor ---\n{ultimas}"
        )


def entrar(page: Page, servidor: Servidor, email: str = EMAIL_ADMIN) -> None:
    """Faz o caminho completo de login, incluindo o segundo factor.

    Espera pela página de verificação em vez de assumir que chega. O
    fingerprint de dispositivo é o hash de User-Agent + Accept-Language
    (`web.py:306`), e o `conftest.py` dá a cada teste um `Accept-Language`
    diferente, portanto o 2F tem de acontecer. Se um dia não acontecer, esta
    função falha e denuncia-o — em vez de passar a testar o caminho sem
    segundo factor, que é o caminho que ninguém deitou o olho.
    """
    servidor.esquecer_codigos()
    page.goto(servidor.url("/entrar"))
    page.fill("#email", email)
    page.fill("#password", PASSWORD_ADMIN)
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url(f"**{servidor.prefix}/verificar**", timeout=10_000)

    page.fill("#code", servidor.wait_for_code())
    page.get_by_role("button", name="Verificar").click()
    page.wait_for_url(f"**{servidor.prefix}/assinatura**", timeout=10_000)


def guardar(page: Page, servidor: Servidor, campos: dict[str, str]) -> None:
    """Preenche o editor e carrega em "Guardar assinatura".

    Os campos são seleccionados por `[data-field]`, não por `name=`: cinco dos
    onze campos do editor não têm atributo `name` (`editor.html`) porque é o
    `app.js` que os serializa para o campo oculto `fields`. Um seletor escrito
    a partir do `name` seria um teste que passa por acerto e falha quando
    alguém corrigir o HTML.
    """
    for campo, valor in campos.items():
        page.locator(f'[data-field="{campo}"]').fill(valor)
    page.get_by_role("button", name="Guardar assinatura").click()
    page.get_by_role("status").filter(has_text="Assinatura guardada.").wait_for(timeout=10_000)
