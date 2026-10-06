import streamlit as st
import pandas as pd
import yfinance as yf
from xgboost import XGBClassifier
import plotly.graph_objects as go
from datetime import datetime
import json
import os
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="AI Trade Terminali", page_icon="📈", layout="wide")
st.title("📈 Kripto & Makro AI Trade Terminali")

# --- KALICI CÜZDAN DOSYA YÖNETİMİ (JSON) ---
DOSYA_ADI = "cuzdan_verileri.json"


def verileri_yukle():
    if os.path.exists(DOSYA_ADI):
        try:
            with open(DOSYA_ADI, "r", encoding="utf-8") as f:
                veri = json.load(f)
                # Tarih alanlarını string'den datetime'a çevir
                for islem in veri.get("islem_gecmisi", []):
                    if isinstance(islem['Tarih'], str):
                        islem['Tarih'] = datetime.fromisoformat(islem['Tarih'])
                return veri
        except:
            pass
    # Dosya yoksa varsayılan başlangıç değerleri
    return {
        "nakit": 10000.0,
        "btc": 0.0,
        "islem_gecmisi": [],
        "son_alim_fiyati": 0.0
    }


def verileri_kaydet(nakit, btc, islem_gecmisi, son_alim_fiyati):
    # Tarih nesnelerini JSON için string'e çevir
    gecmis_kopya = []
    for islem in islem_gecmisi:
        islem_k = islem.copy()
        if isinstance(islem_k['Tarih'], datetime):
            islem_k['Tarih'] = islem_k['Tarih'].isoformat()
        gecmis_kopya.append(islem_k)

    veri = {
        "nakit": nakit,
        "btc": btc,
        "islem_gecmisi": gecmis_kopya,
        "son_alim_fiyati": son_alim_fiyati
    }
    with open(DOSYA_ADI, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=4)


# Verileri yükle
cuzdan = verileri_yukle()
nakit = cuzdan["nakit"]
btc = cuzdan["btc"]
islem_gecmisi = cuzdan["islem_gecmisi"]
son_alim_fiyati = cuzdan["son_alim_fiyati"]


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
def grafik_ciz(df_saatlik, aktif_gecmis):
    fig = go.Figure(data=[go.Candlestick(
        x=df_saatlik.index, open=df_saatlik['Open'], high=df_saatlik['High'],
        low=df_saatlik['Low'], close=df_saatlik['Close'], name='BTC/USD'
    )])

    for islem in aktif_gecmis:
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

        # ACİL DURUM SİGORTASI: ZARAR KES (STOP-LOSS %5)
        acil_satis_yapildi_mi = False
        if btc > 0 and son_alim_fiyati > 0:
            zarar_orani = (btc_fiyat - son_alim_fiyati) / son_alim_fiyati
            if zarar_orani <= -0.05:
                satilacak_tutar = btc * btc_fiyat
                nakit += satilacak_tutar
                btc = 0.0
                son_alim_fiyati = 0.0
                islem_gecmisi.append({
                    'Tarih': son_saat, 'Tip': 'SAT (STOP-LOSS)', 'Fiyat': btc_fiyat, 'Tutar': satilacak_tutar
                })
                st.error("🚨 STOP-LOSS PATLADI! Ani düşüş sebebiyle tüm pozisyon acilen satıldı.")
                acil_satis_yapildi_mi = True

        durum_mesaji = ""
        if not acil_satis_yapildi_mi:
            # A. KADEMELİ VE AĞIRLIKLI ALIM MANTIĞI
            if karar == 1 and nakit > 50:
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
                alinacak_tutar = min(hedef_tutar, nakit)

                if alinacak_tutar > 10:
                    btc += (alinacak_tutar / btc_fiyat)
                    nakit -= alinacak_tutar
                    son_alim_fiyati = btc_fiyat
                    islem_gecmisi.append({
                        'Tarih': son_saat, 'Tip': f'AL ({durum_mesaji})', 'Fiyat': btc_fiyat, 'Tutar': alinacak_tutar
                    })
                    st.toast(f'Akıllı Alım Yapıldı! Tutar: ${alinacak_tutar:,.0f} ({durum_mesaji})', icon='✅')

            # B. KADEMELİ SATIŞ MANTIĞI
            elif karar == 0 and btc > 0.0001:
                satilacak_btc_miktari = btc * 0.50
                satilacak_tutar = satilacak_btc_miktari * btc_fiyat

                nakit += satilacak_tutar
                btc -= satilacak_btc_miktari

                if btc < 0.0001:
                    fazla_tutar = btc * btc_fiyat
                    nakit += fazla_tutar
                    satilacak_tutar += fazla_tutar
                    btc = 0.0
                    son_alim_fiyati = 0.0

                islem_gecmisi.append({
                    'Tarih': son_saat, 'Tip': 'SAT (Kademeli %50)', 'Fiyat': btc_fiyat, 'Tutar': satilacak_tutar
                })
                st.toast(f'Kademeli Satış Yapıldı! Tutar: ${satilacak_tutar:,.0f}', icon='🔴')

        # Güncel cüzdan durumunu diske kaydet
        verileri_kaydet(nakit, btc, islem_gecmisi, son_alim_fiyati)

        # --- ARAYÜZ YERLEŞİMİ ---
        col_grafik, col_hesap = st.columns([3, 1])

        with col_grafik:
            if karar == 1:
                st.success(f"💡 SİNYAL: AL | Piyasa Durumu: {durum_mesaji}")
            else:
                st.warning("⏳ SİNYAL: BEKLE VEYA KADEMELİ SATIŞ AKTİF")

            st.plotly_chart(grafik_ciz(df_saatlik, islem_gecmisi), use_container_width=True)

        with col_hesap:
            st.subheader("💼 Sanal Portföy")
            toplam_varlik = nakit + (btc * btc_fiyat)
            kar_zarar = toplam_varlik - 10000.0

            st.metric("Toplam Varlık", f"${toplam_varlik:,.2f}", f"${kar_zarar:,.2f} Net Kar")
            st.write(f"**Nakit:** ${nakit:,.2f}")
            st.write(f"**Coin:** {btc:.6f} BTC")

            st.divider()
            st.subheader("📜 İşlem Defteri")
            if len(islem_gecmisi) == 0:
                st.info("Henüz işlem yapılmadı.")
            else:
                for islem in reversed(islem_gecmisi):
                    renk = "🟢" if "AL" in islem['Tip'] else "🔴"
                    zaman = islem['Tarih'].strftime("%d %b %H:%M")
                    with st.expander(f"{renk} {islem['Tip']} - {zaman}"):
                        st.write(f"**Birim Fiyat:** ${islem['Fiyat']:,.2f}")
                        st.write(f"**İşlem Tutarı:** ${islem['Tutar']:,.2f}")

else:
    # Butona basılmadığı anlarda bile diske kaydedilmiş cüzdanı ve son durumu göster
    col_grafik, col_hesap = st.columns([3, 1])
    with col_hesap:
        st.subheader("💼 Sanal Portföy (Kayıtlı)")
        # Anlık fiyatı görmek için son veriyi çekelim
        try:
            df_saatlik_anlik = yf.download('BTC-USD', period='2d', interval='1h', progress=False)
            if isinstance(df_saatlik_anlik.columns, pd.MultiIndex):
                df_saatlik_anlik.columns = df_saatlik_anlik.columns.droplevel(1)
            anlik_fiyat = float(
                df_saclik_close if 'df_saclik_close' in locals() else df_saatlik_anlik['Close'].iloc[-1])
        except:
            anlik_fiyat = 85000.0  # Hata durumunda yaklaşık değer

        toplam_varlik = nakit + (btc * anlik_fiyat)
        kar_zarar = toplam_varlik - 10000.0

        st.metric("Toplam Varlık", f"${toplam_varlik:,.2f}", f"${kar_zarar:,.2f} Net Kar")
        st.write(f"**Nakit:** ${nakit:,.2f}")
        st.write(f"**Coin:** {btc:.6f} BTC")

        st.divider()
        st.subheader("📜 İşlem Defteri")
        if len(islem_gecmisi) == 0:
            st.info("Henüz işlem yapılmadı.")
        else:
            for islem in reversed(islem_gecmisi):
                renk = "🟢" if "AL" in islem['Tip'] else "🔴"
                zaman = islem['Tarih'].strftime("%d %b %H:%M")
                with st.expander(f"{renk} {islem['Tip']} - {zaman}"):
                    st.write(f"**Birim Fiyat:** ${islem['Fiyat']:,.2f}")
                    st.write(f"**İşlem Tutarı:** ${islem['Tutar']:,.2f}")

    with col_grafik:
        st.info(
            "👆 Botun hafızası diske bağlandı! Piyasayı analiz etmek ve işlemleri güncellemek için yukarıdaki butona tıklayın.")

time.sleep(3600)
st.rerun()