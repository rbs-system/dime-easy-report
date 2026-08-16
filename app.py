import os
import io
import re
import base64
import pdfplumber
import pandas as pd
import streamlit as st
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# ==========================================
# 1. Page Configuration
# ==========================================
st.set_page_config(
    page_title="Dime Stock Portfolio Dashboard",
    page_icon="📈",
    layout="wide"
)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# ==========================================
# 2. OAuth & Gmail Helper Functions
# ==========================================
def get_oauth_flow():
    """สร้าง OAuth Flow จาก Streamlit Secrets"""
    return Flow.from_client_config(
        {
            "web": {
                "client_id": st.secrets["google_oauth"]["client_id"],
                "client_secret": st.secrets["google_oauth"]["client_secret"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=st.secrets["google_oauth"]["redirect_uri"]
    )

def get_gmail_service():
    """ดึง Gmail Service จาก Credentials ใน Session State"""
    if "credentials" not in st.session_state:
        return None
    return build('gmail', 'v1', credentials=st.session_state.credentials)

# ==========================================
# 3. PDF Extraction Functions
# ==========================================
def extract_pdf_data(pdf_stream, pdf_password=""):
    """อ่านข้อมูลรายการซื้อขายหุ้นจาก PDF Confirmation Note ของ Dime"""
    records = []
    try:
        with pdfplumber.open(pdf_stream, password=pdf_password) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                
                # ค้นหาข้อมูลวันทำรายการ และรายการหุ้น
                lines = text.split('\n')
                date_str = None
                for line in lines:
                    if "Trade Date" in line or "Date:" in line:
                        match = re.search(r'\d{2}/\d{2}/\d{4}', line)
                        if match:
                            date_str = match.group(0)
                    
                    # ตรวจสอบบรรทัดที่มีรายการซื้อ/ขาย (Buy/Sell)
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
    """ค้นหาอีเมล Confirmation Note จาก Dime และอ่านไฟล์แนบ PDF"""
    service = get_gmail_service()
    if not service:
        return []

    # ค้นหาอีเมลจาก Dime
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
                    
                    # สกัดข้อมูลจาก PDF
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
# Authentication Check
# ------------------------------------------
if "credentials" not in st.session_state:
    # หากเปิดกลับมาจากหน้าล็อกอิน Google OAuth
    if "code" in st.query_params:
        flow = get_oauth_flow()
        flow.fetch_token(code=st.query_params["code"])
        st.session_state.credentials = flow.credentials
        st.rerun()
    else:
        # แสดงหน้าขอให้ล็อกอิน
        st.info("👋 กรุณาเข้าสู่ระบบด้วย Google เพื่อดึงข้อมูลสลิป Confirmation Note จาก Gmail ของคุณ")
        flow = get_oauth_flow()
        auth_url, _ = flow.authorization_url(prompt="consent")
        st.link_button("🔑 เข้าสู่ระบบด้วย Google", auth_url, type="primary")
        st.stop()

# ------------------------------------------
# Main Content / Processing
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
