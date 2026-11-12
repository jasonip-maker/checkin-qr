import datetime
import io
import qrcode
import streamlit as st

st.set_page_config(page_title="Check-in QR", page_icon="📱", layout="centered")

st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.block-container {padding-top: 2rem; text-align: center;}
</style>
""", unsafe_allow_html=True)

if "hour_offset" not in st.session_state:
    st.session_state.hour_offset = 0

hkt = datetime.timezone(datetime.timedelta(hours=8), name="HKT")
now_hkt = datetime.datetime.now(hkt)
effective_time = now_hkt + datetime.timedelta(minutes=15)
effective_hour = (effective_time.hour + st.session_state.hour_offset) % 24

computed_bytes = [
    0x2C,
    (41 + effective_hour) % 256,
    (42 + effective_hour) % 256,
    (6 + effective_hour) % 256,
    0x24,
    (79 + effective_hour) % 256,
]

hex_payload = "".join(f"{b:02X}" for b in computed_bytes)
qr_message = f"checkin://q?91/@{hex_payload}"

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

st.subheader(f"{now_hkt.strftime('%Y-%m-%d %H:%M:%S')} HKT")
offset_label = f" (微調: {st.session_state.hour_offset:+d})" if st.session_state.hour_offset != 0 else ""
st.caption(f"有效時段：Hour {effective_hour}{offset_label}")

col1, col2, col3 = st.columns([1, 4, 1])
with col2:
    st.image(buf.getvalue(), use_container_width=True)

st.code(qr_message, language=None)
st.markdown(f'<a href="{qr_message}" class="uri-link">🔗 {qr_message}</a>', unsafe_allow_html=True)
st.write("")

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
