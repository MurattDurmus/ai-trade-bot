import streamlit as st
import pandas as pd
import yfinance as yf
from xgboost import XGBClassifier
import plotly.graph_objects as go
from datetime import datetime
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="AI Trade Terminali", page_icon="📈", layout="wide")
st.title("📈 Kripto & Makro AI Trade Terminali")

# --- HAFIZA VE CÜZDAN YÖNETİMİ ---
if 'nakit' not in st.session_state:
    st.session_state.nakit = 10000.0
if 'btc' not in st.session_state:
    st.session_state.btc = 0.0
if 'islem_gecmisi' not in st.session_state:
    st.session_state.islem_gecmisi = []
if 'son_alim_fiyati' not in st.session_state:
    st.session_state.son_alim_fiyati = 0.0  # Zarar kes (Stop-loss) takibi için


@st.cache_resource
def modeli_yukle():
    model = XGBClassifier()
    model.load_model("makro_xgboost_modeli.json")
    return model


model = modeli_yukle()
ozellik_kolonlari = ['BTC_SMA_20', 'BTC_Degisim_1_Gun', 'BTC_Degisim_7_Gun', 'DXY_Degisim', 'US10Y_Baski',
                     'VIX_Risk_Durumu', 'SP500_Degisim']


# --- VERİ ÇEKME ---
@st.cache_data(ttl=300)
def veri_getir():
    # 1. Makro Karar Verisi (Günlük)
    semboller = ['BTC-USD', 'DX-Y.NYB', '^TNX', '^VIX', '^GSPC']
    veri_sozlugu = {}
    for sembol in semboller:
        df_temp = yf.download(sembol, period='60d', interval='1d', progress=False)
        if isinstance(df_temp.columns, pd.MultiIndex):
            df_temp.columns = df_temp.columns.droplevel(1)
        veri_sozlugu[sembol] = df_temp['Close']

    df_makro = pd.DataFrame(veri_sozlugu).ffill()

    # HATA DÜZELTİLDİ: Sütun isimleri modelin beklediği formata çevrildi
    df_makro.columns = ['BTC', 'DXY', 'US10Y', 'VIX', 'SP500']

    df_makro['BTC_SMA_20'] = df_makro['BTC'].rolling(window=20).mean()
    df_makro['BTC_Degisim_1_Gun'] = df_makro['BTC'].pct_change(periods=1)
    df_makro['BTC_Degisim_7_Gun'] = df_makro['BTC'].pct_change(periods=7)
    df_makro['DXY_Degisim'] = df_makro['DXY'].pct_change(periods=1)
    df_makro['US10Y_Baski'] = df_makro['US10Y'] - df_makro['US10Y'].rolling(window=10).mean()
    df_makro['VIX_Risk_Durumu'] = (df_makro['VIX'] > 20).astype(int)
    df_makro['SP500_Degisim'] = df_makro['SP500'].pct_change(periods=1)

    # 2. Grafik Verisi (Saatlik)
    df_saatlik = yf.download('BTC-USD', period='7d', interval='1h', progress=False)
    if isinstance(df_saatlik.columns, pd.MultiIndex):
        df_saatlik.columns = df_saatlik.columns.droplevel(1)

    return df_makro.dropna().iloc[-1:], df_saatlik


# --- GRAFİK ÇİZİM FONKSİYONU ---
def grafik_ciz(df_saatlik):
    fig = go.Figure(data=[go.Candlestick(
        x=df_saatlik.index, open=df_saatlik['Open'], high=df_saatlik['High'],
        low=df_saatlik['Low'], close=df_saatlik['Close'], name='BTC/USD'
    )])

    # İşlem geçmişini grafiğe raptiyele
    for islem in st.session_state.islem_gecmisi:
        renk = '#00ff00' if islem['Tip'] == 'AL' else '#ff0000'
        sembol = 'triangle-up' if islem['Tip'] == 'AL' else 'triangle-down'
        konum = 'bottom center' if islem['Tip'] == 'AL' else 'top center'
        metin = f"{islem['Tip']}<br>${islem['Fiyat']:,.0f}<br>Tutar:${islem['Tutar']:,.0f}"

        fig.add_trace(go.Scatter(
            x=[islem['Tarih']], y=[islem['Fiyat']], mode='markers+text',
            marker=dict(symbol=sembol, size=16, color=renk, line=dict(width=2, color='white')),
            text=[metin], textposition=konum, name=islem['Tip'], showlegend=False
        ))

    fig.update_layout(
        template='plotly_dark', margin=dict(l=0, r=0, t=10, b=0),
        height=500, xaxis_rangeslider_visible=False, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)'
    )
    return fig


# --- ANA EKRAN YÜKLEMESİ ---
if st.button("🔄 Piyasayı Analiz Et (Verileri Güncelle)", use_container_width=True):
    with st.spinner('Makro veriler ve saatlik mumlar analiz ediliyor...'):
        son_durum_makro, df_saatlik = veri_getir()

        btc_fiyat = float(df_saatlik['Close'].iloc[-1])
        son_saat = df_saatlik.index[-1]

        # 1. MAKRO PANO
        st.subheader("🌍 Mahşerin 4 Atlısı (Makro Göstergeler)")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Bitcoin (Anlık)", f"${btc_fiyat:,.2f}")
        m2.metric("Korku Endeksi (VIX)", f"{float(son_durum_makro['VIX'].iloc[0]):.2f}")
        m3.metric("Dolar Endeksi (DXY)", f"{float(son_durum_makro['DXY'].iloc[0]):.2f}")
        m4.metric("10 Yıllık Tahvil", f"%{float(son_durum_makro['US10Y'].iloc[0]):.2f}")
        st.divider()

        # YAPAY ZEKA TAHMİNİ VE İŞLEM YÜRÜTME
        model_girdisi = son_durum_makro[ozellik_kolonlari]
        karar = model.predict(model_girdisi)[0]

        # ACİL DURUM SİGORTASI: ZARAR KES (STOP-LOSS %5)
        acil_satis_yapildi_mi = False
        if st.session_state.btc > 0 and st.session_state.son_alim_fiyati > 0:
            zarar_orani = (btc_fiyat - st.session_state.son_alim_fiyati) / st.session_state.son_alim_fiyati
            if zarar_orani <= -0.05:  # Fiyat aldığımız yere göre %5 düştüyse
                satilacak_tutar = st.session_state.btc * btc_fiyat
                st.session_state.nakit += satilacak_tutar
                st.session_state.btc = 0.0
                st.session_state.islem_gecmisi.append({
                    'Tarih': son_saat, 'Tip': 'SAT (STOP-LOSS)', 'Fiyat': btc_fiyat, 'Tutar': satilacak_tutar
                })
                st.error("🚨 STOP-LOSS PATLADI! Ani düşüş sebebiyle acil satış yapıldı.")
                acil_satis_yapildi_mi = True

        # NORMAL İŞLEMLER (Eğer acil satış olmadıysa)
        if not acil_satis_yapildi_mi:
            if karar == 1 and st.session_state.nakit > 10:
                alinacak_tutar = st.session_state.nakit
                st.session_state.btc += (alinacak_tutar / btc_fiyat)
                st.session_state.nakit = 0.0
                st.session_state.son_alim_fiyati = btc_fiyat  # Zarar kes için maliyeti kaydet
                st.session_state.islem_gecmisi.append({
                    'Tarih': son_saat, 'Tip': 'AL', 'Fiyat': btc_fiyat, 'Tutar': alinacak_tutar
                })
                st.toast('Yapay Zeka Alım Yaptı!', icon='✅')

            elif karar == 0 and st.session_state.btc > 0:
                satilacak_tutar = st.session_state.btc * btc_fiyat
                st.session_state.nakit += satilacak_tutar
                st.session_state.btc = 0.0
                st.session_state.son_alim_fiyati = 0.0
                st.session_state.islem_gecmisi.append({
                    'Tarih': son_saat, 'Tip': 'SAT', 'Fiyat': btc_fiyat, 'Tutar': satilacak_tutar
                })
                st.toast('Yapay Zeka Satış Yaptı!', icon='🔴')

        # --- ARAYÜZ YERLEŞİMİ ---
        col_grafik, col_hesap = st.columns([3, 1])

        with col_grafik:
            if karar == 1:
                st.success("💡 SİNYAL: AL (Makro veriler ve teknik trend yükselişi destekliyor)")
            else:
                st.warning("⏳ SİNYAL: BEKLE VEYA SAT (Risk yüksek veya trend zayıf. Nakitte kal.)")

            st.plotly_chart(grafik_ciz(df_saatlik), use_container_width=True)

        with col_hesap:
            st.subheader("💼 Sanal Portföy")
            # Değişken adının 'toplam_varlik' olduğundan emin olun
            toplam_varlik = st.session_state.nakit + (st.session_state.btc * btc_fiyat)
            kar_zarar = toplam_varlik - 10000.0

            st.metric("Toplam Varlık", f"${toplam_varlik:,.2f}", f"${kar_zarar:,.2f} Net Kar")
            st.write(f"**Nakit:** ${st.session_state.nakit:,.2f}")
            st.write(f"**Coin:** {st.session_state.btc:.6f} BTC")

            st.divider()
            st.subheader("📜 İşlem Defteri")