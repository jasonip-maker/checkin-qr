import datetime
import io
import qrcode
import streamlit as st

# 設定手機版面置中與簡潔外觀
st.set_page_config(page_title="Check-in QR", page_icon="📱", layout="centered")

# 隱藏 Streamlit 預設選單與頁尾，讓手機畫面更像原生 App
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        .block-container {padding-top: 2rem; text-align: center;}
    </style>
""", unsafe_allow_html=True)

# 1. 取得香港時間 (UTC+8)，並減去 15 分鐘對齊 :15 進位規則
hkt = datetime.timezone(datetime.timedelta(hours=8), name="HKT")
now_hkt = datetime.datetime.now(hkt)
effective_time = now_hkt - datetime.timedelta(minutes=15)
current_hour = effective_time.hour

# 2. 基準資料 (10:15 為 Hour 10 -> 2C3334102459)
base_bytes = [0x2C, 0x33, 0x34, 0x10, 0x24, 0x59]
changing_indices = [1, 2, 3, 5]
hour_delta = current_hour - 10

# 3. 計算當前有效小時的動態位元組
computed_bytes = list(base_bytes)
for idx in changing_indices:
    computed_bytes[idx] = (base_bytes[idx] + hour_delta) % 256

# 4. 組合 URI 訊息
hex_payload = "".join(f"{b:02X}" for b in computed_bytes)
qr_message = f"checkin://q?91/@{hex_payload}"

# 5. 直接在 Python 記憶體生成高畫質 PNG 圖片 (完全不依賴外部 API)
qr = qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_L,
    box_size=10,
    border=2,
)
qr.add_data(qr_message)
qr.make(fit=True)
img = qr.make_image(fill_color="black", back_color="white")

buf = io.BytesIO()
img.save(buf, format="PNG")

# 6. 渲染手機網頁畫面
st.subheader(f"{now_hkt.strftime('%Y-%m-%d %H:%M:%S')} HKT")
st.caption(f"有效時段：Hour {current_hour}")

# 置中顯示 QR Code 圖片
col1, col2, col3 = st.columns([1, 4, 1])
with col2:
    st.image(buf.getvalue(), use_container_width=True)

st.code(qr_message, language=None)

# 顯示可直接點擊開啟的 checkin:// 超連結
st.markdown(f'<a href="{qr_message}" class="uri-link">🔗 {qr_message}</a>', unsafe_allow_html=True)

# 提供一鍵刷新按鈕，方便跨過 :15 分時手動更新
if st.button("🔄 重新整理時間", use_container_width=True):
    st.rerun()
