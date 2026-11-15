#!/usr/bin/env python3
"""
Lunar Landing Site Navigator - Mapa sombreado do polo sul
==========================================================

Gera uma imagem de relevo sombreado (hillshade) do polo sul a partir do
mesmo arquivo LOLA usado em `calcular_horizonte.py`. O app usa essa imagem
como fundo do mapa onde os locais de pouso aparecem.

Só precisa de numpy (o PNG é escrito com a biblioteca padrão).
Aceita o formato PDS (.LBL + .IMG) em estereográfica polar.

Uso:
    python gerar_mapa.py --dem dem/LDEM_85S_20M.LBL
    python gerar_mapa.py --dem dem/LDEM_80S_80M.LBL --raio-km 300 --pixels 1000

Saída:
    dados/mapa_polo.json   imagem (PNG em base64) + extensão em metros
    dados/mapa_polo.png    a mesma imagem, para conferir
"""

import argparse
import base64
import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

from calcular_horizonte import DemPDSPolar


def escrever_png(caminho, rgb):
    h, w, _ = rgb.shape
    bruto = b"".join(b"\x00" + rgb[i].tobytes() for i in range(h))

    def bloco(tipo, dados):
        c = struct.pack(">I", len(dados)) + tipo + dados
        return c + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + bloco(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + bloco(b"IDAT", zlib.compress(bruto, 9)) + bloco(b"IEND", b""))
    Path(caminho).write_bytes(png)
    return png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True, help="Arquivo .LBL (PDS, estereográfica polar sul)")
    ap.add_argument("--raio-km", type=float, default=None,
                    help="Meia-largura do mapa a partir do polo (padrão: o que o arquivo cobrir)")
    ap.add_argument("--pixels", type=int, default=900, help="Largura da imagem gerada")
    ap.add_argument("--dados", default="dados")
    args = ap.parse_args()

    dem = DemPDSPolar(Path(args.dem))
    if not dem.sul:
        print("Este script foi feito para o polo SUL.")
        return 1

    # extensão do arquivo em metros, a partir do polo (pixel do polo = col0, lin0)
    max_x = min(dem.col0, dem.colunas - 1 - dem.col0) * dem.metros_px
    max_y = min(dem.lin0, dem.linhas - 1 - dem.lin0) * dem.metros_px
    ext = min(max_x, max_y)
    if args.raio_km:
        ext = min(ext, args.raio_km * 1000.0)
    if ext <= 0:
        print("O arquivo não contém o polo; use um DEM centrado no polo sul.")
        return 1

    n = args.pixels
    coords = np.linspace(-ext, ext, n)
    xs = coords
    ys = coords[::-1]  # topo da imagem = y máximo
    cols = np.clip(np.round(xs / dem.metros_px + dem.col0).astype(int), 0, dem.colunas - 1)
    lins = np.clip(np.round(dem.lin0 - ys / dem.metros_px).astype(int), 0, dem.linhas - 1)
    z = dem._fisico(dem.dados[np.ix_(lins, cols)]) * dem.fator_m
    if dem.subtrair_raio:
        z = z - 1737400.0

    passo = (2 * ext) / (n - 1)
    zf = np.where(np.isnan(z), np.nanmedian(z), z)
    gy, gx = np.gradient(zf, passo)
    # luz rasante vinda de "cima" da imagem, como o Sol baixo no polo
    az, alt = np.radians(315.0), np.radians(25.0)
    inclin = np.arctan(np.hypot(gx, gy))
    aspecto = np.arctan2(-gx, gy)
    sombra = np.sin(alt) * np.cos(inclin) + np.cos(alt) * np.sin(inclin) * np.cos(az - aspecto)
    sombra = np.clip(sombra, 0, 1)

    # mistura com a altitude para dar profundidade (crateras mais escuras)
    zn = (zf - np.nanpercentile(zf, 2)) / max(1e-6, np.nanpercentile(zf, 98) - np.nanpercentile(zf, 2))
    v = np.clip(0.75 * sombra + 0.25 * np.clip(zn, 0, 1), 0, 1)
    base = np.array([154, 148, 136]) / 255.0  # cinza-regolito
    rgb = (np.clip(v[..., None] * base * 1.25, 0, 1) * 255).astype(np.uint8)
    rgb[np.isnan(z)] = (12, 19, 34)

    pasta = Path(args.dados)
    pasta.mkdir(exist_ok=True)
    png = escrever_png(pasta / "mapa_polo.png", rgb)
    (pasta / "mapa_polo.json").write_text(json.dumps({
        "png_base64": base64.b64encode(png).decode("ascii"),
        "extensao_m": ext,
        "projecao": "estereográfica polar sul, esfera R=1737,4 km, longitude central 0°",
        "fonte": dem.nome,
    }), encoding="utf-8")
    print(f"Mapa: {n}x{n} px, ±{ext/1000:.0f} km a partir do polo -> {pasta/'mapa_polo.png'} ({len(png)/1e3:.0f} kB)")


if __name__ == "__main__":
    sys.exit(main())
