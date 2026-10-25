import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import asyncio
from collections import defaultdict, deque
from contextlib import contextmanager, asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict

ROOT = Path(__file__).resolve().parent
TOKEN = os.getenv('BOT_TOKEN', '') or os.getenv('TELEGRAM_BOT_TOKEN', '')
OWNER = int(os.getenv('OWNER_TELEGRAM_ID', '0') or 0)
from cloud import cloud, CloudError, READ_KEY, MESSAGE_KEY, UNIVERSE
from command_specs import CATALOG, SPEC, BY_NAME, validate
from telegram_setup import webhook_secret, setup_loop, state as setup_state
WEBHOOK = webhook_secret(TOKEN, os.getenv('WEBHOOK_SECRET', '')) if TOKEN else ''
PUBLIC_URL = (os.getenv('PUBLIC_URL', '') or os.getenv('DOMAIN', '')).rstrip('/')
if PUBLIC_URL and '://' not in PUBLIC_URL:
    PUBLIC_URL = 'https://' + PUBLIC_URL
PLACE = int(os.getenv('ROBLOX_PLACE_ID', '80549689028789'))
DATA = Path(os.getenv('DATA_DIR', str(ROOT / 'data')))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'panel.sqlite3'
@asynccontextmanager
async def lifespan(app):
    task=None
    if TOKEN and PUBLIC_URL.startswith('https://'):
        task=asyncio.create_task(setup_loop(TOKEN,PUBLIC_URL,WEBHOOK))
    else:
        setup_state['telegram']='missing_config'
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
buckets = defaultdict(deque)


@contextmanager
def db():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


with db() as con:
    con.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS creators(id INTEGER PRIMARY KEY, label TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, uid INTEGER, expires INTEGER);
    CREATE TABLE IF NOT EXISTS servers(job TEXT PRIMARY KEY, at INTEGER, payload TEXT);
    CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, at INTEGER, payload TEXT);
    CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, actor INTEGER, action TEXT, target INTEGER, args TEXT, job TEXT, at INTEGER, status TEXT, result TEXT, nonce TEXT UNIQUE);
    CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY, at INTEGER);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at INTEGER, actor INTEGER, text TEXT);
    ''')


def limit(key, maximum=60, window=60):
    now = time.monotonic()
    q = buckets[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= maximum:
        raise HTTPException(429, 'Слишком много запросов. Подожди немного.')
    q.append(now)
    if len(buckets) > 10000:
        for k in list(buckets):
            if not buckets[k] or buckets[k][-1] < now - 3600:
                buckets.pop(k, None)


def role(uid):
    if OWNER and uid == OWNER:
        return 'admin'
    with db() as con:
        return 'creator' if con.execute('SELECT 1 FROM creators WHERE id=?', (uid,)).fetchone() else None


def verify_init(raw):
    if not TOKEN or not OWNER or len(raw) > 16384:
        raise HTTPException(401, 'Вход недоступен')
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        if len({k for k, _ in pairs}) != len(pairs):
            raise ValueError()
        data = dict(pairs)
        signature = data.pop('hash')
        check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        key = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        age = time.time() - int(data['auth_date'])
        if age < -30 or age > 300:
            raise ValueError()
        user = json.loads(data['user'])
        uid = user['id']
        if type(uid) is not int or uid <= 0:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, 'Открой панель заново через бота')
    if not role(uid):
        raise HTTPException(403, 'Доступ закрыт')
    return user


def identity(request: Request):
    auth = request.headers.get('authorization', '')
    if not auth.startswith('Bearer ') or len(auth) > 300:
        raise HTTPException(401, 'Нужен вход через Telegram')
    token = hashlib.sha256(auth[7:].encode()).hexdigest()
    with db() as con:
        row = con.execute('SELECT uid FROM sessions WHERE token=? AND expires>?', (token, int(time.time()))).fetchone()
    current = role(row['uid']) if row else None
    if not current:
        raise HTTPException(403, 'Доступ закрыт')
    limit(('user', row['uid']), 120)
    return {'id': row['uid'], 'role': current}


def admin(user=Depends(identity)):
    if user['role'] != 'admin':
        raise HTTPException(403, 'Только для владельца')
    return user


@app.middleware('http')
async def headers(request, call_next):
    if request.method == 'POST':
        parts, size = [], 0
        async for part in request.stream():
            size += len(part)
            if size > 2_000_000:
                return JSONResponse({'detail': 'Payload too large'}, status_code=413)
            parts.append(part)
        request._body = b''.join(parts)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; img-src 'self' data: https://*.rbxcdn.com; connect-src 'self'; object-src 'none'; base-uri 'none'"
    return response


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Strict):
    init_data: str = Field(max_length=16384)


@app.post('/api/login')
def login(body: Login, request: Request):
    limit(('login', request.client.host), 20)
    user = verify_init(body.init_data)
    secret = secrets.token_urlsafe(32)
    with db() as con:
        con.execute('DELETE FROM sessions WHERE expires<?', (int(time.time()),))
        con.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(secret.encode()).hexdigest(), user['id'], int(time.time()) + 8*3600))
    return {'token': secret, 'user': {'id': user['id'], 'name': user.get('first_name', 'Пользователь'), 'role': role(user['id'])}}


@app.get('/health')
def health():
    return {'ok': True}


@app.post('/api/logout')
def logout(request: Request, user=Depends(identity)):
    token = request.headers['authorization'][7:]
    with db() as con:
        con.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(token.encode()).hexdigest(),))
    return {'ok': True}


def live():
    return cloud.live()


@app.get('/api/overview')
def overview(user=Depends(identity)):
    try:
        servers=live()
        players={p['id']:p for server in servers for p in server['players']}
        return {'online':len(players),'servers':len(servers),'updatedAt':max((s['at']for s in servers),default=None),'connected':bool(servers),'error':None}
    except CloudError as exc:
        return {'online':None,'servers':None,'updatedAt':None,'connected':False,'error':str(exc)}


@app.get('/api/exists')
def exists(user=Depends(identity)):
    try:
        result=cloud.exists()
    except CloudError as exc:
        result={'counts':None,'updatedAt':None,'state':'error','error':str(exc)}
    return dict(result,catalog=CATALOG['pets'],mutations=CATALOG['mutations'],eggIcons=CATALOG.get('eggIcons',{}))


@app.get('/api/players')
def players(user=Depends(identity)):
    try:
        found={}
        for server in sorted(live(),key=lambda s:s['at']):
            for p in server['players']:found[p['id']]=dict(p,server=server['job'])
        return list(found.values())
    except CloudError:
        return []


@app.get('/api/admin')
def admin_data(user=Depends(admin)):
    with db()as con:
        pending=con.execute("SELECT id,at FROM commands WHERE status IN ('publishing','sent','unknown') ORDER BY at DESC LIMIT 10").fetchall()
    for row in pending:
        try:result=cloud.command_result(row['id'])
        except CloudError:result=None
        with db()as con:
            if isinstance(result,dict)and result.get('status')in ('done','failed'):
                con.execute('UPDATE commands SET status=?,result=? WHERE id=?',(result['status'],str(result.get('result',''))[:500],row['id']))
            elif time.time()-row['at']>120:
                con.execute("UPDATE commands SET status='unknown',result='Нет подтверждения игрового сервера' WHERE id=?",(row['id'],))
    with db()as con:
        commands=[dict(r)for r in con.execute('SELECT id,action,target,at,status,result FROM commands ORDER BY at DESC LIMIT 40')]
        creators=[dict(r)for r in con.execute('SELECT * FROM creators ORDER BY label')]
    return {'commands':commands,'creators':creators,'specs':SPEC,'choices':{'pets':[p['name']for p in CATALOG['pets']],'eggs':CATALOG['eggs']},'config':{'readKey':bool(READ_KEY),'messagingKey':bool(MESSAGE_KEY),'universe':UNIVERSE,'place':PLACE,'telegram':setup_state['telegram']}}


@app.post('/api/diagnostics')
def diagnostics(user=Depends(admin)):
    limit(('diagnostics',user['id']),3)
    result={}
    try:
        cloud.entry('PetExistsRelease_v1','Release')
        result['DataStore']='Чтение разрешено'
    except CloudError as exc:result['DataStore']=str(exc)
    try:
        result['MemoryStore']='Чтение разрешено; серверов: '+str(len(cloud.live()))
    except CloudError as exc:result['MemoryStore']=str(exc)
    result['Telegram']=setup_state['telegram']
    result['Messaging']='Проверь командой isplayeronline' if MESSAGE_KEY else 'Ключ не задан'
    return result


class Creator(Strict):
    id: int = Field(gt=0, lt=10**16)
    label: str = Field(min_length=1, max_length=60)


@app.post('/api/creators')
def add_creator(body: Creator, user=Depends(admin)):
    if body.id == OWNER:
        raise HTTPException(400, 'Владелец уже имеет доступ')
    with db() as con:
        con.execute('INSERT OR REPLACE INTO creators VALUES(?,?)', (body.id, body.label))
        con.execute('INSERT INTO audit(at,actor,text) VALUES(?,?,?)', (int(time.time()), user['id'], f'Выдан доступ КК: {body.id}'))
    return {'ok': True}


@app.delete('/api/creators/{uid}')
def remove_creator(uid: int, user=Depends(admin)):
    with db() as con:
        con.execute('DELETE FROM creators WHERE id=?', (uid,))
        con.execute('DELETE FROM sessions WHERE uid=?', (uid,))
        con.execute('INSERT INTO audit(at,actor,text) VALUES(?,?,?)', (int(time.time()), user['id'], f'Отозван доступ КК: {uid}'))
    return {'ok': True}


class Command(Strict):
    action: str=Field(max_length=40)
    target: str=Field(default='',max_length=30)
    args: dict=Field(default_factory=dict)
    nonce: str=Field(min_length=16,max_length=80)


@app.post('/api/commands')
def command(body:Command,user=Depends(admin)):
    limit(('command',user['id']),12)
    try:args=validate(body.action,body.args)
    except ValueError as exc:raise HTTPException(400,str(exc))
    target=0
    if BY_NAME[body.action]['target']:
        name=body.target.lstrip('@')
        if not name or len(name)>20 or not all(c.isascii()and(c.isalnum()or c=='_')for c in name):raise HTTPException(400,'Укажи полный ник или Roblox ID')
        try:target=cloud.resolve(name)
        except CloudError as exc:raise HTTPException(400,str(exc))
        if target<=0:raise HTTPException(400,'Неверный Roblox ID')
    with db()as con:
        con.execute('BEGIN IMMEDIATE')
        previous=con.execute('SELECT id FROM commands WHERE nonce=?',(body.nonce,)).fetchone()
        if previous:return {'id':previous['id']}
        cid=secrets.token_hex(16)
        con.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?,?)',(cid,user['id'],body.action,target,json.dumps(args),'',int(time.time()),'publishing','',body.nonce))
    try:
        cloud.send(cid,body.action,args,target)
        status,message='sent','Опубликовано в Messaging; ожидается ответ'
    except CloudError as exc:
        # A timeout can happen AFTER Roblox accepted publication. Never retry automatically.
        status,message='unknown',str(exc)
    with db()as con:con.execute('UPDATE commands SET status=?,result=? WHERE id=?',(status,message,cid))
    return {'id':cid,'status':status,'message':message}


@app.post('/webhook')
async def webhook(request: Request):
    if len(WEBHOOK)<32 or not hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token', ''), WEBHOOK):
        raise HTTPException(403)
    update = await request.json()
    message = update.get('message', {})
    sender = message.get('from', {})
    uid = sender.get('id')
    if not role(uid) or message.get('chat', {}).get('type') != 'private':
        return {'ok': True}
    if str(message.get('text', '')).split(' ')[0] not in {'/start', '/panel'}:
        return {'ok': True}
    with db() as con:
        try:
            con.execute('INSERT INTO updates VALUES(?,?)', (int(update['update_id']), int(time.time())))
        except sqlite3.IntegrityError:
            return {'ok': True}
        con.execute('DELETE FROM updates WHERE at<?', (int(time.time())-86400,))
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            result = await client.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage', json={'chat_id': uid, 'text': 'Панель Steal a Russian Egg', 'reply_markup': {'inline_keyboard': [[{'text': 'Открыть панель', 'web_app': {'url': PUBLIC_URL}}]]}})
            if result.status_code != 200:
                raise HTTPException(503, 'Telegram unavailable')
        except httpx.HTTPError:
            raise HTTPException(503, 'Telegram unavailable')
    return {'ok': True}


@app.get('/')
def index():
    return FileResponse(ROOT / 'static/index.html')


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


if __name__ == '__main__':
    import sys
    if '--check' in sys.argv:
        checks=[]
        try:
            cloud.entry('PetExistsRelease_v1','Release')
            checks.append('DataStore read: OK (Release may not exist before migration)')
        except CloudError as exc:checks.append('DataStore read: '+str(exc))
        try:
            rows=cloud.live()
            checks.append('MemoryStore read: OK; live servers: '+str(len(rows)))
        except CloudError as exc:checks.append('MemoryStore read: '+str(exc))
        checks.append('Messaging key: '+('set; publish not tested' if MESSAGE_KEY else 'MISSING'))
        if TOKEN:
            try:
                with httpx.Client(timeout=10)as client:
                    r=client.post(f'https://api.telegram.org/bot{TOKEN}/getMe')
                    checks.append('Telegram token: '+('OK'if r.status_code==200 and r.json().get('ok')else 'REJECTED'))
            except (httpx.HTTPError,ValueError):checks.append('Telegram: unavailable')
        else:checks.append('Telegram token: MISSING')
        print('\n'.join(checks))
    elif '--configure' in sys.argv:
        if not TOKEN or not PUBLIC_URL.startswith('https://') or len(WEBHOOK)<32:
            raise SystemExit('Fill BOT_TOKEN, PUBLIC_URL, WEBHOOK_SECRET first')
        with httpx.Client(timeout=15) as client:
            for method, payload in [('setWebhook', {'url': PUBLIC_URL+'/webhook', 'secret_token': WEBHOOK, 'allowed_updates': ['message']}), ('setChatMenuButton', {'menu_button': {'type': 'web_app', 'text': 'Панель', 'web_app': {'url': PUBLIC_URL}}}), ('setMyCommands', {'commands':[{'command':'start','description':'Открыть панель'},{'command':'panel','description':'Открыть панель'}]})]:
                try:
                    response = client.post(f'https://api.telegram.org/bot{TOKEN}/{method}', json=payload)
                    if not response.json().get('ok'):
                        raise SystemExit('Telegram rejected '+method)
                except httpx.HTTPError:
                    raise SystemExit('Cannot reach Telegram')
        print('Webhook and menu button configured')
    else:
        if not TOKEN or OWNER<=0 or not READ_KEY or not MESSAGE_KEY or not PUBLIC_URL.startswith('https://'):
            raise SystemExit('Configure BOT_TOKEN, OWNER_TELEGRAM_ID, PUBLIC_URL, ROBLOX_READ_KEY and ROBLOX_MESSAGING_KEY in BotHost settings')
        import uvicorn
        uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8000')), access_log=False)
