"""Automatic BotHost startup setup, without a terminal or exposed setup URL."""
import asyncio
import hashlib
import hmac
import logging
import os

import httpx

log=logging.getLogger('eggcontrol.setup')
state={'telegram':'pending'}


def webhook_secret(token,explicit=''):
    # Stable across redeployments, does not expose the token, no manual generation required.
    return explicit or hmac.new(token.encode(),b'EggControl Telegram webhook v2',hashlib.sha256).hexdigest()


async def configure(token,url,secret,transport=None):
    if not token or not url.startswith('https://')or len(secret)<32:
        state['telegram']='missing_config'
        return False
    operations=[
        ('setWebhook',{'url':url+'/webhook','secret_token':secret,'allowed_updates':['message']}),
        ('setChatMenuButton',{'menu_button':{'type':'web_app','text':'Панель','web_app':{'url':url}}}),
        ('setMyCommands',{'commands':[{'command':'start','description':'Открыть панель'},{'command':'panel','description':'Открыть панель'}]}),
    ]
    async with httpx.AsyncClient(timeout=12,transport=transport)as client:
        for method,body in operations:
            try:
                r=await client.post(f'https://api.telegram.org/bot{token}/{method}',json=body)
                if r.status_code!=200 or r.json().get('ok')is not True:
                    state['telegram']='rejected'
                    return False
            except (httpx.HTTPError,ValueError):
                state['telegram']='unavailable'
                return False
    state['telegram']='ready'
    return True


async def setup_loop(token,url,secret):
    delay=5
    while True:
        if await configure(token,url,secret):
            log.warning('Telegram webhook and Mini App menu ready')
            return
        log.warning('Telegram auto-setup pending (%s); retrying',state['telegram'])
        await asyncio.sleep(delay)
        delay=min(delay*2,120)
