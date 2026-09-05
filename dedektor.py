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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tek-tur", action="store_true", help="tek calistir ve cik")
    ap.add_argument("--baglanti-testi", action="store_true",
                    help="Binance erisilebiliyor mu, sadece onu kontrol et")
    a = ap.parse_args()
    if a.baglanti_testi:
        raise SystemExit(baglanti_testi())
    tur()


if __name__ == "__main__":
    main()
