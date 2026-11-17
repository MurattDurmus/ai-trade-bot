"""
ai-trade-bot — izleme paneli (düzeltilmiş sürüm, 17 Kasım 2026)

Değişenler:
  - Cüzdan okunamazsa sessizce 10.000 $ göstermek yerine hatayı açıkça yazar.
  - Motorun her gün kaydettiği "son_durum" gösterilir: aylık/haftalık CDLTRD (HullMA 20, kapanmış mum),
    model kararı, haber skoru, günün kararı ve gerekçesi.
  - Koda sabit yazılmış "piyasa yorumu" metinleri kaldırıldı; yerine yalnızca ölçülen değerler ve
    motorun kendi kuralları yazılır.
  - Grafik günlük: son 180 günün günlük mumları + haftalık CDLTRD çizgisi (kapanmış haftalar) + işlemler.
  - "Her saat başı" yazısı düzeltildi: motor günde bir, 03:15'te çalışır.
  - Sıfırlama düğmesi onay ister.
Sanal (kağıt üstünde) işlemleri gösterir. Gerçek emir göndermez. Yatırım tavsiyesi değildir.
"""
import base64
import json
import math
import time
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="AI Trade Paneli", page_icon="📊", layout="wide")
st.title("📊 AI Trade Paneli (sanal, günlük)")

BASLANGIC = 10000.0
HMA_UZ = 20


# ------------------------------------------------------------------ GitHub cüzdanı
def _istek_bilgisi():
    repo = st.secrets["GITHUB_REPO"]
    return (f"https://api.github.com/repos/{repo}/contents/cuzdan_verileri.json",
            {"Authorization": f"Bearer {st.secrets['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"})


@st.cache_data(ttl=60)
def cuzdan_oku():
    """(veri, hata) döndürür. Okunamazsa veri None olur."""
    try:
        url, bas = _istek_bilgisi()
        r = requests.get(url, headers=bas, timeout=20)
    except Exception as e:
        return None, f"GitHub'a bağlanılamadı: {e}"
    if r.status_code == 401:
        return None, "GitHub token geçersiz ya da süresi dolmuş (401). Streamlit Secrets'taki GITHUB_TOKEN'ı yenileyin."
    if r.status_code == 404:
        return None, "Depo ya da cuzdan_verileri.json bulunamadı (404). GITHUB_REPO ve token'ın depo erişimini kontrol edin."
    if r.status_code != 200:
        return None, f"GitHub cevabı: {r.status_code}"
    veri = json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
    return veri, None


def cuzdan_sifirla():
    url, bas = _istek_bilgisi()
    r = requests.get(url, headers=bas, timeout=20)
    veri = {"nakit": BASLANGIC, "btc": 0.0, "islem_gecmisi": [], "son_alim_fiyati": 0.0}
    govde = {"message": "🔄 Panelden sıfırlandı",
             "content": base64.b64encode(json.dumps(veri, ensure_ascii=False, indent=4).encode()).decode()}
    if r.status_code == 200:
        govde["sha"] = r.json()["sha"]
    p = requests.put(url, headers=bas, json=govde, timeout=20)
    return p.status_code in (200, 201), p.status_code


def tarih_cevir(x):
    t = pd.Timestamp(x)
    return t.tz_convert(None) if t.tzinfo else t


# ------------------------------------------------------------------ piyasa verisi
@st.cache_data(ttl=900)
def btc_gunluk():
    for taban in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            r = requests.get(f"{taban}/api/v3/klines",
                             params={"symbol": "BTCUSDT", "interval": "1d", "limit": 1000}, timeout=20)
            r.raise_for_status()
            df = pd.DataFrame(r.json()).iloc[:, :5]
            df.columns = ["t", "Open", "High", "Low", "Close"]
            df.index = pd.to_datetime(df["t"], unit="ms")
            return df[["Open", "High", "Low", "Close"]].astype(float), "Binance BTCUSDT"
        except Exception:
            continue
    df = yf.download("BTC-USD", period="3y", interval="1d", progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df[["Open", "High", "Low", "Close"]], "Yahoo BTC-USD"


@st.cache_data(ttl=900)
def makro():
    out = {}
    for ad, kod in {"DXY": "DX-Y.NYB", "US10Y": "^TNX", "VIX": "^VIX"}.items():
        d = yf.download(kod, period="90d", interval="1d", progress=False, auto_adjust=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.droplevel(1)
        out[ad] = d["Close"].dropna()
    return out


@st.cache_data(ttl=1800)
def haberler():
    sonuc = []
    try:
        for h in (yf.Ticker("BTC-USD").news or [])[:5]:
            c = h.get("content") or h
            baslik = c.get("title")
            link = (c.get("canonicalUrl") or {}).get("url") or c.get("link")
            kaynak = (c.get("provider") or {}).get("displayName") or c.get("publisher", "")
            if baslik:
                sonuc.append((baslik, link, kaynak))
    except Exception:
        pass
    return sonuc


def wma(s, n):
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def hma(s, n=HMA_UZ):
    return wma(2 * wma(s, n // 2) - wma(s, n), int(math.floor(math.sqrt(n))))


def haftalik_cizgi(gunluk):
    """Kapanmış haftaların CDLTRD çizgisi, hafta bittikten sonraki günden itibaren geçerli (basamaklı)."""
    k = gunluk["Close"].resample("W-SUN").last()
    k = k[k.index <= gunluk.index[-1]]
    h = hma(k)
    yesil = h > h.shift(1)
    h.index = h.index + pd.Timedelta(days=1)
    yesil.index = h.index
    t = h.index.union(gunluk.index)
    return h.reindex(t).ffill().reindex(gunluk.index), yesil.reindex(t).ffill().reindex(gunluk.index)


# ------------------------------------------------------------------ sayfa
veri, hata = cuzdan_oku()

with st.sidebar:
    st.header("⚙️ Ayarlar")
    st.caption("Sanal cüzdanı 10.000 $ başlangıca döndürür. Geri alınamaz.")
    onay = st.checkbox("Sıfırlamak istediğimi onaylıyorum")
    if st.button("⚠️ Cüzdanı sıfırla", use_container_width=True, disabled=not onay):
        ok, kod = cuzdan_sifirla()
        if ok:
            st.success("Sıfırlandı.")
            cuzdan_oku.clear()
            time.sleep(1)
            st.rerun()
        else:
            st.error(f"Sıfırlanamadı (GitHub {kod}).")

if hata:
    st.error(f"Cüzdan okunamadı: {hata}")
    st.stop()

gunluk, kaynak = btc_gunluk()
fiyat = float(gunluk["Close"].iloc[-1])
nakit, btc = float(veri.get("nakit", 0)), float(veri.get("btc", 0))
toplam = nakit + btc * fiyat
islemler = veri.get("islem_gecmisi", [])
sd = veri.get("son_durum")

st.caption("Motor GitHub Actions'ta **günde bir kez (03:15 TSİ)** BTC'nin kapanmış günlük mumuna göre karar verir. "
           "Bu panel yalnızca izler; işlem yapmaz.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Toplam varlık", f"${toplam:,.2f}", f"{(toplam / BASLANGIC - 1) * 100:+.2f}% (başlangıç 10.000 $)")
c2.metric("Nakit", f"${nakit:,.2f}")
c3.metric("BTC", f"{btc:.6f}", f"${btc * fiyat:,.0f}" if btc else None)
c4.metric("BTC fiyatı", f"${fiyat:,.0f}", help=f"Kaynak: {kaynak}, bugünkü mum (henüz kapanmadı)")

st.divider()
st.subheader("🧭 Motorun son kararı")
if not sd:
    st.info("Motor henüz yeni sürümle çalışmadı; yarın 03:15'ten sonra burada görünecek.")
else:
    def rozet(yesil):
        return "🟢 Yeşil (yükseliyor)" if yesil else "🔴 Kırmızı (düşüyor)"

    a, b, c = st.columns(3)
    a.markdown(f"**Aylık CDLTRD** ({sd['aylik_son_mum']} kapanışı)  \n{rozet(sd['aylik_yesil'])}  \nÇizgi: {sd['aylik_cizgi']:,.0f} $")
    alt = sd["kapanis"] < sd["haftalik_cizgi"]
    b.markdown(f"**Haftalık CDLTRD** ({sd['haftalik_son_mum']} kapanışı)  \n{rozet(sd['haftalik_yesil'])}  \n"
               f"Çizgi: {sd['haftalik_cizgi']:,.0f} $ — kapanış {'**altında**' if alt else 'üstünde'}")
    c.markdown(f"**Model:** {'AL (1)' if sd.get('model') == 1 else 'BEKLE (0)'}  \n"
               f"**Haber skoru:** {sd.get('haber', 0):+.2f} (−1 kötü, +1 iyi)  \n"
               f"**Karar günü:** {sd['gun']} kapanışı {sd['kapanis']:,.0f} $")
    karar_ad = {"AL": "🟢 ALIM", "SAT_HEPSI": "🔴 HEPSİNİ SAT", "SAT_YARI": "🟠 YARISINI SAT"}.get(sd.get("karar"), "⏸️ İŞLEM YOK")
    st.markdown(f"#### {karar_ad}: {sd.get('gerekce', '')}")
    st.caption(f"Motorun çalıştığı an (UTC): {sd.get('calisma', '-')}")

with st.expander("Motorun kuralları"):
    st.markdown(
        "- **Alım:** aylık ve haftalık CDLTRD yeşil, günlük kapanış haftalık çizginin üstünde, model AL diyor, haber skoru −0,40'tan iyi. "
        "Tutar 1.000 $ × makro çarpan (0,5–2), haberler çok iyiyse × 1,5.\n"
        "- **Yarısını sat:** günlük kapanış haftalık çizginin altına ilk kez indi.\n"
        "- **Hepsini sat:** haftalık CDLTRD kırmızı; hafta çizginin altında kapandı; 2 gün üst üste çizginin altında; "
        "çizginin 1 ATR altına indi; son alımdan %5 zarar; haber skoru −0,40'ın altında.\n"
        "- Model BEKLE derse satış yapılmaz, yalnızca alım yapılmaz.")

st.divider()
sol, sag = st.columns([3, 1])
with sol:
    st.subheader("📈 Son 180 gün (günlük) ve haftalık CDLTRD")
    cizgi, yesil = haftalik_cizgi(gunluk)
    g = gunluk.iloc[-180:]
    cz, ys = cizgi.iloc[-180:], yesil.iloc[-180:]
    fig = go.Figure(go.Candlestick(x=g.index, open=g["Open"], high=g["High"], low=g["Low"], close=g["Close"],
                                   name="BTC", showlegend=False))
    fig.add_trace(go.Scatter(x=cz.index, y=cz.where(ys == 1), mode="lines", line=dict(color="#16a34a", width=2),
                             name="Haftalık CDLTRD (yeşil)", line_shape="hv"))
    fig.add_trace(go.Scatter(x=cz.index, y=cz.where(ys == 0), mode="lines", line=dict(color="#dc2626", width=2),
                             name="Haftalık CDLTRD (kırmızı)", line_shape="hv"))
    for i in islemler:
        t = tarih_cevir(i["Tarih"]).normalize()
        if t < g.index[0]:
            continue
        al = str(i["Tip"]).startswith("AL")
        fig.add_trace(go.Scatter(
            x=[t], y=[i["Fiyat"]], mode="markers", showlegend=False,
            marker=dict(symbol="triangle-up" if al else "triangle-down", size=13,
                        color="#16a34a" if al else "#dc2626", line=dict(width=1, color="white")),
            hovertext=f"{i['Tip']}<br>{i['Fiyat']:,.0f} $ — {i['Tutar']:,.0f} $", hoverinfo="text"))
    fig.update_layout(template="plotly_dark", height=520, margin=dict(l=0, r=0, t=10, b=0),
                      xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.02))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Haftalık çizgi yalnızca kapanmış haftalardan hesaplanır ve hafta bittikten sonraki günden itibaren gösterilir.")

with sag:
    st.subheader("📜 İşlem defteri")
    if not islemler:
        st.write("Henüz işlem yok.")
    for i in reversed(islemler[-30:]):
        ikon = "🟢" if str(i["Tip"]).startswith("AL") else "🔴"
        with st.expander(f"{ikon} {tarih_cevir(i['Tarih']):%d %b %Y} — {i['Tip']}"):
            st.write(f"Fiyat: {i['Fiyat']:,.2f} $")
            st.write(f"Tutar: {i['Tutar']:,.2f} $")

st.divider()
st.subheader("🌍 Makro göstergeler (son 90 gün ortalamasına göre)")
m = makro()
kol = st.columns(3)
for k, (ad, etiket) in zip(kol, [("DXY", "Dolar endeksi"), ("US10Y", "ABD 10 yıllık faiz %"), ("VIX", "Korku endeksi (VIX)")]):
    s = m[ad]
    if len(s) < 2:
        k.metric(etiket, "veri yok")
        continue
    son, ort = float(s.iloc[-1]), float(s.mean())
    k.metric(etiket, f"{son:.2f}", f"{(son / float(s.iloc[-2]) - 1) * 100:+.2f}% (1 gün)", delta_color="inverse")
    k.caption(f"Ortalama {ort:.2f}; bugünkü değer ortalamanın %{(son / ort - 1) * 100:+.1f} {'üstünde' if son > ort else 'altında'}")
st.caption("Bu değerler yalnızca bilgi içindir; motorun makro çarpanı alım tutarını belirlerken bunlara bakar.")

h = haberler()
if h:
    with st.expander("📰 Son BTC haberleri (Yahoo Finance, İngilizce)"):
        for baslik, link, kay in h:
            st.markdown(f"- [{baslik}]({link}) — {kay}" if link else f"- {baslik} — {kay}")
