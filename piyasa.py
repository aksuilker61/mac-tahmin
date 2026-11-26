"""Bahis piyasası oranlarını modele çevirir.

Bir maçın 1X2 ve gol alt/üst oranları, piyasanın o maç için beklediği gol
sayılarını (ev, deplasman) ele verir. Bu modül oranlardan bahis şirketi payını
ayıklar ve o beklenen golleri çözer; böylece piyasanın bilgisi (kadro,
sakatlık, form) hem takım güçlerinin hesabına hem de maçın tahminine girer.
"""
import bisect
import math

_MAKS = 11
_T_IZGARA = [0.4 + 0.1 * i for i in range(67)]        # toplam gol beklentisi 0.4 .. 7.0
_PAY_IZGARA = [0.02 * i for i in range(1, 50)]        # ev sahibinin gol payı 0.02 .. 0.98
_tablo = None


def _dagilim(lam):
    p = [math.exp(-lam)]
    for k in range(1, _MAKS):
        p.append(p[-1] * lam / k)
    return p


def _fark(le, ld):
    """P(ev sahibi kazanır) - P(deplasman kazanır)."""
    e, d = _dagilim(le), _dagilim(ld)
    kum, top = 0.0, 0.0
    alt = 0.0                      # P(dep < x) birikimli
    for x in range(_MAKS):
        top += e[x] * alt          # ev x atar, dep daha az
        alt += d[x]
    alt = 0.0
    for y in range(_MAKS):
        kum += d[y] * alt
        alt += e[y]
    return top - kum


def paysiz(*oranlar):
    """Ondalık oranlar -> bahis şirketi payı ayıklanmış olasılıklar (yoksa None)."""
    try:
        ters = [1.0 / float(o) for o in oranlar]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if any(t <= 0 or t >= 1 for t in ters):
        return None
    top = sum(ters)
    return [t / top for t in ters]


def toplam_coz(p_ust, cizgi=2.5):
    """Üst olasılığını veren toplam gol beklentisi (toplam gol Poisson dağılır)."""
    k = int(math.floor(cizgi))
    alt, ust = _T_IZGARA[0], _T_IZGARA[-1]
    for _ in range(40):
        t = (alt + ust) / 2
        if 1 - sum(_dagilim(t)[:k + 1]) < p_ust:
            alt = t
        else:
            ust = t
    return (alt + ust) / 2


def lamda_coz(p1, p2, p_ust, cizgi=2.5):
    """Piyasa olasılıkları -> (ev beklenen gol, deplasman beklenen gol)."""
    global _tablo
    if _tablo is None:
        _tablo = [[_fark(t * p, t * (1 - p)) for p in _PAY_IZGARA] for t in _T_IZGARA]
    t = toplam_coz(p_ust, cizgi)
    i = min(max(int((t - _T_IZGARA[0]) / 0.1), 0), len(_T_IZGARA) - 2)
    a = (t - _T_IZGARA[i]) / 0.1
    satir = [(1 - a) * x + a * y for x, y in zip(_tablo[i], _tablo[i + 1])]
    hedef = min(max(p1 - p2, satir[0]), satir[-1])
    j = min(max(bisect.bisect_left(satir, hedef), 1), len(satir) - 1)
    b = (hedef - satir[j - 1]) / max(satir[j] - satir[j - 1], 1e-12)
    pay = _PAY_IZGARA[j - 1] + b * 0.02
    return t * pay, t * (1 - pay)


def csv_satiri(s):
    """football-data satırı -> piyasanın beklediği goller (yoksa None, None).
    Kapanış ortalaması tercih edilir; yoksa açılış ortalaması, o da yoksa Bet365."""
    for o in ("AvgC", "Avg", "B365C", "B365"):
        x = paysiz(s.get(o + "H"), s.get(o + "D"), s.get(o + "A"))
        g = paysiz(s.get(o + ">2.5"), s.get(o + "<2.5"))
        if x and g:
            return lamda_coz(x[0], x[2], g[0], 2.5)
    return None, None


def amerikan(oran):
    """'+500' -> 6.0, '-160' -> 1.625 (ondalık oran)."""
    try:
        o = float(str(oran).replace("EVEN", "100"))
    except ValueError:
        return None
    if o == 0:
        return None
    return 1 + (o / 100 if o > 0 else 100 / -o)
