#!/usr/bin/env python3
"""
Lunar Landing Site Navigator - Horizonte real com relevo LOLA
==============================================================

Etapa 2 do pipeline (rode depois de `baixar_efemerides.py`).

1. Para cada local de `locais.csv`, lê o relevo LOLA ao redor e calcula o
   PERFIL DE HORIZONTE: para cada direção (0,5° em 0,5°), qual é o ângulo
   do ponto mais alto do terreno, já considerando a curvatura da Lua.
2. Aplica esse horizonte às efemérides da etapa 1:
     - Sol visível?    (fração do disco solar acima do terreno)
     - Terra visível?  (centro da Terra acima do terreno)
3. Gera métricas com relevo e a lista de eventos (nascer/pôr do Sol e da
   Terra), que o app usa para as contagens regressivas.

Formatos de relevo aceitos:
  - PDS .IMG + .LBL em projeção POLAR STEREOGRAPHIC (ex.: produtos LOLA
    polares LDEM_*S_*M do PDS Geosciences Node). Só precisa de numpy.
  - GeoTIFF (.tif) em qualquer projeção (ex.: NASA PGDA 78/90, USGS LOLA
    118 m). Precisa de `rasterio` e `pyproj`.

Vários arquivos podem ser passados, do MAIS DETALHADO para o MAIS AMPLO:
para cada ponto o script usa o primeiro arquivo que cobre aquele ponto.
Assim um DEM de 5 m resolve o terreno próximo e um de 20-240 m o distante.

Uso:
    python calcular_horizonte.py --dem dem/LDEM_85S_20M.LBL
    python calcular_horizonte.py --dem dem/site01_5m.tif dem/LDEM_85S_20M.LBL --altura 2

Saída (pasta ./dados):
    dados/horizontes/<id>.json      perfil de horizonte (720 direções)
    dados/visibilidade/<id>.csv     série temporal com relevo
    dados/eventos/<id>.json         nascer/pôr do Sol e da Terra
    dados/resumo_relevo.json        métricas com relevo x horizonte plano
"""

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from baixar_efemerides import carregar_locais, fracao_disco_visivel, maior_sequencia

R_LUA_M = 1737400.0  # raio de referência do LOLA (m)


# --------------------------------------------------------------------------
# Leitura de relevo
# --------------------------------------------------------------------------

class DemPDSPolar:
    """DEM PDS (.IMG + .LBL) em estereográfica polar, lido sob demanda (memmap)."""

    TIPOS = {
        ("PC_REAL", 32): "<f4", ("PC_REAL", 64): "<f8",
        ("IEEE_REAL", 32): ">f4", ("IEEE_REAL", 64): ">f8",
        ("LSB_INTEGER", 16): "<i2", ("LSB_INTEGER", 32): "<i4",
        ("MSB_INTEGER", 16): ">i2", ("MSB_INTEGER", 32): ">i4",
        ("LSB_UNSIGNED_INTEGER", 16): "<u2", ("MSB_UNSIGNED_INTEGER", 16): ">u2",
    }

    def __init__(self, caminho_lbl: Path):
        self.nome = caminho_lbl.name
        texto = caminho_lbl.read_text(errors="ignore")

        def campo(chave, padrao=None):
            m = re.search(rf"^\s*{chave}\s*=\s*(.+?)\s*$", texto, re.MULTILINE)
            return m.group(1) if m else padrao

        def numero(chave, padrao=None):
            v = campo(chave)
            if v is None:
                return padrao
            m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", v)
            return float(m.group(0)) if m else padrao

        proj = (campo("MAP_PROJECTION_TYPE", "") or "").replace('"', "").upper()
        if "POLAR" not in proj or "STEREO" not in proj:
            raise ValueError(f"{self.nome}: projeção '{proj}' não suportada no modo PDS "
                             f"(use estereográfica polar, ou um GeoTIFF).")

        self.linhas = int(numero("LINES"))
        self.colunas = int(numero("LINE_SAMPLES"))
        tipo = (campo("SAMPLE_TYPE", "") or "").replace('"', "").upper()
        bits = int(numero("SAMPLE_BITS"))
        if (tipo, bits) not in self.TIPOS:
            raise ValueError(f"{self.nome}: tipo de dado não suportado: {tipo} {bits} bits")
        dtype = self.TIPOS[(tipo, bits)]

        self.escala_val = numero("SCALING_FACTOR", 1.0)
        self.offset_val = numero("OFFSET", 0.0)
        self.faltante = numero("MISSING_CONSTANT", None)
        unidade = (campo("UNIT", "") or "").upper()

        escala_txt = campo("MAP_SCALE", "")
        self.metros_px = numero("MAP_SCALE")
        if "KM" in escala_txt.upper():
            self.metros_px *= 1000.0
        self.lin0 = numero("LINE_PROJECTION_OFFSET")
        self.col0 = numero("SAMPLE_PROJECTION_OFFSET")
        self.lon_centro = numero("CENTER_LONGITUDE", 0.0)
        lat_centro = numero("CENTER_LATITUDE", -90.0)
        self.sul = lat_centro < 0
        self.raio_proj = numero("A_AXIS_RADIUS", 1737.4)
        if self.raio_proj < 10000:  # veio em km
            self.raio_proj *= 1000.0

        # Arquivo de dados: ^IMAGE = "ARQUIVO.IMG" ou ("ARQUIVO.IMG", registro)
        ponteiro = campo(r"\^IMAGE", "")
        m = re.search(r'"?([\w\-.]+\.IMG)"?', ponteiro, re.IGNORECASE)
        arquivo = caminho_lbl.with_name(m.group(1)) if m else caminho_lbl.with_suffix(".IMG")
        if not arquivo.exists():
            alternativas = [p for p in caminho_lbl.parent.iterdir()
                            if p.name.lower() == arquivo.name.lower()]
            if not alternativas:
                raise FileNotFoundError(f"Arquivo de dados não encontrado: {arquivo}")
            arquivo = alternativas[0]
        deslocamento = 0
        m2 = re.search(r",\s*(\d+)", ponteiro)
        if m2:
            deslocamento = (int(m2.group(1)) - 1) * int(numero("RECORD_BYTES", 0))

        self.dados = np.memmap(arquivo, dtype=dtype, mode="r", offset=deslocamento,
                               shape=(self.linhas, self.colunas))

        # Unidade e referência: queremos METROS acima da esfera de 1737,4 km
        amostra = self._fisico(self.dados[:: max(1, self.linhas // 50), :: max(1, self.colunas // 50)])
        if "KILOMETER" in unidade or (not unidade and np.nanmedian(np.abs(amostra)) < 100):
            self.fator_m = 1000.0
        else:
            self.fator_m = 1.0
        mediana_m = np.nanmedian(amostra) * self.fator_m
        self.subtrair_raio = mediana_m > 1.0e6  # valores são raio, não altura

    def _fisico(self, bruto):
        v = bruto.astype("f8")
        if self.faltante is not None:
            v[bruto == self.faltante] = np.nan
        return v * self.escala_val + self.offset_val

    def _latlon_para_pixel(self, lat, lon):
        lat_r = np.radians(lat)
        dlon = np.radians(lon - self.lon_centro)
        if self.sul:
            p = 2 * self.raio_proj * np.tan(np.pi / 4 + lat_r / 2)
            x, y = p * np.sin(dlon), p * np.cos(dlon)
        else:
            p = 2 * self.raio_proj * np.tan(np.pi / 4 - lat_r / 2)
            x, y = p * np.sin(dlon), -p * np.cos(dlon)
        col = x / self.metros_px + self.col0
        lin = self.lin0 - y / self.metros_px
        return lin, col

    def amostrar(self, lat, lon):
        """Altura (m, sobre 1737,4 km) com interpolação bilinear; NaN fora do mapa."""
        lin, col = self._latlon_para_pixel(np.asarray(lat, "f8"), np.asarray(lon, "f8"))
        saida = np.full(lin.shape, np.nan)
        ok = (lin >= 0) & (col >= 0) & (lin < self.linhas - 1) & (col < self.colunas - 1)
        if not ok.any():
            return saida
        l, c = lin[ok], col[ok]
        l0, c0 = np.floor(l).astype(int), np.floor(c).astype(int)
        fl, fc = l - l0, c - c0
        v00 = self._fisico(self.dados[l0, c0]); v01 = self._fisico(self.dados[l0, c0 + 1])
        v10 = self._fisico(self.dados[l0 + 1, c0]); v11 = self._fisico(self.dados[l0 + 1, c0 + 1])
        v = (v00 * (1 - fl) * (1 - fc) + v01 * (1 - fl) * fc + v10 * fl * (1 - fc) + v11 * fl * fc)
        v = v * self.fator_m
        if self.subtrair_raio:
            v = v - R_LUA_M
        saida[ok] = v
        return saida


class DemGeoTIFF:
    """GeoTIFF em qualquer projeção (requer rasterio + pyproj)."""

    def __init__(self, caminho: Path):
        try:
            import rasterio
            from pyproj import Transformer
        except ImportError:
            raise ImportError("Para GeoTIFF instale: pip install rasterio pyproj")
        self.nome = caminho.name
        self.ds = rasterio.open(caminho)
        self.banda = self.ds.read(1, masked=True).astype("f8").filled(np.nan)
        self.metros_px = abs(self.ds.transform.a)
        crs = self.ds.crs
        self.para_mapa = Transformer.from_crs(crs.geodetic_crs, crs, always_xy=True)
        if crs.is_geographic:  # graus por pixel -> metros aproximados
            self.metros_px *= math.pi / 180 * R_LUA_M
        mediana = np.nanmedian(self.banda[:: 50, :: 50])
        self.fator_m = 1000.0 if abs(mediana) < 100 and np.nanmax(np.abs(self.banda[::50, ::50])) < 100 else 1.0
        self.subtrair_raio = mediana * self.fator_m > 1.0e6

    def amostrar(self, lat, lon):
        lat = np.asarray(lat, "f8"); lon = np.asarray(lon, "f8")
        x, y = self.para_mapa.transform(lon, lat)
        inv = ~self.ds.transform
        col, lin = inv * (np.asarray(x), np.asarray(y))
        col = np.asarray(col) - 0.5; lin = np.asarray(lin) - 0.5  # centro do pixel
        saida = np.full(lat.shape, np.nan)
        h, w = self.banda.shape
        ok = (lin >= 0) & (col >= 0) & (lin < h - 1) & (col < w - 1)
        if not ok.any():
            return saida
        l, c = lin[ok], col[ok]
        l0, c0 = np.floor(l).astype(int), np.floor(c).astype(int)
        fl, fc = l - l0, c - c0
        b = self.banda
        v = (b[l0, c0] * (1 - fl) * (1 - fc) + b[l0, c0 + 1] * (1 - fl) * fc
             + b[l0 + 1, c0] * fl * (1 - fc) + b[l0 + 1, c0 + 1] * fl * fc) * self.fator_m
        if self.subtrair_raio:
            v = v - R_LUA_M
        saida[ok] = v
        return saida


def abrir_dem(caminho: Path):
    suf = caminho.suffix.lower()
    if suf in (".lbl", ".img"):
        lbl = caminho if suf == ".lbl" else caminho.with_suffix(".LBL")
        if not lbl.exists():
            lbl = caminho.with_suffix(".lbl")
        return DemPDSPolar(lbl)
    if suf in (".tif", ".tiff"):
        return DemGeoTIFF(caminho)
    raise ValueError(f"Formato não reconhecido: {caminho}")


def amostrar_aninhado(dems, lat, lon):
    """Usa o primeiro DEM (mais detalhado) que cobre cada ponto."""
    valores = np.full(np.shape(lat), np.nan)
    for dem in dems:
        falta = np.isnan(valores)
        if not falta.any():
            break
        valores[falta] = dem.amostrar(lat[falta], lon[falta])
    return valores


# --------------------------------------------------------------------------
# Perfil de horizonte
# --------------------------------------------------------------------------

def destino(lat0, lon0, azimute_graus, dist_m):
    """Ponto a `dist_m` na direção `azimute` (a partir do norte, sentido leste), na esfera."""
    f1, l1 = math.radians(lat0), math.radians(lon0)
    az = np.radians(azimute_graus)[:, None]
    d = (dist_m / R_LUA_M)[None, :]
    f2 = np.arcsin(np.sin(f1) * np.cos(d) + np.cos(f1) * np.sin(d) * np.cos(az))
    l2 = l1 + np.arctan2(np.sin(az) * np.sin(d) * np.cos(f1), np.cos(d) - np.sin(f1) * np.sin(f2))
    return np.degrees(f2), (np.degrees(l2) + 360.0) % 360.0


def distancias_amostragem(res_m, dist_max_m, fator=0.01):
    """Passo = 1 pixel perto do local, crescendo 1% da distância ao se afastar."""
    d, lista = 2 * res_m, []
    while d <= dist_max_m:
        lista.append(d)
        d += max(res_m, fator * d)
    return np.array(lista)


def perfil_horizonte(dems, lat, lon, altura_obs_m, passo_az, dist_max_km):
    if abs(lat) > 89.9999:  # no polo exato o "norte" não é definido
        lat = math.copysign(89.9999, lat)
    h0 = float(amostrar_aninhado(dems, np.array([lat]), np.array([lon]))[0])
    if np.isnan(h0):
        return None
    r0 = R_LUA_M + h0 + altura_obs_m

    azimutes = np.arange(0.0, 360.0, passo_az)
    res = min(d.metros_px for d in dems)
    dist = distancias_amostragem(res, dist_max_km * 1000.0)

    lats, lons = destino(lat, lon, azimutes, dist)
    alturas = amostrar_aninhado(dems, lats.ravel(), lons.ravel()).reshape(lats.shape)

    theta = dist[None, :] / R_LUA_M
    r1 = R_LUA_M + alturas
    elev = np.degrees(np.arctan2(r1 * np.cos(theta) - r0, r1 * np.sin(theta)))
    elev_valida = np.where(np.isnan(elev), -np.inf, elev)

    idx = np.argmax(elev_valida, axis=1)
    horizonte = elev_valida[np.arange(len(azimutes)), idx]
    dist_bloqueio = dist[idx] / 1000.0

    # Piso: horizonte de uma esfera lisa (curvatura), usado quando não há terreno mais alto
    piso = -math.degrees(math.acos(min(1.0, R_LUA_M / r0))) if r0 > R_LUA_M else 0.0
    usar_piso = horizonte < piso
    horizonte = np.where(usar_piso, piso, horizonte)
    dist_bloqueio = np.where(usar_piso, np.nan, dist_bloqueio)

    cobertura = float(np.mean(~np.isnan(alturas[:, -1]))) if alturas.shape[1] else 0.0
    return {
        "azimutes": azimutes, "horizonte": horizonte, "dist_bloqueio_km": dist_bloqueio,
        "altura_local_m": h0, "piso_curvatura_graus": piso,
        "cobertura_dist_max": cobertura,
    }


def horizonte_em(perfil, az):
    """Interpola o horizonte (graus) num azimute qualquer, com volta em 360°."""
    azs = np.append(perfil["azimutes"], 360.0)
    hs = np.append(perfil["horizonte"], perfil["horizonte"][0])
    return np.interp(np.asarray(az) % 360.0, azs, hs)


# --------------------------------------------------------------------------
# Aplicar às efemérides
# --------------------------------------------------------------------------

def ler_efemerides(caminho: Path):
    with caminho.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def detectar_eventos(tempos, visivel, nome_nasce, nome_poe):
    eventos = []
    for i in range(1, len(visivel)):
        if visivel[i] != visivel[i - 1]:
            eventos.append({"tipo": nome_nasce if visivel[i] else nome_poe, "tempo_utc": tempos[i]})
    return eventos


def processar_local(local, perfil, efem):
    tempos = [r["tempo_utc"] for r in efem]
    sol_az = np.array([float(r["sol_az"]) for r in efem])
    sol_el = np.array([float(r["sol_el"]) for r in efem])
    sol_raio = np.array([float(r["sol_raio_graus"]) for r in efem])
    sol_dist = np.array([float(r["sol_dist_ua"]) for r in efem])
    ter_az = np.array([float(r["terra_az"]) for r in efem])
    ter_el = np.array([float(r["terra_el"]) for r in efem])

    h_sol = horizonte_em(perfil, sol_az)
    h_ter = horizonte_em(perfil, ter_az)

    frac = np.array([fracao_disco_visivel(e - h, r) for e, h, r in zip(sol_el, h_sol, sol_raio)])
    irr = 1361.0 / sol_dist ** 2 * frac
    sol_vis = frac > 0
    ter_vis = ter_el > h_ter

    linhas = []
    for i, t in enumerate(tempos):
        linhas.append({
            "tempo_utc": t,
            "sol_az": round(sol_az[i], 4), "sol_el": round(sol_el[i], 4),
            "horizonte_sol": round(float(h_sol[i]), 4),
            "sol_fracao_visivel": round(float(frac[i]), 4),
            "irradiancia_wm2": round(float(irr[i]), 2),
            "terra_az": round(ter_az[i], 4), "terra_el": round(ter_el[i], 4),
            "horizonte_terra": round(float(h_ter[i]), 4),
            "terra_visivel": int(ter_vis[i]),
        })

    dt = [datetime.strptime(t, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc) for t in tempos]
    n = len(tempos)
    sol_plano = np.array([float(r.get("sol_fracao_visivel_plano", 0)) > 0 for r in efem])
    resumo = {
        "altura_local_m": round(perfil["altura_local_m"], 1),
        "pct_tempo_com_sol": round(100 * sol_vis.mean(), 2),
        "pct_tempo_com_terra": round(100 * ter_vis.mean(), 2),
        "pct_tempo_sol_e_terra": round(100 * (sol_vis & ter_vis).mean(), 2),
        "pct_tempo_com_sol_horizonte_plano": round(100 * sol_plano.mean(), 2),
        "pct_tempo_com_terra_horizonte_plano": round(100 * (ter_el > 0).mean(), 2),
        "sol_elev_max": round(float(sol_el.max()), 3),
        "irradiancia_media_wm2": round(float(irr.mean()), 1),
        "maior_noite_horas": round(maior_sequencia(dt, list(~sol_vis)), 1),
        "maior_periodo_sem_terra_horas": round(maior_sequencia(dt, list(~ter_vis)), 1),
        "horizonte_max_graus": round(float(perfil["horizonte"].max()), 2),
        "horizonte_medio_graus": round(float(perfil["horizonte"].mean()), 2),
        "amostras": n,
        "regra_terra_visivel": "centro do disco acima do horizonte do terreno",
    }
    eventos = (detectar_eventos(tempos, list(sol_vis), "nascer_do_sol", "por_do_sol")
               + detectar_eventos(tempos, list(ter_vis), "nascer_da_terra", "por_da_terra"))
    eventos.sort(key=lambda e: e["tempo_utc"])
    return linhas, resumo, eventos


# --------------------------------------------------------------------------
# Principal
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Horizonte real (LOLA) e visibilidade de Sol/Terra.")
    ap.add_argument("--dem", nargs="+", required=True,
                    help="Arquivo(s) de relevo, do mais detalhado para o mais amplo (.LBL/.IMG ou .tif)")
    ap.add_argument("--locais", default="locais.csv")
    ap.add_argument("--dados", default="dados", help="Pasta com as efemérides da etapa 1")
    ap.add_argument("--altura", type=float, default=2.0,
                    help="Altura do observador/painel acima do solo, em metros (padrão 2)")
    ap.add_argument("--passo-az", type=float, default=0.5, help="Resolução em azimute (graus)")
    ap.add_argument("--dist-max", type=float, default=150.0, help="Alcance máximo do horizonte (km)")
    args = ap.parse_args()

    dems = [abrir_dem(Path(p)) for p in args.dem]
    for d in dems:
        print(f"[relevo] {d.nome}: {d.metros_px:.1f} m/pixel")

    pasta = Path(args.dados)
    for sub in ("horizontes", "visibilidade", "eventos"):
        (pasta / sub).mkdir(parents=True, exist_ok=True)

    resumos = {}
    for local in carregar_locais(Path(args.locais)):
        print(f"\n{local['nome']}")
        perfil = perfil_horizonte(dems, local["lat"], local["lon_leste"], args.altura,
                                  args.passo_az, args.dist_max)
        if perfil is None:
            print("    fora da área dos arquivos de relevo; pulando.")
            continue

        print(f"    altura do terreno: {perfil['altura_local_m']:.0f} m | "
              f"horizonte máx {perfil['horizonte'].max():.2f}° | "
              f"médio {perfil['horizonte'].mean():.2f}°")
        if perfil["cobertura_dist_max"] < 0.95:
            print(f"    [aviso] relevo cobre só {100*perfil['cobertura_dist_max']:.0f}% das direções "
                  f"até {args.dist_max:.0f} km; além disso usa-se a curvatura da esfera.")

        (pasta / "horizontes" / f"{local['id']}.json").write_text(json.dumps({
            "id": local["id"], "nome": local["nome"],
            "lat": local["lat"], "lon_leste": local["lon_leste"],
            "altura_local_m": round(perfil["altura_local_m"], 1),
            "altura_observador_m": args.altura,
            "piso_curvatura_graus": round(perfil["piso_curvatura_graus"], 4),
            "azimutes": [round(float(a), 2) for a in perfil["azimutes"]],
            "horizonte_graus": [round(float(h), 4) for h in perfil["horizonte"]],
            "distancia_bloqueio_km": [None if np.isnan(x) else round(float(x), 2)
                                      for x in perfil["dist_bloqueio_km"]],
            "fontes_relevo": [d.nome for d in dems],
            "alcance_km": args.dist_max,
        }, ensure_ascii=False), encoding="utf-8")

        arq_efem = pasta / "efemerides" / f"{local['id']}.csv"
        if not arq_efem.exists():
            print(f"    [aviso] {arq_efem} não encontrado; rode baixar_efemerides.py antes. "
                  f"Salvei só o perfil de horizonte.")
            continue

        linhas, resumo, eventos = processar_local(local, perfil, ler_efemerides(arq_efem))
        with (pasta / "visibilidade" / f"{local['id']}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
            w.writeheader(); w.writerows(linhas)
        (pasta / "eventos" / f"{local['id']}.json").write_text(
            json.dumps(eventos, ensure_ascii=False, indent=1), encoding="utf-8")
        resumos[local["id"]] = resumo
        print(f"    Sol {resumo['pct_tempo_com_sol']}% (plano: {resumo['pct_tempo_com_sol_horizonte_plano']}%) | "
              f"Terra {resumo['pct_tempo_com_terra']}% (plano: {resumo['pct_tempo_com_terra_horizonte_plano']}%) | "
              f"ambos {resumo['pct_tempo_sol_e_terra']}%")

    (pasta / "resumo_relevo.json").write_text(json.dumps({
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fontes_relevo": [d.nome for d in dems],
        "altura_observador_m": args.altura,
        "metodo": ("Perfil de horizonte por raios a cada {}° até {} km; elevação do terreno "
                   "com curvatura da esfera (R=1737,4 km); sem refração.").format(args.passo_az, args.dist_max),
        "locais": resumos,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nPronto. Resultados em {pasta.resolve()}")


if __name__ == "__main__":
    sys.exit(main())
