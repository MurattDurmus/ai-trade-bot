import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime
import requests
import base64
import json
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="AI Trade Dashboard", page_icon="📊", layout="wide")
st.title("📊 AI Trade Gösterge Paneli (7/24 Otonom)")


# --- GİTHUB BAĞLANTI FONKSİYONLARI ---
def github_veri_oku():
    try:
        token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]
        url = f"https://api.github.com/repos/{repo}/contents/cuzdan_verileri.json"
        headers = {"Authorization": f"Bearer {token}", "Cache-Control": "no-cache"}

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            content_json = response.json()
            file_content_bytes = base64.b64decode(content_json["content"])
            veri = json.loads(file_content_bytes.decode('utf-8'))
            for islem in veri.get("islem_gecmisi", []):
                if isinstance(islem['Tarih'], str):
                    islem['Tarih'] = datetime.fromisoformat(islem['Tarih'])
            return veri["nakit"], veri["btc"], veri["islem_gecmisi"], veri["son_alim_fiyati"]
    except Exception as e:
        st.error(f"Veri çekilemedi: {e}")
    return 10000.0, 0.0, [], 0.0


def cuzdan_sifirla():
    try:
        token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]
        url = f"https://api.github.com/repos/{repo}/contents/cuzdan_verileri.json"
        headers = {"Authorization": f"Bearer {token}"}

        sha = None
        resp_get = requests.get(url, headers=headers)
        if resp_get.status_code == 200:
            sha = resp_get.json().get("sha")

        veri = {
            "nakit": 10000.0,
            "btc": 0.0,
            "islem_gecmisi": [],
            "son_alim_fiyati": 0.0
        }

        json_str = json.dumps(veri, ensure_ascii=False, indent=4)
        encoded_content = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')

        data = {
            "message": "🔄 Sistem Sıfırlandı (Kullanıcı Talebi)",
            "content": encoded_content
        }
        if sha:
            data["sha"] = sha

        res = requests.put(url, headers=headers, json=data)
        if res.status_code in [200, 201]:
            st.success("✅ Cüzdan başarıyla 10.000$ başlangıç durumuna sıfırlandı!")
            time.sleep(2)
            st.rerun()
        else:
            st.error("❌ Sıfırlama başarısız oldu!")
    except Exception as e:
        st.error(f"GitHub kayıt hatası: {e}")


# Verileri Çek
nakit, btc, islem_gecmisi, son_alim_fiyati = github_veri_oku()

# --- YAN MENÜ (SIDEBAR) & AYARLAR ---
with st.sidebar:
    st.header("⚙️ Sistem Ayarları")
    st.write("Eğer veriler bozulursa veya testi baştan başlatmak isterseniz aşağıdaki butonu kullanabilirsiniz.")
    st.divider()
    if st.button("⚠️ Cüzdanı Sıfırla (Reset)", use_container_width=True):
        cuzdan_sifirla()


# --- ANLIK FİYAT VE GRAFİK İÇİN HAFİF VERİ ÇEKİMİ ---
@st.cache_data(ttl=60)
def btc_fiyat_getir():
    df = yf.download('BTC-USD', period='7d', interval='1h', progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    # Makro verileri çek
    semboller = {'DXY': 'DX-Y.NYB', 'US10Y': '^TNX', 'VIX': '^VIX'}
    veri_sozlugu = {}
    for isim, sembol in semboller.items():
        df_temp = yf.download(sembol, period='5d', interval='1d', progress=False)
        if isinstance(df_temp.columns, pd.MultiIndex):
            df_temp.columns = df_temp.columns.droplevel(1)
        veri_sozlugu[isim] = df_temp['Close']
    df_makro = pd.DataFrame(veri_sozlugu).ffill()

    return df, df_makro


df_saatlik, df_makro = btc_fiyat_getir()
anlik_fiyat = float(df_saatlik['Close'].iloc[-1])
toplam_varlik = nakit + (btc * anlik_fiyat)
kar_zarar = toplam_varlik - 10000.0

# --- PİYASA HAVASI VE NOTU (YENİ EKLENEN BÖLÜM) ---
dxy_anlik = float(df_makro['DXY'].iloc[-1])
dxy_degisim = (dxy_anlik - float(df_makro['DXY'].iloc[-2])) / float(df_makro['DXY'].iloc[-2]) * 100

vix_anlik = float(df_makro['VIX'].iloc[-1])
us10y_anlik = float(df_makro['US10Y'].iloc[-1])

piyasa_durumu = ""
renk = ""
ikon = ""
detay_not = ""

if dxy_degisim > 0 and us10y_anlik > 4.20:
    piyasa_durumu = "TEHLİKELİ / BASKILI"
    renk = "error"
    ikon = "🚨"
    detay_not = "Güçlü Dolar (DXY) ve yüksek ABD Tahvil Faizleri kripto piyasası üzerinde ciddi bir baskı oluşturuyor. Son günlerde spot Bitcoin ETF'lerindeki çıkışlar ve yaklaşan ABD bilanço sezonu belirsizliği, kurumsal talebi zayıflattı. Bu makroekonomik fırtına dinene kadar yapay zeka muhtemelen nakitte kalmayı (beklemeyi) veya çok düşük tutarlı temkinli alımlar yapmayı seçecektir. Likidite havuzlarında aşağı yönlü sarkmalar yaşanabilir."
elif vix_anlik < 18 and dxy_degisim < 0:
    piyasa_durumu = "OLUMLU / RİSK İŞTAHI YÜKSEK"
    renk = "success"
    ikon = "🟢"
    detay_not = "Piyasada korku endeksi (VIX) sakin ve Dolar Endeksi (DXY) geri çekiliyor. Bu durum riskli varlıklara (Bitcoin ve Altcoinler) doğru bir sermaye akışı sağlıyor. 80.000$ psikolojik desteğinin güçlü kalması ve faiz beklentilerindeki yumuşama, yükseliş trendini destekliyor. Yapay zeka bu koşullarda daha agresif 'AL' sinyalleri üretebilir ve portföydeki BTC ağırlığını artırabilir."
else:
    piyasa_durumu = "TEMKİNLİ / YÖN ARAYIŞI"
    renk = "warning"
    ikon = "⏳"
    detay_not = "Piyasa şu an bir kırılım noktasında ve yatay sıkışma (konsolidasyon) sürecinde. Makro veriler karışık sinyaller veriyor; FOMC tutanakları, PMI verileri ve jeopolitik gelişmeler yakından izleniyor. Sistem şu anda fırsat kolluyor, olası bir likidite avına veya stop patlatma (squeeze) hareketine karşı kasanın büyük bölümünü nakitte tutarak güvenliği ön planda tutuyor."

with st.expander(f"{ikon} **GÜNCEL PİYASA DURUMU: {piyasa_durumu}**", expanded=True):
    st.markdown(
        f"<div style='padding:15px; border-radius:10px; border-left: 5px solid {'#ff4b4b' if renk == 'error' else '#00cc96' if renk == 'success' else '#ffcc00'}; background-color: rgba(255,255,255,0.05);'>{detay_not}</div>",
        unsafe_allow_html=True)

    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("Dolar Endeksi (DXY)", f"{dxy_anlik:.2f}", f"{dxy_degisim:.2f}%", delta_color="inverse")
    col_m2.metric("Korku Endeksi (VIX)", f"{vix_anlik:.2f}")
    col_m3.metric("ABD 10Y Tahvil", f"%{us10y_anlik:.2f}")

st.divider()

# --- ARAYÜZ YERLEŞİMİ (Devamı) ---
st.info(
    "ℹ️ Bu panel sadece izleme amaçlıdır. Alım-Satım kararları ve işlemler arka planda GitHub Actions sunucularında her saat başı otonom olarak yapılmaktadır.")

if st.button("🔄 Ekranı Güncelle", use_container_width=True):
    st.rerun()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Toplam Varlık", f"${toplam_varlik:,.2f}", f"${kar_zarar:,.2f} Net Kar")
col2.metric("Nakit Bakiye", f"${nakit:,.2f}")
col3.metric("BTC Miktarı", f"{btc:.6f} BTC")
col4.metric("Anlık BTC Fiyatı", f"${anlik_fiyat:,.2f}")

st.divider()

col_grafik, col_hesap = st.columns([3, 1])

with col_grafik:
    st.subheader("📈 7 Günlük İşlem Grafiği")
    fig = go.Figure(data=[go.Candlestick(
        x=df_saatlik.index, open=df_saatlik['Open'], high=df_saatlik['High'],
        low=df_saatlik['Low'], close=df_saatlik['Close'], name='BTC/USD'
    )])

    for islem in islem_gecmisi:
        islem_renk = '#00ff00' if 'AL' in islem['Tip'] else '#ff0000'
        sembol = 'triangle-up' if 'AL' in islem['Tip'] else 'triangle-down'
        konum = 'bottom center' if 'AL' in islem['Tip'] else 'top center'
        metin = f"{islem['Tip']}<br>${islem['Fiyat']:,.0f}"

        fig.add_trace(go.Scatter(
            x=[islem['Tarih']], y=[islem['Fiyat']], mode='markers+text',
            marker=dict(symbol=sembol, size=14, color=islem_renk, line=dict(width=1, color='white')),
            text=[metin], textposition=konum, name=islem['Tip'], showlegend=False
        ))

    fig.update_layout(template='plotly_dark', margin=dict(l=0, r=0, t=30, b=0), height=500,
                      xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)

with col_hesap:
    st.subheader("📜 İşlem Defteri")
    if len(islem_gecmisi) == 0:
        st.write("Henüz otonom işlem yapılmadı.")
    else:
        for islem in reversed(islem_gecmisi):
            islem_renk_ikon = "🟢" if "AL" in islem['Tip'] else "🔴"
            zaman = islem['Tarih'].strftime("%d %b %H:%M")
            with st.expander(f"{islem_renk_ikon} {islem['Tip']} - {zaman}"):
                st.write(f"**Fiyat:** ${islem['Fiyat']:,.2f}")
                st.write(f"**Tutar:** ${islem['Tutar']:,.2f}")