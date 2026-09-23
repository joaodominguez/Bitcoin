# Bitcoin — Simulador de Gestão de Carteira (`btcsim`)

> ⚠️ **Aviso importante:** isto é uma ferramenta **educativa** que trabalha com
> **dinheiro virtual**. **NÃO é aconselhamento financeiro.** O desempenho passado
> nunca garante resultados futuros. Bitcoin é extremamente volátil — só investe
> dinheiro que estejas disposto a perder e, para decisões reais, fala com um
> profissional certificado.

`btcsim` simula a gestão de uma carteira de criptomoeda (por defeito **Bitcoin**,
mas funciona com **qualquer cripto** — Ethereum, Solana, etc.) com **10 000 €**,
testando várias estratégias de compra/venda sobre **dados históricos reais** e
mostrando qual teria tido melhor desempenho — com métricas de risco e um gráfico.

A ideia central: em vez de tentar "adivinhar" quando comprar e vender, defines uma
**estratégia com regras**, e o simulador mostra-te como ela se teria comportado.

## Funcionalidades

- **Qualquer cripto** via `--coin` (ids da CoinGecko: `bitcoin`, `ethereum`,
  `solana`, ...).
- **Dados reais** do preço via API pública da CoinGecko (até 365 dias,
  o limite do plano gratuito) com cache em disco. Podes também usar o teu próprio
  CSV para históricos mais longos.
- **Intervalo de análise:** **diário** (1 preço de fecho por dia). É a granularidade
  usada para todos os cálculos e sinais; para *backtesting* é a mais robusta.
- **Estratégias incluídas:**
  - `buy_and_hold` — compra tudo no início e mantém (o *benchmark*).
  - `dca` — *Dollar-Cost Averaging*: investe um valor fixo em intervalos regulares.
  - `ma_crossover` — cruzamento de médias móveis (compra/vende nos sinais).
  - `rsi` — compra em sobrevenda (RSI baixo), vende em sobrecompra (RSI alto).
  - `sentiment` — reage a **sentimento de mercado/notícias** (módulo plugável),
    com modo **contrário** opcional (comprar no medo, vender na ganância).
- **Sentimento de mercado real e com histórico** via **Fear & Greed Index**
  (`--news feargreed`): indicador 0–100 que agrega volatilidade, momentum, redes
  sociais e tendências. Gratuito e sem chave.
- **Análise de notícias plugável** (`btcsim/news.py`): índice Fear & Greed, stub
  neutro, análise por palavras-chave a partir do teu CSV de manchetes, ou
  integração com a API da CryptoPanic (sentimento atual, requer `CRYPTOPANIC_TOKEN`).
- **Métricas de desempenho:** retorno total e anualizado, *max drawdown*,
  volatilidade, *Sharpe ratio*, número de trades e comissões pagas.
- **Relatório em texto + gráfico PNG** comparando as estratégias.

## Instalação

```bash
pip install -r requirements.txt
```

## Dashboard interativa (a forma mais fácil de ver)

Arranca a dashboard web e abre no browser:

```bash
python3 -m btcsim.dashboard
# depois abre http://127.0.0.1:8000
```

Na dashboard podes, sem tocar em código:

- escolher a **criptomoeda** (Bitcoin, Ethereum, Solana, ...);
- definir o **capital inicial** (ex.: 10 000 €), a **moeda** e o **período**;
- escolher que **estratégias** comparar (checkboxes);
- escolher a **fonte de sentimento**: nenhuma, **Fear & Greed** (real, do mercado)
  ou notícias de exemplo — com opção de **modo contrário** (comprar no medo,
  vender na ganância);
- ver um **gráfico interativo** das curvas de valor + preço do ativo, os **KPIs**,
  uma **tabela comparativa** (a melhor estratégia fica destacada) e os
  **trades recentes**.

Opções: `--host 0.0.0.0 --port 8080` (útil se quiseres aceder de outra máquina).

![Dashboard](docs/dashboard.png)

## Repartir o capital por várias criptos (alocação / diversificação)

Em vez de pôr os 10 000 € numa só cripto, o `btcsim` **reparte-os da melhor forma
possível** por várias, otimizando a carteira sobre dados históricos:

```bash
# Reparte 10.000 € por BTC/ETH/SOL com o método de máximo Sharpe e rebalanceia mensalmente
python3 -m btcsim --allocate --coins bitcoin,ethereum,solana \
    --method max_sharpe --rebalance-days 30 --chart output/alloc.png
```

Métodos de alocação (`--method`):

- `equal` — peso igual (diversificação ingénua, boa referência).
- `inverse_vol` — **paridade de risco**: mais peso às criptos menos voláteis.
- `min_variance` — minimiza o risco total da carteira (mais defensivo).
- `max_sharpe` — melhor relação risco/retorno histórica.

O `min_variance` e o `max_sharpe` usam **otimização por Monte Carlo** (milhares de
repartições possíveis) e o resultado inclui a **fronteira eficiente**.

Na **dashboard**, o separador *"Repartir capital (alocação)"* mostra a repartição
sugerida (gráfico circular), a curva de valor vs peso igual, a tabela de repartição
e a fronteira eficiente:

![Alocação](docs/allocation.png)

> ⚠️ **Nota importante:** a otimização ajusta os pesos ao **passado**. O passado não
> prevê o futuro e otimizar sobre o histórico tem risco de *overfitting* (parecer
> ótimo no passado e falhar à frente). O `min_variance` e a diversificação simples
> costumam ser mais robustos do que perseguir o retorno máximo. Diversifica sempre
> e nunca ponhas tudo numa única aposta.

## Utilização rápida (linha de comandos)

Gerir 10 000 € durante o último ano com DCA (compra semanal):

```bash
python3 -m btcsim --capital 10000 --currency eur --strategy dca --every-days 7
```

Comparar **todas** as estratégias e gerar um gráfico:

```bash
python3 -m btcsim --compare --chart output/compare.png
```

Outra cripto (ex.: Ethereum):

```bash
python3 -m btcsim --coin ethereum --compare --chart output/eth.png
```

Usar sentimento de mercado real (Fear & Greed) em modo **contrário** — comprar
quando há medo, vender quando há ganância:

```bash
python3 -m btcsim --strategy sentiment --news feargreed --contrarian --sent-threshold 0.4
```

Estratégia de cruzamento de médias móveis (SMA 20/50):

```bash
python3 -m btcsim --strategy ma_crossover --short 20 --long 50
```

Estratégia baseada em RSI:

```bash
python3 -m btcsim --strategy rsi --rsi-window 14 --rsi-low 30 --rsi-high 70
```

Estratégia de sentimento a partir de um CSV de notícias (`date,headline`):

```bash
python3 -m btcsim --strategy sentiment --news keyword \
    --news-csv examples/sample_news.csv --sent-threshold 0.2
```

Usar um histórico próprio (CSV com colunas `date,price`):

```bash
python3 -m btcsim --csv o_meu_historico.csv --compare
```

## Exemplo de resultado

Comparação real dos últimos 365 dias (10 000 €), num período em que o BTC **caiu**:

```
  estrategia  valor_final  retorno_%  max_dd_%  sharpe  trades
         dca     10980.86       9.81    -20.71    0.51      53
   sentiment     10000.00       0.00      0.00    0.00       0
         rsi      9348.16      -6.52    -26.20    0.01       4
ma_crossover      8840.97     -11.59    -27.22   -0.40       9
buy_and_hold      7715.59     -22.77    -51.90   -0.35       1
```

Lição ilustrativa: num mercado em queda, o **DCA** (comprar aos poucos ao longo
do tempo) reduziu bastante o *drawdown* face a comprar tudo de uma vez
(*buy & hold*). Isto **não** significa que o DCA ganha sempre — noutros períodos
o *buy & hold* costuma ganhar. É exatamente para isso que serve o simulador:
testar cenários em vez de adivinhar.

## Uso como biblioteca

```python
from btcsim import data
from btcsim.simulator import Simulator
from btcsim.strategies import DCA

series = data.fetch(days=365, currency="eur")
sim = Simulator(series, initial_cash=10_000.0, fee_rate=0.001)
result = sim.run(DCA(every_days=7))

print(result.metrics.as_dict())
print(result.trades)
```

Ver também `examples/run_demo.py`.

## Sobre "acompanhar notícias/sentimento para decidir"

O sentimento é um **ponto de extensão** (`SentimentProvider` em `btcsim/news.py`).
Todos devolvem uma pontuação diária em `[-1, 1]` (negativo = *bearish*, positivo = *bullish*):

- **`FearGreedProvider`** (`--news feargreed`): índice **Medo & Ganância** do mercado
  cripto (alternative.me). É **real, gratuito e com histórico**, por isso funciona em
  *backtests*. Como é do mercado inteiro, aplica-se a qualquer cripto. Combina bem com
  a estratégia de sentimento em **modo contrário** (`--contrarian`).
- **`KeywordSentimentProvider`**: pontua manchetes que tu forneces (CSV `date,headline`).
- **`CryptoPanicProvider`**: sentimento **atual** via CryptoPanic
  (`export CRYPTOPANIC_TOKEN=...`), útil para apoio à decisão no presente.
- **`NeutralProvider`** (por defeito): sentimento 0, não gera trades.

Podes escrever o teu próprio *provider* (ex.: modelo de NLP sobre um feed de notícias,
outra API) implementando `daily_sentiment(index) -> pd.Series` com valores em `[-1, 1]`.

### Nota sobre notícias em tempo real e intervalos

- **Intervalo de preço:** a análise é **diária**. Em janelas curtas a CoinGecko dá
  dados horários, mas o motor de *backtest* usa diário para manter as métricas
  (volatilidade, Sharpe) corretas e comparáveis.
- **Notícias em texto ao minuto:** não há uma fonte histórica gratuita fiável de
  manchetes ao minuto; por isso, para *backtesting* usa-se o Fear & Greed (histórico)
  e, para o **presente**, podes ligar a CryptoPanic ou o teu próprio feed.

## Testes

```bash
python3 -m pytest -q
```

## Estrutura

```
btcsim/
  data.py         # obtenção/cache de dados de preço
  indicators.py   # SMA, EMA, RSI, MACD
  portfolio.py    # estado da carteira (cash + BTC, trades, comissões)
  strategies.py   # estratégias de compra/venda
  allocation.py   # repartição/otimização de carteira multi-cripto + rebalanceamento
  news.py         # análise de notícias/sentimento (plugável)
  simulator.py    # motor de backtest
  metrics.py      # métricas de desempenho
  report.py       # relatório em texto + gráfico
  cli.py          # interface de linha de comandos
examples/         # CSV de notícias de exemplo + demo
tests/            # testes (pytest)
```

## Isenção de responsabilidade

Este software é fornecido apenas para fins educativos e de investigação. Nada aqui
constitui aconselhamento financeiro, de investimento ou de qualquer outra natureza.
Os autores não se responsabilizam por quaisquer perdas resultantes do uso desta
ferramenta.
