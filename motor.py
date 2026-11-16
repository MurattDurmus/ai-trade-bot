"""
ai-trade-bot — günlük motor (düzeltilmiş sürüm, 16 Kasım 2026)

Önceki sürümden iki temel fark:
  1) CDLTRD artık gerçek tanımıyla hesaplanıyor: kapanışların HullMA 20'si (TradingView "CDLTRD v0.5 by JustUncleL").
     Eski kodda bunun yerine Chandelier Exit (22 günün en yükseği − 3×ATR) kullanılıyordu.
     Renk: çizgi bir önceki kapanmış muma göre yükseliyorsa yeşil, düşüyorsa kırmızı.
     Aylık ve haftalık değerler yalnızca KAPANMIŞ mumlardan okunur.
  2) Motor saatte bir değil, günde bir kez çalışır (BTC günlük mumu kapandıktan sonra, 03:15 TSİ).
     Model günlük verilerle eğitildiği için saatlik çalışmada gün boyu aynı kararı tekrarlıyor ve
     "kademeli" alım satımı birkaç saate sıkıştırıyordu.

Karar akışı (her gün bir kez, dünün kapanışına göre):
  A) ACİL ÇIKIŞ (hepsini sat)
     - son alım fiyatından %5 düşüş (zarar kes), ya da
     - haber skoru −0,40'ın altında (kötü haber kalkanı), ya da
     - haftalık CDLTRD kırmızı (kapanmış hafta), ya da
     - hibrit stop: biten hafta haftalık çizginin altında kapandı / son 2 gün çizginin altında kapandı /
       kapanış çizginin 1 ATR'den fazla altında
  B) TEMKİN (yarısını sat, alım yok)
     - günlük kapanış haftalık çizginin altına ilk kez indi (hibrit stop 1. evre)
  C) ALIM (aşağıdakilerin hepsi doğruysa)
     - aylık ve haftalık CDLTRD yeşil, günlük kapanış haftalık çizginin üstünde
     - XGBoost modeli "AL" (1) diyor
     - haber skoru −0,40'tan iyi
     Tutar: 1.000 $ × makro çarpan (0,5 / 1 / 1,5 / 2; beş maddelik puana göre), haberler çok iyiyse × 1,5.
  Model "BEKLE" (0) derse artık satış yapılmaz, yalnızca alım yapılmaz. Satışlar CDLTRD ve acil çıkış kurallarından gelir.

Cüzdan dosyası (cuzdan_verileri.json) eskisiyle aynı biçimde; app.py değiştirilmeden okuyabilir.
Ek olarak "son_durum" alanına her günün göstergeleri yazılır (karar şeffaf olsun diye).

Çalıştırma:
  GitHub Actions: .github/workflows/bot_calistir.yml (günde bir)
  Yerel deneme : python motor.py --kuru      (karar verir, ekrana yazar, GitHub'a hiçbir şey kaydetmez)
Not: Sanal (kağıt üstünde) işlem yapar. Gerçek emir göndermez. Yatırım tavsiyesi değildir.
"""
import argparse
import base64
import json
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

HMA_UZ = 20
ATR_UZ = 22
BASLANGIC_NAKIT = 10000.0
TEMEL_ALIM = 1000.0
ZARAR_KES = -0.05
HABER_KOTU, HABER_IYI = -0.40, 0.40
DOSYA = "cuzdan_verileri.json"
OZELLIKLER = ['BTC_SMA_20', 'BTC_Degisim_1G', 'BTC_Degisim_7G', 'DXY_Degisim', 'US10Y_Baski', 'US5Y_Degisim',
              'VIX_Seviye', 'SP500_Degisim', 'Nasdaq_Degisim', 'Altin_Degisim', 'Petrol_Degisim', 'Bakir_Degisim']
MAKRO = {'DXY': 'DX-Y.NYB', 'US10Y': '^TNX', 'US5Y': '^FVX', 'VIX': '^VIX', 'SP500': '^GSPC',
         'Nasdaq': '^NDX', 'Altin': 'GC=F', 'Petrol': 'CL=F', 'Bakir': 'HG=F'}


# ------------------------------------------------------------------ GitHub cüzdanı
def _baslik():
    return {"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json"}


def _url():
    return f"https://api.github.com/repos/{os.environ['GH_REPO']}/contents/{DOSYA}"


def cuzdan_oku():
    bos = {"nakit": BASLANGIC_NAKIT, "btc": 0.0, "islem_gecmisi": [], "son_alim_fiyati": 0.0}
    if not os.environ.get("GH_TOKEN") or not os.environ.get("GH_REPO"):
        print("⚠️ GH_TOKEN / GH_REPO yok: boş cüzdanla çalışılıyor.")
        return bos
    r = requests.get(_url(), headers=_baslik(), timeout=30)
    if r.status_code != 200:
        print(f"⚠️ Cüzdan okunamadı ({r.status_code}): boş cüzdanla çalışılıyor.")
        return bos
    veri = json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
    for k, v in bos.items():
        veri.setdefault(k, v)
    return veri


def cuzdan_kaydet(veri, mesaj):
    r = requests.get(_url(), headers=_baslik(), timeout=30)
    govde = {"message": mesaj,
             "content": base64.b64encode(json.dumps(veri, ensure_ascii=False, indent=4).encode("utf-8")).decode()}
    if r.status_code == 200:
        govde["sha"] = r.json()["sha"]
    p = requests.put(_url(), headers=_baslik(), json=govde, timeout=30)
    print("✅ Cüzdan GitHub'a kaydedildi." if p.status_code in (200, 201) else f"❌ Kayıt hatası: {p.status_code} {p.text[:200]}")


# ------------------------------------------------------------------ veri
def btc_gunluk():
    """Binance BTCUSDT günlük mumları (TradingView ile aynı kaynak). ABD sunucularından api.binance.com
    erişilemediği için önce herkese açık veri aynası denenir; olmazsa Yahoo Finance."""
    for taban in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            satirlar, son = [], None
            for _ in range(4):                                   # 4 × 1000 gün ≈ 11 yıl
                p = {"symbol": "BTCUSDT", "interval": "1d", "limit": 1000}
                if son:
                    p["endTime"] = son
                r = requests.get(f"{taban}/api/v3/klines", params=p, timeout=30)
                r.raise_for_status()
                parca = r.json()
                if not parca:
                    break
                satirlar = parca + satirlar
                son = parca[0][0] - 1
                if len(parca) < 1000:
                    break
            df = pd.DataFrame(satirlar, columns=list("tohlcv") + ["ct", "q", "n", "tb", "tq", "x"])
            df["tarih"] = pd.to_datetime(df["t"], unit="ms")
            df = df.drop_duplicates("tarih").set_index("tarih").sort_index()
            df = df[["o", "h", "l", "c"]].astype(float)
            df.columns = ["Open", "High", "Low", "Close"]
            print(f"📈 BTC günlük veri: {taban} ({len(df)} gün)")
            return df
        except Exception as e:
            print(f"   {taban} kullanılamadı: {e}")
    import yfinance as yf
    df = yf.download("BTC-USD", period="max", interval="1d", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    print(f"📈 BTC günlük veri: Yahoo Finance ({len(df)} gün)")
    return df[["Open", "High", "Low", "Close"]]


def makro_gunluk(btc_kapanis):
    import yfinance as yf
    veri = {"BTC": btc_kapanis}
    for ad, kod in MAKRO.items():
        d = yf.download(kod, period="200d", interval="1d", progress=False, auto_adjust=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.droplevel(1)
        s = d["Close"]
        s.index = pd.to_datetime(s.index).tz_localize(None)
        veri[ad] = s
    df = pd.DataFrame(veri)
    df = df[df.index >= btc_kapanis.index[-200]].ffill()
    df['BTC_SMA_20'] = df['BTC'].rolling(20).mean()
    df['BTC_Degisim_1G'] = df['BTC'].pct_change(1)
    df['BTC_Degisim_7G'] = df['BTC'].pct_change(7)
    df['DXY_Degisim'] = df['DXY'].pct_change(1)
    df['US10Y_Baski'] = df['US10Y'] - df['US10Y'].rolling(10).mean()
    df['US5Y_Degisim'] = df['US5Y'].pct_change(1)
    df['VIX_Seviye'] = (df['VIX'] > 20).astype(int)
    for ad in ['SP500', 'Nasdaq', 'Altin', 'Petrol', 'Bakir']:
        df[f'{ad}_Degisim'] = df[ad].pct_change(1)
    return df.dropna()


def haber_skoru():
    try:
        import yfinance as yf
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        an = SentimentIntensityAnalyzer()
        skorlar = []
        for h in (yf.Ticker("BTC-USD").news or [])[:10]:
            baslik = h.get("title") or (h.get("content") or {}).get("title", "")
            if baslik:
                skorlar.append(an.polarity_scores(baslik)["compound"])
        return float(np.mean(skorlar)) if skorlar else 0.0
    except Exception as e:
        print(f"⚠️ Haber skoru hesaplanamadı ({e}); 0 kabul edildi.")
        return 0.0


# ------------------------------------------------------------------ CDLTRD (HullMA 20)
def wma(s, n):
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def hma(s, n=HMA_UZ):
    return wma(2 * wma(s, n // 2) - wma(s, n), int(math.floor(math.sqrt(n))))


def kapanmis_mumlar(gunluk, kural):
    """Günlük kapanışlardan haftalık/aylık mum; yalnızca tamamen bitmiş dönemler."""
    c = gunluk["Close"]
    grup = c.resample(kural)
    kapanis = grup.last()
    son_gun = grup.apply(lambda s: s.index.max())
    bitis = kapanis.index                                       # dönemin son takvim günü
    biten = bitis <= c.index[-1]                                # son günlük mum bu dönemin sonuna ulaştıysa bitmiştir
    return kapanis[biten & son_gun.notna()].dropna()


def cdltrd_durum(gunluk):
    """Son kapanmış aylık ve haftalık CDLTRD durumu + günlük göstergeler."""
    sonuc = {}
    for ad, kural in (("aylik", "ME"), ("haftalik", "W-SUN")):
        k = kapanmis_mumlar(gunluk, kural)
        h = hma(k)
        sonuc[f"{ad}_cizgi"] = float(h.iloc[-1])
        sonuc[f"{ad}_yesil"] = bool(h.iloc[-1] > h.iloc[-2])
        sonuc[f"{ad}_onceki_yesil"] = bool(h.iloc[-2] > h.iloc[-3])
        sonuc[f"{ad}_kapanis"] = float(k.iloc[-1])
        sonuc[f"{ad}_son_mum"] = k.index[-1].strftime("%Y-%m-%d")
    c = gunluk["Close"]
    tr = pd.concat([gunluk["High"] - gunluk["Low"], (gunluk["High"] - c.shift()).abs(),
                    (gunluk["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    sonuc["atr"] = float(tr.ewm(alpha=1 / ATR_UZ, adjust=False).mean().iloc[-1])
    sonuc["kapanis"] = float(c.iloc[-1])
    sonuc["onceki_kapanis"] = float(c.iloc[-2])
    sonuc["gun"] = c.index[-1].strftime("%Y-%m-%d")
    return sonuc


# ------------------------------------------------------------------ karar
def karar_ver(cz, d, model_karari, makro_satir, haber):
    """Bugünkü işlemi döndürür: (tip, oran_veya_tutar, gerekçe). tip: 'SAT_HEPSI', 'SAT_YARI', 'AL', None"""
    cizgi = d["haftalik_cizgi"]
    alt_bugun = d["kapanis"] < cizgi
    alt_dun = d["onceki_kapanis"] < cizgi
    btc, fiyat = cz["btc"], d["kapanis"]

    if btc > 0:
        if cz["son_alim_fiyati"] > 0 and (fiyat / cz["son_alim_fiyati"] - 1) <= ZARAR_KES:
            return "SAT_HEPSI", 1.0, "Zarar kes (son alımdan %5 düşüş)"
        if haber <= HABER_KOTU:
            return "SAT_HEPSI", 1.0, f"Kötü haber kalkanı (skor {haber:.2f})"
        if not d["haftalik_yesil"]:
            return "SAT_HEPSI", 1.0, "Haftalık CDLTRD kırmızı"
        if d["haftalik_kapanis"] < cizgi:
            return "SAT_HEPSI", 1.0, "Hibrit stop: hafta çizginin altında kapandı"
        if alt_bugun and alt_dun:
            return "SAT_HEPSI", 1.0, "Hibrit stop: 2 gün üst üste çizginin altında"
        if alt_bugun and fiyat < cizgi - d["atr"]:
            return "SAT_HEPSI", 1.0, "Hibrit stop: çizginin 1 ATR altına indi"
        if alt_bugun and not alt_dun:
            return "SAT_YARI", 0.5, "Temkin: günlük kapanış haftalık çizginin altına indi"

    if alt_bugun:
        return None, 0, "Alım yok: fiyat haftalık çizginin altında"
    if not (d["aylik_yesil"] and d["haftalik_yesil"]):
        return None, 0, "Alım yok: aylık ya da haftalık CDLTRD kırmızı"
    if model_karari != 1:
        return None, 0, "Alım yok: model BEKLE diyor"
    if haber <= HABER_KOTU:
        return None, 0, f"Alım yok: kötü haber (skor {haber:.2f})"
    m = makro_satir
    puan = int(sum([m['VIX'] < 20, m['DXY_Degisim'] < 0, m['US10Y_Baski'] <= 0,
                    m['Nasdaq_Degisim'] > 0, m['BTC_Degisim_7G'] > 0.02]))
    carpan = 2.0 if puan >= 4 else (1.5 if puan == 3 else (1.0 if puan == 2 else 0.5))
    if haber >= HABER_IYI:
        carpan *= 1.5
    tutar = min(TEMEL_ALIM * carpan, cz["nakit"])
    if tutar < 10:
        return None, 0, "Alım yok: nakit bitti"
    return "AL", tutar, f"Alım: makro puan {puan}/5, çarpan {carpan:.2f}x"


def uygula(cz, tip, deger, gerekce, fiyat, gun):
    if tip == "AL":
        cz["btc"] += deger / fiyat
        cz["nakit"] -= deger
        cz["son_alim_fiyati"] = fiyat
        cz["islem_gecmisi"].append({"Tarih": gun, "Tip": f"AL ({gerekce})", "Fiyat": fiyat, "Tutar": deger})
    elif tip in ("SAT_HEPSI", "SAT_YARI"):
        miktar = cz["btc"] * deger
        if tip == "SAT_YARI" and cz["btc"] - miktar < 0.0001:
            miktar = cz["btc"]
        gelir = miktar * fiyat
        cz["btc"] -= miktar
        cz["nakit"] += gelir
        if cz["btc"] < 1e-9:
            cz["btc"], cz["son_alim_fiyati"] = 0.0, 0.0
        cz["islem_gecmisi"].append({"Tarih": gun, "Tip": f"SAT ({gerekce})", "Fiyat": fiyat, "Tutar": gelir})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kuru", action="store_true", help="karar ver ama GitHub'a kaydetme")
    args = ap.parse_args()
    print("🤖 Günlük motor başladı:", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    gunluk = btc_gunluk()
    bugun = pd.Timestamp(datetime.now(timezone.utc).date())
    gunluk = gunluk[gunluk.index < bugun]                       # bugünün henüz kapanmamış mumunu at
    d = cdltrd_durum(gunluk)
    print(f"📅 Karar günü (kapanmış): {d['gun']}   kapanış {d['kapanis']:,.0f} $")
    print(f"   Aylık CDLTRD   ({d['aylik_son_mum']}): {'YEŞİL' if d['aylik_yesil'] else 'KIRMIZI'}   çizgi {d['aylik_cizgi']:,.0f}")
    print(f"   Haftalık CDLTRD ({d['haftalik_son_mum']}): {'YEŞİL' if d['haftalik_yesil'] else 'KIRMIZI'}   çizgi {d['haftalik_cizgi']:,.0f}"
          f"   (kapanış {'üstünde' if d['kapanis'] >= d['haftalik_cizgi'] else 'ALTINDA'})")

    makro = makro_gunluk(gunluk["Close"])
    makro = makro[makro.index < bugun]                          # bugünün henüz bitmemiş makro verisini kullanma
    satir = makro.iloc[-1]
    from xgboost import XGBClassifier
    model = XGBClassifier()
    model.load_model("makro_xgboost_modeli.json")
    model_karari = int(model.predict(makro.iloc[[-1]][OZELLIKLER])[0])
    haber = haber_skoru()
    print(f"   Model: {'AL (1)' if model_karari == 1 else 'BEKLE (0)'}   haber skoru {haber:+.2f}   (makro verisi {makro.index[-1]:%Y-%m-%d})")

    cz = cuzdan_oku()
    son_gun_islem = cz.get("son_durum", {}).get("gun")
    if son_gun_islem == d["gun"]:
        print("⏸️ Bu gün için karar zaten verilmiş; tekrar işlem yapılmıyor.")
        return
    tip, deger, gerekce = karar_ver(cz, d, model_karari, satir, haber)
    print(f"➡️ KARAR: {tip or 'İŞLEM YOK'} — {gerekce}")
    uygula(cz, tip, deger, gerekce, d["kapanis"], d["gun"] + "T23:59:00")
    cz["son_durum"] = {**d, "model": model_karari, "haber": round(haber, 3), "karar": tip or "YOK", "gerekce": gerekce,
                       "calisma": datetime.now(timezone.utc).isoformat(timespec="minutes")}
    toplam = cz["nakit"] + cz["btc"] * d["kapanis"]
    print(f"💰 Nakit {cz['nakit']:,.2f} $ | BTC {cz['btc']:.6f} | Toplam {toplam:,.2f} $")
    if args.kuru:
        print("🧪 Kuru çalıştırma: hiçbir şey kaydedilmedi.")
    else:
        cuzdan_kaydet(cz, f"🤖 Günlük motor {d['gun']}: {tip or 'işlem yok'}")


if __name__ == "__main__":
    main()
