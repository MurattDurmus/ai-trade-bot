import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime
import requests
import base64
import json

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="AI Trade Dashboard", page_icon="📊", layout="wide")
st.title("📊 AI Trade Gösterge Paneli (7/24 Otonom)")


# --- GİTHUB'DAN SADECE OKUMA İŞLEMİ ---
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


nakit, btc, islem_gecmisi, son_alim_fiyati = github_veri_oku()


# --- ANLIK FİYAT VE GRAFİK İÇİN HAFİF VERİ ÇEKİMİ ---
@st.cache_data(ttl=60)
def btc_fiyat_getir():
    df = yf.download('BTC-USD', period='7d', interval='1h', progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    return df


df_saatlik = btc_fiyat_getir()
anlik_fiyat = float(df_saatlik['Close'].iloc[-1])
toplam_varlik = nakit + (btc * anlik_fiyat)
kar_zarar = toplam_varlik - 10000.0

# --- ARAYÜZ YERLEŞİMİ ---
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
        renk = '#00ff00' if 'AL' in islem['Tip'] else '#ff0000'
        sembol = 'triangle-up' if 'AL' in islem['Tip'] else 'triangle-down'
        konum = 'bottom center' if 'AL' in islem['Tip'] else 'top center'
        metin = f"{islem['Tip']}<br>${islem['Fiyat']:,.0f}"

        fig.add_trace(go.Scatter(
            x=[islem['Tarih']], y=[islem['Fiyat']], mode='markers+text',
            marker=dict(symbol=sembol, size=14, color=renk, line=dict(width=1, color='white')),
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
            renk = "🟢" if "AL" in islem['Tip'] else "🔴"
            zaman = islem['Tarih'].strftime("%d %b %H:%M")
            with st.expander(f"{renk} {islem['Tip']} - {zaman}"):
                st.write(f"**Fiyat:** ${islem['Fiyat']:,.2f}")
                st.write(f"**Tutar:** ${islem['Tutar']:,.2f}")