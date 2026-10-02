# HTML dourado

`stack.html` é o output exacto de `signatures/renderer.py` **antes** do T013,
commit `01ee2f1`, com os campos de `FULL` e o tema `dark`.

Existe porque o T013 afirmou que `stack` produz byte a byte o mesmo HTML, e
nenhum teste o provava: `test_stack_continua_byte_identico` comparava fragmentos.
A mutação `display:inline-block` → `display:inline` passava com 744 testes
verdes. **M-10 da revisão do T014.**

Regenerar, e só quando a mudança for deliberada:

```bash
# a) guardar o renderer antigo
git show 01ee2f1:src/mailutils/signatures/renderer.py \
  > src/mailutils/signatures/_renderer_antes.py
# b) renderizar e gravar
PYTHONPATH=src python3 -c '...'      # ver aes/tickets/T014-listas.md
# c) apagar o renderer antigo — NUNCA fica em src/
rm src/mailutils/signatures/_renderer_antes.py
```

O passo (c) não é opcional: `_renderer_antes.py` é o commit `01ee2f1` inteiro
com um segundo nome dentro de `src/`, e `code-check` passa por lá sem o ver.
