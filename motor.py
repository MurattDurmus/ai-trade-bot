import os
import pandas as pd
import yfinance as yf
from xgboost import XGBClassifier
from datetime import datetime
import requests
import base64
import json

print("🤖 [MOTOR BAŞLADI] 7/24 AI Trade Botu uyanıyor...")

# 1. GitHub Ayarlarını Ortam Değişkenlerinden (Secrets) Al
GITHUB_TOKEN = os.environ.get("GH_TOKEN")
GITHUB_REPO = os.environ.get("GH_REPO")

if not GITHUB_TOKEN or not GITHUB_REPO:
    print("❌ HATA: GitHub Token veya Repo bilgisi eksik! Sistem durduruluyor.")
    exit()


# 2. GİTHUB ÜZERİNDEN VERİ OKUMA VE KAYDETME
def github_veri_oku():
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/cuzdan_verileri.json"
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}

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
        print(f"⚠️ Okuma Hatası: {e}")
    return 10000.0, 0.0, [], 0.0


def github_veri_kaydet(nakit, btc, islem_gecmisi, son_alim_fiyati):
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/cuzdan_verileri.json"
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}

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
            "message": "🤖 Otomatik Motor: Cüzdan Güncellemesi (Saatlik Tetiklenme)",
            "content": encoded_content
        }
        if sha:
            data["sha"] = sha

        res = requests.put(url, headers=headers, json=data)
        if res.status_code in [200, 201]:
            print("✅ Güncel cüzdan başarıyla GitHub'a kaydedildi.")
        else:
            print("❌ Kayıt başarısız oldu!")
    except Exception as e:
        print(f"❌ GitHub kayıt hatası: {e}")


nakit, btc, islem_gecmisi, son_alim_fiyati = github_veri_oku()
print(f"💰 Cüzdan Durumu: ${nakit:,.2f} Nakit | {btc:.6f} BTC")

# 3. VERİ ÇEKME VE ÖZELLİK MÜHENDİSLİĞİ
print("📊 Makro veriler Yahoo Finance üzerinden çekiliyor...")
semboller = {
    'BTC': 'BTC-USD', 'DXY': 'DX-Y.NYB', 'US10Y': '^TNX', 'US5Y': '^FVX',
    'VIX': '^VIX', 'SP500': '^GSPC', 'Nasdaq': '^NDX', 'Altin': 'GC=F',
    'Petrol': 'CL=F', 'Bakir': 'HG=F'
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

df_saatlik = yf.download('BTC-USD', period='2d', interval='1h', progress=False)
if isinstance(df_saatlik.columns, pd.MultiIndex):
    df_saatlik.columns = df_saatlik.columns.droplevel(1)

son_durum_makro = df.dropna().iloc[-1:]
btc_fiyat = float(df_saatlik['Close'].iloc[-1])
son_saat = df_saatlik.index[-1]
print(f"📈 Anlık BTC Fiyatı: ${btc_fiyat:,.2f}")

# 4. MODEL TAHMİNİ
print("🧠 XGBoost Modeli Yükleniyor ve Karar Veriliyor...")
model = XGBClassifier()
model.load_model("makro_xgboost_modeli.json")
ozellik_kolonlari = [
    'BTC_SMA_20', 'BTC_Degisim_1G', 'BTC_Degisim_7G', 'DXY_Degisim', 'US10Y_Baski',
    'US5Y_Degisim', 'VIX_Seviye', 'SP500_Degisim', 'Nasdaq_Degisim', 'Altin_Degisim',
    'Petrol_Degisim', 'Bakir_Degisim'
]
karar = model.predict(son_durum_makro[ozellik_kolonlari])[0]
print(f"💡 Yapay Zeka Sinyali: {'AL (1)' if karar == 1 else 'BEKLE / SAT (0)'}")

# 5. İŞLEM MANTIĞI
islem_yapildi_mi = False
if btc > 0 and son_alim_fiyati > 0:
    zarar_orani = (btc_fiyat - son_alim_fiyati) / son_alim_fiyati
    if zarar_orani <= -0.05:
        satilacak_tutar = btc * btc_fiyat
        nakit += satilacak_tutar
        btc = 0.0
        son_alim_fiyati = 0.0
        islem_gecmisi.append(
            {'Tarih': son_saat, 'Tip': 'SAT (STOP-LOSS)', 'Fiyat': btc_fiyat, 'Tutar': satilacak_tutar})
        print("🚨 STOP-LOSS PATLADI! Acil satış yapıldı.")
        islem_yapildi_mi = True

if not islem_yapildi_mi:
    if karar == 1 and nakit > 50:
        puan = sum([
            float(son_durum_makro['VIX'].iloc[0]) < 20,
            float(son_durum_makro['DXY_Degisim'].iloc[0]) < 0,
            float(son_durum_makro['US10Y_Baski'].iloc[0]) <= 0,
            float(son_durum_makro['Nasdaq_Degisim'].iloc[0]) > 0,
            float(son_durum_makro['BTC_Degisim_7G'].iloc[0]) > 0.02
        ])

        carpan = 2.0 if puan >= 4 else (1.5 if puan == 3 else (1.0 if puan == 2 else 0.5))
        hedef_tutar = min(1000.0 * carpan, nakit)

        if hedef_tutar > 10:
            btc += (hedef_tutar / btc_fiyat)
            nakit -= hedef_tutar
            son_alim_fiyati = btc_fiyat
            islem_gecmisi.append(
                {'Tarih': son_saat, 'Tip': f'AL ({puan} Puan)', 'Fiyat': btc_fiyat, 'Tutar': hedef_tutar})
            print(f"✅ ALIM YAPILDI: ${hedef_tutar:,.2f}")
            islem_yapildi_mi = True

    elif karar == 0 and btc > 0.0001:
        satilacak_btc_miktari = btc * 0.50
        satilacak_tutar = satilacak_btc_miktari * btc_fiyat
        nakit += satilacak_tutar
        btc -= satilacak_btc_miktari

        if btc < 0.0001:
            satilacak_tutar += btc * btc_fiyat
            nakit += btc * btc_fiyat
            btc = 0.0
            son_alim_fiyati = 0.0

        islem_gecmisi.append(
            {'Tarih': son_saat, 'Tip': 'SAT (Kademeli %50)', 'Fiyat': btc_fiyat, 'Tutar': satilacak_tutar})
        print(f"🔴 SATIŞ YAPILDI: ${satilacak_tutar:,.2f}")
        islem_yapildi_mi = True

if islem_yapildi_mi:
    github_veri_kaydet(nakit, btc, islem_gecmisi, son_alim_fiyati)
else:
    print("⏳ Mevcut piyasa şartlarında yeni işlem yapılmadı. Beklemede kalın.")

print("🏁 [MOTOR DURDU] Görev tamamlandı, bir sonraki saate kadar uykuda.")