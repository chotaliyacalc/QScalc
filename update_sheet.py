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

# Spreadsheet ID configuration
spreadsheet_id = "1lKzZm2UldyWTJ2aLIH1KqXitCFbz23RuY_Q9IbOMmWA"

# Connecting to both worksheets
try:
    spreadsheet = client.open_by_key(spreadsheet_id)
    ws_volume = spreadsheet.worksheet("Top 250 Stocks")
    ws_turnover = spreadsheet.worksheet("Top 250 Turnover")
except Exception as e:
    print(f"Sheet Connection Error: {e}")
    exit(1)

# 2. NSE Data Fetcher & Correct Sequence Processor
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
                    
                    # Column Mapping Safety & Case Normalization
                    df.columns = df.columns.str.strip().str.upper()
                    
                    sym_col = 'TCKRSYMB' if 'TCKRSYMB' in df.columns else 'SYMBOL'
                    close_col = 'CLSPRIC' if 'CLSPRIC' in df.columns else 'CLOSE'
                    series_col = 'SCTYSRS' if 'SCTYSRS' in df.columns else 'SERIES'
                    open_col = 'OPNPRIC' if 'OPNPRIC' in df.columns else 'OPEN'
                    low_col = 'LWPRIC' if 'LWPRIC' in df.columns else 'LOW'
                    
                    vol_col = 'TTLTRADGVOL' if 'TTLTRADGVOL' in df.columns else 'TOTTRDQTY'
                    turnover_col = 'TTLTRFVAL' if 'TTLTRFVAL' in df.columns else 'TOTTRDVAL'

                    # Ensure numerical validation
                    for col in [vol_col, turnover_col, close_col, open_col, low_col]:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    df = df.dropna(subset=[sym_col, vol_col, turnover_col, open_col, low_col])
                    
                    # Filter out non-equity and standard index/etf instruments
                    if series_col in df.columns:
                        df = df[df[series_col].astype(str).str.strip() == 'EQ']
                    
                    filter_keywords = 'BEES|ETF|GOLD|LIQUID|CASE|SILVER|LIQ'
                    df = df[~df[sym_col].astype(str).str.contains(filter_keywords, case=False, na=False)]
                    
                    # -----------------------------------------------------------
                    # STEP 1: First extract the true Top 250 Universes
                    # -----------------------------------------------------------
                    df_top_vol = df.sort_values(by=vol_col, ascending=False).head(250).copy()
                    df_top_turnover = df.sort_values(by=turnover_col, ascending=False).head(250).copy()
                    
                    # -----------------------------------------------------------
                    # STEP 2: Apply the Open-Low pattern check ONLY on those Top 250s
                    # -----------------------------------------------------------
                    # Calculate difference percentage: (Open - Low) / Open * 100
                    df_top_vol['OL_DIFF_PCT'] = ((df_top_vol[open_col] - df_top_vol[low_col]) / df_top_vol[open_col]) * 100
                    df_vol_filtered = df_top_vol[(df_top_vol['OL_DIFF_PCT'] >= 0) & (df_top_vol['OL_DIFF_PCT'] <= 0.15)]
                    
                    df_top_turnover['OL_DIFF_PCT'] = ((df_top_turnover[open_col] - df_top_turnover[low_col]) / df_top_turnover[open_col]) * 100
                    df_turnover_filtered = df_top_turnover[(df_top_turnover['OL_DIFF_PCT'] >= 0) & (df_top_turnover['OL_DIFF_PCT'] <= 0.15)]
                    
                    # Convert filtered DataFrames to list format
                    data_vol = df_vol_filtered[[sym_col, vol_col, close_col]].values.tolist()
                    data_turnover = df_turnover_filtered[[sym_col, turnover_col, close_col]].values.tolist()
                    
                    return data_vol, data_turnover
        else:
            print(f"NSE Server returned status code: {response.status_code}")
            return None, None
    except Exception as e:
        print(f"Error handling or parsing data: {e}")
        return None, None

# 3. Execution Logic (Checking past 7 days)
date = datetime.now()
data_vol_to_insert = None
data_turnover_to_insert = None
fetched_date_str = ""

for i in range(7):
    test_date = date - timedelta(days=i)
    if test_date.weekday() >= 5: # Skip Saturday and Sunday
        continue
        
    data_vol, data_turnover = fetch_bhavcopy_for_date(test_date)
    if data_vol is not None and data_turnover is not None:
        data_vol_to_insert = data_vol
        data_turnover_to_insert = data_turnover
        fetched_date_str = test_date.strftime('%d-%b-%Y')
        break

# 4. Update Both Sheets
if data_vol_to_insert is not None and data_turnover_to_insert is not None:
    try:
        # Clear out old blocks fully up to row 251 to ensure fresh updates
        ws_volume.batch_clear(['A2:C251'])
        if data_vol_to_insert:
            ws_volume.update(range_name='A2', values=data_vol_to_insert, value_input_option='USER_ENTERED')
        else:
            print("Notice: No stocks met Open=Low filter inside Top 250 Volume list today.")
        
        ws_turnover.batch_clear(['A2:C251'])
        if data_turnover_to_insert:
            ws_turnover.update(range_name='A2', values=data_turnover_to_insert, value_input_option='USER_ENTERED')
        else:
            print("Notice: No stocks met Open=Low filter inside Top 250 Turnover list today.")
        
        # Post Status Tracking Info to column K
        ist_now = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%d-%b %H:%M')
        status_msg = f"Data Date: {fetched_date_str} | Open=Low filtered | Last Update: {ist_now} (IST)"
        
        ws_volume.update(range_name='K2', values=[[status_msg]], value_input_option='USER_ENTERED')
        ws_turnover.update(range_name='K2', values=[[status_msg]], value_input_option='USER_ENTERED')
        
        print(f"SUCCESS: Sheets synchronized successfully using data from {fetched_date_str}!")
    except Exception as e:
        print(f"Google Sheet Update Error: {e}")
        exit(1)
else:
    print("FAILED: Historical data file could not be fetched
