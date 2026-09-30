---
ticket: T008
tipo: prova-por-mutação
gerado-por: scripts/run-mutations.py
regenerar: python3 scripts/run-mutations.py --escrever
nota: este ficheiro é GERADO. O que está em "Saída observada" é a
  saída real do comando, escrita pelo script — ninguém escreve aqui à
  mão. A primeira ronda afirmava uma prova por mutação que não deixou
  rasto, e duas personas provaram que três testes não detectavam as
  mutações que alegavam detectar. (F-10)
---

# Prova por mutação — T008

Cada linha abaixo é uma alteração de uma linha que, se passar, o gate
está a mentir. A regra é: **ninguém escreve o resultado à mão.**

### M-01 — F-02

- **Ficheiro:** `src/mailutils/signatures/renderer.py`
- **Mutação:** `opaco = _precisa_de_fundo(theme)` → `opaco = False  # MUTACAO M-01`
- **Comando:** `python3 -m pytest tests/test_renderer.py -q --no-cov -k Legivel`
- **Porque:** O tema escuro volta a não levar o fundo: texto #f0f0f0 sobre o branco do cliente dá 1.14:1 e a assinatura fica invisível. Era o F-02.
- **Saída observada:**

```
FAILED tests/test_renderer.py::TestAssinaturaLegivelNoClienteDeEmail::test_todo_o_texto_do_tema_passa_4_5[dark]
FAILED tests/test_renderer.py::TestAssinaturaLegivelNoClienteDeEmail::test_tema_escuro_leva_o_fundo_em_dos_sitios
3 failed, 4 passed, 49 deselected in 0.08s
    (1s)
```

### M-02 — F-02

- **Ficheiro:** `src/mailutils/signatures/renderer.py`
- **Mutação:** `f"{cor_tabela} "` → `(removido)`
- **Comando:** `python3 -m pytest tests/test_renderer.py -q --no-cov -k Legivel`
- **Porque:** Fica o `background` no `<div>` mas sai o `bgcolor` do `<table>`. O Word engine do Outlook ignora `background` num div, pelo que a assinatura continua invisível no Outlook — que é onde a maior parte das pessoas a vê.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_renderer.py::TestAssinaturaLegivelNoClienteDeEmail::test_tema_escuro_leva_o_fundo_em_dos_sitios
1 failed, 6 passed, 49 deselected in 0.06s
    (1s)
```

### M-03 — F-01

- **Ficheiro:** `src/mailutils/main.py`
- **Mutação:** `if not ja_posto and not request.url.path.endswith(_CONTEXTLESS_SUFFIXES):` → `if not request.url.path.endswith(_CONTEXTLESS_SUFFIXES):`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k tema`
- **Porque:** O middleware volta a sobrescrever o cookie de tema que a rota punha, e o botão de tema deixa de funcionar. Duas respostas com `Set-Cookie` para o mesmo nome, e a última ganha.
- **Saída observada:**

```
FAILED e2e/test_fluxo_completo.py::test_tema_claro_por_clique - playwright._i...
FAILED e2e/test_fluxo_completo.py::test_tema_antes_de_entrar - playwright._im...
2 failed, 9 deselected in 24.06s
    (25s)
```

### M-04 — F-05

- **Ficheiro:** `src/mailutils/static/app.js`
- **Mutação:** `if (fill) fill.setAttribute("data-score", vazio ? 0 : score.score);` → `if (fill) fill.setAttribute("data-score", 0);`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k score_actualiza`
- **Porque:** O caminho de actualização do score em JavaScript passa a ser inoperante para o número, e o teste passa a provar que o score não muda. Era o F-05, e era o caminho que uma revisão encontrou morto com a suite toda verde.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED e2e/test_fluxo_completo.py::test_o_score_actualiza_enquanto_se_escreve
1 failed, 10 deselected in 4.75s
    (5s)
```

### M-05 — F-06

- **Ficheiro:** `src/mailutils/analyzer/scoring.py`
- **Mutação:** `score = max(0, min(100, total))` → `score = max(0, min(100, total)) * 1.0`
- **Comando:** `python3 -m pytest tests/test_spam.py -q --no-cov`
- **Porque:** O score passa a fraccionário. A barra de score é uma regra de CSS por valor, e `data-score="20.0"` não casa com nenhuma das 101. A revisão provou que a suíte HTTP inteira passava com isto. Era o F-06, e era a garantia que eu escrevi e que não existia.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_spam.py::TestOScoreEInteiro::test_o_analisador_da_um_int - ...
1 failed, 60 passed in 0.11s
    (1s)
```

### M-06 — F-07

- **Ficheiro:** `src/mailutils/static/app.css`
- **Mutação:** `.score__fill[data-score="20"] { width: 20%; }` → `(removido)`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k discriminante`
- **Porque:** Uma das 101 regras da barra desaparece. O teste que media o score zero continuava verde, porque `width: 0` na regra base dá o mesmo pixel. Era o F-07, e é porque o teste discriminante mede um score de 20.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED e2e/test_fluxo_completo.py::test_a_barra_e_discriminante_com_um_score_nao_zero
1 failed, 10 deselected in 4.19s
    (5s)
```

### M-07 — F-08

- **Ficheiro:** `src/mailutils/signatures/routes.py`
- **Mutação:** `" style-src 'unsafe-inline'; img-src https: http:; base-uri 'none';"` → `" img-src https: http:; base-uri 'none';"`
- **Comando:** `python3 -m pytest tests/test_editor_flows.py -q --no-cov -k Exportado`
- **Porque:** A CSP do documento exportado volta a `default-src 'none'` sem `style-src`, e o ficheiro que o utilizador descarrega para conferir deixa de se mostrar. Era o F-08.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_editor_flows.py::TestOFicheiroExportadoMostraOSeusEstilos::test_permite_estilos_inline
1 failed, 2 passed, 47 deselected in 1.19s
    (2s)
```

### M-08 — F-12

- **Ficheiro:** `src/mailutils/signatures/spam.py`
- **Mutação:** `"vazio": not visible_text and not srcs and not hrefs,` → `"vazio": False,`
- **Comando:** `python3 -m pytest tests/test_editor_flows.py -q --no-cov -k vazia`
- **Porque:** O editor volta a mostrar «0 / 100 SEGURO» com selo verde para um formulário em branco. Era o F-12, e o teste que o apanhava afirmava o contrário — a inversão ficou escrita no docstring dele.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_editor_flows.py::TestEditor::test_uma_assinatura_vazia_nao_promete_que_e_segura
1 failed, 49 deselected in 0.25s
    (1s)
```

### M-09 — F-11

- **Ficheiro:** `src/mailutils/static/app.css`
- **Mutação:** `.hidden { display: none; }` → `.hidden { display: none; }

.orfão-m09 { color: red; }`
- **Comando:** `python3 -m pytest tests/test_browser_regressions.py -q --no-cov -k Orfao`
- **Porque:** Um selector novo que nada referencia. O teste passa a apanhar selectores mortos, que era o F-11.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_browser_regressions.py::TestNenhumSelectorOrfaoEmAppCss::test_todo_o_selector_definido_e_usado
1 failed, 1 passed, 18 deselected in 0.05s
    (1s)
```

### M-10 — F-03

- **Ficheiro:** `.github/workflows/ci.yml`
- **Mutação:** `- name: Install browser
        run: python3 -m playwright install --with-deps chromium` → `- name: Install browser
        run: true  # MUTACAO M-10`
- **Comando:** `python3 -m pytest tests/test_browser_regressions.py -q --no-cov -k ci`
- **Porque:** A CI volta a não instalar o browser, e `make check` fica vermelho no primeiro run. Era o F-03.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_browser_regressions.py::TestAInstalaOCiTemDeTerTudo::test_toda_a_ci_que_corre_o_gate_instala_o_browser
1 failed, 2 passed, 17 deselected in 0.05s
    (1s)
```

### M-11 — F-04

- **Ficheiro:** `src/mailutils/signatures/routes.py`
- **Mutação:** `"Content-Security-Policy": CSP_PREVIEW,` → `# MUTACAO M-11: um <meta> no documento nao chega`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k preview`
- **Porque:** A rota deixa de pôr a CSP no header e o preview volta a herdar a `style-src 'self'` da aplicação: a assinatura aparece em Times New Roman, a preto. Um `<meta http-equiv>` no documento não chega, porque as políticas juntam-se e a mais restritiva ganha. Era o F-04, e a primeira versão da minha correcção cometia exactamente este erro.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED e2e/test_fluxo_completo.py::test_o_preview_mostra_a_assinatura_como_ela_sai
1 failed, 10 deselected in 4.06s
    (4s)
```

### M-12 — F-01

- **Ficheiro:** `src/mailutils/templates/_tema.html`
- **Mutação:** `value="{{ sessao.csrf_token if sessao else csrf }}">` → `value="token-falso" data-mutacao="M-12">`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k tema`
- **Porque:** O botão de tema passa a mandar um token de CSRF falso. Sem token válido o POST é recusado, e um utilizador fica com um botão que não faz nada — que era o sintoma exacto do F-01, em que nada no ecrã escrevia o cookie.
- **Saída observada:**

```
FAILED e2e/test_fluxo_completo.py::test_tema_claro_por_clique - playwright._i...
FAILED e2e/test_fluxo_completo.py::test_tema_antes_de_entrar - playwright._im...
2 failed, 9 deselected in 24.36s
    (25s)
```

### M-13 — F-17

- **Ficheiro:** `src/mailutils/mailer.py`
- **Mutação:** `message["Date"] = formatdate(localtime=True)` → `# MUTACAO M-13: sem Date, o Amavis alerta e a pontuacao sobe`
- **Comando:** `python3 -m pytest tests/test_mailer_and_images.py -q --no-cov -k date`
- **Porque:** A mensagem fica sem `Date`, que o RFC 5322 torna obrigatório. O Amavis injecta `X-Amavis-Alert` e o Gmail e a Microsoft sobem a pontuação logo à entrada. Foi o primeiro sintoma de um email que não chegava. (F-17)
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_mailer_and_images.py::TestOsCabecalhosQueOsFiltrosExigem::test_tem_date
1 failed, 51 deselected in 0.06s
    (1s)
```

### M-14 — F-17

- **Ficheiro:** `src/mailutils/mailer.py`
- **Mutação:** `make_msgid(domain=_dominio_de(settings.mail_from))` → `make_msgid()  # MUTACAO M-14`
- **Comando:** `python3 -m pytest tests/test_mailer_and_images.py -q --no-cov -k message_id`
- **Porque:** O `Message-ID` deixa de levar o domínio do remetente e passa a usar o `fqdn` da máquina. Num servidor de rede interna isso é `.lan`, e um domínio não roteável é penalizado de imediato, porque parece um script mal configurado. (F-17)
- **Saída observada:**

```
FAILED tests/test_mailer_and_images.py::TestOsCabecalhosQueOsFiltrosExigem::test_o_message_id_usa_o_dominio_do_remetente
FAILED tests/test_mailer_and_images.py::TestOsCabecalhosQueOsFiltrosExigem::test_o_message_id_segue_o_remetente_e_nao_a_maquina
2 failed, 50 deselected in 0.07s
    (1s)
```

### M-15 — F-17

- **Ficheiro:** `src/mailutils/mailer.py`
- **Mutação:** `del parte["MIME-Version"]` → `pass  # MUTACAO M-15`
- **Comando:** `python3 -m pytest tests/test_mailer_and_images.py -q --no-cov -k mime`
- **Porque:** A `MIME-Version` volta a entrar na parte `text/html`, que é o que o `add_alternative` do stdlib faz e é MIME inválido: dentro dos limites, aquele cabeçalho pertence só à mensagem. (F-17)
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_mailer_and_images.py::TestOsCabecalhosQueOsFiltrosExigem::test_a_mime_version_so_existe_no_topo
1 failed, 51 deselected in 0.06s
    (1s)
```

**Total: 15 mutações. Sem escape: nenhuma.**
