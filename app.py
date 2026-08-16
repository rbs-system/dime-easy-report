import os
import re
import glob
import base64
import pdfplumber
import pandas as pd
import streamlit as st
import plotly.express as px

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# --- ตั้งค่าหน้าตา Streamlit ---
st.set_page_config(
    page_title="Dime Stock Portfolio Tracker",
    page_icon="📈",
    layout="wide"
)

PDF_DIR = "downloaded_pdfs"
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# --- Gmail API ---
def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def fetch_all_pdfs():
    """ดึงไฟล์ PDF ใบยืนยันรายการทั้งหมดจาก Gmail"""
    service = get_gmail_service()
    query = 'from:Dime has:attachment'
    
    results = service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])

    os.makedirs(PDF_DIR, exist_ok=True)
    downloaded_count = 0

    for msg in messages:
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        payload = msg_data.get('payload', {})
        parts = payload.get('parts', [])

        for part in parts:
            filename = part.get('filename', '')
            if filename and filename.lower().endswith('.pdf'):
                file_path = os.path.join(PDF_DIR, filename)
                
                if not os.path.exists(file_path):
                    body = part.get('body', {})
                    attachment_id = body.get('attachmentId')
                    if attachment_id:
                        attachment = service.users().messages().attachments().get(
                            userId='me', messageId=msg['id'], id=attachment_id
                        ).execute()
                        data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))
                        with open(file_path, 'wb') as f:
                            f.write(data)
                        downloaded_count += 1
    return downloaded_count

def parse_pdf(pdf_path, password):
    """อ่านข้อมูลจาก PDF โดยใช้ password ที่รับมาจากหน้าเว็บ"""
    records = []
    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            full_text = "\n".join([p.extract_text() for p in pdf.pages if p.extract_text()])
        
        trade_date = ""
        date_match = re.search(r'(\d{2}/\d{2}/\d{4})', full_text)
        if date_match:
            trade_date = date_match.group(1)

        for line in full_text.split('\n'):
            line_str = line.strip()
            
            if "Vat calculates" in line_str or "BOT as of" in line_str or "Order History" in line_str:
                continue
            
            if "BUY" in line_str or "SEL" in line_str:
                tokens = line_str.split()
                
                action_idx = -1
                action_type = ""
                for idx, token in enumerate(tokens):
                    if token in ["BUY", "SEL", "SELL"]:
                        action_idx = idx
                        action_type = "ขาย (SELL)" if token in ["SEL", "SELL"] else "ซื้อ (BUY)"
                        break
                
                if action_idx != -1 and len(tokens) > action_idx + 1:
                    symbol = tokens[action_idx + 1]
                    
                    numbers = []
                    for t in tokens[action_idx + 2:]:
                        clean_t = t.replace(',', '')
                        if re.match(r'^\d+(\.\d+)?$', clean_t):
                            numbers.append(float(clean_t))
                    
                    if len(numbers) >= 3:
                        records.append({
                            "Trade_Date": trade_date,
                            "Symbol": symbol,
                            "Type": action_type,
                            "Qty": numbers[0],
                            "Price": numbers[1],
                            "Amount": numbers[2],
                            "Fee": numbers[3] if len(numbers) > 3 else 0.0,
                            "Currency": "USD" if "USD" in line_str else "THB",
                            "File_Name": os.path.basename(pdf_path)
                        })
    except Exception as e:
        st.error(f"เกิดข้อผิดพลาดในการอ่านไฟล์ {os.path.basename(pdf_path)}: {e}")
    return records

def calculate_portfolio(df):
    """คำนวณพอร์ตและ Realized P/L"""
    df['Qty'] = pd.to_numeric(df['Qty'], errors='coerce')
    df['Price'] = pd.to_numeric(df['Price'], errors='coerce')
    df['Fee'] = pd.to_numeric(df['Fee'], errors='coerce').fillna(0)

    portfolio = {}
    realized_trades = []

    for idx, row in df.iterrows():
        sym = row['Symbol']
        ttype = str(row['Type'])
        qty = row['Qty']
        price = row['Price']
        fee = row['Fee']
        date = row.get('Trade_Date', '')

        if sym not in portfolio:
            portfolio[sym] = {
                'Holding_Qty': 0.0,
                'Total_Cost': 0.0,
                'Avg_Cost': 0.0,
                'Realized_PL': 0.0
            }

        p = portfolio[sym]

        if "ซื้อ" in ttype or "BUY" in ttype:
            cost = (qty * price) + fee
            p['Holding_Qty'] += qty
            p['Total_Cost'] += cost
            p['Avg_Cost'] = p['Total_Cost'] / p['Holding_Qty'] if p['Holding_Qty'] > 0 else 0.0

        elif "ขาย" in ttype or "SELL" in ttype:
            if p['Holding_Qty'] > 0:
                current_avg_cost = p['Avg_Cost']
                net_revenue = (qty * price) - fee
                cost_of_goods_sold = qty * current_avg_cost
                gain_loss = net_revenue - cost_of_goods_sold

                p['Realized_PL'] += gain_loss
                p['Holding_Qty'] -= qty
                p['Total_Cost'] -= cost_of_goods_sold
                
                if p['Holding_Qty'] <= 0.000001:
                    p['Holding_Qty'] = 0.0
                    p['Total_Cost'] = 0.0
                    p['Avg_Cost'] = 0.0
                else:
                    p['Avg_Cost'] = p['Total_Cost'] / p['Holding_Qty']

                realized_trades.append({
                    'วันที่ทำรายการ': date,
                    'ชื่อหุ้น': sym,
                    'จำนวนที่ขาย': qty,
                    'ราคาขายต่อหน่วย ($)': price,
                    'ราคาต้นทุนเฉลี่ย ($)': round(current_avg_cost, 4),
                    'ค่าธรรมเนียม ($)': fee,
                    'กำไร/ขาดทุนจริง ($)': round(gain_loss, 2)
                })

    port_list = []
    for sym, val in portfolio.items():
        if val['Holding_Qty'] > 0 or val['Realized_PL'] != 0:
            port_list.append({
                'ชื่อหุ้น': sym,
                'จำนวนหุ้นคงเหลือ': round(val['Holding_Qty'], 4),
                'ราคาต้นทุนเฉลี่ย ($)': round(val['Avg_Cost'], 4),
                'ต้นทุนรวม ($)': round(val['Total_Cost'], 2),
                'กำไร/ขาดทุนสะสมจากการขาย ($)': round(val['Realized_PL'], 2)
            })

    return pd.DataFrame(port_list), pd.DataFrame(realized_trades)

# --- ส่วนของการสร้างหน้าเว็บด้วย Streamlit ---
st.title("📈 Dime Stock Portfolio Dashboard")
st.markdown("ระบบติดตามและคำนวณพอร์ตการลงทุนหุ้นต่างประเทศจากเอกสาร Dime Confirmation Note")

# Sidebar - การจัดการรหัสผ่านและดึงข้อมูล
st.sidebar.header("⚙️ การตั้งค่าระบบ")
pdf_password = st.sidebar.text_input("รหัสผ่านเปิด PDF (เลขบัตรประชาชน)", type="password")

sync_gmail = st.sidebar.checkbox("ดึงไฟล์ใหม่จาก Gmail ก่อนประมวลผล", value=False)
btn_process = st.sidebar.button("🚀 ประมวลผลพอร์ตการลงทุน", type="primary")

if btn_process:
    if not pdf_password:
        st.sidebar.error("⚠️ กรุณาใส่รหัสผ่านเปิด PDF ก่อนประมวลผล")
    else:
        with st.spinner("กำลังดำเนินการ..."):
            if sync_gmail:
                new_files = fetch_all_pdfs()
                st.sidebar.success(f"📥 ดาวน์โหลดไฟล์ใหม่สำเร็จ: {new_files} ไฟล์")

            pdf_files = glob.glob(os.path.join(PDF_DIR, "*.pdf"))
            if not pdf_files:
                st.warning("ไม่พบไฟล์ PDF ในระบบ กรุณาติ๊ก 'ดึงไฟล์ใหม่จาก Gmail' แล้วกดประมวลผลอีกครั้ง")
            else:
                all_records = []
                for f in pdf_files:
                    all_records.extend(parse_pdf(f, pdf_password))

                if all_records:
                    df_trades = pd.DataFrame(all_records)
                    df_trades.drop_duplicates(subset=['File_Name', 'Symbol', 'Type', 'Qty', 'Amount'], inplace=True)
                    
                    if 'Trade_Date' in df_trades.columns:
                        df_trades['Sort_Date'] = pd.to_datetime(df_trades['Trade_Date'], format='%d/%m/%Y', errors='coerce')
                        df_trades.sort_values(by='Sort_Date', inplace=True)
                        df_trades.drop(columns=['Sort_Date'], inplace=True)

                    df_summary, df_realized = calculate_portfolio(df_trades)

                    # บันทึกเข้า Session State ของ Streamlit
                    st.session_state['df_summary'] = df_summary
                    st.session_state['df_realized'] = df_realized
                    st.session_state['df_trades'] = df_trades
                    st.success("✅ ประมวลผลข้อมูลเรียบร้อยแล้ว!")
                else:
                    st.error("❌ อ่านข้อมูลจาก PDF ไม่สำเร็จ กรุณาตรวจสอบรหัสผ่าน PDF")

# --- การแสดงผลข้อมูลหลักบน Dashboard ---
if 'df_summary' in st.session_state:
    df_summary = st.session_state['df_summary']
    df_realized = st.session_state['df_realized']
    df_trades = st.session_state['df_trades']

    # 1. การ์ดตัวเลขสรุป (st.metric)
    active_port = df_summary[df_summary['จำนวนหุ้นคงเหลือ'] > 0]
    total_cost = active_port['ต้นทุนรวม ($)'].sum()
    total_realized = df_summary['กำไร/ขาดทุนสะสมจากการขาย ($)'].sum()
    holding_count = len(active_port)

    col1, col2, col3 = st.columns(3)
    col1.metric("💰 มูลค่าต้นทุนพอร์ตปัจจุบัน", f"${total_cost:,.2f}")
    col2.metric("💵 กำไร/ขาดทุนที่เกิดขึ้นจริง (Realized P/L)", f"${total_realized:,.2f}", 
                delta_color="normal" if total_realized >= 0 else "inverse")
    col3.metric("📦 จำนวนหุ้นในพอร์ตปัจจุบัน", f"{holding_count} ตัว")

    st.markdown("---")

    # 2. กราฟสัดส่วนพอร์ต (Plotly Pie Chart)
    if not active_port.empty:
        col_chart, col_empty = st.columns([2, 1])
        with col_chart:
            fig = px.pie(
                active_port, 
                values='ต้นทุนรวม ($)', 
                names='ชื่อหุ้น', 
                title='สัดส่วนการถือครองหุ้นในพอร์ต (ตามต้นทุน)',
                hole=0.4
            )
            fig.update_traces(textposition='inside', textinfo='percent+label')
            st.plotly_chart(fig, use_container_width=True)

    # 3. ตารางแสดงข้อมูลย่อยแบบ Tabs
    tab1, tab2, tab3 = st.tabs(["📊 ภาพรวมพอร์ตคงเหลือ", "💰 ประวัติการขาย (Realized PnL)", "📑 ประวัติรายการทั้งหมด"])

    with tab1:
        st.subheader("ภาพรวมพอร์ตการลงทุนปัจจุบัน")
        st.dataframe(df_summary, use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("รายการขายและผลกำไร/ขาดทุนที่เกิดขึ้นจริง")
        if not df_realized.empty:
            st.dataframe(df_realized, use_container_width=True, hide_index=True)
        else:
            st.info("ยังไม่มีรายการขายหุ้นในระบบ")

    with tab3:
        st.subheader("ประวัติรายการซื้อ/ขายทั้งหมด")
        # เปลี่ยนชื่อคอลัมน์ให้อ่านง่าย
        df_trades_display = df_trades.rename(columns={
            'Trade_Date': 'วันที่ทำรายการ',
            'Symbol': 'ชื่อหุ้น',
            'Type': 'ประเภท',
            'Qty': 'จำนวน',
            'Price': 'ราคา/หน่วย ($)',
            'Amount': 'มูลค่ารวม ($)',
            'Fee': 'ค่าธรรมเนียม ($)',
            'Currency': 'สกุลเงิน',
            'File_Name': 'ชื่อไฟล์'
        })
        st.dataframe(df_trades_display, use_container_width=True, hide_index=True)

else:
    st.info("👈 กรุณากรอกรหัสผ่าน PDF ที่แถบด้านซ้าย แล้วกดปุ่ม **'ประมวลผลพอร์ตการลงทุน'** เพื่อเปิด Dashboard")