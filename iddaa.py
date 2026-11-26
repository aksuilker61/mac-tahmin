"""İddaa (Türkiye'nin yasal bahis oyunu) oranları.

iddaa.com'un herkese açık program servisinden yaklaşan maçların oranları ve
MBS (kuponda en az kaç maç gerektiği) alınır. Bu oranlar iki işe yarar:
maçın beklenen golleri piyasa bilgisi olarak onlardan çözülür ve sayfada
programın adil oranının yanında gösterilir.

Servis belgelenmiş bir API değildir; biçimi değişirse bu modül boş döner ve
program diğer kaynaklarla çalışmaya devam eder.
"""
import json
import os
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import piyasa
import tahmin as T

MACLAR = "https://sportsbookv2.iddaa.com/sportsbook/events?st=1&type=0&version=0"
LIGLER = "https://sportsbookv2.iddaa.com/sportsbook/competitions"
ONBELLEK_SN = 3600

# İddaa'daki lig adı -> programdaki lig kodu
LIG_ADI = {
    "Türkiye Süper Lig": "T1", "İngiltere Premier Lig": "E0", "İngiltere Championship": "E1",
    "İngiltere 1. Lig": "E2", "İngiltere 2. Lig": "E3", "İskoçya Premiership": "SC0",
    "Almanya Bundesliga": "D1", "Almanya 2. Bundesliga": "D2", "İtalya Serie A": "I1",
    "İtalya Serie B": "I2", "İspanya La Liga": "SP1", "İspanya La Liga 2": "SP2",
    "Fransa Ligue 1": "F1", "Fransa Ligue 2": "F2", "Hollanda Eredivisie": "N1",
    "Belçika Pro Lig": "B1", "Portekiz Premier Lig": "P1", "Yunanistan Süper Lig": "G1",
    "UEFA Şampiyonlar Ligi": "CL", "UEFA Avrupa Ligi": "EL", "UEFA Konferans Ligi": "ECL",
}
# İddaa'daki takım adı -> veri kaynağındaki ad (benzerlikle bulunamayanlar)
TAKIM_ADI = {
    "AE Kifisia FC": "Kifisia", "Dundee Utd": "Dundee United", "O.H. Leuven": "Oud-Heverlee Leuven",
    "Union Saint Gilloise": "St. Gilloise", "Queens Park Rangers": "QPR", "Wolverhampton": "Wolves",
    "MK Dons": "Milton Keynes Dons", "Kopenhag": "F.C. København", "Kuopion": "KuPS Kuopio",
    "Kızıl Yıldız": "Red Star Belgrade", "Zalgiris Kaunas": "Kauno Zalgiris", "Marsilya": "Marseille",
    "PSV": "PSV Eindhoven", "N. Madeira": "Nacional", "Sporting Lizbon": "Sp Lisbon",
    "Cenova": "Genoa", "Karabağ": "FK Qarabag", "Dinamo Kiev": "Dynamo Kyiv",
    "Bodo Glimt": "Bodo/Glimt", "Inter": "Inter", "Inter Club De Escaldes": "Inter D'Escaldes",
}

X = {"0": "X"}


def _cevir(ad):
    """'1 ve 0' değil, tek tek: '0' -> 'X' (beraberlik); '1/0' -> '1/X'."""
    return "/".join(X.get(p, p) for p in ad.split("/"))


def _ms_ile(ad):
    """'0 ve Alt' -> 'X ve Alt'."""
    bas, _, son = ad.partition(" ve ")
    return "%s ve %s" % (X.get(bas, bas), son)


# (tür, alt tür) -> (programdaki pazar adı kalıbı, seçenek adı çevirisi)
PAZARLAR = {
    (1, 1): ("Maç Sonucu (1X2)", _cevir),
    (2, 101): ("%s Gol Alt/Üst", None),
    (2, 89): ("Karşılıklı Gol", None),
    (2, 60): ("İlk Yarı %s Alt/Üst", None),
    (2, 6): ("En Çok Gol Olacak Yarı", lambda a: {"1.": "1. Yarı", "2.": "2. Yarı"}.get(a, a)),
    (2, 90): ("İlk Yarı / Maç Sonucu", _cevir),
    (2, 7): ("Maç Sonucu + %s Gol", _ms_ile),
    (2, 698): ("Maç Sonucu + Karşılıklı Gol", _ms_ile),
    (2, 69): ("Ev Sahibi Her İki Yarıyı Kazanır", None),
    (2, 72): ("Deplasman Her İki Yarıyı Kazanır", None),
    (2, 48): ("Korner %s Alt/Üst", None),
}


def _getir(url, dosya):
    os.makedirs(T.ONBELLEK, exist_ok=True)
    yol = os.path.join(T.ONBELLEK, dosya)
    if os.path.exists(yol) and time.time() - os.path.getmtime(yol) < ONBELLEK_SN:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    try:
        istek = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(istek, timeout=60) as yanit:
            veri = json.loads(yanit.read())
        if not veri.get("isSuccess"):
            raise ValueError("isSuccess değil")
    except Exception as hata:
        print("iddaa: %s alınamadı (%s)" % (dosya, hata))
        if os.path.exists(yol):
            with open(yol, encoding="utf-8") as f:
                return json.load(f)
        return None
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f)
    return veri


def _esle(ad, takimlar):
    """İddaa takım adını ligin takım listesinde bul (yeterince benzemiyorsa None)."""
    if ad in TAKIM_ADI:
        return TAKIM_ADI[ad] if TAKIM_ADI[ad] in takimlar else None
    en = max(takimlar, key=lambda t: T.benzerlik(ad, t))
    return en if T.benzerlik(ad, en) >= 0.72 else None


def _pazarlar(olay):
    """Olayın oranları: {pazar adı: {seçenek: oran}} ve pazar bazında MBS."""
    oranlar, mbs = {}, {}
    for m in olay.get("m", []):
        tanim = PAZARLAR.get((m.get("t"), m.get("st")))
        if not tanim:
            continue
        kalip, cevir = tanim
        baslik = kalip % m["sov"] if "%s" in kalip else kalip
        if "%s" in kalip and not m.get("sov"):
            continue
        secenekler = {}
        for o in m.get("o", []):
            try:
                oran = float(o["odd"])
            except (KeyError, TypeError, ValueError):
                continue
            if oran > 1.0:                      # 1,00 "bu seçenek oynanamaz" demek
                secenekler[cevir(o["n"]) if cevir else o["n"]] = oran
        if secenekler:
            oranlar[baslik] = secenekler
            mbs[baslik] = m.get("mbc") or olay.get("mbc") or 1
    return oranlar, mbs


def _beklenen_gol(oranlar):
    """Oranlardan piyasanın beklediği goller: (ev, deplasman, ilk yarı toplamı).
    Maç sonucu ve gol alt/üst yoksa (None, None, None); ilk yarı oranı yoksa üçüncüsü None."""
    ms = oranlar.get("Maç Sonucu (1X2)", {})
    x = piyasa.paysiz(ms.get("1"), ms.get("X"), ms.get("2"))
    if not x:
        return None, None, None
    ilk = None
    for cizgi in ("1.5", "0.5"):
        au = oranlar.get("İlk Yarı %s Alt/Üst" % cizgi, {})
        g = piyasa.paysiz(au.get("Üst"), au.get("Alt"))
        if g:
            ilk = piyasa.toplam_coz(g[0], float(cizgi))
            break
    for cizgi in ("2.5", "3.5", "1.5"):
        au = oranlar.get("%s Gol Alt/Üst" % cizgi, {})
        g = piyasa.paysiz(au.get("Üst"), au.get("Alt"))
        if g:
            return piyasa.lamda_coz(x[0], x[2], g[0], float(cizgi)) + (ilk,)
    return None, None, None


def yaklasan(lig_takimlari):
    """Yaklaşan maçların İddaa oranları.

    lig_takimlari: {lig kodu: [takım adları]} (programın tanıdığı adlar)
    Döner: {(lig, ev, dep): {"an": başlama anı (epoch sn), "gol": (ev, dep, ilk yarı toplamı) beklenen gol,
            "mbs": maç sonucu pazarının MBS'i, "oran": {pazar: {seçenek: oran}},
            "pazar_mbs": {pazar: MBS}}}, eşleşmeyen takım adları
    """
    maclar, ligler = _getir(MACLAR, "iddaa_maclar.json"), _getir(LIGLER, "iddaa_ligler.json")
    if not maclar or not ligler:
        return {}, []
    try:
        lig_kodu = {c["i"]: LIG_ADI[c["n"]] for c in ligler["data"]
                    if c.get("si") == "1" and c.get("n") in LIG_ADI}
        olaylar = maclar["data"]["events"]
    except (KeyError, TypeError):
        return {}, []
    sonuc, eslesmeyen = {}, []
    simdi = time.time()
    for o in olaylar:
        kod = lig_kodu.get(o.get("ci"))
        if not kod or kod not in lig_takimlari or o.get("sid") != 1 or o.get("d", 0) < simdi:
            continue
        ev, dep = _esle(o["hn"], lig_takimlari[kod]), _esle(o["an"], lig_takimlari[kod])
        if not ev or not dep or ev == dep:
            eslesmeyen.append((kod, o["hn"], o["an"]))
            continue
        oranlar, pazar_mbs = _pazarlar(o)
        if not oranlar:
            continue
        sonuc[(kod, ev, dep)] = {
            "an": int(o["d"]), "gol": _beklenen_gol(oranlar), "oran": oranlar,
            "mbs": pazar_mbs.get("Maç Sonucu (1X2)", o.get("mbc") or 1), "pazar_mbs": pazar_mbs,
        }
    return sonuc, eslesmeyen


def birlestir(yaklasan_mac, lig_takimlari):
    """İddaa oranlarını yaklaşan maç sözlüğüne işler: İddaa'da olan maçta beklenen
    goller ve başlama saati oradan alınır. Döner: İddaa sözlüğü (bkz. yaklasan)."""
    oranlar, eslesmeyen = yaklasan(lig_takimlari)
    for anahtar, o in oranlar.items():
        if o["gol"][0] is not None:
            gun = datetime.fromtimestamp(o["an"], ZoneInfo("Europe/Istanbul")).date()
            yaklasan_mac[anahtar] = (gun, o["gol"][0], o["gol"][1], o["an"], o["gol"][2])
    print("iddaa: %d maç eşleşti" % len(oranlar) +
          (", eşleşmeyen: %s" % eslesmeyen if eslesmeyen else ""))
    return oranlar
