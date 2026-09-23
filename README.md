# Bitcoin — Simulador de Gestão de Carteira (`btcsim`)

> ⚠️ **Aviso importante:** isto é uma ferramenta **educativa** que trabalha com
> **dinheiro virtual**. **NÃO é aconselhamento financeiro.** O desempenho passado
> nunca garante resultados futuros. Bitcoin é extremamente volátil — só investe
> dinheiro que estejas disposto a perder e, para decisões reais, fala com um
> profissional certificado.

`btcsim` simula a gestão de uma carteira de Bitcoin (por defeito **10 000 €**),
testando várias estratégias de compra/venda sobre **dados históricos reais** e
mostrando qual teria tido melhor desempenho — com métricas de risco e um gráfico.

A ideia central: em vez de tentar "adivinhar" quando comprar e vender, defines uma
**estratégia com regras**, e o simulador mostra-te como ela se teria comportado.

## Funcionalidades

- **Dados reais** do preço do Bitcoin via API pública da CoinGecko (até 365 dias,
  o limite do plano gratuito) com cache em disco. Podes também usar o teu próprio
  CSV para históricos mais longos.
- **Estratégias incluídas:**
  - `buy_and_hold` — compra tudo no início e mantém (o *benchmark*).
  - `dca` — *Dollar-Cost Averaging*: investe um valor fixo em intervalos regulares.
  - `ma_crossover` — cruzamento de médias móveis (compra/vende nos sinais).
  - `rsi` — compra em sobrevenda (RSI baixo), vende em sobrecompra (RSI alto).
  - `sentiment` — reage a **sentimento de notícias** (módulo plugável).
- **Análise de notícias plugável** (`btcsim/news.py`): stub neutro por defeito,
  análise por palavras-chave a partir do teu CSV de manchetes, ou integração com
  a API da CryptoPanic (para sentimento atual, requer `CRYPTOPANIC_TOKEN`).
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

- definir o **capital inicial** (ex.: 10 000 €), a **moeda** e o **período**;
- escolher que **estratégias** comparar (checkboxes);
- ligar as **notícias de exemplo** para ativar a estratégia de sentimento;
- ver um **gráfico interativo** das curvas de valor + preço do BTC, os **KPIs**,
  uma **tabela comparativa** (a melhor estratégia fica destacada) e os
  **trades recentes**.

Opções: `--host 0.0.0.0 --port 8080` (útil se quiseres aceder de outra máquina).

![Dashboard](docs/dashboard.png)

## Utilização rápida (linha de comandos)

Gerir 10 000 € durante o último ano com DCA (compra semanal):

```bash
python3 -m btcsim --capital 10000 --currency eur --strategy dca --every-days 7
```

Comparar **todas** as estratégias e gerar um gráfico:

```bash
python3 -m btcsim --compare --chart output/compare.png
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

## Sobre "ver notícias para decidir"

Notícias históricas dia-a-dia não estão disponíveis de forma gratuita/fiável, por
isso o sentimento é um **ponto de extensão** (`SentimentProvider` em
`btcsim/news.py`):

- **`NeutralProvider`** (por defeito): sentimento 0, não gera trades.
- **`KeywordSentimentProvider`**: pontua manchetes que tu forneces (CSV).
- **`CryptoPanicProvider`**: sentimento **atual** via CryptoPanic
  (`export CRYPTOPANIC_TOKEN=...`), útil para apoio à decisão no presente.

Podes escrever o teu próprio *provider* (ex.: modelo de NLP, outra API) implementando
`daily_sentiment(index) -> pd.Series` com valores em `[-1, 1]`.

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
