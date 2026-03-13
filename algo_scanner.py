import os
import yfinance as yf
import pandas_ta as ta
import telebot
import pandas as pd
import time
from datetime import datetime
import pytz
from flask import Flask
from threading import Thread

# --- 1. SECURITY UPGRADE: ENVIRONMENT VARIABLES ---
# Ab token code mein nahi, cloud server (Render) ki settings mein dale jayenge
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "Apna_Token_Yahan_Dalein_Agar_Local_Chala_Rahe_Hain")
CHAT_ID = os.environ.get("CHAT_ID", "Apna_Chat_ID_Yahan_Dalein")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
LAST_SIGNAL_TIME = None

# --- 2. ANTI-SLEEP WEB SERVER (For Render & Cron-job) ---
app = Flask(__name__)

@app.route('/')
def alive():
    return "🔥 Smart Options Algo is LIVE and Scanning!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 3. INSTRUMENT MASTER (AUTO-DOWNLOADER) ---
print("📥 Downloading Option Chain Data...")
try:
    csv_url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    INSTRUMENT_DF = pd.read_csv(csv_url, low_memory=False)
    print("✅ Option Data Downloaded!")
except Exception as e:
    print(f"⚠️ Error downloading Instrument Master: {e}")
    INSTRUMENT_DF = None

def get_security_details(strike, option_type):
    if INSTRUMENT_DF is None:
        return f"NIFTY {strike} {option_type}" 
        
    try:
        df = INSTRUMENT_DF[
            (INSTRUMENT_DF['SEM_EXM_EXCH_ID'] == 'NSE') & 
            (INSTRUMENT_DF['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & 
            (INSTRUMENT_DF['SEM_CUSTOM_SYMBOL'].str.startswith('NIFTY')) &
            (INSTRUMENT_DF['SEM_STRIKE_PRICE'] == float(strike)) &
            (INSTRUMENT_DF['SEM_OPTION_TYPE'] == option_type)
        ].copy()

        df['SEM_EXPIRY_DATE'] = pd.to_datetime(df['SEM_EXPIRY_DATE'])
        current_expiry = df.sort_values('SEM_EXPIRY_DATE').iloc[0]
        return current_expiry['SEM_CUSTOM_SYMBOL']
    except:
        return f"NIFTY {strike} {option_type}"

def get_atm_strike(spot_price):
    return int(round(spot_price / 50) * 50)

# --- 4. THE PRO MARKET SCANNER ---
def check_signals(current_time_obj):
    global LAST_SIGNAL_TIME
    
    df = yf.download("^NSEI", interval="5m", period="5d", progress=False)
    if df.empty: return
    
    # Indicators
    df['EMA20'] = ta.ema(df['Close'], length=20)
    df['EMA50'] = ta.ema(df['Close'], length=50) 
    df['RSI'] = ta.rsi(df['Close'], length=14)
    macd = ta.macd(df['Close'], fast=12, slow=26, signal=9)
    df['MACD_Hist'] = macd['MACDh_12_26_9'] 
    bbands = ta.bbands(df['Close'], length=20, std=2)
    df['BB_Upper'] = bbands['BBU_20_2.0']
    df['BB_Lower'] = bbands['BBL_20_2.0']
    df['BB_Width'] = df['BB_Upper'] - df['BB_Lower']
    df['Volume_SMA'] = ta.sma(df['Volume'], length=20)
    
    # Crash-Proofing
    df.fillna(0, inplace=True)
    df['Volume_Ratio'] = df['Volume'] / df['Volume_SMA'].replace(0, 1)

    last = df.iloc[-1]
    prev = df.iloc[-2]
    current_candle_time = df.index[-1]
    current_price = round(last['Close'], 2)

    if LAST_SIGNAL_TIME != current_candle_time:
        signal = None
        option_type = ""
        strategy_name = ""
        
        # 🟢 UPTREND (CE) STRATEGIES 
        if last['Close'] > last['EMA20'] and last['EMA20'] > last['EMA50'] and 60 < last['RSI'] < 80 and last['Volume_Ratio'] > 1.2:
            signal, option_type, strategy_name = "BUY CALL", "CE", "RSI Uptrend Momentum"
        elif last['MACD_Hist'] > 0 and prev['MACD_Hist'] > 0 and last['MACD_Hist'] > (prev['MACD_Hist'] * 1.3) and last['Volume_Ratio'] > 1.5:
            signal, option_type, strategy_name = "BUY CALL", "CE", "MACD Bullish Burst"
        elif last['Close'] > last['BB_Upper'] and prev['Close'] < last['BB_Upper'] and last['BB_Width'] > (prev['BB_Width'] * 1.1) and last['Volume_Ratio'] > 1.5:
            signal, option_type, strategy_name = "BUY CALL", "CE", "BB Squeeze Upside"

        # 🔴 DOWNTREND (PE) STRATEGIES 
        elif last['Close'] < last['EMA20'] and last['EMA20'] < last['EMA50'] and 20 < last['RSI'] < 40 and last['Volume_Ratio'] > 1.2:
            signal, option_type, strategy_name = "BUY PUT", "PE", "RSI Downtrend Momentum"
        elif last['MACD_Hist'] < 0 and prev['MACD_Hist'] < 0 and last['MACD_Hist'] < (prev['MACD_Hist'] * 1.3) and last['Volume_Ratio'] > 1.5:
            signal, option_type, strategy_name = "BUY PUT", "PE", "MACD Bearish Burst"
        elif last['Close'] < last['BB_Lower'] and prev['Close'] > last['BB_Lower'] and last['BB_Width'] > (prev['BB_Width'] * 1.1) and last['Volume_Ratio'] > 1.5:
            signal, option_type, strategy_name = "BUY PUT", "PE", "BB Squeeze Downside"

        if signal:
            atm_strike = get_atm_strike(current_price)
            contract_name = get_security_details(atm_strike, option_type)

            msg = (f"🚨 *PRO OPTIONS ALERT*\n\n"
                   f"**Strategy:** {strategy_name} 🎯\n"
                   f"**Action:** {signal}\n"
                   f"**Spot Price:** {current_price}\n"
                   f"───────────────\n"
                   f"🛒 **Search Contract:** `{contract_name}`\n"
                   f"───────────────\n"
                   f"**Time:** {current_time_obj.strftime('%H:%M IST')}")
            
            try:
                bot.send_message(CHAT_ID, msg, parse_mode='Markdown')
                LAST_SIGNAL_TIME = current_candle_time
                print(f"Alert Sent: {strategy_name} for {contract_name}")
            except Exception as e:
                print(f"Telegram Error: {e}")

# --- 5. MAIN LOOP ---
def main():
    ist = pytz.timezone('Asia/Kolkata')
    
    while True:
        try:
            now = datetime.now(ist)
            
            if now.weekday() >= 5:
                print("Weekend! Going to sleep. 😴")
                time.sleep(3600) # 1 ghanta wait karega
                continue
                
            market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
            market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
            
            if market_open <= now <= market_close:
                check_signals(now)
                time.sleep(30) 
            elif now > market_close:
                print("Market Closed. Waiting for tomorrow. 🛑")
                time.sleep(1800) # 30 mins wait
            else:
                print(f"Waiting for market to open... ({now.strftime('%H:%M IST')})")
                time.sleep(60)
                
        except Exception as e:
            print(f"Error in Loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    # 1. Flask server ko background thread mein chalu karna
    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    # 2. Main Algo Scanner chalu karna
    main()
