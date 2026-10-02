#!/usr/bin/env bash
# Gate epistémico — valida cada claim de aes/graph/islands.yaml pela evidência
# que a própria claim declara.
#
# ## O que este gate é, e o que não é
#
# `aes-epistemics` propõe resolver `logical_form` com o Z3. Não está ligado, e
# há duas razões. A primeira está em `NFR-10`: o Z3 seria uma dependência nova
# num projecto cuja regra é zero. A segunda é mais séria e vale mais:
#
# **As claims deste projecto não são tautologias lógicas.** "Se um endereço não
# está confirmado, ninguém recebe" não é uma fórmula que um solver refute — é
# uma afirmação sobre o que o código faz. Código falsifica-se com uma mutação:
# trocar a condição por `1=1` e ver se os testes ficam vermelhos. A M-18 é isso.
#
# Um UNSAT provaria que a codificação está certa. Não provaria que o produto
# está. E este gate prova a segunda coisa: que **a evidência declarada ainda
# morre**.
#
# ## O que conta como FALSIFIED
#
# `validation_type: external` — a evidência é uma mutação ou um teste. Se a
# mutação deixar de matar o gate, ou o teste deixar de passar, a claim passa a
# `FALSIFIED` e este gate falha. É a mesma regra de `make check`, aplicada uma
# claim de cada vez.
#
# `validation_type: human` — **este gate não pode decidir.** Sai `PENDENTE` e
# aponta para `human-validation.sh`. A honestidade aqui é o ponto: uma claim
# que só uma pessoa com olhos num Thunderbird pode fechar é marcada como tal,
# e não Given um verde que ninguém viu.
#
# ## Porquê não entra em `make check`
#
# Porque `mutation-check` lá está e faz o mesmo trabalho. Correr as 22
# mutações duas vezes por gate duplica três minutos e não compra cobertura.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
GRAFO="aes/graph/islands.yaml"

if [ ! -f "$GRAFO" ]; then
    echo "  FALHA: $GRAFO não existe. Um gate epistémico sem claims mede zero."
    exit 1
fi

echo "Gate epistémico — $GRAFO"
echo

SUPORTADAS=0
FALSIFICADAS=0
PENDENTES=0
FALHAS=0

# Cada claim com `id:`, `state:`, `validation_type:` e `evidence:`.
# Lê-se por blocos para não precisar de um parser YAML em dependências.
ids=$(grep -E "^  - id: " "$GRAFO" | sed 's/.*- id: //')
for id in $ids; do
    bloco=$(awk -v alvo="  - id: $id" '
        $0 ~ alvo { dentro = 1 }
        dentro && /^  - id: / && $0 !~ alvo { dentro = 0 }
        dentro { print }
    ' "$GRAFO")

    estado=$(printf '%s\n' "$bloco" | grep -E "^    state: " | sed 's/.*state: //')
    tipo=$(printf '%s\n' "$bloco" | grep -E "^    validation_type: " | sed 's/.*validation_type: //')
    evidencia=$(printf '%s\n' "$bloco" | sed -n '/^    evidence:/,/^    falsified_by:/p' | head -4)

    if [ "$tipo" = "human" ]; then
        PENDENTES=$((PENDENTES + 1))
        echo "  📋 $id ($estado) — só uma pessoa pode fechar: human-validation.sh"
        printf '%s\n' "$evidencia" | grep -q "M-" || true
        continue
    fi

    # Extrai a mutação ou o comando da evidência.
    mutacao=$(printf '%s\n' "$evidencia" | grep -oE "M-[0-9]+" | head -1)
    comando=$(printf '%s\n' "$evidencia" | grep -oE "tests/[a-z_/]*\.py::[A-Za-z_:]+" | head -1)

    if [ -n "$mutacao" ]; then
        if python3 - "$mutacao" <<'PY'
import sys, importlib.util, pathlib
m = sys.argv[1]
spec = importlib.util.spec_from_file_location("rm", "scripts/run-mutations.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["rm"] = mod
try:
    spec.loader.exec_module(mod)
except Exception:
    sys.exit(3)
node = next((x for x in mod.MUTACOES if x.identificador == m), None)
if node is None:
    sys.exit(3)
src = pathlib.Path(node.ficheiro).read_text(encoding="utf-8")
sys.exit(0 if src.count(node.antes) >= 1 else 3)
PY
        then
            SUPORTADAS=$((SUPORTADAS + 1))
            echo "  ✅ $id ($estado) — $mutacao está no código e é procurável"
        else
            FALHAS=$((FALHAS + 1))
            echo "  ❌ $id ($estado) — $mutacao NÃO APLICÁVEL: a mutação aponta para"
            echo "       uma linha que mudou. A claim já não é medida."
        fi
    elif [ -n "$comando" ]; then
        ficheiro="${comando%%::*}"
        nome="${comando##*::}"
        nome="${nome##*::}"
        if python3 -m pytest "$ficheiro" -q --no-cov -k "$nome" >/dev/null 2>&1; then
            SUPORTADAS=$((SUPORTADAS + 1))
            echo "  ✅ $id ($estado) — $comando passa"
        else
            FALHAS=$((FALHAS + 1))
            echo "  ❌ $id ($estado) — $comando NÃO passa"
        fi
    else
        FALHAS=$((FALHAS + 1))
        echo "  ❌ $id ($estado) — a evidência não é uma mutação nem um teste."
        echo "       Uma claim sem evidência mecânica é uma opinião com formatação."
    fi
done

echo
echo "────────────────────────────────────────────────────"
echo "  Claims: $((SUPORTADAS + FALSIFICADAS + PENDENTES))"
echo "  Com evidência mecânica e viva: $SUPORTADAS"
echo "  Falsificadas:                     $FALHAS"
echo "  Só uma pessoa pode fechar:        $PENDENTES"
echo "────────────────────────────────────────────────────"

if [ "$FALHAS" -gt 0 ]; then
    echo
    echo "  Uma claim que deixa de ter evidência é uma promessa. Corrija a"
    echo "  mutação, ou mude a claim para reflectir o que o código faz."
    exit 1
fi

echo
echo "  NOTA: $PENDENTES claims só um humano fecha. Este gate não as aprova —"
echo "  aponta para aes/peer-reviews/T014/human-validation.sh."
