#!/usr/bin/env python3
"""
Lunar Landing Site Navigator - Exportar dados para o app
=========================================================

Etapa 3 do pipeline. Junta, num único arquivo `dados/app_dados.json`:
  - os locais (locais.csv)
  - as efemérides de Sol e Terra (etapa 1: dados/efemerides/<id>.csv)
  - os perfis de horizonte com relevo LOLA (etapa 2: dados/horizontes/<id>.json)

O app (index.html) calcula a visibilidade a partir disso, o que permite
ligar/desligar o relevo e trocar a regra de visibilidade da Terra na hora.

Uso:
    python exportar_app.py                  # mantém o passo original (ex.: 1 h)
    python exportar_app.py --reduzir 3      # 1 amostra a cada 3 (arquivo menor)
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from baixar_efemerides import carregar_locais


def ler_csv(caminho):
    with caminho.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validar(series, locais_ids):
    """Testes de sanidade física, mostrados no app (seção 'Como sabemos que está certo')."""
    testes = []

    def add(id_local, nome, descricao, fn, valor_txt):
        if id_local not in series:
            testes.append({"teste": nome, "descricao": descricao, "status": "nao_rodou",
                           "detalhe": f"local '{id_local}' não incluído"})
            return
        s = series[id_local]
        ok, valor = fn(s)
        testes.append({"teste": nome, "descricao": descricao, "status": "ok" if ok else "falhou",
                       "detalhe": valor_txt(valor)})

    add("polo_sul", "Sol no polo", "No polo sul o Sol nunca sobe mais que ~1,54° (inclinação do eixo lunar).",
        lambda s: (max(s["sol_el"]) < 1.7, max(s["sol_el"])), lambda v: f"máximo encontrado: {v:.2f}°")
    add("polo_sul", "Balanço da Terra", "Vista do polo, a Terra oscila até ~6,7° por causa da libração.",
        lambda s: (5.0 < max(s["terra_el"]) < 8.5, max(s["terra_el"])), lambda v: f"máximo encontrado: {v:.2f}°")
    add("change4", "Lado oculto", "Do local da Chang'e 4, no lado oculto, a Terra nunca aparece.",
        lambda s: (max(s["terra_el"]) < 0, max(s["terra_el"])), lambda v: f"máximo encontrado: {v:.1f}°")
    add("apollo11", "Terra fixa no céu", "Do local da Apollo 11, perto do centro da face visível, a Terra está sempre no céu.",
        lambda s: (min(s["terra_el"]) > 0, min(s["terra_el"])), lambda v: f"mínimo encontrado: {v:.1f}°")
    add("apollo11", "Dia e noite no equador", "Perto do equador lunar, o Sol fica acima do horizonte cerca de metade do tempo.",
        lambda s: (40 < 100 * sum(e > 0 for e in s["sol_el"]) / len(s["sol_el"]) < 60,
                   100 * sum(e > 0 for e in s["sol_el"]) / len(s["sol_el"])),
        lambda v: f"encontrado: {v:.1f}% do tempo")
    return testes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--locais", default="locais.csv")
    ap.add_argument("--dados", default="dados")
    ap.add_argument("--reduzir", type=int, default=1, help="Usar 1 a cada N amostras")
    args = ap.parse_args()

    pasta = Path(args.dados)
    locais_saida, series = [], {}
    inicio_ref, passo_ref, n_ref = None, None, None

    for local in carregar_locais(Path(args.locais)):
        arq = pasta / "efemerides" / f"{local['id']}.csv"
        if not arq.exists():
            print(f"[pulado] {local['nome']}: sem efemérides ({arq})")
            continue
        linhas = ler_csv(arq)[:: args.reduzir]
        tempos = [datetime.strptime(l["tempo_utc"], "%Y-%m-%dT%H:%MZ") for l in linhas]
        passos = {(b - a).total_seconds() / 3600 for a, b in zip(tempos, tempos[1:])}
        if len(passos) != 1:
            print(f"[pulado] {local['nome']}: intervalo de tempo irregular {sorted(passos)[:5]}")
            continue
        passo = passos.pop()
        inicio = linhas[0]["tempo_utc"]
        if inicio_ref is None:
            inicio_ref, passo_ref, n_ref = inicio, passo, len(linhas)
        elif (inicio, passo) != (inicio_ref, passo_ref):
            print(f"[pulado] {local['nome']}: período/passo diferente dos demais locais")
            continue
        n = min(n_ref, len(linhas))
        linhas = linhas[:n]

        series[local["id"]] = {
            "sol_az": [round(float(l["sol_az"]), 2) for l in linhas],
            "sol_el": [round(float(l["sol_el"]), 3) for l in linhas],
            "terra_az": [round(float(l["terra_az"]), 2) for l in linhas],
            "terra_el": [round(float(l["terra_el"]), 3) for l in linhas],
            "irr0": [round(1361.0 / float(l["sol_dist_ua"]) ** 2) for l in linhas],
        }

        horizonte = None
        altura = None
        arq_h = pasta / "horizontes" / f"{local['id']}.json"
        if arq_h.exists():
            h = json.loads(arq_h.read_text(encoding="utf-8"))
            horizonte = {"el": h["horizonte_graus"], "dist_km": h["distancia_bloqueio_km"],
                         "fontes": h.get("fontes_relevo", [])}
            altura = h.get("altura_local_m")
        else:
            print(f"[aviso] {local['nome']}: sem perfil de horizonte; o app usará horizonte plano.")

        locais_saida.append({**local, "altura_local_m": altura, "horizonte": horizonte})
        print(f"[ok] {local['nome']}: {n} amostras" + (" + relevo" if horizonte else ""))

    if not series:
        print("Nada para exportar. Rode baixar_efemerides.py (e calcular_horizonte.py) antes.")
        return 1

    n_final = min(len(s["sol_az"]) for s in series.values())
    for s in series.values():
        for k in s:
            s[k] = s[k][:n_final]

    mapa = None
    arq_mapa = pasta / "mapa_polo.json"
    if arq_mapa.exists():
        mapa = json.loads(arq_mapa.read_text(encoding="utf-8"))
        print(f"[ok] mapa do polo sul incluído (±{mapa['extensao_m']/1000:.0f} km)")
    else:
        print("[aviso] sem dados/mapa_polo.json; rode gerar_mapa.py para ter o mapa de relevo no app.")

    validacao = validar(series, [l["id"] for l in locais_saida])
    for t in validacao:
        print(f"[validação] {t['status']:>9}  {t['teste']}: {t['detalhe']}")

    saida = pasta / "app_dados.json"
    saida.write_text(json.dumps({
        "meta": {
            "demo": False,
            "fonte": "NASA/JPL Horizons (efemérides) + LRO LOLA (relevo)",
            "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "inicio_utc": inicio_ref,
            "passo_horas": passo_ref,
            "amostras": n_final,
            "mapa": mapa,
            "validacao": validacao,
        },
        "locais": locais_saida,
        "series": series,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\nPronto: {saida} ({saida.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
