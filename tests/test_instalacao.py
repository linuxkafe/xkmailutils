"""A instalação de um comando tem de ser previsível.

`deploy.sh` é o ficheiro que alguém corre com `sudo` a partir de um URL. Não
tem testes de unidade no sentido habitual — é bash — mas tem invariantes que
valem a pena fixar, e este ficheiro fixa-as por texto.

O que se fixou aqui foi um defeito real: a porta era `8080` fixa, que é
exactamente a porta que meia dúzia de projectos auto-hospedados usa, e o
objectivo de servir numa porta alta era o de não chocar com nada. Pior: se
`8080` estivesse ocupada, o contentor não arrancava e a mensagem era do docker,
não do script.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
DEPLOY = RAIZ / "deploy.sh"
COMPOSE = RAIZ / "docker-compose.yml"
README = RAIZ / "README.md"
PROIBIDAS = {"8080", "80", "443", "3000", "5000", "8000"}


@pytest.fixture(scope="module")
def deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


class TestAPortaNaoE8080:
    def test_a_predefinicao_nao_e_8080(self, deploy: str) -> None:
        """A porta por omissão é escolhida, não fixada.

        `PORTA=""` e a procura é feita depois. Se alguém voltar a escrever
        `PORTA="${XKMAILUTILS_PORTA:-8080}"`, esta falha e diz porquê.
        """
        assert 'PORTA="${XKMAILUTILS_PORTA:-}"' in deploy, (
            "a porta voltou a ter um valor fixo por omissão. A procura por uma "
            "porta livre é o que evita chocar com outro serviço."
        )
        assert ":8080" not in deploy, "o 8080 voltou ao script de instalação"

    def test_nenhuma_candidata_e_uma_porta_proibida(self, deploy: str) -> None:
        candidatas = re.search(r'PORTAS_CANDIDATAS="([^"]+)"', deploy)
        assert candidatas, "não há lista de portas candidatas"
        lista = candidatas.group(1).split()
        assert lista, "a lista de candidatas está vazia"
        intersecao = PROIBIDAS & set(lista)
        assert not intersecao, f"portas que vão chocar com serviços comuns: {intersecao}"
        assert len(set(lista)) == len(lista), "há candidatas repetidas"

    def test_o_compose_tem_a_mesma_predefinicao(self) -> None:
        """O compose e o script têm de concordar na última recurso.

        O compose é lido primeiro para todas as variáveis, mesmo nos perfis que
        não vão arrancar. Se a predefinição divergir, o contentor arranca na
        porta errada e a URL que o utilizador vê não é a que está a responder.
        """
        compose = COMPOSE.read_text(encoding="utf-8")
        assert re.search(r"XKMAILUTILS_PORTA:-(\d+)", compose), "o compose não tem predefinição"
        do_compose = re.search(r"XKMAILUTILS_PORTA:-(\d+)", compose).group(1)
        do_script = re.search(
            r'PORTAS_CANDIDATAS="([0-9]+)', DEPLOY.read_text(encoding="utf-8")
        ).group(1)
        assert do_compose == do_script, (
            f"o compose assume {do_compose} e o script prefere {do_script}: "
            f"sem `--porta`, o contentor arrancaria noutra porta"
        )


class TestAProcuraDePortaFunciona:
    """As funções do bash, executadas de facto. Não uma descrição delas."""

    @staticmethod
    def _funcoes() -> str:
        """As funções de porta do `deploy.sh`, tal como estão escritas.

        Extraídas por expressão regular para que o teste não dependa de
        posições no ficheiro: mexer no topo do script não pode partir um teste
        por um motivo que nada tem a ver com a porta.
        """
        texto = DEPLOY.read_text(encoding="utf-8")
        blocos = re.findall(r"^(?:porta_ocupada|escolher_porta)\(\) \{.*?^\}", texto, re.M | re.S)
        assert len(blocos) == 2, (
            f"esperava as duas funções de porta em `deploy.sh`, achei {len(blocos)}. "
            f"A procura de porta livre é o que substituiu o 8080 fixo."
        )
        return "\n\n".join(blocos)

    def _script(self, corpo: str) -> str:
        return (
            "set -euo pipefail\n"
            + self._funcoes()
            + '\nPORTAS_CANDIDATAS="8642 8643 8644"\n'
            + corpo
        )

    def _correr(self, corpo: str) -> str:
        feito = subprocess.run(  # noqa: S603, S607
            ["/bin/bash", "-c", self._script(corpo)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return feito.stdout.strip()

    def test_escolhe_a_primeira_livre(self) -> None:
        assert self._correr("escolher_porta") == "8642"

    def test_salta_as_ocupadas(self) -> None:
        """Com 8642 e 8643 a escutar, tem de escolher a 8644.

        É este teste que prova que a procura existe e não é decorativa: a
        versão anterior do script tinha 8080 fixo e não escolhia nada.
        """
        import socket

        escutas = []
        for porta in (8642, 8643):
            s = socket.socket()
            s.bind(("127.0.0.1", porta))
            s.listen(1)
            escutas.append(s)
        try:
            assert self._correr("escolher_porta") == "8644"
        finally:
            for s in escutas:
                s.close()

    def test_falha_quando_nada_esta_livre(self) -> None:
        """A última receita: se a lista inteira estiver ocupada, falhar.

        Falhar com a lista é o que dá à pessoa a informação para escolher
        outra com `--porta`. Devolver a última candidata a occupied seria
        contentar-se com um contentor que não arranca.
        """
        import socket

        escuta = socket.socket()
        escuta.bind(("127.0.0.1", 8642))
        escuta.listen(1)
        try:
            assert (
                self._correr('PORTAS_CANDIDATAS="8642"; escolher_porta || printf "nenhuma"')
                == "nenhuma"
            )
        finally:
            escuta.close()


class TestOPreVooDoDns:
    """O contentor de build resolve nomes?

    Um `git clone` a funcionar não prova nada disto: o clone corre **no host**
    e o `pip install` corre **dentro do contentor**. Num servidor com
    `systemd-resolved` — o omisso em Debian e Ubuntu desde 2018 — o contentor
    herda `nameserver 127.0.0.53`, e esse stub só escuta no loopback do host.
    Lá dentro o `pip` falha com `Temporary failure in name resolution` ao fim de
    quatro tentativas e mais de seis minutos de espera.

    Estes testes correm a lógica com um `docker` falso, para a decisão ser
    verificável sem uma máquina com o problema.
    """

    DOCKER_FALSO = """#!/bin/sh
# `docker` falso: responde como o contentor que não resolve.
case "$*" in
  *"--network=host"*) exit 0 ;;   # com a rede do host, resolve
  *"getent hosts pypi.org"*) exit 1 ;;  # sem, não resolve
esac
exit 0
"""

    @staticmethod
    def _logica() -> str:
        texto = DEPLOY.read_text(encoding="utf-8")
        achado = re.findall(
            r"^(?:build_resolve_dns|build_sem_dns|dns_do_build)\(\) \{.*?^\}",
            texto,
            re.M | re.S,
        )
        assert len(achado) == 3, f"esperava as três funções de DNS, achei {len(achado)}"
        return "\n\n".join(achado)

    def _correr(self, dns: str) -> str:
        import tempfile

        with tempfile.TemporaryDirectory() as pasta:
            binpath = Path(pasta) / "bin"
            binpath.mkdir()
            falso = binpath / "docker"
            falso.write_text(self.DOCKER_FALSO, encoding="utf-8")
            falso.chmod(0o755)
            script = (
                "set -euo pipefail\n"
                'IMAGEM_BUILD="img"\n'
                + self._logica()
                + '\nif dns_do_build; then printf "normal"; else printf "network-host"; fi\n'
            )
            feito = subprocess.run(  # noqa: S603, S607
                ["/bin/bash", "-c", script],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env={"PATH": f"{binpath}:/usr/bin:/bin", "HOME": pasta},
            )
            return feito.stdout.strip()

    def test_detecta_que_o_contentor_nao_resolve(self) -> None:
        assert self._correr("sem-dns") == "network-host", (
            "com o contentor sem DNS o script tem de escolher --network=host. "
            "Se isto deixar de ser verdade, volta a falhar aos 6 minutos."
        )

    def test_nao_toca_em_nada_quando_o_dns_ja_funciona(self) -> None:
        """Num contentor que resolve, o build tem de ser o de sempre.

        Mudar o build para `--network=host` por omissão seria resolver um
        problema de uma máquina à custa de todas as outras, e sem o ninguém dar
        conta. A regra é o contrário: só se mexe quando o DNS está quebrado.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as pasta:
            binpath = Path(pasta) / "bin"
            binpath.mkdir()
            falso = binpath / "docker"
            # Aqui o contentor resolve sempre.
            falso.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            falso.chmod(0o755)
            script = (
                "set -euo pipefail\n"
                'IMAGEM_BUILD="img"\n'
                + self._logica()
                + '\nif dns_do_build; then printf "normal"; else printf "network-host"; fi\n'
            )
            feito = subprocess.run(  # noqa: S603, S607
                ["/bin/bash", "-c", script],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env={"PATH": f"{binpath}:/usr/bin:/bin", "HOME": pasta},
            )
            assert feito.stdout.strip() == "normal"

    def test_a_explicacao_diz_o_que_se_passou(self) -> None:
        """O aviso tem de dizer a causa, não o sintoma.

        Um aviso que só diz «a rede falhou» obriga a ir procurar. Este diz que o
        host resolve e o contentor não, e porque: o stub do systemd-resolved
        só escuta no host.
        """
        deploy = DEPLOY.read_text(encoding="utf-8")
        assert "systemd-resolved" in deploy
        assert "127.0.0.53" in deploy, "o aviso tem de nomear a linha que causa isto"
        assert "--network=host" in deploy

    def test_o_build_normal_ainda_existe(self) -> None:
        """O caminho comum não pode ter sido substituído pelo de recurso.

        Se alguém apagar o `docker compose build` normal e deixar só o
        `--network=host`, toda a gente passa a construir com a rede do host sem
        o saber, e o teste acima deixa de estar a testar o que pensa.
        """
        deploy = DEPLOY.read_text(encoding="utf-8")
        assert "docker compose build" in deploy, (
            "o build normal sumiu. O `--network=host` é o plano B, para contentores "
            "sem DNS — não o plano único."
        )

    def test_a_saida_nao_reconstroi_a_imagem(self) -> None:
        """A imagem foi construída à mão; o compose não a constrói outra vez.

        Sem isto, o script constrói com `--network=host` e a seguir o compose
        volta a construir sem rede, e a falha é a mesma — com a deceptive
        confirmação de que a primeira passagem tinha funcionado.
        """
        deploy = DEPLOY.read_text(encoding="utf-8")
        assert "ARRANQUE=(--no-build)" in deploy
        assert 'ARRANQUE[@]+"${ARRANQUE[@]}"' in deploy, (
            "o `--no-build` não está a chegar ao `docker compose up`"
        )
