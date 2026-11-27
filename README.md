# Maç Tahmin

İki takım verilir, maç için olasılık ve adil oran (1 / olasılık) üretir:

- Maç sonucu 1X2
- 1.5, 2.5 ve 3.5 gol Alt/Üst
- Karşılıklı gol var / yok
- İlk yarı 0.5 ve 1.5 Alt/Üst
- İki yarıda da gol olur / olmaz
- En çok gol olacak yarı
- 1. yarı / 2. yarı karşılıklı gol (Evet/Evet, Evet/Hayır, Hayır/Evet, Hayır/Hayır)
- Ev sahibi / deplasman herhangi bir yarıyı kazanır, her iki yarıyı kazanır (Evet/Hayır)
- İlk yarı / maç sonucu (1/1, 1/X, ... 2/2)
- Maç sonucu + 1.5 gol, maç sonucu + 2.5 gol, maç sonucu + karşılıklı gol
- Çifte şans (1X, 12, X2), toplam gol aralığı, tek/çift, ilk golü hangi takım atar
- Takım golleri: ev sahibi ve deplasman 0.5 / 1.5 / 2.5 alt/üst, ilk yarı 0.5
  alt/üst, iki yarıda da gol, hangi yarıda daha çok gol atar
- 1. yarı sonucu, 2. yarı sonucu, 1. yarı tek/çift, 1. ve 2. yarı karşılıklı gol
- 1. yarı sonucu + 1. yarı 1.5 gol, 1. yarı sonucu + 1. yarı karşılıklı gol
- Korner 8.5, 9.5 ve 10.5 Alt/Üst (aynı hücum/savunma modeli korner sayılarıyla
  kurulur; toplam korner negatif binom dağılır)

## Kullanım

```
python3 tahmin.py "Galatasaray" "Fenerbahce"   # ilk takım ev sahibi
python3 tahmin.py                               # takımları sorar
python3 tahmin.py --takimlar T1                 # ligdeki takım adları
```

Python 3 dışında kurulum gerekmez. Veriler her çalıştırmada indirilir ve
`~/.cache/mac-tahmin` altında saklanır (güncel veriler 2 saatte bir yenilenir).

Desteklenen ligler: T1, E0, E1, E2, E3, SC0, D1, D2, I1, I2, SP1, SP2, F1, F2,
N1, B1, P1, G1. Avrupa kupaları: CL (Şampiyonlar Ligi), EL (Avrupa Ligi),
ECL (Konferans Ligi). İki takım aynı ligde ya da aynı Avrupa kupasında
olmalıdır; ulusal kupa maçları desteklenmez.

```
python3 tahmin.py "Galatasaray" "Arsenal"          # kupa otomatik bulunur
python3 tahmin.py "Bayern" "Real Madrid" --lig CL
```

## Yöntem

Son 3 sezonun maçları, yakın tarihli maçlara daha çok ağırlık verilerek
(yarı ömür 240 gün) bir Poisson / Dixon-Coles modeline oturtulur: her takımın
hücum ve savunma gücü ile ev sahibi avantajı. Beklenen goller, iki takımın
ilk yarı gol payına göre yarılara dağıtılır.

### Veri kaynakları ve bahis oranları

Her çalıştırmada güncel veri indirilir (2 saatlik önbellek):

- **football-data.co.uk**: maç sonuçları, bu sezonun xG verisi, bahis
  şirketlerinin ortalama 1X2 ve 2.5 alt/üst oranları (kapanış), ayrıca
  yaklaşan maçların güncel oranları (`fixtures.csv`).
- **ESPN skor servisi**: Avrupa kupası sonuçları; önümüzdeki 7 günün lig ve
  kupa maçları ile (yayımlanmışsa) güncel oranları. football-data'nın oranı
  varsa lig maçında o tercih edilir.

Oranlar iki yerde kullanılır (`piyasa.py`):

1. **Takım güçleri.** Her geçmiş maçın oranlarından bahis şirketi payı
   ayıklanır ve piyasanın o maç için beklediği goller çözülür. Takım gücü
   hesaplanırken atılan gol, xG ve bu piyasa beklentisi 1 : 1 : 3 ağırlıkla
   karıştırılır.
2. **Maçın kendisi.** Seçilen maçın güncel oranı yayımlanmışsa beklenen
   goller %90 piyasa, %10 model olarak alınır; bütün pazarlar bu beklenen
   gollerden türetilir.

Geriye dönük test (8 lig, 3.631 maç, hafta hafta ileri doğru; 1X2 için
logaritmik kayıp, düşük olan iyi): yalnız gol 0,998 · gol + piyasa 0,991 ·
maçın kendi oranı da dahil 0,978.

### Avrupa kupaları

Farklı liglerin takımları doğrudan karşılaştırılamadığı için iki aşama var
(`avrupa.py`): takımın kendi ligindeki gücü, son 3 yılın Avrupa kupası
maçlarından hesaplanan lig düzeyi katsayısıyla çarpılır. Avrupa sonuçları
ESPN'in herkese açık skor servisinden alınır; ilk yarı ve 90 dakika skorları
gol dakikalarından çıkarılır. Ön eleme maçları dahil değildir. Yukarıdaki 18
lig dışındaki takımların (ör. Çekya, Norveç) gücü yalnızca kendi Avrupa
maçlarından tahmin edilir ve daha belirsizdir.

## Web arayüzü ve yayın

Tarayıcıda açılan tek sayfa: lig ve iki takım seçilir, sonuçlar anında
hesaplanır. Sayfa internetten veri çekmez; model katsayıları ve yaklaşan
maçların oranları üretim anında içine gömülür.

Tahmin satırına tıklayınca seçim kupona eklenir (her maçtan tek seçim); kupon
toplam tutma olasılığını ve adil oranı gösterir, tarayıcıda saklanır. Kupon
bölümünde gün ve lig süzgeci vardır; süzgece uyan başlamamış maçlardan
program, seçilen maç sayısında (2–8) düşük, orta ya da yüksek riskli kupon
seçer. Aynı maçlar listelenir; listeden tek tek eklenip karma kupon yapılabilir.

```
python3 web_olustur.py --tam index.html   # sablon.html + güncel veri
```

`.github/workflows/yenile.yml` günde üç kez (08:17, 14:17, 18:17 Türkiye
saati) `index.html` dosyasını yeniden üretip `main` dalına işler; GitHub Pages
de onu yayınlar. Pages ayarı "Deploy from a branch", dal `main`, klasör `/`
olmalıdır. Elle yenilemek için: Actions → "Veriyi yenile ve yayınla" →
Run workflow.
