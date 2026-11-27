"""Avrupa kupaları (Şampiyonlar Ligi, Avrupa Ligi, Konferans Ligi).

Farklı liglerin takımları doğrudan karşılaştırılamaz; bu yüzden iki aşama var:
1. Her takımın kendi ligindeki hücum/savunma gücü (tahmin.model_kur).
2. Son 3 yılın Avrupa kupası maçlarından her lig için bir "lig düzeyi"
   katsayısı (hücum ve savunma). Takımın Avrupa gücü = lig içi güç x lig düzeyi.
Verisi olmayan liglerin takımları (ör. Çekya, Norveç) yalnızca kendi Avrupa
maçlarından tahmin edilir; bu takımlarda belirsizlik daha yüksektir.

Sonuçlar ESPN'in herkese açık skor servisinden alınır; ilk yarı ve 90 dakika
skorları gol dakikalarından çıkarılır (uzatma golleri sayılmaz). Aynı servis
yaklaşan maçların güncel bahis oranlarını da verir; varsa tahmine katılır.
"""
import calendar
import json
import math
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import piyasa
import tahmin as T

KAYNAK = "https://site.api.espn.com/apis/site/v2/sports/soccer/%s/scoreboard?dates=%d&limit=500"
KUPALAR = {
    "CL": ("UEFA Şampiyonlar Ligi", "uefa.champions"),
    "EL": ("UEFA Avrupa Ligi", "uefa.europa"),
    "ECL": ("UEFA Konferans Ligi", "uefa.europa.conf"),
}
# ESPN adı -> football-data adı (yazımı birebir tutmayanlar)
AD_ESLEME = {
    "1. FC Heidenheim 1846": "Heidenheim", "AC Milan": "Milan", "AEK Athens": "AEK",
    "AFC Bournemouth": "Bournemouth", "AS Monaco": "Monaco", "AS Roma": "Roma",
    "Ajax Amsterdam": "Ajax", "Athletic Club": "Ath Bilbao", "Atlético Madrid": "Ath Madrid",
    "Bayer Leverkusen": "Leverkusen", "Borussia Dortmund": "Dortmund", "Braga": "Sp Braga",
    "Borussia Mönchengladbach": "M'gladbach", "Brighton & Hove Albion": "Brighton",
    "Celta Vigo": "Celta", "Cercle Brugge KSV": "Cercle Brugge",
    "Eintracht Frankfurt": "Ein Frankfurt", "FC Porto": "Porto", "FC Twente": "Twente",
    "FC Utrecht": "Utrecht", "Feyenoord Rotterdam": "Feyenoord",
    "Heart of Midlothian": "Hearts", "Internazionale": "Inter",
    "Istanbul Basaksehir": "Buyuksehyr", "KAA Gent": "Gent", "Leicester City": "Leicester",
    "Manchester City": "Man City", "Manchester United": "Man United",
    "NEC Nijmegen": "Nijmegen", "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest", "Olympiacos": "Olympiakos",
    "Paris Saint-Germain": "Paris SG", "Racing Genk": "Genk", "Rayo Vallecano": "Vallecano",
    "Real Betis": "Betis", "Real Sociedad": "Sociedad", "Royal Antwerp": "Antwerp",
    "SC Freiburg": "Freiburg", "Sint-Truidense": "St Truiden", "Sporting CP": "Sp Lisbon",
    "Stade Rennais": "Rennes", "TSG Hoffenheim": "Hoffenheim",
    "Tottenham Hotspur": "Tottenham", "Union St.-Gilloise": "St. Gilloise",
    "VfB Stuttgart": "Stuttgart", "Vitória de Guimaraes": "Guimaraes",
    "West Ham United": "West Ham", "Wolverhampton Wanderers": "Wolves",
}


def _dakika(olay):
    """"45'+3'" -> 45 (uzatma dakikası değil, asıl dakika)."""
    metin = olay.get("clock", {}).get("displayValue", "")
    sayi = ""
    for c in metin:
        if c.isdigit():
            sayi += c
        else:
            break
    return int(sayi) if sayi else None


def _oran(k):
    """Oynanmamış maçın güncel oranlarından piyasanın beklediği goller."""
    try:
        o = (k.get("odds") or [None])[0]
        def son(d):
            return piyasa.amerikan((d.get("current") or d.get("close") or d.get("open"))["odds"])
        ml, top = o["moneyline"], o["total"]
        x = piyasa.paysiz(son(ml["home"]), son(ml["draw"]), son(ml["away"]))
        g = piyasa.paysiz(son(top["over"]), son(top["under"]))
        cizgi = float(o["overUnder"])
        if not x or not g or cizgi % 1 != 0.5:
            return [None, None]
        return list(piyasa.lamda_coz(x[0], x[2], g[0], cizgi))
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return [None, None]


def _korner(takim):
    for ist in takim.get("statistics") or []:
        if ist.get("name") == "wonCorners":
            try:
                return int(ist["displayValue"])
            except (KeyError, ValueError, TypeError):
                return None
    return None


def _ayikla(veri):
    """ESPN yanıtı -> [[tarih, ev, dep, oynandı, ev_gol, dep_gol, iy_ev, iy_dep,
                        piyasa_ev_gol, piyasa_dep_gol, başlama (UTC, ISO),
                        ev_korner, dep_korner]]
    Piyasa golleri yalnız henüz başlamamış maçta dolu; canlı oranlar kullanılmaz.
    Goller 90 dakikalık skordur. Gol dakikaları skorla tutmuyorsa maç atlanır."""
    satirlar = []
    for o in veri.get("events", []):
        try:
            k = o["competitions"][0]
            ev = next(t for t in k["competitors"] if t["homeAway"] == "home")
            dep = next(t for t in k["competitors"] if t["homeAway"] == "away")
            tarih = o["date"][:10]
            adlar = [ev["team"]["displayName"], dep["team"]["displayName"]]
            if not o["status"]["type"]["completed"]:
                basladi = o["status"]["type"].get("state") != "pre"
                satirlar.append([tarih] + adlar + [False, 0, 0, 0, 0] +
                                ([None, None] if basladi else _oran(k)) + [o["date"], None, None])
                continue
            goller = [d for d in k.get("details", []) if d.get("scoringPlay") and not d.get("shootout")]
            if len(goller) != int(ev["score"]) + int(dep["score"]):
                continue
            say = {"ev": 0, "dep": 0, "iy_ev": 0, "iy_dep": 0}
            for g in goller:
                dk = _dakika(g)
                if dk is None:
                    raise ValueError
                kim = "ev" if g["team"]["id"] == ev["team"]["id"] else "dep"
                if dk <= 90:
                    say[kim] += 1
                if dk <= 45:
                    say["iy_" + kim] += 1
            satirlar.append([tarih] + adlar + [True, say["ev"], say["dep"], say["iy_ev"], say["iy_dep"], None, None, o["date"], _korner(ev), _korner(dep)])
        except (KeyError, ValueError, StopIteration, IndexError, TypeError):
            continue
    return satirlar


def _indir(slug, yil, guncel):
    os.makedirs(T.ONBELLEK, exist_ok=True)
    yol = os.path.join(T.ONBELLEK, "espn4_%s_%d.json" % (slug, yil))
    omur = T.ONBELLEK_SAAT * 3600 if guncel else 30 * 86400
    if os.path.exists(yol) and time.time() - os.path.getmtime(yol) < omur:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    try:
        istek = urllib.request.Request(KAYNAK % (slug, yil), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(istek, timeout=40) as yanit:
            satirlar = _ayikla(json.loads(yanit.read()))
    except Exception:
        if os.path.exists(yol):
            with open(yol, encoding="utf-8") as f:
                return json.load(f)
        return []
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(satirlar, f)
    return satirlar


def yukle(bugun):
    """Ulusal lig verisi ve Avrupa maçlarını indirir (önbellekli).

    Döner: {"ulusal": {kod: (güncel sezon maçları, tüm maçlar)},
            "kupa": {kod: [satırlar]}, "ad": ESPN adı -> gösterilecek ad,
            "lig": gösterilen ad -> ulusal lig kodu}
    """
    szn = T.sezonlar(bugun)
    isler = [(k, s, i == 0) for k in T.LIGLER for i, s in enumerate(szn)]
    yillar = [bugun.year - 2, bugun.year - 1, bugun.year]
    kupa_isler = [(k, KUPALAR[k][1], y) for k in KUPALAR for y in yillar]
    with ThreadPoolExecutor(8) as havuz:
        metinler = list(havuz.map(lambda i: T.indir(*i), isler))
        kupa_veri = list(havuz.map(lambda i: _indir(i[1], i[2], i[2] == bugun.year), kupa_isler))

    ulusal, son_mac = {}, {}
    for (kod, s, ilk), metin in zip(isler, metinler):
        m = T.maclari_oku(metin) if metin else []
        guncel, tum = ulusal.setdefault(kod, ([], []))
        tum.extend(m)
        if ilk:
            guncel.extend(m)
        for x in m:                       # takımın en son oynadığı lig geçerlidir
            for t in (x[1], x[2]):
                if t not in son_mac or x[0] > son_mac[t][0]:
                    son_mac[t] = (x[0], kod)
    ulusal = {k: v for k, v in ulusal.items() if v[0]}
    lig = {t: kod for t, (_, kod) in son_mac.items() if kod in ulusal}

    sade_ad = {T.sade(t): t for t in lig}
    kupa, ad = {k: [] for k in KUPALAR}, {}
    for (k, _, _), satirlar in zip(kupa_isler, kupa_veri):
        for s in satirlar:
            for espn in (s[1], s[2]):
                if espn not in ad:
                    hedef = AD_ESLEME.get(espn) or sade_ad.get(T.sade(espn))
                    ad[espn] = hedef if hedef in lig else espn
            kupa[k].append([datetime.strptime(s[0], "%Y-%m-%d").date(), ad[s[1]], ad[s[2]]] + s[3:])
    return {"ulusal": ulusal, "kupa": kupa, "lig": lig}


# Programdaki lig kodu -> ESPN'deki lig adı
LIG_SLUG = {
    "T1": "tur.1", "E0": "eng.1", "E1": "eng.2", "E2": "eng.3", "E3": "eng.4", "SC0": "sco.1",
    "D1": "ger.1", "D2": "ger.2", "I1": "ita.1", "I2": "ita.2", "SP1": "esp.1", "SP2": "esp.2",
    "F1": "fra.1", "F2": "fra.2", "N1": "ned.1", "B1": "bel.1", "P1": "por.1", "G1": "gre.1",
}
GUNLUK = "https://site.api.espn.com/apis/site/v2/sports/soccer/%s/scoreboard?dates=%s"
FIKSTUR_GUN = 7
# ESPN'deki lig takımı adı -> football-data adı (benzerlikle bulunamayanlar)
LIG_AD_ESLEME = {
    "OH Leuven": "Oud-Heverlee Leuven", "FC Cologne": "FC Koln", "Queens Park Rangers": "QPR",
    "Deportivo": "La Coruna", "Sporting Gijón": "Sp Gijon", "RC Celta Fortuna": "Celta B",
    "Real Sociedad II": "Sociedad B", "Erzurum BB": "Erzurumspor", "Amed SFK": "Amedspor",
}


def _lig_adi(espn, takimlar):
    """ESPN takım adını ligin takım listesinde bul (yeterince benzemiyorsa None)."""
    hedef = LIG_AD_ESLEME.get(espn) or AD_ESLEME.get(espn)
    if hedef:
        return hedef if hedef in takimlar else None
    en = max(takimlar, key=lambda t: T.benzerlik(espn, t))
    return en if T.benzerlik(espn, en) >= 0.72 else None


def lig_fiksturu(bugun, lig_takimlari):
    """Önümüzdeki günlerin lig maçları ve varsa güncel oranları (ESPN).

    Döner: {(lig, ev, dep): (tarih, piyasa_ev_gol, piyasa_dep_gol, başlama anı)}
    Oranı olmayan maçta piyasa golleri None. Eşleşmeyen takım adları yazdırılır.
    """
    os.makedirs(T.ONBELLEK, exist_ok=True)
    yol = os.path.join(T.ONBELLEK, "espn_lig_fikstur_%s.json" % bugun.isoformat())
    ham = None
    if os.path.exists(yol) and time.time() - os.path.getmtime(yol) < 3600:
        with open(yol, encoding="utf-8") as f:
            ham = json.load(f)
    if ham is None:
        isler = [(kod, LIG_SLUG[kod], (bugun + timedelta(days=g)).strftime("%Y%m%d"))
                 for kod in lig_takimlari if kod in LIG_SLUG for g in range(FIKSTUR_GUN)]

        def getir(i):
            try:
                istek = urllib.request.Request(GUNLUK % (i[1], i[2]), headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(istek, timeout=40) as yanit:
                    return [[i[0]] + s for s in _ayikla(json.loads(yanit.read()))]
            except Exception:
                return []
        with ThreadPoolExecutor(8) as havuz:
            ham = [s for parca in havuz.map(getir, isler) for s in parca]
        if ham:
            with open(yol, "w", encoding="utf-8") as f:
                json.dump(ham, f)
    sonuc, eslesmeyen = {}, set()
    for kod, gun, ev_espn, dep_espn, oynandi, *_, le, ld, an, _, _ in ham:
        if oynandi or kod not in lig_takimlari:
            continue
        ev, dep = _lig_adi(ev_espn, lig_takimlari[kod]), _lig_adi(dep_espn, lig_takimlari[kod])
        if not ev or not dep or ev == dep:
            eslesmeyen.add((kod, ev_espn, dep_espn))
            continue
        try:
            epoch = calendar.timegm(time.strptime(an, "%Y-%m-%dT%H:%MZ"))
        except (TypeError, ValueError):
            continue
        if epoch < time.time():
            continue
        tarih = datetime.fromtimestamp(epoch, ZoneInfo("Europe/Istanbul")).date()
        sonuc[(kod, ev, dep)] = (tarih, le, ld, epoch)
    print("lig fikstürü: %d maç (%d oranlı)" % (len(sonuc), sum(1 for v in sonuc.values() if v[1] is not None)) +
          (", eşleşmeyen: %s" % sorted(eslesmeyen) if eslesmeyen else ""))
    return sonuc


def kupa_takimlari(veri, bugun):
    """Her kupada bu sezon yer alan takımlar."""
    sezon_basi = date(bugun.year if bugun.month >= 7 else bugun.year - 1, 7, 1)
    return {k: sorted({t for s in satirlar if s[0] >= sezon_basi for t in (s[1], s[2])})
            for k, satirlar in veri["kupa"].items()}


def yaklasan(veri, bugun):
    """Yaklaşan kupa maçlarının güncel oranları:
    {(kupa, ev, dep): (tarih, piyasa_ev_gol, piyasa_dep_gol, başlama anı (epoch sn))}"""
    def an(metin):
        try:
            return calendar.timegm(time.strptime(metin, "%Y-%m-%dT%H:%MZ"))
        except (TypeError, ValueError):
            return None
    return {(k, s[1], s[2]): (s[0], s[8], s[9], an(s[10]) if len(s) > 10 else None)
            for k, satirlar in veri["kupa"].items() for s in satirlar
            if not s[3] and s[0] >= bugun and len(s) > 9 and s[8] is not None}


def _oturt(maclar, agir, takimlar, grup, ic_huc, ic_sav):
    """Lig düzeyi katsayılarını oturtur. maclar: [(ev, dep, ev_değer, dep_değer)]
    (değer gol ya da korner). Döner: taban, ev avantajı, takım hücum/savunma, A, D."""
    gruplar = sorted(set(grup.values()))
    ligler = [g for g in gruplar if not g.startswith("#")]
    tek = [g for g in gruplar if g.startswith("#")]
    A = dict.fromkeys(gruplar, 1.0)
    D = dict.fromkeys(gruplar, 1.0)
    taban = sum(w * (m[2] + m[3]) for w, m in zip(agir, maclar)) / (2 * sum(agir))
    ev_av = 1.2
    for _ in range(80):
        at = dict.fromkeys(gruplar, 0.0); at_b = dict.fromkeys(gruplar, 0.0)
        ye = dict.fromkeys(gruplar, 0.0); ye_b = dict.fromkeys(gruplar, 0.0)
        for w, (e, d, eg, dg) in zip(agir, maclar):
            ge, gd = grup[e], grup[d]
            ev_b = taban * ev_av * ic_huc[e] * ic_sav[d]      # A, D hariç beklenen
            dep_b = taban * ic_huc[d] * ic_sav[e]
            at[ge] += w * eg; at_b[ge] += w * ev_b * D[gd]
            at[gd] += w * dg; at_b[gd] += w * dep_b * D[ge]
            ye[ge] += w * dg; ye_b[ge] += w * dep_b * A[gd]
            ye[gd] += w * eg; ye_b[gd] += w * ev_b * A[ge]
        # Verisi olmayan lig takımları kendi ortak ortalamalarına çekilir
        c_a = sum(at[g] for g in tek) / max(sum(at_b[g] for g in tek), 1e-9) if tek else 1.0
        c_d = sum(ye[g] for g in tek) / max(sum(ye_b[g] for g in tek), 1e-9) if tek else 1.0
        on = T.ONSEL_MAC * taban
        for g in gruplar:
            ma, md = (c_a, c_d) if g.startswith("#") else (1.0, 1.0)
            A[g] = (at[g] + on * ma) / (at_b[g] + on)
            D[g] = (ye[g] + on * md) / (ye_b[g] + on)
        if ligler:
            oa = math.exp(sum(math.log(A[g]) for g in ligler) / len(ligler))
            od = math.exp(sum(math.log(D[g]) for g in ligler) / len(ligler))
            A = {g: v / oa for g, v in A.items()}
            D = {g: v / od for g, v in D.items()}
        huc = {t: ic_huc[t] * A[grup[t]] for t in takimlar}
        sav = {t: ic_sav[t] * D[grup[t]] for t in takimlar}
        ev_b = sum(w * huc[m[0]] * sav[m[1]] for w, m in zip(agir, maclar))
        dep_b = sum(w * huc[m[1]] * sav[m[0]] for w, m in zip(agir, maclar))
        taban = sum(w * m[3] for w, m in zip(agir, maclar)) / dep_b
        ev_av = sum(w * m[2] for w, m in zip(agir, maclar)) / (taban * ev_b)
    return taban, ev_av, huc, sav, A, D


def model_kur(veri, bugun):
    """Avrupa modeli: lig içi güç x lig düzeyi. tahmin.tahmin_et ile kullanılır."""
    ulusal = {}
    for kod, (_, tum) in veri["ulusal"].items():
        tum = [m for m in tum if m[0] < bugun]
        if tum:
            ulusal[kod] = T.model_kur(tum, bugun)
    lig = veri["lig"]

    maclar = [s for satirlar in veri["kupa"].values() for s in satirlar if s[3] and s[0] < bugun]
    agir = [0.5 ** ((bugun - m[0]).days / T.YARI_OMUR_GUN) for m in maclar]
    takimlar = sorted({t for satirlar in veri["kupa"].values() for s in satirlar for t in (s[1], s[2])})

    def bilinen(t):
        return lig.get(t) in ulusal and t in ulusal[lig[t]]["huc"]
    grup = {t: lig[t] if bilinen(t) else "#" + t for t in takimlar}
    ic_huc = {t: ulusal[lig[t]]["huc"][t] if bilinen(t) else 1.0 for t in takimlar}
    ic_sav = {t: ulusal[lig[t]]["sav"][t] if bilinen(t) else 1.0 for t in takimlar}
    taban, ev_av, huc, sav, A, D = _oturt([(m[1], m[2], m[4], m[5]) for m in maclar], agir,
                                         takimlar, grup, ic_huc, ic_sav)

    def lamdalar(e, d):
        return taban * ev_av * huc[e] * sav[d], taban * huc[d] * sav[e]

    def olabilirlik(rho):
        top = 0.0
        for w, m in zip(agir, maclar):
            if m[4] <= 1 and m[5] <= 1:
                le, ld = lamdalar(m[1], m[2])
                top += w * math.log(max(T.dc_duzeltme(m[4], m[5], le, ld, rho), 1e-9))
        return top
    rho = max((r / 100 for r in range(-20, 11)), key=olabilirlik)

    kupa_pay = (sum(w * (m[6] + m[7]) for w, m in zip(agir, maclar)) /
                sum(w * (m[4] + m[5]) for w, m in zip(agir, maclar)))
    pay = {t: kupa_pay + (ulusal[lig[t]]["pay"][t] - ulusal[lig[t]]["lig_pay"] if bilinen(t) else 0.0)
           for t in takimlar}

    # Korner: aynı yapı, takımın ligindeki korner gücü x ligin Avrupa'daki korner düzeyi
    korner = None
    km = [(w, m) for w, m in zip(agir, maclar) if len(m) > 12 and m[11] is not None and m[12] is not None]
    if len(km) >= 100:
        def kb(t):
            return bilinen(t) and ulusal[lig[t]]["korner"] and t in ulusal[lig[t]]["korner"]["huc"]
        k_grup = {t: lig[t] if kb(t) else "#" + t for t in takimlar}
        k_huc = {t: ulusal[lig[t]]["korner"]["huc"][t] if kb(t) else 1.0 for t in takimlar}
        k_sav = {t: ulusal[lig[t]]["korner"]["sav"][t] if kb(t) else 1.0 for t in takimlar}
        kt, ke, kh, ks, _, _ = _oturt([(m[1], m[2], m[11], m[12]) for _, m in km], [w for w, _ in km],
                                      takimlar, k_grup, k_huc, k_sav)
        korner = {"taban": kt, "ev_av": ke, "huc": kh, "sav": ks, "mac": len(km),
                  "lamdalar": lambda e, d: (kt * ke * kh[e] * ks[d], kt * kh[d] * ks[e])}

    return {"lamdalar": lamdalar, "rho": rho, "pay": pay, "lig_pay": kupa_pay,
            "huc": huc, "sav": sav, "ev_av": ev_av, "taban": taban, "korner": korner,
            "mac": len(maclar), "son": max(m[0] for m in maclar),
            "bilinen": {t: bilinen(t) for t in takimlar}, "lig_duzeyi": (A, D), "ulusal": ulusal}
