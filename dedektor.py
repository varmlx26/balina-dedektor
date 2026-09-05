"""Balina konsensusu dedektoru - GitHub Actions surumu.

Her calistirildiginda son calismadan bu yana gerceklesen islemleri ceker, sembol
basina son 24 saatin en buyuk islemlerini gunceller ve konsensus olusup
olusmadigina bakar. Durum repoya commit edilir, yani calismalar arasi hafiza var.

BAGIMLILIK YOK: sadece Python standart kutuphanesi.

GETIRI HESAPLANMAZ. Bu arac olay kaydeder, kar/zarar bakmaz. Kuru calisma
sirasinda getiriye bakilirsa istatistiksel ornek yanar - bkz. README.

Yerel deneme:
    python dedektor.py --tek-tur
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

FAPI = "https://fapi.binance.com"
BURASI = os.path.dirname(os.path.abspath(__file__))
DURUM_DOSYA = os.path.join(BURASI, "durum.json")
OLAY_DOSYA = os.path.join(BURASI, "olaylar.csv")

# --- DONDURULMUS TANIM (on-kayitla muhurlu, degistirilemez) ------------------
PENCERE_SAAT = 4        # en buyuk 3 islem arasindaki azami yayilim
KONSENSUS_SAYISI = 3    # "uc balina"
SIRALAMA_GUN = 24       # "balina" = son 24 saatin en buyuk N islemi
OI_ONAY = True          # open interest artmali (yeni pozisyon, kapanis degil)
ISINMA_SAAT = 24        # bu sure dolmadan uretilen sinyaller GECERSIZ

# --- Isletme sinirlari (tanimin parcasi degil) -------------------------------
TUTULAN = 60            # sembol basina saklanan en buyuk islem sayisi
ISTEK_ARASI = 0.35      # saniye; Binance agirlik limiti 2400/dk
TUR_ISTEK_TAVANI = 400  # bir calistirmada azami istek
ILK_TUR_DAKIKA = 10     # durum yoksa ne kadar geriye bakilir

EVREN = [
    "1000XECUSDT", "ACEUSDT", "AKEUSDT", "ALLOUSDT", "APRUSDT", "BANKUSDT",
    "BEATUSDT", "BTWUSDT", "DEXEUSDT", "EDGEUSDT", "ELSAUSDT", "EPICUSDT",
    "ESPORTSUSDT", "EVAAUSDT", "EWYUSDT", "HEIUSDT", "HYPEUSDT", "KORUUSDT",
    "LABUSDT", "MUUUSDT", "SNDKUSDT", "STARUSDT", "TUSDT", "UAIUSDT",
    "UBUSDT", "USUSDT", "VELVETUSDT", "ZECUSDT",
]

_istek_sayaci = 0


def istek(yol, deneme=3):
    global _istek_sayaci
    _istek_sayaci += 1
    for i in range(deneme):
        try:
            with urllib.request.urlopen(FAPI + yol, timeout=25) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if i == deneme - 1:
                raise
            time.sleep(2 * (i + 1))
    return None


def durum_oku():
    if os.path.exists(DURUM_DOSYA):
        with open(DURUM_DOSYA, encoding="utf-8") as f:
            return json.load(f)
    return {"baslangic": None, "semboller": {}, "bildirilen": []}


def durum_yaz(d):
    with open(DURUM_DOSYA, "w", encoding="utf-8") as f:
        json.dump(d, f, separators=(",", ":"), sort_keys=True)


def olay_yaz(satir):
    yeni = not os.path.exists(OLAY_DOSYA)
    with open(OLAY_DOSYA, "a", encoding="utf-8") as f:
        if yeni:
            f.write("gorunur_zaman,sembol,yon,yayilim_saat,toplam_notional,"
                    "oi_degisim,fiyat,isinma\n")
        f.write(satir + "\n")


def yeni_islemler(sembol, son_id):
    """Son gorulen id'den sonraki islemleri getir. (islemler, yeni_son_id)"""
    if son_id:
        yol = "/fapi/v1/aggTrades?symbol=%s&fromId=%d&limit=1000" % (sembol, son_id + 1)
    else:
        bas = int(time.time() * 1000) - ILK_TUR_DAKIKA * 60 * 1000
        yol = ("/fapi/v1/aggTrades?symbol=%s&startTime=%d&endTime=%d&limit=1000"
               % (sembol, bas, bas + 3600000))
    toplanan = []
    while _istek_sayaci < TUR_ISTEK_TAVANI:
        d = istek(yol)
        if not d:
            break
        for x in d:
            toplanan.append([x["T"], round(float(x["p"]) * float(x["q"]), 2),
                             "short" if x["m"] else "long", float(x["p"])])
        son_id = d[-1]["a"]
        if len(d) < 1000:
            break
        yol = "/fapi/v1/aggTrades?symbol=%s&fromId=%d&limit=1000" % (sembol, son_id + 1)
        time.sleep(ISTEK_ARASI)
    return toplanan, son_id


def oi_artti(sembol):
    """Pencere boyunca acik pozisyon artti mi? (oran veya None)"""
    try:
        d = istek("/futures/data/openInterestHist?symbol=%s&period=1h&limit=%d"
                  % (sembol, PENCERE_SAAT + 1))
    except Exception:
        return None
    if not d or len(d) < 2:
        return None
    bas, son = float(d[0]["sumOpenInterest"]), float(d[-1]["sumOpenInterest"])
    return (son - bas) / bas if bas else None


def konsensus(en_buyuk):
    """En buyuk 3 islem ayni yonde ve <= 4 saat yayilimda mi?"""
    if len(en_buyuk) < KONSENSUS_SAYISI:
        return None
    en3 = en_buyuk[:KONSENSUS_SAYISI]
    if len({k[2] for k in en3}) != 1:
        return None
    zamanlar = [k[0] for k in en3]
    yayilim = (max(zamanlar) - min(zamanlar)) / 3600000.0
    if yayilim > PENCERE_SAAT:
        return None
    return {"yon": en3[0][2], "yayilim": yayilim, "ilk": min(zamanlar),
            "notional": sum(k[1] for k in en3),
            "fiyat": max(en3, key=lambda k: k[0])[3]}


def tur():
    d = durum_oku()
    simdi = time.time()
    if d["baslangic"] is None:
        d["baslangic"] = simdi
        print("Isinma basladi. Gecerli sinyal icin %d saat gerekiyor." % ISINMA_SAAT)
    gecen_saat = (simdi - d["baslangic"]) / 3600.0
    isindi = gecen_saat >= ISINMA_SAAT
    bildirilen = set(tuple(x) for x in d["bildirilen"])
    sinir_ms = int(simdi * 1000) - SIRALAMA_GUN * 3600 * 1000

    print("Calisma zamani: %s | uptime %.1f saat | %s"
          % (time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(simdi)), gecen_saat,
             "ISINDI" if isindi else "ISINMA SURUYOR"))

    yeni_olay = 0
    for sembol in EVREN:
        s = d["semboller"].setdefault(sembol, {"son_id": None, "en_buyuk": []})
        try:
            islemler, son_id = yeni_islemler(sembol, s["son_id"])
        except Exception as e:
            print("  %-14s HATA %s" % (sembol, type(e).__name__))
            continue
        s["son_id"] = son_id

        havuz = s["en_buyuk"] + islemler
        havuz = [k for k in havuz if k[0] >= sinir_ms]
        havuz.sort(key=lambda k: -k[1])
        del havuz[TUTULAN:]
        s["en_buyuk"] = havuz

        k = konsensus(havuz)
        if not k:
            continue
        anahtar = (sembol, k["yon"], k["ilk"])
        if anahtar in bildirilen:
            continue
        oi = oi_artti(sembol)
        if OI_ONAY and not (oi is not None and oi > 0):
            continue
        bildirilen.add(anahtar)
        yeni_olay += 1
        zaman_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        olay_yaz("%s,%s,%s,%.2f,%.1f,%.4f,%s,%s"
                 % (zaman_iso, sembol, k["yon"], k["yayilim"], k["notional"],
                    oi, k["fiyat"], "hayir" if isindi else "evet"))
        print("  *** %s %s %s  yayilim %.1fsa  %.0f USDT  OI %+.2f%%  @ %s"
              % ("SINYAL" if isindi else "[isinma]", sembol, k["yon"].upper(),
                 k["yayilim"], k["notional"], oi * 100, k["fiyat"]))

    # Bildirilen listesini 24 saatten eskilerden temizle
    d["bildirilen"] = [list(x) for x in bildirilen if x[2] >= sinir_ms]
    durum_yaz(d)
    toplam = sum(len(v["en_buyuk"]) for v in d["semboller"].values())
    print("Bitti: %d istek, %d sembol, %d saklanan islem, %d yeni olay"
          % (_istek_sayaci, len(EVREN), toplam, yeni_olay))


def baglanti_testi():
    """Binance bu IP'den erisilebiliyor mu? GitHub Actions kosucularinin bir kismi
    Binance tarafindan cografi olarak engelli (HTTP 451). Bunu 10 saniyede ogren."""
    try:
        d = istek("/fapi/v1/time")
        print("BAGLANTI TAMAM - Binance sunucu saati: %s"
              % time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(d["serverTime"] / 1000)))
        return 0
    except urllib.error.HTTPError as e:
        if e.code == 451:
            print("ENGELLI (HTTP 451): Binance bu IP'ye kapali.")
            print("GitHub kosucusu cografi engelli bolgede. Bu yol calismaz;")
            print("VPS veya Oracle Cloud secenegine gecmek gerekiyor.")
        else:
            print("HTTP HATASI %s: %s" % (e.code, e.reason))
        return 1
    except Exception as e:
        print("BAGLANTI HATASI %s: %s" % (type(e).__name__, e))
        return 1


ADAY_HOSTLAR = [
    ("Binance futures (mevcut)", "https://fapi.binance.com/fapi/v1/time"),
    ("Binance spot", "https://api.binance.com/api/v3/time"),
    ("Binance data-api.vision", "https://data-api.binance.vision/api/v3/time"),
    ("Binance api1", "https://api1.binance.com/api/v3/time"),
    ("Binance api4", "https://api4.binance.com/api/v3/time"),
    ("Bybit", "https://api.bybit.com/v5/market/time"),
    ("OKX", "https://www.okx.com/api/v5/public/time"),
    ("Gate.io", "https://api.gateio.ws/api/v4/spot/time"),
    ("MEXC futures", "https://contract.mexc.com/api/v1/contract/ping"),
    ("Bitget futures", "https://api.bitget.com/api/v2/public/time"),
    ("KuCoin futures", "https://api-futures.kucoin.com/api/v1/timestamp"),
]


def _dene(url, basliksiz=False):
    req = urllib.request.Request(url, headers={"User-Agent": "dedektor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read(), None
    except urllib.error.HTTPError as e:
        return None, ("ENGELLI(451)" if e.code == 451 else "HTTP %d" % e.code)
    except Exception as e:
        return None, type(e).__name__


def _veri_yetenegi():
    """Sadece erisim degil: ihtiyacimiz olan islem akisini verebiliyor mu?

    Bize lazim olan, bir sembolun son islemlerini toplu halde cekebilmek.
    Her borsanin uc noktasi farkli; kac islem ve kac dakikalik alan dondugunu
    olcuyoruz, cunku 10 dakikalik cron'da bosluk kalmamasi gerekiyor.
    """
    testler = [
        ("Binance fapi aggTrades",
         "https://fapi.binance.com/fapi/v1/aggTrades?symbol=BANKUSDT&limit=1000",
         lambda d: [(x["T"], 1) for x in d]),
        ("Bybit recent-trade",
         "https://api.bybit.com/v5/market/recent-trade?category=linear&symbol=BANKUSDT&limit=1000",
         lambda d: [(int(x["time"]), 1) for x in d["result"]["list"]]),
        ("Gate futures trades",
         "https://api.gateio.ws/api/v4/futures/usdt/trades?contract=BANK_USDT&limit=1000",
         lambda d: [(int(float(x["create_time_ms"])), 1) for x in d]),
        ("MEXC deals",
         "https://contract.mexc.com/api/v1/contract/deals/BANK_USDT",
         lambda d: [(int(x["t"]), 1) for x in d["data"]]),
        ("Bitget fills",
         "https://api.bitget.com/api/v2/mix/market/fills?symbol=BANKUSDT&productType=USDT-FUTURES&limit=100",
         lambda d: [(int(x["ts"]), 1) for x in d["data"]]),
        ("KuCoin trade history",
         "https://api-futures.kucoin.com/api/v1/trade/history?symbol=BANKUSDTM",
         lambda d: [(int(x["ts"]) // 1000000, 1) for x in d["data"]]),
    ]
    satirlar = []
    for ad, url, ayikla in testler:
        govde, hata = _dene(url)
        if hata:
            satirlar.append("  %-12s %-26s" % (hata, ad))
            continue
        try:
            kayitlar = ayikla(json.loads(govde))
            if not kayitlar:
                satirlar.append("  BOS          %-26s" % ad)
                continue
            zamanlar = [k[0] for k in kayitlar]
            dakika = (max(zamanlar) - min(zamanlar)) / 60000.0
            satirlar.append("  VERI VAR     %-26s %4d islem, %.1f dakikalik alan"
                            % (ad, len(kayitlar), dakika))
        except Exception as e:
            satirlar.append("  AYRISTIRMA   %-26s %s" % (ad, type(e).__name__))
    return satirlar


def host_testi():
    """Bu IP'den hangi borsalar acik ve hangisi ise yarar veri veriyor?

    Sonucu hem ekrana hem teshis.txt'ye yazar; dosya repoya commit edildigi icin
    log erisimi olmadan da okunabilir.
    """
    satirlar = ["Adres teshisi - %s" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "", "ERISIM:"]
    acik = []
    for ad, url in ADAY_HOSTLAR:
        govde, hata = _dene(url)
        if hata:
            satirlar.append("  %-12s %-26s" % (hata, ad))
        else:
            satirlar.append("  ACIK         %-26s %s"
                            % (ad, govde[:50].decode("utf-8", "replace")))
            acik.append(ad)
    satirlar.append("")
    satirlar.append("Acik: %d / %d" % (len(acik), len(ADAY_HOSTLAR)))
    satirlar.append("")
    satirlar.append("VERI YETENEGI (BANKUSDT son islemler):")
    satirlar += _veri_yetenegi()

    metin = "\n".join(satirlar)
    print(metin)
    with open(os.path.join(BURASI, "teshis.txt"), "w", encoding="utf-8") as f:
        f.write(metin + "\n")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tek-tur", action="store_true", help="tek calistir ve cik")
    ap.add_argument("--baglanti-testi", action="store_true",
                    help="Binance erisilebiliyor mu, sadece onu kontrol et")
    ap.add_argument("--host-testi", action="store_true",
                    help="hangi borsa adresleri bu IP'den acik, teshis")
    a = ap.parse_args()
    if a.host_testi:
        raise SystemExit(host_testi())
    if a.baglanti_testi:
        raise SystemExit(baglanti_testi())
    tur()


if __name__ == "__main__":
    main()
