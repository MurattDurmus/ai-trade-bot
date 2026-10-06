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
    st.session_state.son_alim_fiyati = 0.0


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
    semboller = ['BTC-USD', 'DX-Y.NYB', '^TNX', '^VIX', '^GSPC']
    veri_sozlugu = {}
    for sembol in semboller:
        df_temp = yf.download(sembol, period='60d', interval='1d', progress=False)
        if isinstance(df_temp.columns, pd.MultiIndex):
            df_temp.columns = df_temp.columns.droplevel(1)
        veri_sozlugu[sembol] = df_temp['Close']

    df_makro = pd.DataFrame(veri_sozlugu).ffill()
    df_makro.columns = ['BTC', 'DXY', 'US10Y', 'VIX', 'SP500']

    df_makro['BTC_SMA_20'] = df_makro['BTC'].rolling(window=20).mean()
    df_makro['BTC_Degisim_1_Gun'] = df_makro['BTC'].pct_change(periods=1)
    df_makro['BTC_Degisim_7_Gun'] = df_makro['BTC'].pct_change(periods=7)
    df_makro['DXY_Degisim'] = df_makro['DXY'].pct_change(periods=1)
    df_makro['US10Y_Baski'] = df_makro['US10Y'] - df_makro['US10Y'].rolling(window=10).mean()
    df_makro['VIX_Risk_Durumu'] = (df_makro['VIX'] > 20).astype(int)
    df_makro['SP500_Degisim'] = df_makro['SP500'].pct_change(periods=1)

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

    for islem in st.session_state.islem_gecmisi:
        renk = '#00ff00' if 'AL' in islem['Tip'] else '#ff0000'
        sembol = 'triangle-up' if 'AL' in islem['Tip'] else 'triangle-down'
        konum = 'bottom center' if 'AL' in islem['Tip'] else 'top center'
        metin = f"{islem['Tip']}<br>${islem['Fiyat']:,.0f}<br>Tutar: ${islem['Tutar']:,.0f}"

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
    with st.spinner('Makro veriler, ağırlıklı skorlar ve saatlik mumlar analiz ediliyor...'):
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

        model_girdisi = son_durum_makro[ozellik_kolonlari]
        karar = model.predict(model_girdisi)[0]

        # ACİL DURUM SİGORTASI: ZARAR KES (STOP-LOSS %5) -> Bu güvenlik amaçlı tam çıkış yapar
        acil_satis_yapildi_mi = False
        if st.session_state.btc > 0 and st.session_state.son_alim_fiyati > 0:
            zarar_orani = (btc_fiyat - st.session_state.son_alim_fiyati) / st.session_state.son_alim_fiyati
            if zarar_orani <= -0.05:
                satilacak_tutar = st.session_state.btc * btc_fiyat
                st.session_state.nakit += satilacak_tutar
                st.session_state.btc = 0.0
                st.session_state.son_alim_fiyati = 0.0
                st.session_state.islem_gecmisi.append({
                    'Tarih': son_saat, 'Tip': 'SAT (STOP-LOSS)', 'Fiyat': btc_fiyat, 'Tutar': satilacak_tutar
                })
                st.error("🚨 STOP-LOSS PATLADI! Ani düşüş sebebiyle tüm pozisyon acilen satıldı.")
                acil_satis_yapildi_mi = True

        durum_mesaji = ""
        if not acil_satis_yapildi_mi:
            # A. KADEMELİ VE AĞIRLIKLI ALIM MANTIĞI
            if karar == 1 and st.session_state.nakit > 50:
                vix_val = float(son_durum_makro['VIX'].iloc[0])
                dxy_degisimi = float(son_durum_makro['DXY_Degisim'].iloc[0])
                us10y_baski = float(son_durum_makro['US10Y_Baski'].iloc[0])
                btc_7d = float(son_durum_makro['BTC_Degisim_7_Gun'].iloc[0])

                puan = 0
                if vix_val < 18: puan += 1
                if dxy_degisimi < 0: puan += 1
                if us10y_baski <= 0: puan += 1
                if btc_7d > 0.02: puan += 1

                if puan >= 4:
                    carpan = 2.0
                    durum_mesaji = "🚀 Aşırı Olumlu (X2 Güçlü Giriş)"
                elif puan == 3:
                    carpan = 1.5
                    durum_mesaji = "📈 Oldukça Olumlu (1.5X Giriş)"
                elif puan == 2:
                    carpan = 1.0
                    durum_mesaji = "⚖️ Dengeli / Normal (1X Giriş)"
                else:
                    carpan = 0.5
                    durum_mesaji = "🛡️ Temkinli / Zayıf (0.5X Küçük Giriş)"

                temel_butce = 1000.0
                hedef_tutar = temel_butce * carpan
                alinacak_tutar = min(hedef_tutar, st.session_state.nakit)

                if alinacak_tutar > 10:
                    st.session_state.btc += (alinacak_tutar / btc_fiyat)
                    st.session_state.nakit -= alinacak_tutar
                    st.session_state.son_alim_fiyati = btc_fiyat
                    st.session_state.islem_gecmisi.append({
                        'Tarih': son_saat, 'Tip': f'AL ({durum_mesaji})', 'Fiyat': btc_fiyat, 'Tutar': alinacak_tutar
                    })
                    st.toast(f'Akıllı Alım Yapıldı! Tutar: ${alinacak_tutar:,.0f} ({durum_mesaji})', icon='✅')

            # B. KADEMELİ SATIŞ MANTIĞI
            elif karar == 0 and st.session_state.btc > 0.0001:
                # Elimizdeki toplam BTC'nin %50'sini kademeli olarak satıyoruz
                satilacak_btc_miktari = st.session_state.btc * 0.50
                satilacak_tutar = satilacak_btc_miktari * btc_fiyat

                st.session_state.nakit += satilacak_tutar
                st.session_state.btc -= satilacak_btc_miktari

                # Eğer kalan BTC miktarı çok küçük kaldıysa tamamen sıfırla
                if st.session_state.btc < 0.0001:
                    fazla_tutar = st.session_state.btc * btc_fiyat
                    st.session_state.nakit += fazla_tutar
                    satilacak_tutar += fazla_tutar
                    st.session_state.btc = 0.0
                    st.session_state.son_alim_fiyati = 0.0

                st.session_state.islem_gecmisi.append({
                    'Tarih': son_saat, 'Tip': 'SAT (Kademeli %50)', 'Fiyat': btc_fiyat, 'Tutar': satilacak_tutar
                })
                st.toast(f'Kademeli Satış Yapıldı! Tutar: ${satilacak_tutar:,.0f}', icon='🔴')

        # --- ARAYÜZ YERLEŞİMİ ---
        col_grafik, col_hesap = st.columns([3, 1])

        with col_grafik:
            if karar == 1:
                st.success(f"💡 SİNYAL: AL | Piyasa Durumu: {durum_mesaji}")
            else:
                st.warning("⏳ SİNYAL: BEKLE VEYA KADEMELİ SATIŞ AKTİF")

            st.plotly_chart(grafik_ciz(df_saatlik), use_container_width=True)

        with col_hesap:
            st.subheader("💼 Sanal Portföy")
            toplam_varlik = st.session_state.nakit + (st.session_state.btc * btc_fiyat)
            kar_zarar = toplam_varlik - 10000.0

            st.metric("Toplam Varlık", f"${toplam_varlik:,.2f}", f"${kar_zarar:,.2f} Net Kar")
            st.write(f"**Nakit:** ${st.session_state.nakit:,.2f}")
            st.write(f"**Coin:** {st.session_state.btc:.6f} BTC")

            st.divider()
            st.subheader("📜 İşlem Defteri")
            if len(st.session_state.islem_gecmisi) == 0:
                st.info("Henüz işlem yapılmadı.")
            else:
                for islem in reversed(st.session_state.islem_gecmisi):
                    renk = "🟢" if "AL" in islem['Tip'] else "🔴"
                    zaman = islem['Tarih'].strftime("%d %b %H:%M")
                    with st.expander(f"{renk} {islem['Tip']} - {zaman}"):
                        st.write(f"**Birim Fiyat:** ${islem['Fiyat']:,.2f}")
                        st.write(f"**İşlem Tutarı:** ${islem['Tutar']:,.2f}")

else:
    st.info("👆 Başlamak için 'Piyasayı Analiz Et' butonuna tıklayın.")

time.sleep(3600)
st.rerun()