import datetime
import io
import qrcode
from PIL import Image
import streamlit as st

st.set_page_config(page_title="Check-in QR", page_icon="📱", layout="centered")

# 初始化 Session State
if "hour_offset" not in st.session_state:
    st.session_state.hour_offset = 0
if "init_wakeup_min" not in st.session_state:
    st.session_state.init_wakeup_min = 15    # 1年多前設定的初始喚醒分鐘 (:15)
if "sw_wakeup_fast_min" not in st.session_state:
    st.session_state.sw_wakeup_fast_min = 20 # 軟體喚醒累計走快 (20分鐘 -> :55觸發)
if "hw_rtc_fast_sec" not in st.session_state:
    st.session_state.hw_rtc_fast_sec = 690   # 硬體晶振累計走快 (690秒 = 11分30秒 -> 讀出:06:30)
if "qr_version" not in st.session_state:
    st.session_state.qr_version = 0
if "qr_ec" not in st.session_state:
    st.session_state.qr_ec = "L"
if "qr_box_size" not in st.session_state:
    st.session_state.qr_box_size = 24
if "qr_mask" not in st.session_state:
    st.session_state.qr_mask = -1
if "qr_transpose" not in st.session_state:
    st.session_state.qr_transpose = True

EC_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

hkt = datetime.timezone(datetime.timedelta(hours=8), name="HKT")
now_hkt = datetime.datetime.now(hkt)

# 1. 計算實際喚醒觸發時刻 (真實時間何時換碼)
# 原本 :15 喚醒，軟體快了 20 分鐘 -> 提早到真實時間 :55 觸發 (即基準時間往前推 5 分鐘進位)
wakeup_minute = (st.session_state.init_wakeup_min - st.session_state.sw_wakeup_fast_min) % 60
adjusted_now = now_hkt + datetime.timedelta(hours=st.session_state.hour_offset)

# 找出當前生效的「真實世界喚醒整點時刻」
actual_wakeup_time = adjusted_now.replace(minute=wakeup_minute, second=0, microsecond=0)
if adjusted_now.minute < wakeup_minute:
    actual_wakeup_time -= datetime.timedelta(hours=1)

# 2. 加上硬體晶振偏移，得出喚醒當刻裝置內部 Read_RTC_Time() 讀到的時間
# 真實 :55:00 + 硬體晶振走快 690 秒 (11分30秒) = 下個小時的 :06:30
days_drift = (actual_wakeup_time.date() - datetime.date(2026, 11, 12)).days
effective_time = actual_wakeup_time + datetime.timedelta(seconds=st.session_state.hw_rtc_fast_sec - days_drift)

Y = effective_time.year % 100
M = effective_time.month
D = effective_time.day
H = effective_time.hour
m = effective_time.minute
s = effective_time.second

# 正向編碼計算 (6 Bytes)
b0 = (Y + D + m) % 256
b1 = (M + H + s) % 256
b2 = (D + H + s) % 256
b3 = (H + m) % 256
b4 = (m + s) % 256
b5 = (Y + M + D + H + s) % 256

# 逆向解碼還原驗證
dec_Y = -2*b0 - 3*b1 - b2 + b3 + b4 + 3*b5
dec_M =  2*b0 + 3*b1      - b3 - b4 - 2*b5
dec_D =  2*b0 + 2*b1 + b2 - b3 - b4 - 2*b5
dec_H =   -b0 -   b1      + b3      +   b5
dec_m =    b0 +   b1                -   b5
dec_s =   -b0 -   b1           + b4 +   b5
decoded_src = (dec_Y, dec_M, dec_D, dec_H, dec_m, dec_s)

hex_payload = f"{b0:02X}{b1:02X}{b2:02X}{b3:02X}{b4:02X}{b5:02X}"
qr_message = f"checkin://q?91/@{hex_payload}"

# 產生 QR Code（Auto 模式下 Hour 15 自動切換為 Mask 1，其餘交給 Auto）
target_version = None if st.session_state.qr_version == 0 else st.session_state.qr_version
if st.session_state.qr_mask == -1:
    target_mask = 1 if effective_time.hour == 15 else None
else:
    target_mask = st.session_state.qr_mask

try:
    qr = qrcode.QRCode(
        version=target_version,
        error_correction=EC_MAP[st.session_state.qr_ec],
        box_size=st.session_state.qr_box_size,
        border=2,
        mask_pattern=target_mask,
    )
    qr.add_data(qr_message)
    qr.make(fit=(target_version is None))
except qrcode.exceptions.DataOverflowError:
    qr = qrcode.QRCode(
        version=None,
        error_correction=EC_MAP[st.session_state.qr_ec],
        box_size=st.session_state.qr_box_size,
        border=2,
        mask_pattern=target_mask,
    )
    qr.add_data(qr_message)
    qr.make(fit=True)

img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

# 套用對角線翻轉（沿左上至右下主對角線鏡像 / Transpose）
if st.session_state.qr_transpose:
    img = img.transpose(Image.Transpose.TRANSPOSE)

buf = io.BytesIO()
img.save(buf, format="PNG")

st.subheader(f"{now_hkt.strftime('%Y-%m-%d %H:%M:%S')} HKT")
offset_label = f" (微調: {st.session_state.hour_offset:+d})" if st.session_state.hour_offset != 0 else ""
ver_label = "Auto" if st.session_state.qr_version == 0 else f"V{qr.version}"
mask_label = f"Auto(M{qr.mask_pattern})" if st.session_state.qr_mask == -1 else f"M{qr.mask_pattern}"
trans_label = " · 對角翻轉:ON" if st.session_state.qr_transpose else ""
st.caption(f"有效日期時段：{effective_time.strftime('%Y/%m/%d')} Hour {effective_time.hour}{offset_label} ｜ {ver_label} · EC:{st.session_state.qr_ec} · Mask:{mask_label} · Size:{st.session_state.qr_box_size}{trans_label}")

col1, col2, col3 = st.columns([1, 4, 1])
with col2:
    st.image(buf.getvalue(), use_container_width=True)

st.markdown(f'<a href="{qr_message}" class="uri-link">🔗 {qr_message}</a>', unsafe_allow_html=True)
st.code(qr_message, language=None)
st.caption(f"伺服器逆向還原核對：20{decoded_src[0]:02d}-{decoded_src[1]:02d}-{decoded_src[2]:02d} {decoded_src[3]:02d}:{decoded_src[4]:02d}:{decoded_src[5]:02d}")
st.write("")

# 時段調整按鈕
btn_col1, btn_col2, btn_col3 = st.columns(3)
with btn_col1:
    if st.button("➖ 時段 -1", use_container_width=True):
        st.session_state.hour_offset -= 1
        st.rerun()
with btn_col2:
    if st.button("🔄 刷新/重置", use_container_width=True):
        st.session_state.hour_offset = 0
        st.rerun()
with btn_col3:
    if st.button("➕ 時段 +1", use_container_width=True):
        st.session_state.hour_offset += 1
        st.rerun()

st.divider()
    
# 雙層時鐘漂移參數微調 (軟體喚醒偏移 + 硬體晶振偏移)
st.caption(f"**觸發狀態：** 真實時間每小時 `:{wakeup_minute:02d}` 換碼 ｜ 裝置內部 RTC 讀出 `:{m:02d}:{s:02d}`")
drift_col1, drift_col2, drift_col3 = st.columns(3)
with drift_col1:
    new_init = st.number_input("初始喚醒分", min_value=0, max_value=59, value=st.session_state.init_wakeup_min, step=1)
    if new_init != st.session_state.init_wakeup_min:
        st.session_state.init_wakeup_min = new_init
        st.rerun()
with drift_col2:
    new_sw = st.number_input("軟體走快(分)", min_value=-60, max_value=60, value=st.session_state.sw_wakeup_fast_min, step=1)
    if new_sw != st.session_state.sw_wakeup_fast_min:
        st.session_state.sw_wakeup_fast_min = new_sw
        st.rerun()
with drift_col3:
    new_hw = st.number_input("硬體晶振快(秒)", min_value=-3600, max_value=3600, value=st.session_state.hw_rtc_fast_sec, step=1)
    if new_hw != st.session_state.hw_rtc_fast_sec:
        st.session_state.hw_rtc_fast_sec = new_hw
        st.rerun()

st.divider()

# QR Code 與內部時間參數調整區塊
with st.expander("⚙️ QR Code 與裝置時間參數設定", expanded=False):
    # 1. 對角線翻轉開關
    st.caption(f"**對角線翻轉 (Transpose):** {'開啟 (ON)' if st.session_state.qr_transpose else '關閉 (OFF)'}")
    t_col1, t_col2 = st.columns(2)
    with t_col1:
        if st.button("正常方向", key="trans_off", type="secondary" if st.session_state.qr_transpose else "primary", use_container_width=True):
            st.session_state.qr_transpose = False
            st.rerun()
    with t_col2:
        if st.button("🔄 對角線翻轉", key="trans_on", type="primary" if st.session_state.qr_transpose else "secondary", use_container_width=True):
            st.session_state.qr_transpose = True
            st.rerun()

    # 2. Version 調整
    st.caption(f"**Version (版本):** {'自動 (Auto)' if st.session_state.qr_version == 0 else f'Version {st.session_state.qr_version} (實際: V{qr.version})'}")
    v_col1, v_col2, v_col3 = st.columns(3)
    with v_col1:
        if st.button("➖ Ver -1", use_container_width=True, disabled=(st.session_state.qr_version <= 0)):
            st.session_state.qr_version -= 1
            st.rerun()
    with v_col2:
        if st.button("Auto (V0)", use_container_width=True):
            st.session_state.qr_version = 0
            st.rerun()
    with v_col3:
        if st.button("➕ Ver +1", use_container_width=True, disabled=(st.session_state.qr_version >= 10)):
            st.session_state.qr_version += 1
            st.rerun()

    # 3. Error Correction 容錯率調整
    st.caption(f"**Error Correction (容錯率):** {st.session_state.qr_ec}")
    ec_cols = st.columns(4)
    for idx, level in enumerate(["L", "M", "Q", "H"]):
        with ec_cols[idx]:
            btn_type = "primary" if st.session_state.qr_ec == level else "secondary"
            if st.button(f"{level}", key=f"ec_{level}", type=btn_type, use_container_width=True):
                st.session_state.qr_ec = level
                st.rerun()

    # 4. Mask Pattern 遮罩樣式調整 (Auto + 0~7)
    st.caption(f"**Mask Pattern (遮罩樣式):** {'Auto' if st.session_state.qr_mask == -1 else st.session_state.qr_mask} (實際: {qr.mask_pattern})")
    mask_row1 = st.columns(5)
    with mask_row1[0]:
        btn_type = "primary" if st.session_state.qr_mask == -1 else "secondary"
        if st.button("Auto", key="mask_auto", type=btn_type, use_container_width=True):
            st.session_state.qr_mask = -1
            st.rerun()
    for m in range(4):
        with mask_row1[m + 1]:
            btn_type = "primary" if st.session_state.qr_mask == m else "secondary"
            if st.button(f"{m}", key=f"mask_{m}", type=btn_type, use_container_width=True):
                st.session_state.qr_mask = m
                st.rerun()

    mask_row2 = st.columns(4)
    for m in range(4, 8):
        with mask_row2[m - 4]:
            btn_type = "primary" if st.session_state.qr_mask == m else "secondary"
            if st.button(f"{m}", key=f"mask_{m}", type=btn_type, use_container_width=True):
                st.session_state.qr_mask = m
                st.rerun()

    # 5. Module Size (box_size) 調整
    st.caption(f"**Module Size (方格像素):** {st.session_state.qr_box_size} px")
    m_col1, m_col2, m_col3 = st.columns(3)
    with m_col1:
        if st.button("➖ Size -2", use_container_width=True, disabled=(st.session_state.qr_box_size <= 4)):
            st.session_state.qr_box_size -= 2
            st.rerun()
    with m_col2:
        if st.button("預設 (24px)", use_container_width=True):
            st.session_state.qr_box_size = 24
            st.rerun()
    with m_col3:
        if st.button("➕ Size +2", use_container_width=True, disabled=(st.session_state.qr_box_size >= 24)):
            st.session_state.qr_box_size += 2
            st.rerun()
