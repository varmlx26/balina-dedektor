# balina-dedektor

Bir kripto sinyal hipotezinin ileri testi için olay kaydı tutan küçük bir bot.
GitHub Actions üzerinde 10 dakikada bir çalışır, durumunu bu repoya commit eder.

**Bu repoda kişisel veri yoktur.** Sadece herkese açık Binance piyasa verisi ve
tespit edilen olayların kaydı bulunur. İşlem geçmişi, hesap bilgisi, API anahtarı
yoktur ve olmayacaktır.

## Ne yapıyor

Test edilen hipotez: *büyük oyuncular kısa bir pencerede aynı yöne pozisyon
alınca, o yön kısa vadede öngörülebilir mi?*

Dedektör bunu **kâr/zarar hesaplamadan** ölçer. Yaptığı tek şey olay kaydetmek.

### Dondurulmuş tanım

Aşağıdaki tanım test başlamadan önce yazıldı ve test bitene kadar değişmeyecek.
Hiçbir eşik geçmiş performansa bakılarak seçilmedi.

| Öğe | Değer | Nereden geliyor |
| --- | --- | --- |
| Evren | 28 USDT perpetual | Geçmişte fiilen işlem görmüş semboller; seçilmedi, okundu |
| "Balina işlemi" | Son 24 saatin en büyük 3 işlemi | Nadirlik tanımın içinde; uydurulmuş dolar eşiği yok |
| Yön | Saldırgan taraf (`aggTrade`'de alıcı maker değilse LONG) | Piyasaya vuran taraf |
| Konsensüs | Bu 3 işlemin de aynı yönde olması | Hipotezin kendi ifadesi: "üç balina" |
| Pencere | Aralarındaki yayılım ≤ 4 saat | Hipotezin kendi ifadesi: "dört saat içinde" |
| Onay | Open interest artmış olmalı | Yeni pozisyon mu, kapanış mı ayırmak için |
| Isınma | İlk 24 saatin sinyalleri geçersiz | 24 saatlik sıralama penceresi ancak o zaman dolar |

Saf şansla oran: 3 işlemin aynı yönde olması 2 × (1/2)³ = **%25 sembol-gün**.
Yani olay nadir değil, koşulludur. Test tam olarak bu koşulun bir şey öngörüp
öngörmediğini sorar.

### Neden bu tanım — ve reddedilen ilk tanım

İlk deneme "balina işlemi = notional ≥ 99. yüzdelik" idi. Canlı veride 4 saatlik
pencerede 31–119 işlem üretti (USUSDT 31, HEIUSDT 99, STARUSDT 119): "üç balina"
koşulu neredeyse her zaman sağlanıyordu. Sürekli ateşleyen bir sinyalin
öngörü gücü olamaz, o yüzden reddedildi.

Revizyon **getiriye bakılmadan** yapıldı. Tek gerekçe olay oranıydı. "Hangi tanım
daha çok kazandırıyor" sorusu sorulsaydı test geçersiz olurdu.

## Çıktılar

- `olaylar.csv` — tespit edilen her konsensüs olayı. `isinma=evet` olanlar geçersiz.
- `durum.json` — sembol başına son görülen işlem id'si ve 24 saatlik en büyükler listesi.

`olaylar.csv` dosyasına **kâr/zarar sütunu eklenmeyecektir.** Kuru çalışma
sırasında getiriye bakmak istatistiksel örneği yakar.

## Bağımsız zaman damgası

Her tarama GitHub tarafından commit'lenir. Commit zamanları repo sahibinin
kontrolünde olmadığı için olay kaydı sonradan düzenlenemez — bir olayın ne zaman
tespit edildiği bağımsız olarak doğrulanabilir. Ön kayıt disiplininin istediği
kurcalama kanıtı budur.

## Yerel çalıştırma

Bağımlılık yok, sadece Python 3 standart kütüphanesi.

```bash
python dedektor.py --baglanti-testi   # Binance bu IP'den erişilebiliyor mu
python dedektor.py --tek-tur          # bir tarama yap
```

## Bilinen risk

Binance bazı bulut sağlayıcılarının IP aralıklarını coğrafi olarak engelliyor
(HTTP 451). GitHub Actions koşucuları bu duruma düşebilir. Workflow her çalışmada
önce bağlantı testi yapar; engel varsa iş hemen ve açık bir mesajla düşer.
O durumda çözüm bir VPS'e taşımaktır.
