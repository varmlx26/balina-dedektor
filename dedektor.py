"""Balina konsensusu dedektoru - GitHub Actions surumu (Bitget).

Her calistirildiginda son calismadan bu yana gerceklesen islemleri ceker, sembol
basina son 24 saatin en buyuk islemlerini gunceller ve konsensus olusup
olusmadigina bakar. Durum repoya commit edilir, yani calismalar arasi hafiza var.

NEDEN BITGET, NEDEN BINANCE DEGIL:
GitHub Actions kosuculari Binance tarafindan cografi olarak engelli (HTTP 451,
teshis.txt'de olculdu). Bybit de 403 veriyor. Kosucudan erisilebilen ve Melih'in
evrenini kapsayan tek uygun kaynak Bitget: 26/28 sembol, 100 islem ~47 dakikalik
alan, sayfalama destegi, yon bilgisi dogrudan. Bu bir tanim revizyonudur ve
getiriye bakilmadan, yalnizca erisilebilirlige gore secilmistir.

BAGIMLILIK YOK: sadece Python standart kutuphanesi.

GETIRI HESAPLANMAZ. Bu arac olay kaydeder, kar/zarar bakmaz.

Yerel deneme (repodaki dosyalara dokunmaz, yerel_ onekiyle yazar):
    python dedektor.py --tek-tur
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

BG = "https://api.bitget.com/api/v2/mix/market"
URUN = "USDT-FUTURES"
BURASI = os.path.dirname(os.path.abspath(__file__))

# Yerelde calisirken repodaki dosyalara DOKUNMA: bunlar botun urettigi ciktilar,
# yerel test onlari degistirirse her push'ta merge catismasi cikiyor.
_CI = os.environ.get("GITHUB_ACTIONS") == "true"
_ON = "" if _CI else "yerel_"
DURUM_DOSYA = os.path.join(BURASI, _ON + "durum.json")
OLAY_DOSYA = os.path.join(BURASI, _ON + "olaylar.csv")
TESHIS_DOSYA = os.path.join(BURASI, _ON + "teshis.txt")

# --- DONDURULMUS TANIM (on-kayitla muhurlu, degistirilemez) ------------------
PENCERE_SAAT = 4        # en buyuk 3 islem arasindaki azami yayilim
KONSENSUS_SAYISI = 3    # "uc balina"
SIRALAMA_GUN = 24       # "balina" = son 24 saatin en buyuk N islemi
OI_ONAY = True          # open interest artmali (yeni pozisyon, kapanis degil)
ISINMA_SAAT = 24        # bu sure dolmadan uretilen sinyaller GECERSIZ

# --- Isletme sinirlari (tanimin parcasi degil) -------------------------------
TUTULAN = 60            # sembol basina saklanan en buyuk emir sayisi
ISTEK_ARASI = 0.12      # saniye
SEMBOL_SAYFA_TAVANI = 40   # bir sembol icin azami sayfa
TUR_ISTEK_TAVANI = 700     # bir calistirmada azami istek
ILK_TUR_DAKIKA = 15     # durum yoksa ne kadar geriye bakilir
OI_SAKLA_SAAT = 8       # kendi OI gecmisimizi ne kadar tutalim

# Melih'in fiilen islem yaptigi ve Bitget'te bulunan semboller.
# Binance'te olup Bitget'te olmayan ikisi (HEIUSDT, STARUSDT) disarida kaldi.
EVREN = [
    "1000XECUSDT", "ACEUSDT", "AKEUSDT", "ALLOUSDT", "APRUSDT", "BANKUSDT",
    "BEATUSDT", "BTWUSDT", "DEXEUSDT", "EDGEUSDT", "ELSAUSDT", "EPICUSDT",
    "ESPORTSUSDT", "EVAAUSDT", "EWYUSDT", "HYPEUSDT", "KORUUSDT", "LABUSDT",
    "MUUUSDT", "SNDKUSDT", "TUSDT", "UAIUSDT", "UBUSDT", "USUSDT",
    "VELVETUSDT", "ZECUSDT",
]

_istek_sayaci = 0


def istek(url, deneme=3):
    global _istek_sayaci
    _istek_sayaci += 1
    for i in range(deneme):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dedektor/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
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


def _emirlere_birlestir(fills):
    """Ham fill'leri taker emirlerine topla.

    Bitget her fill'i ayri veriyor: tek bir buyuk market emri, farkli fiyat
    seviyelerinde onlarca parcaya bolunmus halde gorunuyor. Binance'in aggTrade'i
    bunlari zaten birlestirdigi icin tanimimiz "emir" olcegindeydi. Ayni ts ve
    ayni side'a sahip fill'ler tek bir taker emridir; toplamazsak "en buyuk 3
    islem" emirleri degil parcalari olcer.
    """
    gruplar = {}
    for ts, notional, yon, fiyat in fills:
        anahtar = (ts, yon)
        g = gruplar.get(anahtar)
        if g is None:
            gruplar[anahtar] = [ts, notional, yon, fiyat]
        else:
            g[1] += notional
    return list(gruplar.values())


def yeni_islemler(sembol, son_id, sinir_ms):
    """Son gorulen tradeId'den sonraki islemleri getir. (emirler, yeni_son_id)"""
    ham, en_kucuk_id, en_buyuk_id = [], None, None
    for _ in range(SEMBOL_SAYFA_TAVANI):
        if _istek_sayaci >= TUR_ISTEK_TAVANI:
            break
        if en_kucuk_id is None:
            url = "%s/fills?symbol=%s&productType=%s&limit=100" % (BG, sembol, URUN)
        else:
            url = ("%s/fills-history?symbol=%s&productType=%s&limit=100&idLessThan=%s"
                   % (BG, sembol, URUN, en_kucuk_id))
        d = istek(url)
        kayitlar = (d or {}).get("data") or []
        if not kayitlar:
            break
        bitti = False
        for x in kayitlar:
            tid, ts = int(x["tradeId"]), int(x["ts"])
            if son_id is not None and tid <= int(son_id):
                bitti = True
                break
            if ts < sinir_ms:          # siralama penceresinin disina ciktik
                bitti = True
                break
            if en_buyuk_id is None or tid > int(en_buyuk_id):
                en_buyuk_id = str(tid)
            ham.append([ts, float(x["price"]) * float(x["size"]),
                        "long" if x["side"] == "buy" else "short", float(x["price"])])
        en_kucuk_id = kayitlar[-1]["tradeId"]
        if bitti or len(kayitlar) < 100:
            break
        time.sleep(ISTEK_ARASI)
    return _emirlere_birlestir(ham), (en_buyuk_id or son_id)


def oi_oku(sembol):
    """Anlik acik pozisyon. Bitget gecmis vermiyor, kendi gecmisimizi biriktiriyoruz."""
    try:
        d = istek("%s/open-interest?symbol=%s&productType=%s" % (BG, sembol, URUN))
        return float(d["data"]["openInterestList"][0]["size"])
    except Exception:
        return None


def oi_degisimi(gecmis, simdi_ms):
    """Simdiki OI, ~PENCERE_SAAT once kaydedilene gore ne kadar degismis?

    Bitget open-interest ucu yalnizca anlik deger veriyor. 10 dakikada bir
    calistigimiz icin kendi zaman serimizi kuruyoruz; ilk PENCERE_SAAT boyunca
    karsilastirilacak nokta olmadigindan onay verilemez (isinma zaten sart).
    """
    if len(gecmis) < 2:
        return None
    hedef = simdi_ms - PENCERE_SAAT * 3600 * 1000
    eski = [g for g in gecmis if g[0] <= hedef]
    if not eski:
        return None
    bas, son = eski[-1][1], gecmis[-1][1]
    return (son - bas) / bas if bas else None


def konsensus(en_buyuk):
    """En buyuk 3 emir ayni yonde ve <= 4 saat yayilimda mi?"""
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
    simdi_ms = int(simdi * 1000)
    if d["baslangic"] is None:
        d["baslangic"] = simdi
        print("Isinma basladi. Gecerli sinyal icin %d saat gerekiyor." % ISINMA_SAAT)
    gecen_saat = (simdi - d["baslangic"]) / 3600.0
    isindi = gecen_saat >= ISINMA_SAAT
    bildirilen = set(tuple(x) for x in d["bildirilen"])
    sinir_ms = simdi_ms - SIRALAMA_GUN * 3600 * 1000
    ilk_sinir = simdi_ms - ILK_TUR_DAKIKA * 60 * 1000

    print("Kaynak: Bitget | %s | uptime %.1f saat | %s"
          % (time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(simdi)), gecen_saat,
             "ISINDI" if isindi else "ISINMA SURUYOR"))

    yeni_olay = 0
    for sembol in EVREN:
        s = d["semboller"].setdefault(
            sembol, {"son_id": None, "en_buyuk": [], "oi": []})
        s.setdefault("oi", [])
        try:
            emirler, son_id = yeni_islemler(
                sembol, s["son_id"], sinir_ms if s["son_id"] else ilk_sinir)
        except Exception as e:
            print("  %-14s HATA %s" % (sembol, type(e).__name__))
            continue
        s["son_id"] = son_id

        havuz = [k for k in s["en_buyuk"] + emirler if k[0] >= sinir_ms]
        havuz.sort(key=lambda k: -k[1])
        del havuz[TUTULAN:]
        s["en_buyuk"] = havuz

        oi = oi_oku(sembol)
        if oi is not None:
            s["oi"] = [g for g in s["oi"]
                       if g[0] >= simdi_ms - OI_SAKLA_SAAT * 3600 * 1000]
            s["oi"].append([simdi_ms, oi])

        k = konsensus(havuz)
        if not k:
            continue
        anahtar = (sembol, k["yon"], k["ilk"])
        if anahtar in bildirilen:
            continue
        oi_fark = oi_degisimi(s["oi"], simdi_ms)
        if OI_ONAY and not (oi_fark is not None and oi_fark > 0):
            continue
        bildirilen.add(anahtar)
        yeni_olay += 1
        zaman_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        olay_yaz("%s,%s,%s,%.2f,%.1f,%.4f,%s,%s"
                 % (zaman_iso, sembol, k["yon"], k["yayilim"], k["notional"],
                    oi_fark, k["fiyat"], "hayir" if isindi else "evet"))
        print("  *** %s %s %s  yayilim %.1fsa  %.0f USDT  OI %+.2f%%  @ %s"
              % ("SINYAL" if isindi else "[isinma]", sembol, k["yon"].upper(),
                 k["yayilim"], k["notional"], oi_fark * 100, k["fiyat"]))

    d["bildirilen"] = [list(x) for x in bildirilen if x[2] >= sinir_ms]
    durum_yaz(d)
    toplam = sum(len(v["en_buyuk"]) for v in d["semboller"].values())
    print("Bitti: %d istek, %d sembol, %d saklanan emir, %d yeni olay"
          % (_istek_sayaci, len(EVREN), toplam, yeni_olay))


# --- Teshis ------------------------------------------------------------------

ADAY_HOSTLAR = [
    ("Bitget futures (mevcut)", "https://api.bitget.com/api/v2/public/time"),
    ("Binance futures", "https://fapi.binance.com/fapi/v1/time"),
    ("Bybit", "https://api.bybit.com/v5/market/time"),
    ("MEXC futures", "https://contract.mexc.com/api/v1/contract/ping"),
    ("KuCoin futures", "https://api-futures.kucoin.com/api/v1/timestamp"),
    ("OKX", "https://www.okx.com/api/v5/public/time"),
]


def _dene(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dedektor/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read(), None
    except urllib.error.HTTPError as e:
        return None, ("ENGELLI(451)" if e.code == 451 else "HTTP %d" % e.code)
    except Exception as e:
        return None, type(e).__name__


def host_testi():
    """Bu IP'den hangi borsalar acik? Sonuc teshis.txt'ye de yazilir."""
    satirlar = ["Adres teshisi - %s"
                % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "", "ERISIM:"]
    for ad, url in ADAY_HOSTLAR:
        govde, hata = _dene(url)
        if hata:
            satirlar.append("  %-12s %-26s" % (hata, ad))
        else:
            satirlar.append("  ACIK         %-26s %s"
                            % (ad, govde[:50].decode("utf-8", "replace")))
    metin = "\n".join(satirlar)
    print(metin)
    with open(TESHIS_DOSYA, "w", encoding="utf-8") as f:
        f.write(metin + "\n")
    return 0


def baglanti_testi():
    try:
        d = istek("%s/contracts?productType=%s" % (BG, URUN))
        n = len((d or {}).get("data") or [])
        print("BAGLANTI TAMAM - Bitget %d kontrat listeledi" % n)
        return 0
    except Exception as e:
        print("BAGLANTI HATASI %s: %s" % (type(e).__name__, e))
        return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tek-tur", action="store_true", help="tek calistir ve cik")
    ap.add_argument("--baglanti-testi", action="store_true")
    ap.add_argument("--host-testi", action="store_true")
    a = ap.parse_args()
    if a.host_testi:
        raise SystemExit(host_testi())
    if a.baglanti_testi:
        raise SystemExit(baglanti_testi())
    tur()


if __name__ == "__main__":
    main()
