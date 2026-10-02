#!/usr/bin/env python3
"""Corre cada mutação e escreve `docs/MUTATIONS.md` a partir da saída real.

Isto é o gate do F-10. A "prova por mutação" da primeira ronda foi feita à
mão, uma vez, e deixou rasto zero. Duas personas da revisão Runsnieram, por
sorte, que três dos testes **não** detectam as mutações que alegam detectar — e
a garantia escrita no ticket e no docstring era verdadeira no momento em que a
escrevi e falsa como prova.

A regra aqui é simples e é a razão de este script existir: **ninguém escreve o
resultado à mão.** O ficheiro é gerado, e o que lá está é a saída que o comando
deu. Se alguém mudar um teste e a mutação deixar de morrer, o `make check` fica
vermelho.

Cada mutação é uma edição de uma linha seguida da reversão. Uma mutação que
não faz o gate ficar vermelho é um teste que não prova o que diz provar.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

RAIZ = pathlib.Path(__file__).resolve().parent.parent
#: A prova vive em `docs/`, e não em `aes/`: `NFR-16` cita-a, e um clone
#: tem de poder confirmar que cada mutação morre. O andaço de processo
#: (`aes/`) não está no repositório.
SAIDA = RAIZ / "docs" / "MUTATIONS.md"

@dataclass(frozen=True)
class Mutacao:
    """Uma alteração que, se passar, o gate tem de dizer que é mentira."""

    identificador: str
    ficheiro: str
    antes: str
    depois: str
    comando: tuple[str, ...]
    porque: str
    tickets: tuple[str, ...]

MUTACOES: tuple[Mutacao, ...] = (
    Mutacao(
        "M-01",
        "src/mailutils/signatures/renderer.py",
        "        opaco = _precisa_de_fundo(self.theme)",
        "        opaco = False  # MUTACAO M-01",
        ("python3", "-m", "pytest", "tests/test_renderer.py", "-q", "--no-cov", "-k", "Legivel"),
        "O tema escuro volta a não levar o fundo: texto #f0f0f0 sobre o branco do "
        "cliente dá 1.14:1 e a assinatura fica invisível. Era o F-02.",
        ("F-02",),
    ),
    Mutacao(
        "M-02",
        "src/mailutils/signatures/renderer.py",
'            f\'{cor_tabela} style="{estilo}">\'',
        '            \'style="{estilo}">\'',
        ("python3", "-m", "pytest", "tests/test_renderer.py", "-q", "--no-cov", "-k", "Legivel"),
        "Fica o `background` no `<div>` mas sai o `bgcolor` do `<table>`. O Word "
        "engine do Outlook ignora `background` num div, pelo que a assinatura "
        "continua invisível no Outlook — que é onde a maior parte das pessoas a vê.",
        ("F-02",),
    ),
    Mutacao(
        "M-03",
        "src/mailutils/main.py",
        "if not ja_posto and not request.url.path.endswith(_CONTEXTLESS_SUFFIXES):",
        "if not request.url.path.endswith(_CONTEXTLESS_SUFFIXES):",
        ("python3", "-m", "pytest", "e2e", "-q", "--no-cov", "-k", "tema"),
        "O middleware volta a sobrescrever o cookie de tema que a rota punha, e o "
        "botão de tema deixa de funcionar. Duas respostas com `Set-Cookie` para o "
        "mesmo nome, e a última ganha.",
        ("F-01",),
    ),
    Mutacao(
        "M-04",
        "src/mailutils/static/app.js",
        'if (fill) fill.setAttribute("data-score", vazio ? 0 : score.score);',
        'if (fill) fill.setAttribute("data-score", 0);',
        ("python3", "-m", "pytest", "e2e", "-q", "--no-cov", "-k", "score_actualiza"),
        "O caminho de actualização do score em JavaScript passa a ser inoperante "
        "para o número, e o teste passa a provar que o score não muda. Era o F-05, e "
        "era o caminho que uma revisão encontrou morto com a suite toda verde.",
        ("F-05",),
    ),
    Mutacao(
        "M-05",
        "src/mailutils/analyzer/scoring.py",
        "score = max(0, min(100, total))",
        "score = max(0, min(100, total)) * 1.0",
        ("python3", "-m", "pytest", "tests/test_spam.py", "-q", "--no-cov"),
        "O score passa a fraccionário. A barra de score é uma regra de CSS por "
        'valor, e `data-score="20.0"` não casa com nenhuma das 101. A revisão '
        "provou que a suíte HTTP inteira passava com isto. Era o F-06, e era a "
        "garantia que eu escrevi e que não existia.",
        ("F-06",),
    ),
    Mutacao(
        "M-06",
        "src/mailutils/static/app.css",
        '.score__fill[data-score="20"] { width: 20%; }',
        "",
        ("python3", "-m", "pytest", "e2e", "-q", "--no-cov", "-k", "discriminante"),
        "Uma das 101 regras da barra desaparece. O teste que media o score zero "
        "continuava verde, porque `width: 0` na regra base dá o mesmo pixel. Era o "
        "F-07, e é porque o teste discriminante mede um score de 20.",
        ("F-07",),
    ),
    Mutacao(
        "M-07",
        "src/mailutils/signatures/routes.py",
        "\" style-src 'unsafe-inline'; img-src https: http:; base-uri 'none';\"",
        "\" img-src https: http:; base-uri 'none';\"",
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_editor_flows.py",
            "-q",
            "--no-cov",
            "-k",
            "Exportado",
        ),
        "A CSP do documento exportado volta a `default-src 'none'` sem `style-src`, e "
        "o ficheiro que o utilizador descarrega para conferir deixa de se mostrar. "
        "Era o F-08.",
        ("F-08",),
    ),
    Mutacao(
        "M-08",
        "src/mailutils/signatures/spam.py",
        '"vazio": not visible_text and not srcs and not hrefs,',
        '"vazio": False,',
        ("python3", "-m", "pytest", "tests/test_editor_flows.py", "-q", "--no-cov", "-k", "vazia"),
        "O editor volta a mostrar «0 / 100 SEGURO» com selo verde para um formulário "
        "em branco. Era o F-12, e o teste que o apanhava afirmava o contrário — a "
        "inversão ficou escrita no docstring dele.",
        ("F-12",),
    ),
    Mutacao(
        "M-09",
        "src/mailutils/static/app.css",
        ".hidden { display: none; }",
        ".hidden { display: none; }\n\n.orfão-m09 { color: red; }",
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_browser_regressions.py",
            "-q",
            "--no-cov",
            "-k",
            "Orfao",
        ),
        "Um selector novo que nada referencia. O teste passa a apanhar selectores "
        "mortos, que era o F-11.",
        ("F-11",),
    ),
    Mutacao(
        "M-10",
        ".github/workflows/ci.yml",
        "      - name: Install browser\n"
        "        run: python3 -m playwright install --with-deps chromium",
        "      - name: Install browser\n        run: true  # MUTACAO M-10",
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_browser_regressions.py",
            "-q",
            "--no-cov",
            "-k",
            "ci",
        ),
        "A CI volta a não instalar o browser, e `make check` fica vermelho no "
        "primeiro run. Era o F-03.",
        ("F-03",),
    ),
    Mutacao(
        "M-11",
        "src/mailutils/signatures/routes.py",
        '"Content-Security-Policy": CSP_PREVIEW,',
        '# MUTACAO M-11: um <meta> no documento nao chega',
        ("python3", "-m", "pytest", "e2e", "-q", "--no-cov", "-k", "preview"),
        "A rota deixa de pôr a CSP no header e o preview volta a herdar a "
        "`style-src 'self'` da aplicação: a assinatura aparece em Times New Roman, a "
        "preto. Um `<meta http-equiv>` no documento não chega, porque as políticas "
        "juntam-se e a mais restritiva ganha. Era o F-04, e a primeira versão da "
        "minha correcção cometia exactamente este erro.",
        ("F-04",),
    ),
    Mutacao(
        "M-12",
        "src/mailutils/templates/_tema.html",
        'value="{{ sessao.csrf_token if sessao else csrf }}">',
        'value="token-falso" data-mutacao="M-12">',
        ("python3", "-m", "pytest", "e2e", "-q", "--no-cov", "-k", "tema"),
        "O botão de tema passa a mandar um token de CSRF falso. Sem token válido o "
        "POST é recusado, e um utilizador fica com um botão que não faz nada — que "
        "era o sintoma exacto do F-01, em que nada no ecrã escrevia o cookie.",
        ("F-01",),
    ),
    Mutacao(
        "M-13",
        "src/mailutils/mailer.py",
        '    message["Date"] = formatdate(localtime=True)',
        '    # MUTACAO M-13: sem Date, o Amavis alerta e a pontuacao sobe',
        (
            "python3", "-m", "pytest", "tests/test_mailer_and_images.py",
            "-q", "--no-cov", "-k", "date",
        ),
        "A mensagem fica sem `Date`, que o RFC 5322 torna obrigatório. O Amavis "
        "injecta `X-Amavis-Alert` e o Gmail e a Microsoft sobem a pontuação logo à "
        "entrada. Foi o primeiro sintoma de um email que não chegava. (F-17)",
        ("F-17",),
    ),
    Mutacao(
        "M-14",
        "src/mailutils/mailer.py",
        "make_msgid(domain=_dominio_de(settings.mail_from))",
        "make_msgid()  # MUTACAO M-14",
        (
            "python3", "-m", "pytest", "tests/test_mailer_and_images.py",
            "-q", "--no-cov", "-k", "message_id",
        ),
        "O `Message-ID` deixa de levar o domínio do remetente e passa a usar o "
        "`fqdn` da máquina. Num servidor de rede interna isso é `.lan`, e um "
        "domínio não roteável é penalizado de imediato, porque parece um script "
        "mal configurado. (F-17)",
        ("F-17",),
    ),
    Mutacao(
        "M-15",
        "src/mailutils/mailer.py",
        '            del parte["MIME-Version"]',
        "            pass  # MUTACAO M-15",
        (
            "python3", "-m", "pytest", "tests/test_mailer_and_images.py",
            "-q", "--no-cov", "-k", "mime",
        ),
        "A `MIME-Version` volta a entrar na parte `text/html`, que é o que o "
        "`add_alternative` do stdlib faz e é MIME inválido: dentro dos limites, "
        "aquele cabeçalho pertence só à mensagem. (F-17)",
        ("F-17",),
    ),
    Mutacao(
        "M-16",
        "src/mailutils/signatures/renderer.py",
        "    render = _RENDERERS.get(data.layout, _render_stack)",
        "    render = _RENDERERS[DEFAULT_LAYOUT]",
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_renderer.py",
            "tests/test_editor_flows.py",
            "-q",
            "--no-cov",
        ),
        "A escolha da estrutura deixa de ser lida e todas renderizam vertical. "
        "O selector da interface continua a marcar a estrutura escolhida, o campo "
        "escondido continua a ir no formulário, e a coluna `layout` continua a "
        "gravar o que o utilizador escolheu — a falha só aparece no email. É o "
        "T013 inteiro a partir-se sem um único sintoma na interface.",
        ("F-03",),
    ),

    Mutacao(
        "M-17",
        "src/mailutils/signatures/renderer.py",
        '        muted="#aec4d9",',
        '        muted="#2a3f52",',
        ("python3", "-m", "pytest", "tests/test_renderer.py", "-q", "--no-cov", "-k", "Legivel"),
        "O `muted` do tema `navy` passa de 9.69:1 para 1.6:1 sobre o seu próprio "
        "fundo. Cargo, empresa e morada tornam-se ilegíveis. Esta mutação prova "
        "que os temas novos do T013 entram no gate de contraste **sem** que "
        "ninguém escreva um teste novo: `TestAssinaturaLegivelNoClienteDeEmail` "
        "está parametrizado sobre `sorted(THEMES)`. Foi o que permitiu escolher "
        "as cores a calcular em vez de a olho.",
        ("F-03",),
    ),
    Mutacao(
        "M-18",
        "src/mailutils/lists/service.py",
        '            "   AND confirmed_at IS NOT NULL"',
        '            "   AND 1=1"',
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_lists.py",
            "-q",
            "--no-cov",
            "-k",
            "InvarianteCentral",
        ),
        "A unica clausula que separa uma lista de contactos de um relay de email "
        "bombing passa a ser `1=1`. Todos os pendentes — os que receberam um "
        "codigo de confirmacao e nunca responderam — entram no envio. O produto "
        "passa a enviar para quem nao pediu, usando o endereco de outra pessoa "
        "como remetente. E a mutacao que o `CLAUDE.md` proibe em letras: "
        "`confirmed_at IS NULL` nao entra no SELECT, em nenhum caminho.",
        ("F-01",),
    ),
    Mutacao(
        "M-19",
        "src/mailutils/lists/service.py",
        '        if ja_pendentes + len(a_inserir) >= settings.max_pending_confirmations:',
        '        if ja_pendentes + len(a_inserir) >= settings.max_pending_confirmations + 10**6:',
        ("python3", "-m", "pytest", "tests/test_lists.py", "-q", "--no-cov", "-k", "AntiAbuso"),
        "O tecto de confirmacoes por confirmar deixa de existir. Um utilizador "
        "com sessao importa cinquenta mil enderecos e pede os codigos todos de "
        "uma vez. E o tecto anti-abuso que o `CLAUDE.md` diz ser feature e nao "
        "detalhe de implementacao.",
        ("F-01",),
    ),
    Mutacao(
        "M-20",
        "src/mailutils/web.py",
        '        and dados.get("a") == address_id',
        '        and dados.get("a") is not None  # MUTACAO M-20',
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_lists.py",
            "-q",
            "--no-cov",
            "-k",
            "LinkAssinado",
        ),
        "A verificacao de posse do token desaparece: o `address_id` deixa de ser "
        "comparado com o do payload assinado. O link de Ana passa a confirmar o "
        "endereco do Bruno. E o B-04 desta mesma revisao, que era a razao de o "
        "token existir.",
        ("F-01",),
    ),
    Mutacao(
        "M-21",
        "src/mailutils/lists/service.py",
        '            " SET unsubscribed_at = NULL, confirmed_at = NULL,"',
        '            " SET unsubscribed_at = NULL,"',
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_lists.py",
            "-q",
            "--no-cov",
            "-k",
            "BypassConsentimento",
        ),
        "Repor uma inscricao volta a ser `unsubscribed_at = NULL` e mais nada. "
        "O endereco deixa de estar em `destinatarios()` quando se cancela e "
        "volta sem ninguem confirmar quando o dono da lista clica em 'Repor'. "
        "E o M-01: o produto a decidir por quem se cancelou.",
        ("F-01",),
    ),
    Mutacao(
        "M-22",
        "src/mailutils/web.py",
        '        and dados.get("l") == list_id',
        '        and dados.get("l") is not None  # MUTACAO M-22',
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_lists.py",
            "-q",
            "--no-cov",
            "-k",
            "token_da_lista_a",
        ),
        "A lista deixa de estar no token. Um link de confirmacao da lista A passa "
        "a abrir a rota da lista B. So a seguranca que sobra e a de `address_id` "
        "estar filtrado por lista — e isso e seguro por acidente do esquema, nao "
        "por decisao.",
        ("F-01",),
    ),
    Mutacao(
        "M-23",
        "src/mailutils/signatures/renderer.py",
        '            f\'display:inline-block;">\'',
        '            f\'display:inline;">\'',
        (
            "python3",
            "-m",
            "pytest",
            "tests/test_renderer.py",
            "-q",
            "--no-cov",
            "-k",
            "StackNaoMudou",
        ),
        "Muda UMA palavra e portanto alguns bytes do HTML do `stack`. Esta e a "
        "mutacao que provou que o T013 afirmava 'byte a byte' sem nada que o "
        "provasse: os 744 testes passavam. Agora morre em "
        "`tests/golden/stack.html`.",
        ("F-01",),
    ),
)

def correr(mutacao: Mutacao) -> tuple[bool, str]:
    """Aplica a mutação, corre o comando e reverte. Devolve (morreu?, saída)."""
    alvo = RAIZ / mutacao.ficheiro
    original = alvo.read_text(encoding="utf-8")
    if original.count(mutacao.antes) < 1:
        return False, f"NÃO APLICADA: {mutacao.antes!r} não está em {mutacao.ficheiro}"

    with tempfile.TemporaryDirectory() as pasta:
        copia = pathlib.Path(pasta) / alvo.name
        copia.write_text(original, encoding="utf-8")
        try:
            alvo.write_text(original.replace(mutacao.antes, mutacao.depois, 1), encoding="utf-8")
            inicio = time.monotonic()
            feito = subprocess.run(  # noqa: S603
                list(mutacao.comando),
                cwd=RAIZ,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            duracao = time.monotonic() - inicio
        finally:
            alvo.write_text(original, encoding="utf-8")

    ultimas = [linha for linha in feito.stdout.splitlines() if linha.strip()][-3:]
    resumo = "\n".join(ultimas) or feito.stderr.strip()[-400:]
    return (feito.returncode != 0), f"{resumo}\n    ({duracao:.0f}s)"

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--escrever",
        action="store_true",
        help="escreve o ficheiro a partir dos resultados (modo normal)",
    )
    ap.add_argument(
        "--verificar",
        action="store_true",
        help="sai != 0 se alguma mutação não fizer o gate ficar vermelho",
    )
    args = ap.parse_args()
    if not (args.escrever or args.verificar):
        ap.error("escolhe --escrever ou --verificar")

    print(f"{'mutação':6} {'morreu':7} tickets  ficheiro")
    linhas = []
    silenciosas = []
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    for mutacao in MUTACOES:
        morreu, saida = correr(mutacao)
        marca = "SIM" if morreu else "NÃO"
        print(
            f"{mutacao.identificador:6} {marca:7} {','.join(mutacao.tickets):8} {mutacao.ficheiro}"
        )
        if not morreu:
            silenciosas.append(mutacao.identificador)
        linhas.append(
            f"### {mutacao.identificador} — {mutacao.tickets[0]}\n\n"
            f"- **Ficheiro:** `{mutacao.ficheiro}`\n"
            f"- **Mutação:** `{mutacao.antes.strip()}` → "
            f"`{mutacao.depois.strip() or '(removido)'}`\n"
            f"- **Comando:** `{' '.join(mutacao.comando)}`\n"
            f"- **Porque:** {mutacao.porque}\n"
            f"- **Saída observada:**\n\n```\n{saida}\n```\n"
        )

    if args.escrever:
        cabecalho = (
            "---\n"
            "ticket: T008\n"
            "tipo: prova-por-mutação\n"
            "gerado-por: scripts/run-mutations.py\n"
            "regenerar: python3 scripts/run-mutations.py --escrever\n"
            'nota: este ficheiro é GERADO. O que está em "Saída observada" é a\n'
            "  saída real do comando, escrita pelo script — ninguém escreve aqui à\n"
            "  mão. A primeira ronda afirmava uma prova por mutação que não deixou\n"
            "  rasto, e duas personas provaram que três testes não detectavam as\n"
            "  mutações que alegavam detectar. (F-10)\n"
            "---\n\n"
            "# Prova por mutação — T008\n\n"
            "Cada linha abaixo é uma alteração de uma linha que, se passar, o gate\n"
            "está a mentir. A regra é: **ninguém escreve o resultado à mão.**\n"
        )
        SAIDA.write_text(
            cabecalho + "\n" + "\n".join(linhas) + f"\n**Total: {len(MUTACOES)} mutações. "
            f"Sem escape: {len(silenciosas) or 'nenhuma'}.**\n",
            encoding="utf-8",
        )
        print(f"\nescrito em {SAIDA.relative_to(RAIZ)}")

    if silenciosas:
        print(
            f"\nFALHA: {len(silenciosas)} mutações NÃO fizeram o gate ficar "
            f"vermelho: {', '.join(silenciosas)}"
        )
        print("Isso significa que os testes que dizem provar essas coisas não as provam.")
        return 1
    print(f"\n{len(MUTACOES)}/{len(MUTACOES)} mutações detectadas.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
