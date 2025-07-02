import aiohttp
import json
import pandas as pd
import time
import asyncio
from datetime import datetime
from telethon import TelegramClient  # change import to non-blocking version

# NSE API URLs and headers
url_oc = "https://www.nseindia.com/option-chain"
url_nf = "https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=NIFTY&expiry=03-Jul-2025"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://www.nseindia.com/option-chain',
    'Connection': 'keep-alive'
}

# Telegram credentials (replace with your own)
api_id = '98808'
api_hash = '2262a10191f883eeb8c871c55564a0b1'
chat_id = -4533968547  # Replace with your chat/channel/user id

# Placeholder for cookies and previous prices
cookies = {}
previous_prices = {}

async def set_cookie_aiohttp(session):
    """Set session cookies by visiting the NSE option chain page."""
    try:
        async with session.get(url_oc, timeout=10) as response:
            cookies = session.cookie_jar.filter_cookies(url_oc)
            cookie_dict = {cookie.key: cookie.value for cookie in cookies.values()}
            print(f"Cookies set: {cookie_dict}")
            return cookie_dict
    except Exception as e:
        print(f"Error setting cookies: {e}")
        return {}

async def fetch_option_chain():
    """Fetch Nifty option chain data from NSE."""
    global cookies
    retries = 3
    backoff = 1
    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession(cookies=cookies, headers=headers) as session:
                if not cookies:
                    cookies = await set_cookie_aiohttp(session)
                    if not cookies:
                        print("Failed to set cookies")
                        return None
                
                async with session.get(url_nf, timeout=10) as response:
                    if response.status == 401:
                        print("Session expired, refreshing cookies...")
                        cookies = await set_cookie_aiohttp(session)
                        if not cookies:
                            print("Failed to refresh cookies")
                            return None
                        async with session.get(url_nf, timeout=10) as retry_response:
                            if retry_response.status == 200:
                                return await retry_response.json()
                            else:
                                print(f"Failed to fetch data: Status code {retry_response.status}")
                                return None
                    
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"Failed to fetch data: Status code {response.status}")
                        return None
        except Exception as e:
            print(f"Error fetching option chain: {e}")
            if attempt < retries - 1:
                print(f"Retrying in {backoff} seconds...")
                await asyncio.sleep(backoff)
                backoff *= 2
            else:
                print("Max retries reached.")
                return None
    return None

async def send_telegram_message(message):
    async with TelegramClient('option_chain_session', api_id, api_hash) as client:
        await client.send_message(chat_id, message)

async def monitor_price_changes(data, expiry_date):
    """Monitor price changes in the option chain and calculate ATM and nearby sums."""
    global previous_prices
    try:
        print("Processing option chain data...")
        records = data.get('records', {}).get('data', [])
        underlyingValue = data.get('records', {}).get('underlyingValue', 0)
        if not records:
            print("No data found in response.")
            return

        # Find all available strike prices
        strikes = sorted([rec.get('strikePrice') for rec in records if rec.get('strikePrice') is not None])
        if not strikes:
            print("No strikes found.")
            return

        # Round underlying to nearest strike (ATM)
        strike_interval = min([strikes[i+1] - strikes[i] for i in range(len(strikes)-1)]) if len(strikes) > 1 else 50
        atm_strike = int(round(underlyingValue / strike_interval) * strike_interval)
        if atm_strike not in strikes:
            # fallback to closest strike
            atm_strike = min(strikes, key=lambda x: abs(x - underlyingValue))

        # Helper to get option data by strike and type
        def get_option_data(strike, opt_type):
            for rec in records:
                if rec.get('strikePrice') == strike and opt_type in rec:
                    return rec[opt_type].get('lastPrice', 0)
            return 0

        # ATM CE and PE lastPrice
        ce_atm = get_option_data(atm_strike, 'CE')
        pe_atm = get_option_data(atm_strike, 'PE')
        atm_sum = ce_atm + pe_atm

        msg_lines = [
            f"Underlying: {underlyingValue}, ATM Strike: {atm_strike}",
            f"ATM CE lastPrice: {ce_atm}, ATM PE lastPrice: {pe_atm}, ATM Sum: {atm_sum}"
        ]

        # Next Above and Next Below
        above_strikes = [s for s in strikes if s > atm_strike]
        below_strikes = [s for s in strikes if s < atm_strike]
        next_above = above_strikes[0] if len(above_strikes) > 0 else None
        next_below = below_strikes[-1] if len(below_strikes) > 0 else None
        next_next_above = above_strikes[1] if len(above_strikes) > 1 else None
        next_next_below = below_strikes[-2] if len(below_strikes) > 1 else None

        # Next Above: CE(next_above) + PE(next_below)
        if next_above and next_below:
            ce_next_above = get_option_data(next_above, 'CE')
            pe_next_below = get_option_data(next_below, 'PE')
            sum_next = ce_next_above + pe_next_below
            msg_lines.append(
                f"Next Above CE Strike: {next_above}, : {ce_next_above},Next Above PE Strike {next_below}: {pe_next_below}, Sum: {sum_next}"
            )

        # Next Next Above: CE(next_next_above) + PE(next_next_below)
        if next_next_above and next_next_below:
            ce_next_next_above = get_option_data(next_next_above, 'CE')
            pe_next_next_below = get_option_data(next_next_below, 'PE')
            sum_next_next = ce_next_next_above + pe_next_next_below
            msg_lines.append(
                f"Next Next Above CE Strike: {next_next_above}, CE: {ce_next_next_above}, PE(NEXt next_below {next_next_below}): {pe_next_next_below}, Sum: {sum_next_next}"
            )

        # Print and send to Telegram
        message = "\n".join(msg_lines)
        print(message)
        await send_telegram_message(message)

    except Exception as e:
        print(f"Error monitoring price changes: {e}")

async def main():
    """Main function to run the monitoring loop."""
    expiry_date = "03-Jul-2025"  # Updated to match the requested URL
    while True:
        print(f"Checking option chain at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        data = await fetch_option_chain()
        if data:
            await monitor_price_changes(data, expiry_date)
        else:
            print("No data fetched, retrying...")
        await asyncio.sleep(60)  # Wait 60 seconds before next fetch

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Script stopped by user.")
    except Exception as e:
        print(f"Unexpected error: {e}")