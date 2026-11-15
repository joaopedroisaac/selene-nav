# Selene Nav — NASA Space Apps 2026

*English version: [README.md](README.md)*

Projeto para o desafio **Lunar Landing Site Navigator**.

Compare locais e datas de pouso no polo sul da Lua: quando há **sol para energia** e quando a **Terra está à vista para comunicação**, considerando o relevo real.

Não é um produto oficial da NASA.

## Arquivos

| Arquivo | O que faz |
|---|---|
| `locais.csv` | Lista de locais (edite à vontade). Longitude em graus leste; latitude sul negativa. |
| `baixar_efemerides.py` | Etapa 1: baixa do JPL Horizons a posição do Sol e da Terra vista de cada local. |
| `calcular_horizonte.py` | Etapa 2: calcula o horizonte real (relevo LOLA) e aplica às efemérides. |
| `gerar_mapa.py` | Etapa 3: gera o mapa sombreado do polo sul a partir do mesmo arquivo LOLA. |
| `exportar_app.py` | Etapa 4: junta tudo em `dados/app_dados.json` e roda os testes de validação. |
| `index.html` | O app. Abre em modo demonstração; carrega dados reais pelo botão ou automaticamente se servido junto da pasta `dados/`. |

## Passo a passo

Requisitos: Python 3.10+ e numpy. Para relevo em GeoTIFF, também `pip install rasterio pyproj`.

```bash
# 0. Teste rápido do Horizons (30 dias)
python baixar_efemerides.py --teste

# 1. Efemérides completas (2027–2028, de hora em hora)
python baixar_efemerides.py

# 2. Horizonte real com relevo LOLA (polo sul, formato PDS .LBL/.IMG)
python calcular_horizonte.py --dem dem/LDEM_85S_20M.LBL

# 3. Mapa do polo sul
python gerar_mapa.py --dem dem/LDEM_85S_20M.LBL

# 4. Exportar para o app (mostra os testes de validação no terminal)
python exportar_app.py

# 5. Abrir o app com os dados
python -m http.server 8000      # e abra http://localhost:8000
```

## Antes de apresentar, confira

- [ ] Os testes de validação do `exportar_app.py` passaram (Sol no polo, Terra no lado oculto etc.).
- [ ] A altitude de cada local mostrada pelo `calcular_horizonte.py` bate com o Quickmap.
- [ ] A % de tempo com sol dos locais polares bate com o mapa de iluminação média do Quickmap.
- [ ] As coordenadas marcadas como "Referência aproximada" (regiões Artemis, missões recentes) foram conferidas no Quickmap.

## Fontes de dados

- NASA/JPL Horizons — https://ssd.jpl.nasa.gov/horizons/
- LRO LOLA (relevo) — PDS Geosciences Node e NASA PGDA
- LROC Quickmap — https://quickmap.lroc.asu.edu/
- NASA Moon Trek — https://trek.nasa.gov/moon/
- CSA Moon Exploration — https://www.asc-csa.gc.ca/eng/astronomy/moon-exploration/
