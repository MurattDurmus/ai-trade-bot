import streamlit as st
import pandas as pd
import yfinance as yf
from xgboost import XGBClassifier
import plotly.graph_objects as go
from datetime import datetime
import requests
import base64
import json
import time

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="AI Trade Terminali", page_icon="📈", layout="wide")
st.title("📈 Kripto & 10'lu Makro AI Trade Terminali")


# --- GİTHUB ÜZERİNDEN VERİ OKUMA VE KAYDETME ---
def github_veri_oku():
    try:
        token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]
        url = f"https://api.github.com/repos/{repo}/contents/cuzdan_verileri.json"
        headers = {"Authorization": f"Bearer {token}"}

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            content_json = response.json()
            file_content_bytes = base64.b64decode(content_json["content"])
            veri = json.loads(file_content_bytes.decode('utf-8'))
            for islem in veri.get("islem_gecmisi", []):
                if isinstance(islem['Tarih'], str):
                    islem['Tarih'] = datetime.fromisoformat(islem['Tarih'])
            return veri["nakit"], veri["btc"], veri["islem_gecmisi"], veri["son_alim_fiyati"]
    except Exception:
        pass
    return 10000.0, 0.0, [], 0.0


def github_veri_kaydet(nakit, btc, islem_gecmisi, son_alim_fiyati):
    try:
        token = st.secrets["GITHUB_TOKEN"]
        repo = st.secrets["GITHUB_REPO"]
        url = f"https://api.github.com/repos/{repo}/contents/cuzdan_verileri.json"
        headers = {"Authorization": f"Bearer {token}"}

        sha = None
        resp_get = requests.get(url, headers=headers)
        if resp_get.status_code == 200:
            sha = resp_get.json().get("sha")

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

        json_str = json.dumps(veri, ensure_ascii=False, indent=4)
        encoded_content = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')

        data = {
            "message": "10'lu Makro Bot: Otomatik cüzdan güncellemesi",
            "content": encoded_content
        }
        if sha:
            data["sha"] = sha

        requests.put(url, headers=headers, json=data)
    except Exception as e:
        st.warning(f"GitHub kayıt hatası: {e}")


# Verileri GitHub'dan yükle
nakit, btc, islem_gecmisi, son_alim_fiyati = github_veri_oku()


@st.cache_resource
def modeli_yukle():
    model = XGBClassifier()
    model.load_model("makro_xgboost_modeli.json")
    return model


model = modeli_yukle()

# Yeni 10'lu Makro Özellik Seti
ozellik_kolonlari = [
    'BTC_SMA_20', 'BTC_Degisim_1G', 'BTC_Degisim_7G',
    'DXY_Degisim', 'US10Y_Baski', 'US5Y_Degisim',
    'VIX_Seviye', 'SP500_Degisim', 'Nasdaq_Degisim',
    'Altin_Degisim', 'Petrol_Degisim', 'Bakir_Degisim'
]


# --- VERİ ÇEKME VE ÖZELLİK MÜHENDİSLİĞİ ---
@st.cache_data(ttl=300)
def veri_getir():
    semboller = {
        'BTC': 'BTC-USD',
        'DXY': 'DX-Y.NYB',
        'US10Y': '^TNX',
        'US5Y': '^FVX',
        'VIX': '^VIX',
        'SP500': '^GSPC',
        'Nasdaq': '^NDX',
        'Altin': 'GC=F',
        'Petrol': 'CL=F',
        'Bakir': 'HG=F'
    }

    veri_sozlugu = {}
    for isim, sembol in semboller.items():
        df_temp = yf.download(sembol, period='100d', interval='1d', progress=False)
        if isinstance(df_temp.columns, pd.MultiIndex):
            df_temp.columns = df_temp.columns.droplevel(1)
        veri_sozlugu[isim] = df_temp['Close']

    df = pd.DataFrame(veri_sozlugu).ffill()

    df['BTC_SMA_20'] = df['BTC'].rolling(window=20).mean()
    df['BTC_Degisim_1G'] = df['BTC'].pct_change(periods=1)
    df['BTC_Degisim_7G'] = df['BTC'].pct_change(periods=7)

    df['DXY_Degisim'] = df['DXY'].pct_change(periods=1)
    df['US10Y_Baski'] = df['US10Y'] - df['US10Y'].rolling(window=10).mean()
    df['US5Y_Degisim'] = df['US5Y'].pct_change(periods=1)
    df['VIX_Seviye'] = (df['VIX'] > 20).astype(int)
    df['SP500_Degisim'] = df['SP500'].pct_change(periods=1)
    df['Nasdaq_Degisim'] = df['Nasdaq'].pct_change(periods=1)
    df['Altin_Degisim'] = df['Altin'].pct_change(periods=1)
    df['Petrol_Degisim'] = df['Petrol'].pct_change(periods=1)
    df['Bakir_Degisim'] = df['Bakir'].pct_change(periods=1)

    df_saatlik = yf.download('BTC-USD', period='7d', interval='1h', progress=False)
    if isinstance(df_saatlik.columns, pd.MultiIndex):
        df_saatlik.columns = df_saatlik.columns.droplevel(1)

    return df.dropna().iloc[-1:], df_saatlik


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
if st.button("🔄 Piyasayı Analiz Et (10 Makro Veri)", use_container_width=True):
    with st.spinner('10 küresel makro gösterge ve yapay zeka modeli analiz ediliyor...'):
        son_durum_makro, df_saatlik = veri_getir()

        btc_fiyat = float(df_saatlik['Close'].iloc[-1])
        son_saat = df_saatlik.index[-1]

        # 1. MAKRO PANO (Önemli Göstergeler)
        st.subheader("🌍 10'lu Küresel Makro Pano")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Bitcoin", f"${btc_fiyat:,.2f}")
        m2.metric("Nasdaq", f"{float(son_durum_makro['Nasdaq'].iloc[0]):,.1f}")
        m3.metric("DXY", f"{float(son_durum_makro['DXY'].iloc[0]):.2f}")
        m4.metric("10Y Tahvil", f"%{float(son_durum_makro['US10Y'].iloc[0]):.2f}")
        m5.metric("VIX", f"{float(son_durum_makro['VIX'].iloc[0]):.2f}")
        st.divider()

        model_girdisi = son_durum_makro[ozellik_kolonlari]
        karar = model.predict(model_girdisi)[0]

        # ACİL DURUM SİGORTASI: ZARAR KES (%5)
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
                st.error("🚨 STOP-LOSS PATLADI! Acil tam çıkış yapıldı.")
                acil_satis_yapildi_mi = True

        durum_mesaji = ""
        if not acil_satis_yapildi_mi:
            # A. KADEMELİ VE AĞIRLIKLI ALIM MANTIĞI
            if karar == 1 and nakit > 50:
                vix_val = float(son_durum_makro['VIX'].iloc[0])
                dxy_degisimi = float(son_durum_makro['DXY_Degisim'].iloc[0])
                us10y_baski = float(son_durum_makro['US10Y_Baski'].iloc[0])
                nasdaq_deg = float(son_durum_makro['Nasdaq_Degisim'].iloc[0])
                btc_7d = float(son_durum_makro['BTC_Degisim_7G'].iloc[0])

                puan = 0
                if vix_val < 20: puan += 1
                if dxy_degisimi < 0: puan += 1
                if us10y_baski <= 0: puan += 1
                if nasdaq_deg > 0: puan += 1
                if btc_7d > 0.02: puan += 1

                if puan >= 4:
                    carpan = 2.0
                    durum_mesaji = "🚀 Aşırı Olumlu (X2 Güçlü Giriş)"
                elif puan == 3:
                    carpan = 1.5
                    durum_mesaji = "📈 Oldukça Olumlu (1.5X Giriş)"
                elif puan >= 2:
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
                    st.toast(f'10 Makro Destekli Alım! Tutar: ${alinacak_tutar:,.0f}', icon='✅')

            # B. KADEMELİ SATIŞ MANTIĞI (%50)
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

        # Güncel cüzdanı GitHub'a kaydet
        github_veri_kaydet(nakit, btc, islem_gecmisi, son_alim_fiyati)

        # --- ARAYÜZ YERLEŞİMİ ---
        col_grafik, col_hesap = st.columns([3, 1])

        with col_grafik:
            if karar == 1:
                st.success(f"💡 SİNYAL: AL | Piyasa Skoru: {durum_mesaji}")
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
    col_grafik, col_hesap = st.columns([3, 1])
    with col_hesap:
        st.subheader("💼 Sanal Portföy (GitHub Hafızası)")
        try:
            df_saatlik_anlik = yf.download('BTC-USD', period='2d', interval='1h', progress=False)
            if isinstance(df_saatlik_anlik.columns, pd.MultiIndex):
                df_saatlik_anlik.columns = df_saatlik_anlik.columns.droplevel(1)
            anlik_fiyat = float(df_saatlik_anlik['Close'].iloc[-1])
        except:
            anlik_fiyat = 85000.0

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
            "👆 10'lu makro bot aktif! Piyasayı analiz etmek ve işlemleri güncellemek için yukarıdaki butona tıklayın.")

time.sleep(3600)
st.rerun()