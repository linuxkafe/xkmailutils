#!/usr/bin/env bash
# Human Validation Script — T008
#
# Executor: uma pessoa que NÃO é o autor do candidato (nem o agente que o
# escreveu, nem o dono do projecto). Sem isto executado, o veredicto desta
# ronda é REJECT por PEER_REVIEW.md §6, e não há como contornar.
#
# Data: ____/____/________     Resultado: ____/____/________
#
# Porquê isto e não confiar nos 508 testes: o autor e as quatro personas de
# revisão mediram com as mesmas ferramentas. A mitigação real de
# PEER_REVIEW.md §10.1 é alguém, de fora, olhar para o ecrã.

set -uo pipefail
cd "$(dirname "$0")/../../.." || exit 1

echo "=============================================================="
echo " T008 — validação humana"
echo " Repo: $(pwd)"
echo "=============================================================="

echo
echo "[1/6] make check num clone limpo"
echo "      (é o gate que o autor diz estar verde; e o que a CI vai correr)"
rm -rf /tmp/t008-clean && git clone -q . /tmp/t008-clean && cd /tmp/t008-clean
git checkout -q aes/t008-playwright-e2e
if make check > /tmp/t008-check.log 2>&1; then
  echo "      OK — make check passou"
  tail -6 /tmp/t008-check.log | sed 's/^/      /'
else
  echo "      FALHOU — ver /tmp/t008-check.log"
  tail -25 /tmp/t008-check.log | sed 's/^/      /'
fi

echo
echo "[2/6] A CIinstala o browser? (finding F-03, BLOCKER)"
echo "      Se a resposta for não, a CI está vermelha no primeiro run."
if grep -rq "playwright install" .github/ .gitlab-ci.yml .woodpecker.yml Makefile 2>/dev/null; then
  echo "      OK — há um passo de instalação do browser"
else
  echo "      FALHA — nenhum 'playwright install' em .github/, .gitlab-ci.yml,"
  echo "             .woodpecker.yml ou Makefile. A CI faz 'make setup' e depois"
  echo "             'make check', e 'make check' exige o Chromium."
fi

echo
echo "[3/6] A assinatura por omissão é legível? (finding F-02, BLOCKER)"
echo "      Isto exige OLHAR. É o finding que nenhum teste apanha."
cat > /tmp/t008-legibilidade.py <<'PY'
import sys
sys.path.insert(0, "src")
from mailutils.signatures import renderer

def lum(hexa):
    h = hexa.lstrip("#")
    c = [int(h[i:i+2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

def razao(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

from mailutils.config import load_settings
settings = load_settings()
dados = renderer.build_signature_data(
    {"name": "Alexandra Ferreira", "role": "Técnica de TI", "company": "Exemplo, Lda."},
    settings,
)
html = renderer.render_html(dados, settings)
falha = False
for chave, tema in renderer.THEMES.items():
    fundo = "#ffffff"   # o fundo real de um Thunderbird/Outlook
    r = razao(tema.text, fundo)
    marca = "OK " if r >= 4.5 else "BAIXO"
    if r < 4.5:
        falha = True
    print(f"      {marca} {chave:5} texto {tema.text} vs branco: {r:.1f}:1")
print(f"      o fragmento declara background? {'SIM' if 'background' in html else 'NAO'}")
if falha:
    print("      FALHA: um tema nao passa 4.5:1 contra o fundo branco do cliente.")
    print("             Com texto branco e sem background, a assinatura e invisivel.")
else:
    print("      OK")
PY
python3 /tmp/t008-legibilidade.py

echo
echo "[4/6] O preview mostra a assinatura? (finding F-04, MAJOR)"
echo "      Arranca a aplicacao e OLHA para o painel de pre-visualizacao."
echo "      Espera: letra do browser (serifado), texto preto, sem cores, e"
echo "      acentos escritos TÃ©cnica em vez de Técnica."
echo "      Ver o ficheiro exportado: abre assinatura.html num browser. Se sair"
echo "      sem estilos nem fundo, e o finding F-08."
echo "      (encaminhar a quem quiser fazer esta tarefa; exige um browser e olhos)"

echo
echo "[5/6] O utilizador consegue mudar de tema? (finding F-01, BLOCKER)"
echo "      Sem abrir as devtools. Sozinha, sem ler o codigo, sem cookies à mao."
echo "      Procura um botao de tema no cabecalho, no perfil, no editor."
echo "      No editor, o botao 'Claro' que existe é o tema DA ASSINATURA, nao da"
echo "      aplicacao — o proprio cartao avisa disso. Confirmar se e claro para"
echo "      alguem que nao le codigo."

echo
echo "[6/6] O editor vazio promete que a assinatura e segura? (finding F-12, MAJOR)"
echo "      Abre a aplicacao, faz login, e OLHA o editor sem escrever nada."
echo "      Espera ver: '0 / 100', selo verde SEGURO, e 'Creditos: COMPACT (-4)'."
echo "      Para uma assinatura que nao existe ainda."

echo
echo "=============================================================="
echo " Anota aqui o que viste, mesmo que os scripts passem:"
echo
echo "   [1] clone limpo:      PASS / FALHOU / nao fiz"
echo "   [2] CI instala browser: SIM / NAO"
echo "   [3] assinatura legivel:  legivel / ILEGIVEL, tema: ______"
echo "   [4] preview estilizado:  SIM / NAO, acentos: OK / PARTIDOS"
echo "   [5] tema por clique:    SIM / NAO"
echo "   [6] editor vazio:        selo verde: SIM / NAO"
echo
echo " Anexa este bloco ao synthesis.md e guarda em"
echo " aes/peer-reviews/T008/archive/. Sem o bloco preenchido, a ronda"
echo " nao pode veredicto ACCEPT."
echo "=============================================================="
