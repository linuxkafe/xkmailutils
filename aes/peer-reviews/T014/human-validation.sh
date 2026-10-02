#!/usr/bin/env bash
# Human Validation Script — T014
#
# Executor: uma pessoa que NÃO é o autor do candidato nem o agente que o
# escreveu. Sem isto executado, o veredicto desta ronda é REJECT por
# PEER_REVIEW.md §6, e não há como contornar.
#
# Data: ____/____/________     Resultado: ____/____/________
#
# Porquê isto, quando o `make check` está verde e quatro personas já correram
# 744 testes, 19 mutações e os E2E:
#
#   Os quatro de nós medimos com as mesmas ferramentas sobre o mesmo código, e
#   medimos com o mesmo instrument. A rubrica `MAILUTILS-T014-v1` provou isso
#   da pior maneira possível — o critério C-11 corre ZERO testes e sai com
#   código 5, porque o `-k` dizia "tecto" e o teste chama-se "teto". Um
#   instrumento não verificado dá um falso-verde. Isto é o oposto de um
#   instrumento: pede olhos.
#
#   Concretamente: o candidato diz que `stack` produz byte a byte o HTML de
#   antes, e ninguém — nem as 744 asserções nem as quatro personas — abriu um
#   email num Thunderbird. Os passos [3] a [6] são os que nenhum script
#   apanha.

set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 1
REPO="$(pwd)"

echo "=============================================================="
echo " T014 — validação humana"
echo " Repo: $REPO"
echo " Rubrica: MAILUTILS-T014-v1   Veredicto das personas: REJECT (x4)"
echo "=============================================================="

# ------------------------------------------------------------------ [1/8]
echo
echo "[1/8] make check num clone limpo"
echo "      (é o gate que o autor diz estar verde; é o que a CI vai correr)"
rm -rf /tmp/t014-clean && git clone -q . /tmp/t014-clean && cd /tmp/t014-clean
git checkout -q aes/sprint-02-temas-listas
if make check > /tmp/t014-check.log 2>&1; then
  echo "      OK — make check passou"
  tail -4 /tmp/t014-check.log | sed 's/^/      /'
else
  echo "      FALHOU — ver /tmp/t014-check.log"
  tail -25 /tmp/t014-check.log | sed 's/^/      /'
fi
cd "$REPO" || exit 1

# ------------------------------------------------------------------ [2/8]
echo
echo "[2/8] A destinatária consegue confirmar a inscrição sem ter conta?"
echo "      B-02, BLOCKER, três personas. Este é o achado mais grave da ronda."
echo
echo "      a) Arrasta o email de confirmação que apareceu no terminal com o"
echo "         backend=console. Procura um endereço http:// ou https:// dentro"
echo "         dele. O candidato diz que não há (verificado: 'http' in te → False)."
echo "         Espera por: um link para /listas/<id>/confirmar."
echo
echo "      b) Copia esse link se existir e abre-o numa janela de browser"
echo "         INCOGNÓGITO, sem sessão nenhuma. Espera ver o formulário"
echo "         'Código de 6 dígitos'."
echo "         Se aparecer a página de login, o B-02 está confirmado: a"
echo "         destinatária não tem conta e portanto nunca chega lá."
echo
echo "      c) Anota o que viste:  com link / sem link ; form / login"
echo "      _: ______________________________________________"

# ------------------------------------------------------------------ [3/8]
echo
echo "[3/8] Alguém consegue ler o email de outra pessoa? (B-04, IDOR)"
echo "      Feed-back de Object Authorisation. Verificado por código:"
echo "      rotas.py chama endereco_da_lista() ANTES de qualquer verificação de"
echo "      dono, e essa query não filtra por user_id."
echo
echo "      a) Entra com o utilizador A, cria uma lista, adiciona ana@exemplo.pt."
echo "      b) Sai. Entra com o utilizador B (uma conta diferente, invite do admin)."
echo "      c) Vai a /listas/1/confirmar?address_id=<o id do ana@exemplo.pt>"
echo "         — o número da lista e o do endereço saem da barra de URL quando"
echo "         passas o rato, ou do HTML da página de detalhe."
echo
echo "      d) Anota: viste 'ana@exemplo.pt' no ecrã?   SIM / NAO"
echo "         _: ______________________________________________"

# ------------------------------------------------------------------ [4/8]
echo
echo "[4/8] As 28 combinações de tema e estrutura num cliente de email (M-10)"
echo "      Sete temas x quatro estruturas. Nenhum script deste projecto abre"
echo "      um email num Thunderbird ou num Outlook — é por isso que este"
echo "      passo existe e não é opcional."
echo
echo "      a) No editor, escolhe 'Compacto' + 'Azul escuro'. Descarrega o .html."
echo "         Abre o ficheiro num browser (é uma aproximação, serve para"
echo "         ajudar a iterar, não para fechar o achado)."
echo
echo "      b) O que exiges OLHAR num cliente real, se tiveres:"
echo "         - 'Compacto' é mesmo mais baixo que 'Vertical'?"
echo "         - 'Com moldura' mostra moldura nos temas claros?"
echo "         - 'Azul escuro' e 'Verde escuro' são legíveis contra o branco?"
echo "         - Alguma combinação parte ao meio numa janela estreita (320 px)?"
echo
echo "      c) Anota por combinação (tema/estrutura): legivel ILEGIVEL, junto com"
echo "      a indentação de cada anomalia que encontrares:"
echo "      _: ______________________________________________"

# ------------------------------------------------------------------ [5/8]
echo
echo "[5/8] A mensagem de erro diz a verdade? (M-08, M-09, M-07)"
echo "      Três findings sobre o que a interface afirma."
echo
echo "      a) Cria uma lista, adiciona 3 endereços, marca 2, pede os códigos."
echo "         Espera ver o número de códigos ENVIADOS. O candidato diz que"
echo "         mostra '0 endereço(s)' sempre (M-08 — o 'n' nunca chega ao"
echo "         template). Anota: mostra ___ ; esperava ___"
echo
echo "      b) Importa um CSV de 30 linhas numa lista que já tem confirmações"
echo "         pendentes. O relatório diz 'importados / rejeitados'. Espera"
echo "         que 'rejeitados' seja o número de linhas que NÃO entraram."
echo "         Anota: importados ___ / rejeitados ___ / linhas no csv ___"
echo
echo "      c) Bate o tecto de confirmações pendentes. A mensagem tem de dizer"
echo "         que o limite é de CONFIRMAÇÕES e dizer o que fazer a seguir"
echo "         (M-09 — hoje diz que a lista ficou cheia, o que é falso)."
echo "         Anota a mensagem literal que viste:"
echo "      _: ______________________________________________"

# ------------------------------------------------------------------ [6/8]
echo
echo "[6/8] Um clique de distância de perder tudo? (m-06)"
echo
echo "      Elimina a lista. Pede confirmação? Se apagar sem perguntar, com a"
echo "      lista e os confirmados dentro, isso é m-06. Anota:"
echo "      _: ______________________________________________"

# ------------------------------------------------------------------ [7/8]
echo
echo "[7/8] A Persona 4 é plausível?"
echo "      Não há resposta certa. O que interessa é registar o que achas, porque"
echo "      a persona foi escrita pelo autor do candidato e não veio de"
echo "      entrevista (ele diz isso em PERSONAS.md). Se a pessoa real for"
echo "      outra, as FR-6/7/8 estão a servir o caso errado."
echo
echo "      A quem lendes em docs/PERSONAS.md: Marta manda uma actualização"
echo "      mensal para ~150 pessoas que pediram para a receber."
echo "      - Chega à conclusão de que o que ela precisa ainda não existe?"
echo "      - O que já está feito resolve-lhe alguma coisa?"
echo "      _: ______________________________________________"

# ------------------------------------------------------------------ [8/8]
echo
echo "[8/8] O README conta a verdade? (m-01)"
echo "      README.md:129 ainda diz 'Não é um cliente de email. Não envia,'"
echo "      e a :131 diz que o envio é do cliente. As duas são falsas desde o"
echo "      commit 074a9fc — a aplicação envia um email de confirmação por SMTP."
echo "      E o README é a primeira coisa que o operador lê, porque o comando"
echo "      de instalação é 'curl … deploy.sh | sudo bash'."
echo "      Anota: o que dirias a alguém que lesse o README e descobrisse que"
echo "      recebeu um email da instalação:"
echo "      _: ______________________________________________"

echo
echo "=============================================================="
echo " Anota aqui o que viste, mesmo que os scripts passem:"
echo
echo "   [1] clone limpo:         PASS / FALHOU / nao fiz"
echo "   [2] confirmacao sem conta: com link / sem link ; form / login"
echo "   [3] IDOR:                SIM (email alheio visivel) / NAO"
echo "   [4] 28 combinacoes:      legiveis / ILEGIVEIS, quais: ______"
echo "   [5] mensagens:           n enviados ___ ; rejeitados ___ de ___ ; erro: ____"
echo "   [6] eliminar sem confirmar: SIM / NAO"
echo "   [7] persona plausivel:   SIM / NAO, porque: ______________"
echo "   [8] README:              mente / nao mente"
echo
echo " Guarda este bloco em aes/peer-reviews/T014/archive/2026-10-02.md"
echo " Sem o bloco preenchido, a ronda nao pode veredicto ACCEPT."
echo "=============================================================="