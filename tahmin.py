#!/usr/bin/env python3
"""Futbol maç tahmin programı.

Kullanım:
    python3 tahmin.py "Galatasaray" "Fenerbahce"     (ilk takım ev sahibi)
    python3 tahmin.py                                 (takımları sorar)
    python3 tahmin.py "Galatasaray" "Arsenal"        (Avrupa kupası maçı)
    python3 tahmin.py --takimlar T1                   (ligdeki takım adları)
    python3 tahmin.py --takimlar CL                   (CL, EL, ECL: Avrupa kupaları)

Yöntem: football-data.co.uk'tan son 3 sezonun sonuçları, xG verisi ve bahis
şirketlerinin ortalama oranları indirilir. Yakın maçlara daha çok ağırlık veren
bir Poisson (Dixon-Coles) modeliyle takımların hücum/savunma güçleri ve ev
sahibi avantajı hesaplanır; güç göstergesi olarak gol, xG ve piyasanın maç
öncesi beklentisi birlikte kullanılır. Maçın güncel oranı yayımlanmışsa tahmin
ağırlıklı olarak ona dayanır (piyasa.py). Yarı bazlı pazarlar
için goller, takımların ilk yarı gol payına göre yarılara dağıtılır.
Yalnızca Python standart kütüphanesi gerekir.
"""
import argparse
import csv
import difflib
import io
import math
import os
import sys
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo

import piyasa

KAYNAK = "https://www.football-data.co.uk/mmz4281/{sezon}/{lig}.csv"
LIGLER = {
    "T1": "Türkiye Süper Lig", "E0": "İngiltere Premier Lig", "E1": "İngiltere Championship",
    "E2": "İngiltere League One", "E3": "İngiltere League Two", "SC0": "İskoçya Premiership",
    "D1": "Almanya Bundesliga", "D2": "Almanya 2. Bundesliga", "I1": "İtalya Serie A",
    "I2": "İtalya Serie B", "SP1": "İspanya La Liga", "SP2": "İspanya La Liga 2",
    "F1": "Fransa Ligue 1", "F2": "Fransa Ligue 2", "N1": "Hollanda Eredivisie",
    "B1": "Belçika Pro Lig", "P1": "Portekiz Primeira Liga", "G1": "Yunanistan Süper Lig",
}
# Kaynaktaki yazımı tahmin edilemeyen takımlar: aranan ad -> kaynaktaki ad
TAKMA_ADLAR = {
    "basaksehir": "buyuksehyr", "istanbulbasaksehir": "buyuksehyr",
    "gaziantepfk": "gaziantep", "manutd": "manunited", "manchesterunited": "manunited",
    "manchestercity": "mancity", "tottenham": "tottenham", "spurs": "tottenham",
    "atleticomadrid": "athmadrid", "atletico": "athmadrid", "athleticbilbao": "athbilbao",
    "bayernmunih": "bayernmunich", "bayern": "bayernmunich", "psg": "parissg",
    "parissaintgermain": "parissg", "inter": "inter", "gladbach": "mgladbach",
    "leverkusen": "leverkusen", "wolves": "wolves", "sporting": "spcp", "sportinglizbon": "spcp",
}
ONBELLEK = os.path.join(os.path.expanduser("~"), ".cache", "mac-tahmin")
ONBELLEK_SAAT = 2          # güncel sezon dosyası bu kadar saatte bir yenilenir
YARI_OMUR_GUN = 240        # bir maçın ağırlığı bu kadar günde yarıya iner
ONSEL_MAC = 3              # az maçı olan takımı lig ortalamasına çeken sanal maç sayısı
ONSEL_GOL = 30             # ilk yarı gol payı için aynı işi gören sanal gol sayısı
MAKS_GOL = 12
# Takım gücü sinyalinin bileşenleri. Geriye dönük testte (8 lig, 3.631 maç) piyasa
# beklentisine ağırlık vermek yalnız gole göre daha isabetli çıktı; xG'nin katkısı küçük.
AGIRLIK_GOL, AGIRLIK_XG, AGIRLIK_PIYASA = 1.0, 1.0, 3.0
PIYASA_PAYI = 0.9          # maçın kendi güncel oranı varsa beklenen goldeki payı
YAKLASAN = "https://www.football-data.co.uk/fixtures.csv"
KORNER_CIZGILER = (8.5, 9.5, 10.5)
KORNER_R = 50.0            # toplam kornerin negatif binom yayılım katsayısı (geriye dönük testten)


def sezonlar(bugun, adet=3):
    """Bugünden geriye doğru sezon kodları: 2026-11 -> ['2627', '2526', '2425']."""
    bas = bugun.year if bugun.month >= 7 else bugun.year - 1
    return ["%02d%02d" % ((bas - i) % 100, (bas - i + 1) % 100) for i in range(adet)]


def indir(lig, sezon, guncel):
    """CSV metnini döndürür; önbellekte tazeyse oradan okur. Yoksa None."""
    os.makedirs(ONBELLEK, exist_ok=True)
    yol = os.path.join(ONBELLEK, "%s_%s.csv" % (lig, sezon))
    omur = ONBELLEK_SAAT * 3600 if guncel else 30 * 86400
    if os.path.exists(yol) and time.time() - os.path.getmtime(yol) < omur:
        with open(yol, encoding="utf-8") as f:
            return f.read()
    try:
        istek = urllib.request.Request(KAYNAK.format(sezon=sezon, lig=lig),
                                       headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(istek, timeout=30) as yanit:
            metin = yanit.read().decode("utf-8-sig", errors="replace")
    except Exception:
        if os.path.exists(yol):                 # ağ yoksa eski kopya da iş görür
            with open(yol, encoding="utf-8") as f:
                return f.read()
        return None
    if "HomeTeam" not in metin[:500]:
        return None
    with open(yol, "w", encoding="utf-8") as f:
        f.write(metin)
    return metin


def yaklasan_ulusal():
    """Yaklaşan lig maçlarının güncel oranları:
    {(lig, ev, dep): (tarih, piyasa_ev_gol, piyasa_dep_gol, başlama anı (epoch sn))}"""
    os.makedirs(ONBELLEK, exist_ok=True)
    yol = os.path.join(ONBELLEK, "fixtures.csv")
    metin = None
    if not (os.path.exists(yol) and time.time() - os.path.getmtime(yol) < 3600):
        try:
            istek = urllib.request.Request(YAKLASAN, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(istek, timeout=30) as yanit:
                metin = yanit.read().decode("utf-8-sig", errors="replace")
            if "HomeTeam" in metin[:500]:
                with open(yol, "w", encoding="utf-8") as f:
                    f.write(metin)
        except Exception:
            metin = None
    if metin is None and os.path.exists(yol):
        with open(yol, encoding="utf-8") as f:
            metin = f.read()
    sonuc = {}
    for s in csv.DictReader(io.StringIO(metin or "")):
        try:
            gun = s["Date"].strip()
            tarih = datetime.strptime(gun, "%d/%m/%Y" if len(gun) > 8 else "%d/%m/%y").date()
            le, ld = piyasa.csv_satiri(s)
            if le is not None and tarih >= date.today():
                try:                # kaynaktaki saatler Birleşik Krallık saatidir
                    an = int(datetime.strptime(gun + " " + s["Time"].strip(),
                                               ("%d/%m/%Y" if len(gun) > 8 else "%d/%m/%y") + " %H:%M")
                             .replace(tzinfo=ZoneInfo("Europe/London")).timestamp())
                except Exception:
                    an = None
                sonuc[(s["Div"].strip(), s["HomeTeam"].strip(), s["AwayTeam"].strip())] = (tarih, le, ld, an)
        except (KeyError, ValueError, AttributeError):
            continue
    return sonuc


def maclari_oku(metin):
    """CSV -> [(tarih, ev, dep, ev_gol, dep_gol, iy_ev_gol, iy_dep_gol,
                ev_xG, dep_xG, piyasa_ev_gol, piyasa_dep_gol, ev_korner, dep_korner)]
    xG, piyasa beklentisi ve korner olmayan maçlarda None."""
    maclar = []
    for s in csv.DictReader(io.StringIO(metin)):
        try:
            gun = s["Date"].strip()
            tarih = datetime.strptime(gun, "%d/%m/%Y" if len(gun) > 8 else "%d/%m/%y").date()
            maclar.append((tarih, s["HomeTeam"].strip(), s["AwayTeam"].strip(),
                           int(s["FTHG"]), int(s["FTAG"]), int(s["HTHG"]), int(s["HTAG"]),
                           _sayi(s.get("HxG")), _sayi(s.get("AxG"))) + piyasa.csv_satiri(s) +
                          (_sayi(s.get("HC")), _sayi(s.get("AC"))))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue                            # oynanmamış / eksik satır
    return maclar


def _sayi(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def sinyal(m):
    """Maçın takım gücü göstergesi: atılan gol, xG ve piyasanın maç öncesi
    beklediği golün ağırlıklı ortalaması (olmayan bileşen dışarıda kalır)."""
    parca = [(AGIRLIK_GOL, m[3], m[4])]
    if len(m) > 10 and m[9] is not None:
        parca.append((AGIRLIK_PIYASA, m[9], m[10]))
    if len(m) > 8 and m[7] is not None and m[8] is not None:
        parca.append((AGIRLIK_XG, m[7], m[8]))
    top = sum(p[0] for p in parca)
    return sum(p[0] * p[1] for p in parca) / top, sum(p[0] * p[2] for p in parca) / top


def sade(ad):
    """'Fenerbahçe SK' -> 'fenerbahcesk' (karşılaştırma için)."""
    ad = ad.replace("ı", "i").replace("İ", "i").lower()
    ad = unicodedata.normalize("NFKD", ad)
    return "".join(c for c in ad if c.isascii() and c.isalnum())


def benzerlik(aranan, takim):
    a, t = sade(aranan), sade(takim)
    a = TAKMA_ADLAR.get(a, a)
    if a == t:
        return 1.0
    if len(a) >= 4 and (a in t or t in a):
        return 0.9
    return difflib.SequenceMatcher(None, a, t).ratio()


def takimlari_bul(ev, dep, lig_takimlari, lig=None):
    """İki takımın birlikte en iyi eşleştiği ligi ve kaynaktaki adlarını bulur."""
    en_iyi = None
    for kod, takimlar in lig_takimlari.items():
        if lig and kod != lig:
            continue
        e = max(takimlar, key=lambda t: benzerlik(ev, t))
        d = max(takimlar, key=lambda t: benzerlik(dep, t))
        puan = min(benzerlik(ev, e), benzerlik(dep, d))
        if e != d and (en_iyi is None or puan > en_iyi[0]):
            en_iyi = (puan, kod, e, d)
    return en_iyi


def poisson(lam, k):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def dc_duzeltme(x, y, le, ld, rho):
    """Dixon-Coles: düşük skorlarda bağımsız Poisson'un sapmasını düzeltir."""
    if x == 0 and y == 0:
        return 1 - le * ld * rho
    if x == 0 and y == 1:
        return 1 + le * rho
    if x == 1 and y == 0:
        return 1 + ld * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def model_kur(maclar, bugun):
    """Ağırlıklı Poisson modelini yinelemeli olarak oturtur."""
    agir = [0.5 ** ((bugun - m[0]).days / YARI_OMUR_GUN) for m in maclar]
    sig = [sinyal(m) for m in maclar]
    takimlar = sorted({m[1] for m in maclar} | {m[2] for m in maclar})
    huc = dict.fromkeys(takimlar, 1.0)
    sav = dict.fromkeys(takimlar, 1.0)
    w_top = sum(agir)
    taban = sum(w * (g[0] + g[1]) for w, g in zip(agir, sig)) / (2 * w_top)
    ev_av = 1.2
    for _ in range(60):
        atilan = dict.fromkeys(takimlar, 0.0); atilan_b = dict.fromkeys(takimlar, 0.0)
        yenen = dict.fromkeys(takimlar, 0.0); yenen_b = dict.fromkeys(takimlar, 0.0)
        for w, m, (eg, dg) in zip(agir, maclar, sig):
            e, d = m[1], m[2]
            atilan[e] += w * eg; atilan_b[e] += w * taban * ev_av * sav[d]
            atilan[d] += w * dg; atilan_b[d] += w * taban * sav[e]
            yenen[e] += w * dg; yenen_b[e] += w * taban * huc[d]
            yenen[d] += w * eg; yenen_b[d] += w * taban * ev_av * huc[e]
        on = ONSEL_MAC * taban
        huc = {t: (atilan[t] + on) / (atilan_b[t] + on) for t in takimlar}
        sav = {t: (yenen[t] + on) / (yenen_b[t] + on) for t in takimlar}
        ort = math.exp(sum(math.log(v) for v in huc.values()) / len(takimlar))
        huc = {t: v / ort for t, v in huc.items()}
        ort = math.exp(sum(math.log(v) for v in sav.values()) / len(takimlar))
        sav = {t: v / ort for t, v in sav.items()}
        ev_b = sum(w * huc[m[1]] * sav[m[2]] for w, m in zip(agir, maclar))
        dep_b = sum(w * huc[m[2]] * sav[m[1]] for w, m in zip(agir, maclar))
        ev_g = sum(w * g[0] for w, g in zip(agir, sig))
        dep_g = sum(w * g[1] for w, g in zip(agir, sig))
        taban = dep_g / dep_b
        ev_av = ev_g / (taban * ev_b)

    def lamdalar(e, d):
        return taban * ev_av * huc[e] * sav[d], taban * huc[d] * sav[e]

    # Dixon-Coles rho: düşük skorlu maçların olabilirliğini en çok artıran değer
    def olabilirlik(rho):
        top = 0.0
        for w, m in zip(agir, maclar):
            if m[3] <= 1 and m[4] <= 1:
                le, ld = lamdalar(m[1], m[2])
                top += w * math.log(max(dc_duzeltme(m[3], m[4], le, ld, rho), 1e-9))
        return top
    rho = max((r / 100 for r in range(-20, 11)), key=olabilirlik)

    # İlk yarı gol payı: lig geneli ve (lig ortalamasına çekilmiş) takım bazında
    iy = dict.fromkeys(takimlar, 0.0); tum = dict.fromkeys(takimlar, 0.0)
    for w, m in zip(agir, maclar):
        e, d, eg, dg, ieg, idg = m[1:7]
        for t in (e, d):
            iy[t] += w * (ieg + idg); tum[t] += w * (eg + dg)
    lig_pay = sum(iy.values()) / sum(tum.values())
    pay = {t: (iy[t] + ONSEL_GOL * lig_pay) / (tum[t] + ONSEL_GOL) for t in takimlar}

    return {"lamdalar": lamdalar, "rho": rho, "pay": pay, "lig_pay": lig_pay,
            "huc": huc, "sav": sav, "ev_av": ev_av, "taban": taban,
            "korner": korner_modeli(maclar, bugun)}


def korner_modeli(maclar, bugun):
    """Aynı hücum/savunma modelinin kornerle oturtulmuşu (korner verisi yoksa None)."""
    k = [(m[0], m[1], m[2], m[11], m[12], 0, 0) for m in maclar
         if len(m) > 12 and m[11] is not None and m[12] is not None]
    return model_kur(k, bugun) if len(k) >= 50 else None


def nb_dagilim(ort, r, n):
    """Negatif binom olasılıkları 0..n-1 (ortalama ort, yayılım r; r büyüdükçe Poisson)."""
    p = [(r / (r + ort)) ** r]
    for k in range(n - 1):
        p.append(p[-1] * (k + r) / (k + 1) * ort / (r + ort))
    return p


def _sonuc(x, y):
    return 0 if x > y else 1 if x == y else 2


def tahmin_et(model, ev, dep, piyasa_gol=None):
    """piyasa_gol: bu maçın güncel oranlarından çözülen beklenen goller:
    (ev, dep) ya da (ev, dep, ilk yarı toplamı)."""
    le, ld = model["lamdalar"](ev, dep)
    if piyasa_gol and piyasa_gol[0] is not None:
        le = PIYASA_PAYI * piyasa_gol[0] + (1 - PIYASA_PAYI) * le
        ld = PIYASA_PAYI * piyasa_gol[1] + (1 - PIYASA_PAYI) * ld
    rho = model["rho"]
    n = MAKS_GOL + 1

    # Maç sonu skor matrisi
    ms = [[poisson(le, x) * poisson(ld, y) * dc_duzeltme(x, y, le, ld, rho)
           for y in range(n)] for x in range(n)]
    top = sum(map(sum, ms))
    ms = [[p / top for p in satir] for satir in ms]
    p1 = sum(ms[x][y] for x in range(n) for y in range(n) if x > y)
    px = sum(ms[x][x] for x in range(n))
    alt15, alt25, alt35 = (sum(ms[x][y] for x in range(n) for y in range(n) if x + y <= k)
                           for k in (1, 2, 3))
    kg = sum(ms[x][y] for x in range(1, n) for y in range(1, n))

    # Yarılar: toplam gol beklentisi ilk yarı payına göre bölünür
    s = model["pay"][ev] + model["pay"][dep] - model["lig_pay"]
    if piyasa_gol and len(piyasa_gol) > 2 and piyasa_gol[2] and piyasa_gol[0] is not None:
        # İlk yarı oranı varsa ilk yarı gol payı da ağırlıklı olarak piyasadan alınır
        s = PIYASA_PAYI * piyasa_gol[2] / (piyasa_gol[0] + piyasa_gol[1]) + (1 - PIYASA_PAYI) * s
    s = min(max(s, 0.30), 0.60)
    l1, l2 = s * (le + ld), (1 - s) * (le + ld)
    y1 = [poisson(l1, k) for k in range(n)]
    y2 = [poisson(l2, k) for k in range(n)]
    iy_esit = sum(y1[k] * y2[k] for k in range(n))
    iy_cok = sum(y1[a] * y2[b] for a in range(n) for b in range(a))

    # Yarı bazında karşılıklı gol: o yarıda iki takım da gol atar mı
    kg1 = (1 - math.exp(-s * le)) * (1 - math.exp(-s * ld))
    kg2 = (1 - math.exp(-(1 - s) * le)) * (1 - math.exp(-(1 - s) * ld))

    # Yarı skorları: iki takımın her yarıdaki golleri bağımsız Poisson
    m = 9
    e1 = [poisson(s * le, k) for k in range(m)]
    d1 = [poisson(s * ld, k) for k in range(m)]
    e2 = [poisson((1 - s) * le, k) for k in range(m)]
    d2 = [poisson((1 - s) * ld, k) for k in range(m)]
    r1, r2 = [0.0] * 3, [0.0] * 3                 # yarı sonucu: ev / berabere / deplasman
    for a in range(m):
        for b in range(m):
            r1[_sonuc(a, b)] += e1[a] * d1[b]
            r2[_sonuc(a, b)] += e2[a] * d2[b]
    # İlk yarı / maç sonucu: ikinci yarı gol farkının dağılımı üzerinden
    fark2 = {}
    for c in range(m):
        for d in range(m):
            fark2[c - d] = fark2.get(c - d, 0.0) + e2[c] * d2[d]
    iyms = [[0.0] * 3 for _ in range(3)]
    for a in range(m):
        for b in range(m):
            for f, pf in fark2.items():
                iyms[_sonuc(a, b)][_sonuc(a - b + f, 0)] += e1[a] * d1[b] * pf
    ms3 = (p1, px, 1 - p1 - px)                   # maç sonucu sütunları 1X2 ile tutarlı olsun
    for j in range(3):
        top_j = sum(iyms[i][j] for i in range(3))
        for i in range(3):
            iyms[i][j] *= ms3[j] / top_j
    isaret = ("1", "X", "2")

    def ms_ile(kosul, evet, hayir):
        """Maç sonucu ile ikinci bir koşulun (toplam gol, karşılıklı gol) birleşimi."""
        satirlar = []
        for j in range(3):
            pe = sum(ms[x][y] for x in range(n) for y in range(n) if _sonuc(x, y) == j and kosul(x, y))
            satirlar += [("%s ve %s" % (isaret[j], evet), pe), ("%s ve %s" % (isaret[j], hayir), ms3[j] - pe)]
        return satirlar

    def evet_hayir(p):
        return [("Evet", p), ("Hayır", 1 - p)]

    korner, korner_pazar = None, []
    km = model.get("korner")
    if km and ev in km["huc"] and dep in km["huc"]:
        korner = km["lamdalar"](ev, dep)
        dag = nb_dagilim(korner[0] + korner[1], KORNER_R, 40)
        for cizgi in KORNER_CIZGILER:
            alt = sum(dag[:int(cizgi) + 1])
            korner_pazar.append(("Korner %.1f Alt/Üst" % cizgi, [("Alt", alt), ("Üst", 1 - alt)]))

    skorlar = sorted(((ms[x][y], x, y) for x in range(8) for y in range(8)), reverse=True)
    return {
        "beklenen": (le, ld, l1, l2),
        "korner": korner,
        "skorlar": skorlar[:3],
        "pazarlar": [
            ("Maç Sonucu (1X2)", [("1", p1), ("X", px), ("2", 1 - p1 - px)]),
            ("1.5 Gol Alt/Üst", [("Alt", alt15), ("Üst", 1 - alt15)]),
            ("2.5 Gol Alt/Üst", [("Alt", alt25), ("Üst", 1 - alt25)]),
            ("3.5 Gol Alt/Üst", [("Alt", alt35), ("Üst", 1 - alt35)]),
            ("Karşılıklı Gol", [("Var", kg), ("Yok", 1 - kg)]),
            ("İlk Yarı 0.5 Alt/Üst", [("Alt", y1[0]), ("Üst", 1 - y1[0])]),
            ("İlk Yarı 1.5 Alt/Üst", [("Alt", y1[0] + y1[1]), ("Üst", 1 - y1[0] - y1[1])]),
            ("İki Yarıda da Gol", [("Olur", (1 - y1[0]) * (1 - y2[0])),
                                    ("Olmaz", 1 - (1 - y1[0]) * (1 - y2[0]))]),
            ("En Çok Gol Olacak Yarı", [("1. Yarı", iy_cok), ("Eşit", iy_esit),
                                         ("2. Yarı", 1 - iy_cok - iy_esit)]),
            ("1. Yarı / 2. Yarı Karşılıklı Gol", [
                ("Evet/Evet", kg1 * kg2), ("Evet/Hayır", kg1 * (1 - kg2)),
                ("Hayır/Evet", (1 - kg1) * kg2), ("Hayır/Hayır", (1 - kg1) * (1 - kg2))]),
            ("İlk Yarı / Maç Sonucu", [("%s/%s" % (isaret[i], isaret[j]), iyms[i][j])
                                       for i in range(3) for j in range(3)]),
            ("Maç Sonucu + 1.5 Gol", ms_ile(lambda x, y: x + y > 1, "Üst", "Alt")),
            ("Maç Sonucu + 2.5 Gol", ms_ile(lambda x, y: x + y > 2, "Üst", "Alt")),
            ("Maç Sonucu + Karşılıklı Gol", ms_ile(lambda x, y: x > 0 and y > 0, "Var", "Yok")),
            ("Ev Sahibi Herhangi Bir Yarıyı Kazanır", evet_hayir(1 - (1 - r1[0]) * (1 - r2[0]))),
            ("Deplasman Herhangi Bir Yarıyı Kazanır", evet_hayir(1 - (1 - r1[2]) * (1 - r2[2]))),
            ("Ev Sahibi Her İki Yarıyı Kazanır", evet_hayir(r1[0] * r2[0])),
            ("Deplasman Her İki Yarıyı Kazanır", evet_hayir(r1[2] * r2[2])),
        ] + korner_pazar,
    }


def yazdir(ev, dep, lig_adi, mac_sayisi, son_tarih, sonuc, uyari=None, oran=None, idd=None):
    """idd: bu maçın İddaa oranları (iddaa.yaklasan çıktısındaki kayıt) ya da None."""
    le, ld, l1, l2 = sonuc["beklenen"]
    print()
    print("=" * 52)
    print("  %s  -  %s" % (ev, dep))
    print("  %s | %d maç, son veri %s" % (lig_adi, mac_sayisi, son_tarih.strftime("%d.%m.%Y")))
    print("=" * 52)
    print("  Beklenen gol: %s %.2f - %.2f %s" % (ev, le, ld, dep))
    print("  Yarılara göre: 1. yarı %.2f, 2. yarı %.2f" % (l1, l2))
    if sonuc.get("korner"):
        print("  Beklenen korner: %.1f - %.1f (toplam %.1f)" % (sonuc["korner"] + (sum(sonuc["korner"]),)))
    if oran:
        print("  Bahis piyasası: %s tarihli maçın güncel %soranları dahil" % (
            oran[0].strftime("%d.%m.%Y"), "İddaa " if idd else ""))
        print("  (piyasanın beklediği gol %.2f - %.2f)" % (oran[1], oran[2]))
    else:
        print("  Bu maç için güncel bahis oranı yok; takım güçleri geçmiş")
        print("  maçların oranları, golleri ve xG verisinden hesaplandı.")
    for baslik, secenekler in sonuc["pazarlar"]:
        favori = max(p for _, p in secenekler)
        print()
        io = idd["oran"].get(baslik, {}) if idd else {}
        print("  " + baslik + ("   (MBS %d)" % idd["pazar_mbs"][baslik] if io else ""))
        print("  " + "-" * 40)
        for ad, p in secenekler:
            print("  %-11s %% %5.1f    adil %6.2f %s%s" % (
                ad, 100 * p, 1 / max(p, 1e-9),
                ("   İddaa %6.2f" % io[ad]) if ad in io else ("   İddaa      -" if idd else ""),
                "  <--" if p == favori else ""))
    print()
    print("  En olası skorlar: " + ",  ".join("%d-%d (%%%.0f)" % (x, y, 100 * p)
                                             for p, x, y in sonuc["skorlar"]))
    print()
    if uyari:
        print("  Not: " + uyari)
    if idd:
        print("  İddaa sütunu iddaa.com programındaki orandır; oynamadan önce")
        print("  güncel oranı ve MBS'yi İddaa'da doğrulayın.")
    print("  Adil oran = 1 / olasılık (komisyonsuz). İstatistiksel")
    print("  tahmindir; sakatlık, kadro, motivasyon gibi etkenleri bilmez.")
    print()


def main():
    import avrupa                                # avrupa.py bu modülü kullanır
    ap = argparse.ArgumentParser(description="Futbol maç tahmini (ilk takım ev sahibi).")
    ap.add_argument("ev", nargs="?", help="ev sahibi takım")
    ap.add_argument("dep", nargs="?", help="deplasman takımı")
    ap.add_argument("--lig", help="lig kodu (ör. T1, E0, CL, EL, ECL); verilmezse otomatik bulunur")
    ap.add_argument("--takimlar", metavar="LIG", help="ligdeki takım adlarını listele")
    a = ap.parse_args()

    bugun = date.today()
    szn = sezonlar(bugun)
    kupa_adi = {k: v[0] for k, v in avrupa.KUPALAR.items()}
    tum_kodlar = ", ".join(list(LIGLER) + list(kupa_adi))
    lig = (a.takimlar or a.lig or "").upper()
    if lig and lig not in LIGLER and lig not in kupa_adi:
        sys.exit("Lig bulunamadı: %s. Kodlar: %s" % (lig, tum_kodlar))

    # Yalnızca tek bir ulusal lig istendiyse onu, aksi halde her şeyi indir
    if lig in LIGLER:
        metinler = [indir(lig, s, i == 0) for i, s in enumerate(szn)]
        okunan = [maclari_oku(t) if t else [] for t in metinler]
        veri = {"ulusal": {lig: (okunan[0], sum(okunan, []))} if okunan[0] else {}, "kupa": {}}
        kupa_takim = {}
    else:
        veri = avrupa.yukle(bugun)
        kupa_takim = {k: t for k, t in avrupa.kupa_takimlari(veri, bugun).items() if t}
    if not veri["ulusal"]:
        sys.exit("Veri indirilemedi (football-data.co.uk). İnternet bağlantısını kontrol edin.")

    lig_takimlari = {k: sorted({x[1] for x in g} | {x[2] for x in g})
                     for k, (g, _) in veri["ulusal"].items()}
    lig_takimlari.update(kupa_takim)             # ulusal ligler önce: eşitlikte onlar seçilir

    if a.takimlar:
        if lig not in lig_takimlari:
            sys.exit("Bu lig için veri indirilemedi: %s" % lig)
        print("%s:" % (LIGLER.get(lig) or kupa_adi[lig]))
        print(", ".join(lig_takimlari[lig]))
        return

    ev = a.ev or input("Ev sahibi takım: ").strip()
    dep = a.dep or input("Deplasman takımı: ").strip()
    if not ev or not dep:
        sys.exit("İki takım adı gerekli.")

    bulunan = takimlari_bul(ev, dep, lig_takimlari, lig or None)
    if not bulunan or bulunan[0] < 0.6:
        oneri = []
        for ad in (ev, dep):
            hepsi = {(benzerlik(ad, t), t, k) for k, ts in lig_takimlari.items() for t in ts}
            oneri.append("  '%s' için en yakınlar: %s" % (ad, ", ".join(
                "%s (%s)" % (t, k) for _, t, k in sorted(hepsi, reverse=True)[:3])))
        sys.exit("İki takım aynı ligde veya aynı Avrupa kupasında bulunamadı.\n" +
                 "\n".join(oneri) + "\nTakım adları için: python3 tahmin.py --takimlar T1")
    _, kod, ev_ad, dep_ad = bulunan
    yaklasan = yaklasan_ulusal()
    if kupa_takim:
        yaklasan.update(avrupa.yaklasan(veri, bugun))
    import iddaa
    idd = iddaa.birlestir(yaklasan, lig_takimlari).get((kod, ev_ad, dep_ad))
    oran = yaklasan.get((kod, ev_ad, dep_ad))
    piyasa_gol = (oran[1:3] + tuple(oran[4:5])) if oran else None

    if kod in kupa_adi:
        model = avrupa.model_kur(veri, bugun)
        verisiz = [t for t in (ev_ad, dep_ad) if not model["bilinen"][t]]
        uyari = None
        if verisiz:
            uyari = ("%s için ulusal lig verisi yok; gücü yalnızca Avrupa\n  maçlarından "
                     "tahmin edildi, belirsizlik daha yüksek." % " ve ".join(verisiz))
        yazdir(ev_ad, dep_ad, kupa_adi[kod] + " (son 3 yılın Avrupa maçları)", model["mac"],
               model["son"], tahmin_et(model, ev_ad, dep_ad, piyasa_gol), uyari, oran, idd)
    else:
        maclar = veri["ulusal"][kod][1]
        model = model_kur(maclar, bugun)
        yazdir(ev_ad, dep_ad, LIGLER[kod], len(maclar), max(m[0] for m in maclar),
               tahmin_et(model, ev_ad, dep_ad, piyasa_gol), oran=oran, idd=idd)


if __name__ == "__main__":
    main()
