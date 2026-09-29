# VISION — mailutils

## Problem

Quem envia email profissional a partir de um cliente de desktop vive um problema
oposto ao do spam: tem de produzir conteúdo visualmente apresentável sem nunca
cruzar a linha do que os filtros de spam consideram lixo. A assinatura com
logótipo, contactos e ligações é precisamente o tipo de fragmento HTML que os
filtros penalizam — table layouts, imagens remotas, CSS inline, `<style>` e
`data-URI` mal usados. O utilizador perde tempo a testar no próprio email: se a
assinatura aparecer, recebe-a no spam.

Agravante: cada pessoa solve isto à sua maneira, e a maioria das soluções
encontradas online são precisamente as que piores scores fazem (base64 inline,
`<script>`, tracking pixels). Não existe uma ferramenta que diga *onde* está o
problema.

## Solution

Uma aplicação web self-hosted que gera assinaturas de email **por defeito
compatíveis com filtros de spam**, e que explica *porquê*.

1. O utilizador carrega o logótipo, preenche os campos (nome, cargo, contactos,
   ligações) e escolhe o esquema de cores.
2. A aplicação gera HTML **table-based com estilos inline**, sem `<script>`, sem
   `<iframe>`, sem formulários, sem CSS externo, sem tracking pixels.
3. A aplicação calcula um **score de risco de spam** sobre o output real e mostra
   exactamente que regra foi violada e porque — não um número abstracto.
4. O resultado é copiado directamente para o Thunderbird (ou exportado como
   ficheiro), com instruções de configuração por cliente.

A app tem gestão de utilizadores com **segundo factor por email apenas em
dispositivos novos**, para que o estado da assinatura não fique exposto a quem
encontre a palavra-passe noutro lado.

## Value

- **O utilizador sabe o score antes de enviar.** Deixa de adivinhar.
- **O logótipo não é o vilão.** Servido por URL, não em base64 — o sinal de spam
  mais severo numa assinatura desaparece.
- **Zero decisões erradas por omissão.** As regras comummente aplicadas à pressa
  (base64, scripts, tracking) estão desligadas e o produto diz porquê.
- **Privacidade.** Self-hosted, sem telemetria, sem Letters, sem plano SaaS
  obrigatório. Os logótipos e o texto das assinaturas não saem da máquina.

## Limites honestos

- Nenhuma assinatura é **garantida** à prova de spam. O score é uma heurística
  local; os algoritmos do Gmail/Outlook são caixas-negras e mudam sem aviso.
  O que o produto garante é **não introduzir** padrões de spam conhecidos.
- A compatibilidade é testada contra assinaturas; o resto do email é do cliente
  de email, não nosso.

## Success criteria

| Métrica | Alvo |
|---|---|
| Score de spam do output por omissão | ≤ 10 / 100 |
| Tempo para gerar uma assinatura | < 60 s do login ao output |
| Testes de cobertura | ≥ 80% (`make test-check`) |
| Dependências externas novas | 0 |
