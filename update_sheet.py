import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
import zipfile
import io
from datetime import datetime, timedelta, timezone
import os
import json

# 1. Credentials Setup
creds_json = os.environ.get('GCP_CREDENTIALS')
if not creds_json:
    print("ERROR: GCP_CREDENTIALS secret missing!")
    exit(1)

creds_dict = json.loads(creds_json)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# Spreadsheet ID configuration (Extracted key from URL)
spreadsheet_id = "1lKzZm2UldyWTJ2aLIH1KqXitCFbz23RuY_Q9IbOMmWA"

# Connecting to both worksheets
try:
    spreadsheet = client.open_by_key(spreadsheet_id)
    ws_volume = spreadsheet.worksheet("Top 250 Stocks")
    ws_turnover = spreadsheet.worksheet("Top 250 Turnover")
except Exception as e:
    print(f"Sheet Connection Error: {e}")
    exit(1)

# 2. NSE UDiFF Data Fetcher with Open-Low Filtering
def fetch_bhavcopy_for_date(date_obj):
    date_str = date_obj.strftime("%Y%m%d")
    url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    print(f"--- Checking date: {date_obj.strftime('%d-%m-%Y')} ---")
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            print("File found! Opening and extracting contents...")
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_filename = z.namelist()[0]
                with z.open(csv_filename) as f:
                    df = pd.read_csv(f)
                    
                    # Column Mapping Safety
                    df.columns = df.columns.str.strip()
                    df.columns = df.columns.str.upper()
                    
                    sym_col = 'TCKRSYMB' if 'TCKRSYMB' in df.columns else 'SYMBOL'
                    close_col = 'CLSPRIC' if 'CLSPRIC' in df.columns else 'CLOSE'
                    series_col = 'SCTYSRS' if 'SCTYSRS' in df.columns else 'SERIES'
                    open_col = 'OPNPRIC' if 'OPNPRIC' in df.columns else 'OPEN'
                    low_col = 'LWPRIC' if 'LWPRIC' in df.columns else 'LOW'
                    
                    # Volume & Turnover Columns identification
                    vol_col = 'TTLTRADGVOL' if 'TTLTRADGVOL' in df.columns else 'TOTTRDQTY'
                    turnover_col = 'TTLTRFVAL' if 'TTLTRFVAL' in df.columns else 'TOTTRDVAL'

                    # Clean data types to floats/ints
                    for col in [vol_col, turnover_col, close_col, open_col, low_col]:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    df = df.dropna(subset=[sym_col, vol_col, turnover_col, open_col, low_col])
                    
                    # Filter for EQ series only
                    if series_col in df.columns:
                        df = df[df[series_col].astype(str).str.strip() == 'EQ']
                    
                    # Filter out ETFs, Gold, and Liquid funds
                    filter_keywords = 'BEES|ETF|GOLD|LIQUID|CASE|SILVER|LIQ'
                    df = df[~df[sym_col].astype(str).str.contains(filter_keywords, case=False, na=False)]
                    
                    # ----------------- OPEN = LOW FILTER LOGIC -----------------
                    # Calculate percentage difference between Open and Low
                    # Keeps stocks where Open is extremely close to Low (within 0.15%)
                    df['OL_DIFF_PCT'] = ((df[open_col] - df[low_col]) / df[open_col]) * 100
                    df = df[(df['OL_DIFF_PCT'] >= 0) & (df['OL_DIFF_PCT'] <= 0.15)]
                    # -----------------------------------------------------------
                    
                    # List A: Top 250 by Volume matching Open-Low condition
                    df_vol = df.sort_values(by=vol_col, ascending=False).head(250)
                    data_vol = df_vol[[sym_col, vol_col, close_col]].values.tolist()
                    
                    # List B: Top 250 by Turnover matching Open-Low condition
                    df_turnover = df.sort_values(by=turnover_col, ascending=False).head(250)
                    data_turnover = df_turnover[[sym_col, turnover_col, close_col]].values.tolist()
                    
                    return data_vol, data_turnover
        else:
            print(f"NSE Server returned status code: {response.status
