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

## A segunda ferramenta — compor e enviar

A mesma instalação sabe também **escrever e enviar email para listas de
destinatários**. Não é um cliente de email: não recebe, não sincroniza, não tem
caixas de entrada. Envia, e envia apenas para listas que o próprio utilizador
construiu, onde **cada endereço confirmou a sua presença por código único**.

3. O utilizador escreve assunto e corpo, escolhe uma lista e vê **o mesmo score
   de spam** que vê na assinatura — o mesmo motor, as mesmas regras nomeadas.
4. Manda agora ou agenda. O envio passa pelo mesmo `spam.py` e é bloqueado se
   for `CRÍTICO`, pela mesma política que bloqueia a exportação da assinatura.

### Porquê isto não é uma ferramenta de spam

O risco óbvio de um produto que avalia spam é virar spam. Três decisões fecham
esse caminho, e as três são código, não intenção:

- **A lista é de quem a construiu.** Não há campo de destinatário livre, não há
  BCC escrito à mão. Um endereço entra no `SELECT` de destinatários só depois de
  `confirmed_at IS NOT NULL` — nenhum caminho, nem imediato, nem agendado, nem
  reexecução.
- **A aplicação não envia o que reprovaria.** O unifying invariant: o email que
  sai passa pelo mesmo `spam.py` que avalia a assinatura. Um produto que ensina
  a não parecer spam não pode ser usado para parecer spam.
- **Há descadência e há um remetente identificável.** `List-Unsubscribe` com
  one-click, e o envio é recusado sem endereço postal do remetente.

O que isto **não** resolve: reputação de domínio. O score é heurístico e mede
conteúdo, não comportamento. Duzentos emails para quem pediu é diferente de
duzentos mil, e o produto não sabe a diferença — sabe é que ninguém foi
confirmado por engano.

## Value

- **O utilizador sabe o score antes de enviar.** Deixa de adivinhar.
- **O logótipo não é o vilão.** Servido por URL, não em base64 — o sinal de spam
  mais severo numa assinatura desaparece.
- **Zero decisões erradas por omissão.** As regras comummente aplicadas à pressa
  (base64, scripts, tracking) estão desligadas e o produto diz porquê.
- **Privacidade.** Self-hosted, sem telemetria, sem Letters, sem plano SaaS
  obrigatório. Os logótipos e o texto das assinaturas não saem da máquina.
- **Quem não pediu, não recebe.** A confirmação por código é a diferença entre
  uma lista de contactos e uma lista de spam, e está no esquema, não na
  documentação.

## Limites honestos

- Nenhuma assinatura é **garantida** à prova de spam. O score é uma heurística
  local; os algoritmos do Gmail/Outlook são caixas-negras e mudam sem aviso.
  O que o produto garante é **não introduzir** padrões de spam conhecidos.
- A compatibilidade é testada contra assinaturas; o resto do email é do cliente
  de email, não nosso.
- **O score não é garantia de entrega nem de reputação.** Um score de 0 é um
  score baixo, não um email entregue. O produto não conhece o histórico do
  domínio de quem envia, e é o histórico que os filtros do Gmail olham primeiro.
- **A conformidade legal é do owner.** O produto exige remetente identificável
  e descadência funcional, mas a suficiência jurídica disto varia por jurisdição.
  Quem opera responde por isso. Ver NFR-17.

## Success criteria

| Métrica | Alvo |
|---|---|
| Score de spam do output por omissão | ≤ 10 / 100 |
| Tempo para gerar uma assinatura | < 60 s do login ao output |
| Score de spam do email enviado | ≤ 10 / 100 sem assinatura anexada |
| Destinatários com `confirmed_at IS NULL` num envio | **0** — sem excepção, em nenhum caminho |
| Testes de cobertura | ≥ 80% (`make test-check`) |
| Dependências externas novas | 0 |

A terceira linha e a quarta são as que distinguem esta aplicação de um
disparador de email. A quarta é verificável com uma mutação que troque
`confirmed_at IS NOT NULL` por `1=1`: se o teste não morrer, a métrica está a
ser afirmada e não provada.
