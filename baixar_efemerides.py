#!/usr/bin/env python3
"""
Lunar Landing Site Navigator - Download de efemérides (JPL Horizons)
=====================================================================

Para cada local de pouso listado em `locais.csv`, consulta a API do
JPL Horizons e baixa, hora a hora, a posição APARENTE do Sol e da Terra
vista daquele ponto da superfície lunar:

    - azimute e elevação (graus)
    - diâmetro angular (para tratar o Sol como disco, não como ponto)
    - distância do Sol (para estimar a irradiância em W/m²)

Também calcula métricas-resumo considerando um HORIZONTE PLANO (Lua lisa).
O relevo real (LOLA) entra numa etapa seguinte: estes arquivos são a base
sobre a qual o perfil de horizonte será aplicado.

Requisitos: só Python 3.8+ (biblioteca padrão, nada para instalar).

Uso:
    python baixar_efemerides.py                       # período padrão (2027-2028)
    python baixar_efemerides.py --teste               # 30 dias, para conferir rápido
    python baixar_efemerides.py --inicio 2027-03-01 --fim 2027-06-01 --passo "10 m"

Saída (pasta ./dados):
    dados/locais.json              lista de locais usada
    dados/efemerides/<id>.csv      série temporal por local
    dados/resumo.json              métricas por local (horizonte plano)
    dados/metadados.json           parâmetros exatos das consultas (reprodutibilidade)
"""

import argparse
import csv
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
LUA = "301"                  # código Horizons da Lua (corpo onde está o observador)
ALVOS = {"sol": "10", "terra": "399"}
CONSTANTE_SOLAR = 1361.0     # W/m² a 1 UA
PAUSA_ENTRE_CONSULTAS = 1.0  # segundos, para não sobrecarregar a API

# Locais de exemplo. As coordenadas polares e de missões recentes são
# aproximadas: confiram no Quickmap antes de usar na apresentação.
# Longitude: graus LESTE. Latitude: negativa = hemisfério sul.
LOCAIS_PADRAO = [
    # id, nome, lat, lon_leste, alt_km, categoria, observacao
    ("polo_sul", "Polo Sul geográfico", -89.999, 0.0, 0.0, "referencia",
     "Teste: Sol nunca passa de ~1,5°"),
    ("shackleton", "Cratera Shackleton (centro)", -89.67, 129.78, 0.0, "polo sul",
     "Fundo da cratera: com relevo, sombra permanente"),
    # Regiões candidatas do Artemis (NASA, out/2024). A NASA não publicou coordenadas exatas:
    # estes pontos são REFERÊNCIAS APROXIMADAS das feições que dão nome às regiões. Confiram no Quickmap.
    ("art_cabeus_b", "Artemis: Pico perto de Cabeus B", -82.3, 306.0, 0.0, "Artemis (regiões candidatas)", "Referência aproximada - verificar"),
    ("art_haworth", "Artemis: Haworth", -87.45, 355.0, 0.0, "Artemis (regiões candidatas)", "Referência aproximada (cratera) - verificar"),
    ("art_malapert", "Artemis: Maciço Malapert", -86.0, 2.9, 0.0, "Artemis (regiões candidatas)", "Referência aproximada - verificar"),
    ("art_mouton_plat", "Artemis: Planalto Mons Mouton", -85.4, 35.0, 0.0, "Artemis (regiões candidatas)", "Referência aproximada - verificar"),
    ("art_mouton", "Artemis: Mons Mouton", -84.6, 31.3, 0.0, "Artemis (regiões candidatas)", "Referência aproximada - verificar"),
    ("art_nobile1", "Artemis: Borda de Nobile 1", -84.8, 48.0, 0.0, "Artemis (regiões candidatas)", "Referência aproximada - verificar"),
    ("art_nobile2", "Artemis: Borda de Nobile 2", -85.6, 60.0, 0.0, "Artemis (regiões candidatas)", "Referência aproximada - verificar"),
    ("art_gerlache2", "Artemis: Borda de de Gerlache 2", -88.3, 290.0, 0.0, "Artemis (regiões candidatas)", "Referência aproximada - verificar"),
    ("art_slater", "Artemis: Planície Slater", -88.0, 250.0, 0.0, "Artemis (regiões candidatas)", "Referência aproximada - verificar"),
    ("im2_athena", "IM-2 Athena (Mons Mouton)", -84.79, 29.19, 0.0, "comercial/CLPS",
     "Coordenada aproximada - verificar"),
    ("im1_odysseus", "IM-1 Odysseus (Malapert A)", -80.13, 1.44, 0.0, "comercial/CLPS",
     "Coordenada aproximada - verificar"),
    ("chandrayaan3", "Chandrayaan-3 Vikram (Índia)", -69.373, 32.319, 0.0, "outros países",
     ""),
    ("change4", "Chang'e 4 (China, lado oculto)", -45.4446, 177.5991, 0.0, "outros países",
     "Teste: Terra nunca visível"),
    ("blue_ghost1", "Blue Ghost M1 (Mare Crisium)", 18.56, 61.81, 0.0, "comercial/CLPS",
     "Coordenada aproximada - verificar"),
    ("apollo11", "Apollo 11", 0.6741, 23.4730, 0.0, "Apollo",
     "Teste: Terra sempre visível"),
    ("apollo15", "Apollo 15", 26.1322, 3.6339, 0.0, "Apollo", ""),
    ("apollo17", "Apollo 17", 20.1908, 30.7717, 0.0, "Apollo", ""),
]


# --------------------------------------------------------------------------
# Locais
# --------------------------------------------------------------------------

def carregar_locais(caminho: Path):
    """Lê locais.csv; se não existir, cria um com os locais de exemplo."""
    if not caminho.exists():
        with caminho.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "nome", "lat", "lon_leste", "alt_km", "categoria", "observacao"])
            w.writerows(LOCAIS_PADRAO)
        print(f"[info] {caminho} não existia; criei com {len(LOCAIS_PADRAO)} locais de exemplo.")

    locais = []
    with caminho.open(encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            locais.append({
                "id": linha["id"].strip(),
                "nome": linha["nome"].strip(),
                "lat": float(linha["lat"]),
                "lon_leste": float(linha["lon_leste"]) % 360.0,  # Horizons: 0-360 leste
                "alt_km": float(linha.get("alt_km") or 0.0),
                "categoria": (linha.get("categoria") or "").strip(),
                "observacao": (linha.get("observacao") or "").strip(),
            })
    return locais


# --------------------------------------------------------------------------
# Consulta ao Horizons
# --------------------------------------------------------------------------

def montar_parametros(alvo, local, inicio, fim, passo):
    """Parâmetros da API. As aspas simples fazem parte do formato do Horizons."""
    return {
        "format": "json",
        "COMMAND": f"'{alvo}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'OBSERVER'",
        "CENTER": f"'coord@{LUA}'",
        "COORD_TYPE": "'GEODETIC'",
        # E-longitude (graus), latitude (graus), altitude (km) sobre a esfera de referência
        "SITE_COORD": f"'{local['lon_leste']:.6f},{local['lat']:.6f},{local['alt_km']:.4f}'",
        "START_TIME": f"'{inicio}'",
        "STOP_TIME": f"'{fim}'",
        "STEP_SIZE": f"'{passo}'",
        # 4 = azimute/elevação aparentes | 13 = diâmetro angular | 20 = distância
        "QUANTITIES": "'4,13,20'",
        "ANG_FORMAT": "'DEG'",
        "CSV_FORMAT": "'YES'",
        "TIME_DIGITS": "'MINUTES'",
        "EXTRA_PREC": "'YES'",
        "APPARENT": "'AIRLESS'",  # sem refração: a Lua não tem atmosfera
    }


def consultar_horizons(params, tentativas=4):
    url = API_URL + "?" + urllib.parse.urlencode(params)
    ultimo_erro = None
    for n in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                dados = json.loads(resp.read().decode("utf-8"))
            if "error" in dados:
                raise RuntimeError(f"Horizons retornou erro: {dados['error']}")
            return dados
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            ultimo_erro = e
            if n == tentativas:
                break
            espera = 2 ** n
            print(f"    [aviso] falha na tentativa {n}/{tentativas} ({e}); nova tentativa em {espera}s")
            time.sleep(espera)
    raise RuntimeError(f"Não foi possível consultar o Horizons: {ultimo_erro}")


def interpretar_resultado(texto):
    """Converte o bloco $$SOE ... $$EOE em lista de dicionários."""
    if "$$SOE" not in texto:
        trecho = texto[:1500]
        raise RuntimeError(f"Resposta sem dados ($$SOE ausente). Início da resposta:\n{trecho}")

    antes, resto = texto.split("$$SOE", 1)
    corpo = resto.split("$$EOE", 1)[0]

    cabecalho = None
    for linha in antes.splitlines():
        if "Date__(UT)" in linha:
            cabecalho = [c.strip() for c in linha.split(",")]
    if cabecalho is None:
        raise RuntimeError("Cabeçalho de colunas não encontrado na resposta do Horizons.")

    def indice(teste, nome):
        for i, c in enumerate(cabecalho):
            if teste(c):
                return i
        raise RuntimeError(f"Coluna '{nome}' não encontrada. Cabeçalho: {cabecalho}")

    i_az = indice(lambda c: c.startswith("Azi"), "Azi")
    i_el = indice(lambda c: c.startswith("Elev"), "Elev")
    i_diam = indice(lambda c: c.startswith("Ang-diam"), "Ang-diam")
    i_dist = indice(lambda c: c == "delta" or (c.startswith("delta") and not c.startswith("deldot")), "delta")

    linhas = []
    for linha in corpo.strip().splitlines():
        partes = [p.strip() for p in linha.split(",")]
        if len(partes) <= max(i_az, i_el, i_diam, i_dist):
            continue
        t = datetime.strptime(partes[0], "%Y-%b-%d %H:%M").replace(tzinfo=timezone.utc)
        linhas.append({
            "tempo": t,
            "az": float(partes[i_az]),
            "el": float(partes[i_el]),
            "diam_arcsec": float(partes[i_diam]),
            "dist_au": float(partes[i_dist]),
        })
    return linhas


def dividir_periodo(inicio, fim):
    """Divide em blocos de 1 ano para respeitar o limite de linhas por consulta."""
    a = datetime.strptime(inicio, "%Y-%m-%d")
    b = datetime.strptime(fim, "%Y-%m-%d")
    blocos = []
    while a < b:
        prox = min(datetime(a.year + 1, a.month, a.day), b)
        blocos.append((a.strftime("%Y-%m-%d"), prox.strftime("%Y-%m-%d")))
        a = prox
    return blocos


def baixar_serie(alvo, local, inicio, fim, passo):
    serie = []
    for (a, b) in dividir_periodo(inicio, fim):
        params = montar_parametros(alvo, local, a, b, passo)
        dados = consultar_horizons(params)
        pedaco = interpretar_resultado(dados["result"])
        # evita duplicar o instante de junção entre blocos
        if serie and pedaco and pedaco[0]["tempo"] == serie[-1]["tempo"]:
            pedaco = pedaco[1:]
        serie.extend(pedaco)
        time.sleep(PAUSA_ENTRE_CONSULTAS)
    return serie


# --------------------------------------------------------------------------
# Cálculos (horizonte plano / Lua lisa)
# --------------------------------------------------------------------------

def fracao_disco_visivel(elev_graus, raio_graus):
    """Fração da ÁREA do disco acima de um horizonte reto (aprox. de ângulos pequenos)."""
    if raio_graus <= 0:
        return 1.0 if elev_graus > 0 else 0.0
    if elev_graus >= raio_graus:
        return 1.0
    if elev_graus <= -raio_graus:
        return 0.0
    r = raio_graus
    d = -elev_graus  # distância do centro até a linha do horizonte (positiva = centro abaixo)
    area_segmento_escondido = r * r * math.acos(max(-1.0, min(1.0, -d / r))) + d * math.sqrt(max(0.0, r * r - d * d))
    # o segmento acima da linha é o complemento
    area_total = math.pi * r * r
    area_visivel = area_total - area_segmento_escondido
    return max(0.0, min(1.0, area_visivel / area_total))


def maior_sequencia(tempos, condicao):
    """Duração (horas) do maior intervalo contínuo em que `condicao` é verdadeira."""
    maior = 0.0
    inicio = None
    anterior = None
    for t, ok in zip(tempos, condicao):
        if ok and inicio is None:
            inicio = t
        if not ok and inicio is not None:
            maior = max(maior, (anterior - inicio).total_seconds() / 3600)
            inicio = None
        anterior = t
    if inicio is not None and anterior is not None:
        maior = max(maior, (anterior - inicio).total_seconds() / 3600)
    return maior


def juntar_e_calcular(sol, terra):
    por_tempo_terra = {p["tempo"]: p for p in terra}
    linhas = []
    for s in sol:
        e = por_tempo_terra.get(s["tempo"])
        if e is None:
            continue
        raio_sol = s["diam_arcsec"] / 3600.0 / 2.0
        frac = fracao_disco_visivel(s["el"], raio_sol)
        irradiancia = CONSTANTE_SOLAR / (s["dist_au"] ** 2) * frac
        linhas.append({
            "tempo_utc": s["tempo"].strftime("%Y-%m-%dT%H:%MZ"),
            "sol_az": round(s["az"], 5),
            "sol_el": round(s["el"], 5),
            "sol_raio_graus": round(raio_sol, 5),
            "sol_dist_ua": round(s["dist_au"], 8),
            "sol_fracao_visivel_plano": round(frac, 4),
            "irradiancia_plano_wm2": round(irradiancia, 2),
            "terra_az": round(e["az"], 5),
            "terra_el": round(e["el"], 5),
            "terra_raio_graus": round(e["diam_arcsec"] / 3600.0 / 2.0, 5),
            "_tempo": s["tempo"],
        })
    return linhas


def resumir(linhas):
    if not linhas:
        return {}
    n = len(linhas)
    tempos = [l["_tempo"] for l in linhas]
    sol_up = [l["sol_fracao_visivel_plano"] > 0 for l in linhas]
    terra_up = [l["terra_el"] > 0 for l in linhas]  # regra: centro da Terra acima do horizonte
    ambos = [a and b for a, b in zip(sol_up, terra_up)]
    return {
        "amostras": n,
        "inicio_utc": linhas[0]["tempo_utc"],
        "fim_utc": linhas[-1]["tempo_utc"],
        "pct_tempo_com_sol": round(100 * sum(sol_up) / n, 2),
        "pct_tempo_com_terra": round(100 * sum(terra_up) / n, 2),
        "pct_tempo_sol_e_terra": round(100 * sum(ambos) / n, 2),
        "sol_elev_max": round(max(l["sol_el"] for l in linhas), 3),
        "terra_elev_max": round(max(l["terra_el"] for l in linhas), 3),
        "terra_elev_min": round(min(l["terra_el"] for l in linhas), 3),
        "irradiancia_media_wm2": round(sum(l["irradiancia_plano_wm2"] for l in linhas) / n, 1),
        "maior_noite_horas": round(maior_sequencia(tempos, [not x for x in sol_up]), 1),
        "maior_periodo_sem_terra_horas": round(maior_sequencia(tempos, [not x for x in terra_up]), 1),
        "regra_terra_visivel": "centro do disco acima do horizonte",
        "horizonte": "plano (Lua lisa, sem relevo LOLA)",
    }


def salvar_csv(caminho, linhas):
    colunas = [k for k in linhas[0].keys() if not k.startswith("_")]
    with caminho.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=colunas, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)


# --------------------------------------------------------------------------
# Validação automática (testes de sanidade física)
# --------------------------------------------------------------------------

def validar(resumos):
    testes = [
        ("polo_sul", lambda r: r["sol_elev_max"] < 1.7,
         "No polo, o Sol não passa de ~1,54° (inclinação do eixo lunar)"),
        ("change4", lambda r: r["terra_elev_max"] < 0,
         "Do lado oculto, a Terra nunca aparece"),
        ("apollo11", lambda r: r["terra_elev_min"] > 0,
         "Perto do centro da face visível, a Terra está sempre no céu"),
        ("apollo11", lambda r: 40 < r["pct_tempo_com_sol"] < 60,
         "Perto do equador, o Sol fica ~50% do tempo acima do horizonte"),
    ]
    print("\n=== Validação física ===")
    for id_local, teste, descricao in testes:
        r = resumos.get(id_local)
        if not r:
            print(f"  [--]  {descricao}  (local '{id_local}' não incluído)")
            continue
        ok = teste(r)
        print(f"  [{'OK' if ok else 'FALHOU'}]  {descricao}")


# --------------------------------------------------------------------------
# Principal
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Baixa efemérides de Sol e Terra para locais lunares (JPL Horizons).")
    ap.add_argument("--locais", default="locais.csv", help="CSV com os locais (criado se não existir)")
    ap.add_argument("--inicio", default="2027-01-01", help="Data inicial AAAA-MM-DD (UTC)")
    ap.add_argument("--fim", default="2029-01-01", help="Data final AAAA-MM-DD (UTC)")
    ap.add_argument("--passo", default="1 h", help="Intervalo: '1 h', '30 m', '10 m'...")
    ap.add_argument("--saida", default="dados", help="Pasta de saída")
    ap.add_argument("--teste", action="store_true", help="Baixa só 30 dias, para conferir rápido")
    args = ap.parse_args()

    if args.teste:
        args.inicio, args.fim = "2027-01-01", "2027-01-31"

    saida = Path(args.saida)
    (saida / "efemerides").mkdir(parents=True, exist_ok=True)

    locais = carregar_locais(Path(args.locais))
    print(f"Período: {args.inicio} → {args.fim} | passo: {args.passo} | locais: {len(locais)}\n")

    resumos = {}
    for i, local in enumerate(locais, 1):
        print(f"[{i}/{len(locais)}] {local['nome']}  (lat {local['lat']}, lon {local['lon_leste']}°E)")
        try:
            sol = baixar_serie(ALVOS["sol"], local, args.inicio, args.fim, args.passo)
            terra = baixar_serie(ALVOS["terra"], local, args.inicio, args.fim, args.passo)
        except RuntimeError as e:
            print(f"    [ERRO] {e}\n")
            continue

        linhas = juntar_e_calcular(sol, terra)
        if not linhas:
            print("    [ERRO] nenhuma linha válida.\n")
            continue
        salvar_csv(saida / "efemerides" / f"{local['id']}.csv", linhas)
        resumos[local["id"]] = resumir(linhas)
        r = resumos[local["id"]]
        print(f"    Sol {r['pct_tempo_com_sol']}% | Terra {r['pct_tempo_com_terra']}% | "
              f"ambos {r['pct_tempo_sol_e_terra']}% | Sol máx {r['sol_elev_max']}°\n")

    (saida / "locais.json").write_text(json.dumps(locais, ensure_ascii=False, indent=2), encoding="utf-8")
    (saida / "resumo.json").write_text(json.dumps(resumos, ensure_ascii=False, indent=2), encoding="utf-8")
    exemplo = montar_parametros("<alvo>", {"lon_leste": 0, "lat": 0, "alt_km": 0}, "<inicio>", "<fim>", args.passo)
    (saida / "metadados.json").write_text(json.dumps({
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fonte": "NASA/JPL Horizons API (" + API_URL + ")",
        "alvos": ALVOS,
        "observador": "superfície da Lua (coord@301, GEODETIC, longitude leste)",
        "periodo": [args.inicio, args.fim],
        "passo": args.passo,
        "parametros_modelo": exemplo,
        "observacoes": [
            "Elevações relativas a horizonte plano (Lua lisa). Relevo LOLA aplicado em etapa posterior.",
            "Sem refração (Lua sem atmosfera).",
            "Libração e variações orbitais já incluídas pelo Horizons.",
            "Terra visível = centro do disco acima do horizonte.",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    validar(resumos)
    print(f"\nPronto. Arquivos em: {saida.resolve()}")


if __name__ == "__main__":
    sys.exit(main())
