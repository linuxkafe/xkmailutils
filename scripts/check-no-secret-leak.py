#!/usr/bin/env python3
"""Gate: nenhum segredo é impresso ou registado.

Porquê um script e não um `grep` no Makefile: a versão com `grep` acusou
falso positivo numa mensagem de erro que *ensina* o operador a gerar um
segredo (`python -c "import secrets; print(...)"`). Um gate que dispara sobre
a própria documentação do gate treina a pessoa a ignorá-lo, e aí deixou de
servir para nada.

A regra é: uma chamada a `print`/`logger`/`logging` cujo argumento nomeie um
segredo. O texto de erro que explica ao operador como gerar uma chave não é uma
chamada, e não conta.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src"

#: Nomes que, quando aparecem num argumento de log, são um segredo.
SECRET_WORDS = re.compile(
    r"\b(otp|otp_code|code_hash|code|senha|password|passwd|secret|token|cookie)\b",
    re.IGNORECASE,
)

#: Chamadas que imprimem ou registam.
CALL = re.compile(r"(?:^|[^\w.])(print|logger\.\w+|logging\.\w+|pprint)\s*\(")


def offending_lines(path: Path) -> list[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    found: list[tuple[int, str]] = []
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not CALL.search(line):
            continue
        # A chamada tem de ser a *primeira* coisa da linha depois de indentação.
        # Uma referência a `print(` a meio de uma string é texto, não código.
        if not CALL.match(stripped) and not re.match(r"\w*\.?(print|logger)\w*\s*\(", stripped):
            continue
        if SECRET_WORDS.search(stripped):
            found.append((number, stripped))
    return found


def main() -> int:
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        for number, text in offending_lines(path):
            offenders.append(f"  {path.relative_to(SOURCE_ROOT)}:{number}: {text}")

    if offenders:
        print("FALHA: segredo impresso ou registado:", file=sys.stderr)
        print("\n".join(offenders), file=sys.stderr)
        return 1

    print("  ok nenhum segredo é impresso ou registado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
