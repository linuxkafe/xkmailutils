#!/usr/bin/env python3
"""Gate: o browser de que o suite E2E precisa está de facto instalado.

Porquê um script e não um `playwright install` dentro do Makefile: o
comando de instalação descarrega ~150 MB e o `make check` tem de correr em
máquinas onde isso não foi feito. Baixar o browser a meio de um gate é
transformar uma verificação numa operação de rede, e o que se quer é a
verificação.

Porquê FALHAR e não avisar: o T008 existe porque os fluxos estavam testados
por HTTP e não por clique. Um `e2e-check` que passa sem browser está a dizer
que o fluxo de login está verificado num browser onde não correu browser
nenhum. É a mesma mentira que o `format-check` contava antes de ser corrigido.

A mensagem de erro diz o comando exacto de reparação. Um gate que falha sem
dizer como se arruma é um gate que se contorna.
"""

from __future__ import annotations

import sys

REPAIR = "python3 -m pip install -e '.[dev]' && python3 -m playwright install chromium"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("FALHA: o pacote `playwright` não está instalado.", file=sys.stderr)
        print(f"  para instalar: {REPAIR}", file=sys.stderr)
        return 1

    try:
        with sync_playwright() as p:
            executable = p.chromium.executable_path
            navegador = p.chromium.launch()
            # Fechar dentro do `with`: em cima do loop de eventos já parado,
            # o `close()` levanta e o gate reportava uma falha que não é
            # falta de browser.
            navegador.close()
    except Exception as exc:
        print("FALHA: o Chromium do Playwright não arrancou.", file=sys.stderr)
        print(f"  motivo: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"  para instalar: {REPAIR}", file=sys.stderr)
        return 1
    print(f"  ok chromium disponível em {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
