#!/usr/bin/env python3
"""Web arayüzünü üretir: tüm liglerin ve Avrupa kupalarının model
katsayılarını hesaplayıp sablon.html içine gömer, index.html olarak yazar.

    python3 web_olustur.py                       # index.html (sayfa parçası)
    python3 web_olustur.py --tam site/index.html # tek başına açılan tam sayfa

Sayfa internetten veri çekmez; tahminler ve yaklaşan maçların bahis oranları
bu betiğin çalıştığı andaki veriye dayanır. Güncellemek için yeniden çalıştırın.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import avrupa
import iddaa
import tahmin as T

KLASOR = os.path.dirname(os.path.abspath(__file__))
# Şablon bir sayfa parçasıdır; kendi başına yayınlanırken bu iskelete oturtulur
ISKELET = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<style>body{margin:0}img{max-width:100%%}[hidden]{display:none!important}</style>
%s</head>
<body>
%s</body>
</html>
"""


IDDAA_PAZAR = []          # sayfaya gömülen İddaa pazar adları (sıra numarasıyla anılır)


def _iddaa_kaydi(o):
    """İddaa oranlarını sayfa için sıkıştırır: {pazar sırası: [MBS, {seçenek: oran}]}"""
    if not o:
        return None
    cikti = {}
    for pazar, secenekler in o["oran"].items():
        if pazar not in IDDAA_PAZAR:
            IDDAA_PAZAR.append(pazar)
        cikti[IDDAA_PAZAR.index(pazar)] = [o["pazar_mbs"][pazar], secenekler]
    return cikti


def kayit(kod, ad, tur, m, takimlar, mac, son, yaklasan, idd, verisiz=()):
    """Bir lig/kupa için sayfaya gömülecek katsayılar.
    takım: [ad, hücum, savunma, ilk yarı gol payı, ulusal lig verisi yok mu,
            korner hücum, korner savunma]  (korner modeli yoksa son ikisi None)
    yaklaşan: [ev, dep, tarih, piyasanın beklediği ev golü, dep golü, başlama anı,
               İddaa oranları (yoksa None), piyasanın beklediği ilk yarı golü (yoksa None)]"""
    mac_oran = sorted((v[0], v[3] or 0, k[1], k[2], v[1], v[2], (v[4] if len(v) > 4 else None) or 0)
                      for k, v in yaklasan.items()
                      if k[0] == kod and k[1] in takimlar and k[2] in takimlar)
    km = m.get("korner")

    def kor(t, alan):
        return round(km[alan][t], 4) if km and t in km[alan] else None
    return {
        "kTaban": round(km["taban"], 4) if km else None, "kEv": round(km["ev_av"], 4) if km else None,
        "kod": kod, "ad": ad, "tur": tur, "mac": mac, "son": son.strftime("%d.%m.%Y"),
        "taban": round(m["taban"], 4), "ev": round(m["ev_av"], 4),
        "rho": m["rho"], "pay": round(m["lig_pay"], 4),
        "takimlar": [[t, round(m["huc"][t], 4), round(m["sav"][t], 4),
                      round(m["pay"][t], 4), int(t in verisiz), kor(t, "huc"), kor(t, "sav")]
                     for t in takimlar],
        "yaklasan": [[e, d, t.strftime("%d.%m"), round(le, 3), round(ld, 3), an,
                      _iddaa_kaydi(idd.get((kod, e, d))), round(l1, 3) or None]
                     for t, an, e, d, le, ld, l1 in mac_oran],
    }


def main():
    bugun = date.today()
    veri = avrupa.yukle(bugun)
    ligler = []
    yaklasan = T.yaklasan_ulusal()
    yaklasan.update(avrupa.yaklasan(veri, bugun))
    lig_takimlari = {k: sorted({x[1] for x in g} | {x[2] for x in g})
                     for k, (g, _) in veri["ulusal"].items()}
    lig_takimlari.update(avrupa.kupa_takimlari(veri, bugun))
    idd = iddaa.birlestir(yaklasan, lig_takimlari)

    am = avrupa.model_kur(veri, bugun)
    verisiz = {t for t, b in am["bilinen"].items() if not b}
    for kod, takimlar in avrupa.kupa_takimlari(veri, bugun).items():
        if takimlar:
            ligler.append(kayit(kod, avrupa.KUPALAR[kod][0], "kupa", am, takimlar,
                                am["mac"], am["son"], yaklasan, idd, verisiz))
            print("%-4s %3d takım (%d verisiz)" % (kod, len(takimlar), len(verisiz & set(takimlar))))

    for kod, ad in T.LIGLER.items():
        if kod not in veri["ulusal"]:
            print("atlandı:", kod)
            continue
        guncel, maclar = veri["ulusal"][kod]
        takimlar = sorted({x[1] for x in guncel} | {x[2] for x in guncel})
        ligler.append(kayit(kod, ad, "lig", am["ulusal"][kod], takimlar, len(maclar),
                            max(x[0] for x in maclar), yaklasan, idd))
        print("%-4s %3d takım, %d maç" % (kod, len(takimlar), len(maclar)))

    with open(os.path.join(KLASOR, "sablon.html"), encoding="utf-8") as f:
        sayfa = f.read()
    sayfa = sayfa.replace("__VERI__", json.dumps(
        {"ligler": ligler, "piyasaPayi": T.PIYASA_PAYI, "iddaaPazar": IDDAA_PAZAR,
         "kornerR": T.KORNER_R, "kornerCizgiler": list(T.KORNER_CIZGILER),
         "uretim": datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")},
        ensure_ascii=False,
                                                 separators=(",", ":")))
    cikti = os.path.join(KLASOR, "index.html")
    if "--tam" in sys.argv:
        cikti = os.path.abspath(sys.argv[sys.argv.index("--tam") + 1])
        bas, govde = sayfa.split('<div class="sayfa">', 1)   # başlık, yazı tipi ve stil <head>'e
        sayfa = ISKELET % (bas, '<div class="sayfa">' + govde)
        os.makedirs(os.path.dirname(cikti), exist_ok=True)
    with open(cikti, "w", encoding="utf-8") as f:
        f.write(sayfa)
    print("%s yazıldı (%d KB)" % (cikti, len(sayfa.encode()) // 1024))


if __name__ == "__main__":
    main()
