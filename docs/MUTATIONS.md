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
- **Mutação:** `opaco = _precisa_de_fundo(self.theme)` → `opaco = False  # MUTACAO M-01`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_renderer.py -q --no-cov -k Legivel`
- **Porque:** O tema escuro volta a não levar o fundo: texto #f0f0f0 sobre o branco do cliente dá 1.14:1 e a assinatura fica invisível. Era o F-02.
- **Saída observada:**

```
FAILED tests/test_renderer.py::TestAssinaturaLegivelNoClienteDeEmail::test_todo_o_texto_do_tema_passa_4_5[navy]
FAILED tests/test_renderer.py::TestAssinaturaLegivelNoClienteDeEmail::test_tema_escuro_leva_o_fundo_em_dos_sitios
9 failed, 8 passed, 184 deselected in 0.18s
    (1s)
```

### M-02 — F-02

- **Ficheiro:** `src/mailutils/signatures/renderer.py`
- **Mutação:** `f'{cor_tabela} style="{estilo}">'` → `'style="{estilo}">'`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_renderer.py -q --no-cov -k Legivel`
- **Porque:** Fica o `background` no `<div>` mas sai o `bgcolor` do `<table>`. O Word engine do Outlook ignora `background` num div, pelo que a assinatura continua invisível no Outlook — que é onde a maior parte das pessoas a vê.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_renderer.py::TestAssinaturaLegivelNoClienteDeEmail::test_tema_escuro_leva_o_fundo_em_dos_sitios
1 failed, 16 passed, 184 deselected in 0.09s
    (1s)
```

### M-03 — F-01

- **Ficheiro:** `src/mailutils/main.py`
- **Mutação:** `if not ja_posto and not request.url.path.endswith(_CONTEXTLESS_SUFFIXES):` → `if not request.url.path.endswith(_CONTEXTLESS_SUFFIXES):`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k tema`
- **Porque:** O middleware volta a sobrescrever o cookie de tema que a rota punha, e o botão de tema deixa de funcionar. Duas respostas com `Set-Cookie` para o mesmo nome, e a última ganha.
- **Saída observada:**

```
FAILED e2e/test_fluxo_completo.py::test_tema_claro_por_clique - playwright._i...
FAILED e2e/test_fluxo_completo.py::test_tema_antes_de_entrar - playwright._im...
2 failed, 9 deselected in 24.07s
    (24s)
```

### M-04 — F-05

- **Ficheiro:** `src/mailutils/static/app.js`
- **Mutação:** `if (fill) fill.setAttribute("data-score", vazio ? 0 : score.score);` → `if (fill) fill.setAttribute("data-score", 0);`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k score_actualiza`
- **Porque:** O caminho de actualização do score em JavaScript passa a ser inoperante para o número, e o teste passa a provar que o score não muda. Era o F-05, e era o caminho que uma revisão encontrou morto com a suite toda verde.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED e2e/test_fluxo_completo.py::test_o_score_actualiza_enquanto_se_escreve
1 failed, 10 deselected in 3.66s
    (4s)
```

### M-05 — F-06

- **Ficheiro:** `src/mailutils/analyzer/scoring.py`
- **Mutação:** `score = max(0, min(100, total))` → `score = max(0, min(100, total)) * 1.0`
- **Estado:** `morreu`
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
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k discriminante`
- **Porque:** Uma das 101 regras da barra desaparece. O teste que media o score zero continuava verde, porque `width: 0` na regra base dá o mesmo pixel. Era o F-07, e é porque o teste discriminante mede um score de 20.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED e2e/test_fluxo_completo.py::test_a_barra_e_discriminante_com_um_score_nao_zero
1 failed, 10 deselected in 3.74s
    (4s)
```

### M-07 — F-08

- **Ficheiro:** `src/mailutils/signatures/routes.py`
- **Mutação:** `" style-src 'unsafe-inline'; img-src https: http:; base-uri 'none';"` → `" img-src https: http:; base-uri 'none';"`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_editor_flows.py -q --no-cov -k Exportado`
- **Porque:** A CSP do documento exportado volta a `default-src 'none'` sem `style-src`, e o ficheiro que o utilizador descarrega para conferir deixa de se mostrar. Era o F-08.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_editor_flows.py::TestOFicheiroExportadoMostraOSeusEstilos::test_permite_estilos_inline
1 failed, 3 passed, 56 deselected in 1.25s
    (2s)
```

### M-08 — F-12

- **Ficheiro:** `src/mailutils/signatures/spam.py`
- **Mutação:** `"vazio": not visible_text and not srcs and not hrefs,` → `"vazio": False,`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_editor_flows.py -q --no-cov -k vazia`
- **Porque:** O editor volta a mostrar «0 / 100 SEGURO» com selo verde para um formulário em branco. Era o F-12, e o teste que o apanhava afirmava o contrário — a inversão ficou escrita no docstring dele.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_editor_flows.py::TestEditor::test_uma_assinatura_vazia_nao_promete_que_e_segura
1 failed, 59 deselected in 0.30s
    (1s)
```

### M-09 — F-11

- **Ficheiro:** `src/mailutils/static/app.css`
- **Mutação:** `.hidden { display: none; }` → `.hidden { display: none; }

.orfão-m09 { color: red; }`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_browser_regressions.py -q --no-cov -k Orfao`
- **Porque:** Um selector novo que nada referencia. O teste passa a apanhar selectores mortos, que era o F-11.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_browser_regressions.py::TestNenhumSelectorOrfaoEmAppCss::test_todo_o_selector_definido_e_usado
1 failed, 1 passed, 19 deselected in 0.05s
    (1s)
```

### M-11 — F-04

- **Ficheiro:** `src/mailutils/signatures/routes.py`
- **Mutação:** `"Content-Security-Policy": CSP_PREVIEW,` → `# MUTACAO M-11: um <meta> no documento nao chega`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k preview`
- **Porque:** A rota deixa de pôr a CSP no header e o preview volta a herdar a `style-src 'self'` da aplicação: a assinatura aparece em Times New Roman, a preto. Um `<meta http-equiv>` no documento não chega, porque as políticas juntam-se e a mais restritiva ganha. Era o F-04, e a primeira versão da minha correcção cometia exactamente este erro.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED e2e/test_fluxo_completo.py::test_o_preview_mostra_a_assinatura_como_ela_sai
1 failed, 10 deselected in 3.95s
    (4s)
```

### M-12 — F-01

- **Ficheiro:** `src/mailutils/templates/_tema.html`
- **Mutação:** `value="{{ sessao.csrf_token if sessao else csrf }}">` → `value="token-falso" data-mutacao="M-12">`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest e2e -q --no-cov -k tema`
- **Porque:** O botão de tema passa a mandar um token de CSRF falso. Sem token válido o POST é recusado, e um utilizador fica com um botão que não faz nada — que era o sintoma exacto do F-01, em que nada no ecrã escrevia o cookie.
- **Saída observada:**

```
FAILED e2e/test_fluxo_completo.py::test_tema_claro_por_clique - playwright._i...
FAILED e2e/test_fluxo_completo.py::test_tema_antes_de_entrar - playwright._im...
2 failed, 9 deselected in 23.93s
    (24s)
```

### M-13 — F-17

- **Ficheiro:** `src/mailutils/mailer.py`
- **Mutação:** `message["Date"] = formatdate(localtime=True)` → `# MUTACAO M-13: sem Date, o Amavis alerta e a pontuacao sobe`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_mailer_and_images.py -q --no-cov -k date`
- **Porque:** A mensagem fica sem `Date`, que o RFC 5322 torna obrigatório. O Amavis injecta `X-Amavis-Alert` e o Gmail e a Microsoft sobem a pontuação logo à entrada. Foi o primeiro sintoma de um email que não chegava. (F-17)
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_mailer_and_images.py::TestOsCabecalhosQueOsFiltrosExigem::test_tem_date
1 failed, 53 deselected in 0.06s
    (1s)
```

### M-14 — F-17

- **Ficheiro:** `src/mailutils/mailer.py`
- **Mutação:** `make_msgid(domain=_dominio_de(settings.mail_from))` → `make_msgid()  # MUTACAO M-14`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_mailer_and_images.py -q --no-cov -k message_id`
- **Porque:** O `Message-ID` deixa de levar o domínio do remetente e passa a usar o `fqdn` da máquina. Num servidor de rede interna isso é `.lan`, e um domínio não roteável é penalizado de imediato, porque parece um script mal configurado. (F-17)
- **Saída observada:**

```
FAILED tests/test_mailer_and_images.py::TestOsCabecalhosQueOsFiltrosExigem::test_o_message_id_usa_o_dominio_do_remetente
FAILED tests/test_mailer_and_images.py::TestOsCabecalhosQueOsFiltrosExigem::test_o_message_id_segue_o_remetente_e_nao_a_maquina
2 failed, 52 deselected in 0.08s
    (1s)
```

### M-15 — F-17

- **Ficheiro:** `src/mailutils/mailer.py`
- **Mutação:** `del parte["MIME-Version"]` → `pass  # MUTACAO M-15`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_mailer_and_images.py -q --no-cov -k mime`
- **Porque:** A `MIME-Version` volta a entrar na parte `text/html`, que é o que o `add_alternative` do stdlib faz e é MIME inválido: dentro dos limites, aquele cabeçalho pertence só à mensagem. (F-17)
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_mailer_and_images.py::TestOsCabecalhosQueOsFiltrosExigem::test_a_mime_version_so_existe_no_topo
1 failed, 53 deselected in 0.06s
    (1s)
```

### M-16 — F-03

- **Ficheiro:** `src/mailutils/signatures/renderer.py`
- **Mutação:** `render = _RENDERERS.get(data.layout, _render_stack)` → `render = _RENDERERS[DEFAULT_LAYOUT]`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_renderer.py tests/test_editor_flows.py -q --no-cov`
- **Porque:** A escolha da estrutura deixa de ser lida e todas renderizam vertical. O selector da interface continua a marcar a estrutura escolhida, o campo escondido continua a ir no formulário, e a coluna `layout` continua a gravar o que o utilizador escolheu — a falha só aparece no email. É o T013 inteiro a partir-se sem um único sintoma na interface.
- **Saída observada:**

```
FAILED tests/test_editor_flows.py::TestEstruturaDaAssinatura::test_toda_a_estrutura_chega_ao_html_exportado
FAILED tests/test_editor_flows.py::TestEstruturaDaAssinatura::test_as_estruturas_sao_visivelmente_diferentes
7 failed, 254 passed in 16.05s
    (17s)
```

### M-17 — F-03

- **Ficheiro:** `src/mailutils/signatures/renderer.py`
- **Mutação:** `muted="#aec4d9",` → `muted="#2a3f52",`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_renderer.py -q --no-cov -k Legivel`
- **Porque:** O `muted` do tema `navy` passa de 9.69:1 para 1.6:1 sobre o seu próprio fundo. Cargo, empresa e morada tornam-se ilegíveis. Esta mutação prova que os temas novos do T013 entram no gate de contraste **sem** que ninguém escreva um teste novo: `TestAssinaturaLegivelNoClienteDeEmail` está parametrizado sobre `sorted(THEMES)`. Foi o que permitiu escolher as cores a calcular em vez de a olho.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_renderer.py::TestAssinaturaLegivelNoClienteDeEmail::test_todo_o_texto_do_tema_passa_4_5[navy]
1 failed, 16 passed, 184 deselected in 0.10s
    (1s)
```

### M-18 — F-01

- **Ficheiro:** `src/mailutils/lists/service.py`
- **Mutação:** `if not lista_pode_enviar(conn, list_id):
        return {"enviavel": False, "destinatarios": [], "motivo": "sem from confirmado"}` → `if False:
        return {"enviavel": False, "destinatarios": [], "motivo": "sem from confirmado"}`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_lists.py -q --no-cov -k InvarianteCentral or RemetenteConfirmado`
- **Porque:** O portao do `from` desaparece de `destinatarios()`: uma lista sem remetente confirmado devolve os seus endereços. O produto envia em nome de quem nao confirmou nada, a uma lista que o operador nunca fechou. E a mutacao que o `CLAUDE.md` proibe em letras: sem `from` confirmado, nenhum caminho de envio devolve destinatarios — nem o imediato, nem o agendado, nem a reexecucao.

**Esta e a M-18 de antes, com outro assunto.** A antiga mutava `confirmed_at IS NOT NULL` para `1=1`; a coluna nao existe, e o filtro que ela protegia foi retirado por decisao do dono. O que a substituicao tem de provar e a mesma coisa com a porta que ficou no lugar — e a porta que ficou e o remetente. O `CLAUDE.md` diz que retirar um dos seis portoes obriga a dizer qual dos outros deixa de valer; esta mutacao e a forma de essa frase ser verificavel.
- **Saída observada:**

```
FAILED tests/test_lists.py::TestInvarianteCentral::test_importado_nao_e_destinatario
FAILED tests/test_lists.py::TestRemetenteConfirmado::test_uma_lista_sem_remetente_nao_envia
2 failed, 12 passed, 41 deselected in 3.64s
    (4s)
```

### M-19 — F-01

- **Ficheiro:** `src/mailutils/lists/service.py`
- **Mutação:** `if passado is not None and passado < settings.confirm_cooldown_seconds:` → `if False and passado is not None and passado < settings.confirm_cooldown_seconds:`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_lists.py -q --no-cov -k RemetenteConfirmado`
- **Porque:** O cooldown de pedido de codigo desaparece. Um utilizador com sessao pede codigos de confirmacao de `from` sem parar, a um endereco que nao e seu, para adivinhar o de outra pessoa. E o mesmo anti-abuso que o `CLAUDE.md` proibe afrouxar: o cooldown e feature, nao detalhe de implementacao.

**O alvo mudou e a propriedade nao.** O cooldown era por endereco de destinatario e protegia o relay de email bombing via codigos de confirmacao. Sem confirmacao por destinatario, esse objecto nao existe — e o cooldown passou a proteger o pedido de codigo do **remetente**, que e a unica coisa que ainda pede codigo. O `CLAUDE.md` foi corrigido para dizer isto antes de a mutacao escrever-se, porque a tabela de portoes afirmava uma protecao que o codigo ja nao tinha.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_lists.py::TestRemetenteConfirmado::test_o_cooldown_e_por_remetente
1 failed, 9 passed, 45 deselected in 2.71s
    (3s)
```

### M-20 — F-01

- **Ficheiro:** `src/mailutils/web.py`
- **Mutação:** `and dados.get("a") == address_id` → `and dados.get("a") is not None  # MUTACAO M-20`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_lists.py -q --no-cov -k LinkAssinado`
- **Porque:** A verificacao de posse do token desaparece: o `address_id` deixa de ser comparado com o do payload assinado. O link de Ana passa a confirmar o endereco do Bruno. E o B-04 desta mesma revisao, que era a razao de o token existir.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_lists.py::TestLinkAssinado::test_o_token_de_um_nao_abre_o_outro
1 failed, 5 passed, 49 deselected in 1.42s
    (2s)
```

### M-21 — F-01

- **Ficheiro:** `src/mailutils/lists/service.py`
- **Mutação:** `"UPDATE list_addresses SET unsubscribed_at = ?"
            " WHERE id = ? AND unsubscribed_at IS NULL",` → `"UPDATE list_addresses SET unsubscribed_at = ?"
            " WHERE id = ?",`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_lists.py -q --no-cov -k LinkAssinado or InvarianteCentral`
- **Porque:** A descadencia deixa de verificar que o endereco estava subscrito. `descadenciar()` passa a devolver `True` sempre que o `id` existe, e o `address_id` enumeravel de um link re-utilizado volta a escrever um timestamp novo em cada clique. E o M-01 pela outra porta: a idempotencia e o que impede que o estado de um cancelamento seja reescrito.

**A mutacao anterior desta linha foi apagada com a funcionalidade.** `repor_inscricao` deixou de existir no `T017-A` — nao ha subscricao para repor, porque nao ha confirmacao — e a mutacao que a protegia ficou sem subjecto. Esta e a substituta: a propriedade que ela protegia (o produto nao decide por quem cancelou, e um cancelamento nao e reversivel) mudou de codigo, e a prova muda com ela. Uma mutacao sem subjecto e uma mutacao que passa a nao morrer, e uma mutacao que nao morre e uma porta que ninguem sabe se esta fechada.
- **Saída observada:**

```
FAILED tests/test_lists.py::TestInvarianteCentral::test_descadencia_e_irreversivel_pelo_produto
FAILED tests/test_lists.py::TestLinkAssinado::test_a_descadencia_e_idempotente
2 failed, 8 passed, 45 deselected in 2.41s
    (3s)
```

### M-22 — F-01

- **Ficheiro:** `src/mailutils/web.py`
- **Mutação:** `and dados.get("l") == list_id` → `and dados.get("l") is not None  # MUTACAO M-22`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_lists.py -q --no-cov -k token_da_lista_a`
- **Porque:** A lista deixa de estar no token. Um link de confirmacao da lista A passa a abrir a rota da lista B. So a seguranca que sobra e a de `address_id` estar filtrado por lista — e isso e seguro por acidente do esquema, nao por decisao.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_lists.py::TestLinkAssinado::test_o_token_da_lista_a_nao_verifica_na_lista_b
1 failed, 54 deselected in 0.08s
    (1s)
```

### M-23 — F-01

- **Ficheiro:** `src/mailutils/signatures/renderer.py`
- **Mutação:** `f'display:inline-block;">'` → `f'display:inline;">'`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_renderer.py -q --no-cov -k StackNaoMudou`
- **Porque:** Muda UMA palavra e portanto alguns bytes do HTML do `stack`. Esta e a mutacao que provou que o T013 afirmava 'byte a byte' sem nada que o provasse: os 744 testes passavam. Agora morre em `tests/golden/stack.html`.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_renderer.py::TestStackNaoMudouUmByte::test_o_html_do_stack_e_o_golden
1 failed, 1 passed, 199 deselected in 0.08s
    (1s)
```

### M-24 — F-01

- **Ficheiro:** `src/mailutils/compose/service.py`
- **Mutação:** `if avaliacao["bloqueado"]:` → `if False:`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_compose.py -q --no-cov -k BloqueioNoEnvio`
- **Porque:** O portão do `T017-B` desaparece: um email que a aplicação reprovaria sai. E a quebra da unifying invariant do `CLAUDE.md`, que diz que a aplicação nunca envia algo que ela própria reprovaria.

A mutação substitui o `if` do **caminho de envio**, não o da interface: `avaliar()` continua a calcular o score e a dizer que está bloqueado, e a página continua a mostrar a barra a vermelho. Só o envio deixa de olhar.

Por isso o `-k` é `BloqueioNoEnvio` e não `PortaoDeScore`: os testes do score continuam verdes com esta mutação aplicada, e um `-k` mais largo daria uma prova que passa pelo motivo errado.
- **Saída observada:**

```
FAILED tests/test_compose.py::TestBloqueioNoEnvio::test_nao_envia_sem_pontuar
FAILED tests/test_compose.py::TestBloqueioNoEnvio::test_bloqueado_diz_porque_e_nao_so_que_nao_pode
2 failed, 1 passed, 22 deselected in 0.65s
    (1s)
```

### M-25 — F-01

- **Ficheiro:** `src/mailutils/compose/service.py`
- **Mutação:** `"bloqueado": spam.bloqueado(relatorio["score"], regras),` → `"bloqueado": relatorio["score"] >= spam.CATEGORIES[3][1],`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_compose.py -q --no-cov -k PortaoDeScore`
- **Porque:** O bloqueio passa a ser decidido **só pelo score**, e a metade da `FR-4.9` que olha para a gravidade de cada regra desaparece. Um email com um único sinal crítico e texto suficiente para manter o total abaixo do limiar sai. E o que a `FR-4.9` diz na primeira linha: decidir só pelo total dava ao utilizador forma de contornar o bloqueio com mais texto.

Morre em `test_a_gravidade_da_regra_bloqueia_so_por_si`, escrito exactamente para isto: precisa de um sinal crítico que o score não apanhe. Um email de texto de spam puro **não** o apanha — e há um teste separado a dizer que não deve, porque a calibração do `scoring.py` sobe a `crítico` no HTML perigoso e não num assunto agressivo.
- **Saída observada:**

```
=========================== short test summary info ============================
FAILED tests/test_compose.py::TestPortaoDeScore::test_a_gravidade_da_regra_bloqueia_so_por_si
1 failed, 4 passed, 20 deselected in 1.41s
    (2s)
```

### M-26 — F-01

- **Ficheiro:** `src/mailutils/compose/service.py`
- **Mutação:** `"<p>" + htmllib.escape(corpo)` → `"<p>" + corpo`
- **Estado:** `morreu`
- **Comando:** `python3 -m pytest tests/test_compose.py -q --no-cov -k Escapamento`
- **Porque:** O corpo do operador deixa de ser escapado. O rascunho é do operador e o email vai para a caixa de outra pessoa: um `<script>` colado no rascunho passa a ser `<script>` no email de um destinatário.

O `scoring.py` daria 50 pontos a isso — mas só **depois** do HTML estar montado e do `<script>` já lá estar. Com o score perfeito, um corpo com `<script>` sairia limpo.

O que esta mutação prova é que o escape é a defesa e o score não: o escapamento tem de acontecer **antes** de qualquer pontuação. É a única mutação do gate que prova isso.
- **Saída observada:**

```
FAILED tests/test_compose.py::TestEscapamento::test_o_html_escapa_o_que_o_operador_escreveu
FAILED tests/test_compose.py::TestEscapamento::test_a_falha_de_escape_mata_a_mutacao
2 failed, 23 deselected in 0.29s
    (1s)
```

**Total: 25 mutações. Sem escape: nenhuma. Avulsas (a âncora já não existe no ficheiro): nenhuma.**
