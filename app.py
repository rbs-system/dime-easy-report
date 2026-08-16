import os
import io
import re
import base64
import pdfplumber
import pandas as pd
import streamlit as st
from streamlit_oauth import OAuth2Component
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# ==========================================
# 1. Page Configuration
# ==========================================
st.set_page_config(
    page_title="Dime Stock Portfolio Dashboard",
    page_icon="📈",
    layout="wide"
)

# Read secrets
CLIENT_ID = st.secrets["google_oauth"]["client_id"]
CLIENT_SECRET = st.secrets["google_oauth"]["client_secret"]
REDIRECT_URI = st.secrets["google_oauth"]["redirect_uri"]
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REFRESH_TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_TOKEN_URL = "https://oauth2.googleapis.com/revoke"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, REFRESH_TOKEN_URL, REVOKE_TOKEN_URL)

# ==========================================
# 2. Gmail Helper Functions
# ==========================================
def get_gmail_service():
    """ดึง Gmail Service จาก Credentials ใน Session State"""
    token_info = st.session_state.get("token")
    if not token_info or "access_token" not in token_info:
        return None
    
    creds = Credentials(
        token=token_info["access_token"],
        refresh_token=token_info.get("refresh_token"),
        token_uri=TOKEN_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=[SCOPE]
    )
    return build('gmail', 'v1', credentials=creds)

# ==========================================
# 3. PDF Extraction Functions
# ==========================================
def extract_pdf_data(pdf_stream, pdf_password=""):
    records = []
    try:
        with pdfplumber.open(pdf_stream, password=pdf_password) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                
                lines = text.split('\n')
                date_str = None
                for line in lines:
                    if "Trade Date" in line or "Date:" in line:
                        match = re.search(r'\d{2}/\d{2}/\d{4}', line)
                        if match:
                            date_str = match.group(0)
                    
                    if any(action in line.upper() for action in ["BUY", "SELL"]):
                        parts = line.split()
                        if len(parts) >= 5:
                            records.append({
                                "RawLine": line,
                                "TradeDate": date_str
                            })
    except Exception as e:
        st.error(f"เกิดข้อผิดพลาดในการอ่านไฟล์ PDF: {e}")
    return records

def fetch_all_pdfs(pdf_password=""):
    service = get_gmail_service()
    if not service:
        st.error("ไม่สามารถเชื่อมต่อ Gmail ได้ กรุณาล็อกอินใหม่อีกครั้ง")
        return []

    query = 'from:dime subject:"Confirmation Note"'
    results = service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])

    all_records = []
    progress_bar = st.progress(0)
    
    for idx, msg in enumerate(messages):
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        payload = msg_data.get('payload', {})
        parts = payload.get('parts', [])
        
        for part in parts:
            if part.get('filename') and part.get('filename').endswith('.pdf'):
                att_id = part['body'].get('attachmentId')
                if att_id:
                    attachment = service.users().messages().attachments().get(
                        userId='me', messageId=msg['id'], id=att_id
                    ).execute()
                    file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
                    pdf_stream = io.BytesIO(file_data)
                    
                    records = extract_pdf_data(pdf_stream, pdf_password)
                    all_records.extend(records)
                    
        progress_bar.progress((idx + 1) / len(messages))
        
    progress_bar.empty()
    return all_records

# ==========================================
# 4. Main Application UI & Logic
# ==========================================
st.title("📈 Dime Stock Portfolio Dashboard")
st.caption("ระบบติดตามและคำนวณพอร์ตการลงทุนหุ้นต่างประเทศจากเอกสาร Dime Confirmation Note")

# ------------------------------------------
# Sidebar Settings
# ------------------------------------------
with st.sidebar:
    st.header("⚙️ การตั้งค่าระบบ")
    pdf_password = st.text_input("รหัสผ่านเปิด PDF (เลขบัตรประชาชน)", type="password")
    btn_process = st.button("🚀 ประมวลผลพอร์ตการลงทุน", type="primary")

# ------------------------------------------
# OAuth Gatekeeper
# ------------------------------------------
if "token" not in st.session_state:
    st.info("👋 กรุณาเข้าสู่ระบบด้วย Google เพื่อดึงข้อมูลสลิป Confirmation Note จาก Gmail ของคุณ")
    result = oauth2.authorize_button(
        name="🔑 เข้าสู่ระบบด้วย Google",
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        key="google_auth",
        extras_params={"access_type": "offline", "prompt": "consent"}
    )
    if result and "token" in result:
        st.session_state["token"] = result["token"]
        st.rerun()
    st.stop()
else:
    st.success("✅ เชื่อมต่อกับ Google Gmail เรียบร้อยแล้ว พร้อมประมวลผลข้อมูล")
    if st.button("🚪 ออกจากระบบ"):
        del st.session_state["token"]
        st.rerun()

# ------------------------------------------
# Processing Action
# ------------------------------------------
if btn_process:
    if not pdf_password:
        st.warning("⚠️ กรุณากรอกรหัสผ่านสำหรับเปิดไฟล์ PDF ที่เมนูด้านซ้ายก่อนครับ")
    else:
        with st.spinner("กำลังดึงข้อมูลสลิปจาก Gmail และอ่านไฟล์ PDF..."):
            records = fetch_all_pdfs(pdf_password)
            if records:
                st.success(f"ดึงข้อมูลสำเร็จทั้งหมด {len(records)} รายการ!")
                df = pd.DataFrame(records)
                st.dataframe(df, use_container_width=True)
            else:
                st.info("ไม่พบรายการใหม่หรือรหัสผ่าน PDF ไม่ถูกต้อง")
