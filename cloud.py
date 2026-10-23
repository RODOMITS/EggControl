"""Roblox Open Cloud only. Never writes player profiles or executes arbitrary Luau."""
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

UNIVERSE=int(os.getenv('ROBLOX_UNIVERSE_ID','10765242573'))
PLACE=int(os.getenv('ROBLOX_PLACE_ID','80549689028789'))
READ_KEY=os.getenv('ROBLOX_READ_KEY','')or os.getenv('ROBLOX_API_KEY','')
MESSAGE_KEY=os.getenv('ROBLOX_MESSAGING_KEY','')or os.getenv('ROBLOX_API_KEY','')
BASE=f'https://apis.roblox.com/cloud/v2/universes/{UNIVERSE}'
RELEASE=int(datetime(2026,10,25,16,tzinfo=timezone.utc).timestamp())
TOPIC='EggControl_v2'
OWNER_ROBLOX=3373383473


class CloudError(Exception):
    pass


class Cloud:
    def __init__(self, transport=None):
        self.http=httpx.Client(timeout=12,transport=transport)
        self.cache={}
        self.locks={}
        self.lock=threading.Lock()

    def cached(self,key,seconds,fn):
        with self.lock:
            lock=self.locks.setdefault(key,threading.Lock())
        with lock:
            old=self.cache.get(key)
            if old and time.monotonic()-old[0]<seconds:
                if isinstance(old[1],Exception):raise old[1]
                return old[1]
            try:
                value=fn()
            except CloudError as exc:
                self.cache[key]=(time.monotonic()-seconds+min(10,seconds),exc)
                raise
            self.cache[key]=(time.monotonic(),value)
            return value

    def call(self,method,url,*,message=False,missing=False,**kwargs):
        key=MESSAGE_KEY if message else READ_KEY
        if not key:raise CloudError('Не задан Roblox Open Cloud ключ')
        try:
            response=self.http.request(method,url,headers={'x-api-key':key},**kwargs)
        except httpx.HTTPError:
            raise CloudError('Нет связи с Roblox API') from None
        if response.status_code==404 and missing:return None
        if response.status_code in (401,403):raise CloudError('Roblox отклонил ключ или его права')
        if response.status_code==429:raise CloudError('Лимит Roblox API. Повтори позже')
        if response.status_code>=400:raise CloudError(f'Roblox API: HTTP {response.status_code}')
        try:return response.json() if response.content else {}
        except ValueError:raise CloudError('Неверный ответ Roblox API') from None

    def entry(self,store,key):
        data=self.call('GET',BASE+'/data-stores/'+quote(store,safe='')+'/scopes/global/entries/'+quote(key,safe=''),missing=True)
        if data is None:return None
        value=data.get('value')
        if isinstance(value,str):
            try:value=json.loads(value)
            except ValueError:pass
        return value

    def exists(self):
        def fetch():
            if time.time()<RELEASE:return {'counts':None,'updatedAt':None,'state':'scheduled'}
            release=self.entry('PetExistsRelease_v1','Release')
            if not isinstance(release,dict)or release.get('Done') is not True:
                return {'counts':None,'updatedAt':None,'state':'migrating'}
            def shard(i):return self.entry('PetExistsLedger_v1',f'Shard_{i}')
            with ThreadPoolExecutor(max_workers=4)as pool:shards=list(pool.map(shard,range(32)))
            totals={}
            for data in shards:
                if data is None:continue
                if not isinstance(data,dict):raise CloudError('Повреждён ответ учёта экзистов')
                values=data.get('Totals')
                if values==[]:values={}
                if not isinstance(values,dict):raise CloudError('Повреждён ответ учёта экзистов')
                for key,n in values.items():
                    if type(n)is not int or n<0:raise CloudError('Неверное количество экзистов')
                    totals[key]=totals.get(key,0)+n
            return {'counts':totals,'updatedAt':int(time.time()),'state':'ready'}
        return self.cached('exists',60,fetch)

    def live(self):
        def fetch():
            rows=[];page='';pages=set()
            for _ in range(100):
                params={'maxPageSize':100}
                if page:params['pageToken']=page
                data=self.call('GET',BASE+'/memory-store/sorted-maps/EggControlLive_v2/items',params=params)
                items=data.get('items',data.get('memoryStoreSortedMapItems',[]))
                if not isinstance(items,list):raise CloudError('Неверный ответ Memory Store')
                for item in items:
                    v=item.get('value')
                    if isinstance(v,dict)and v.get('place')==PLACE and time.time()-v.get('at',0)<90:
                        if not isinstance(v.get('players'),list):v['players']=[]
                        rows.append(v)
                page=data.get('nextPageToken')
                if not page:return rows
                if page in pages:raise CloudError('Ошибка страниц Roblox API')
                pages.add(page)
            raise CloudError('Слишком много серверов: требуется загрузка следующих страниц')
        return self.cached('live',15,fetch)

    def command_result(self,cid):
        def fetch():
            data=self.call('GET',BASE+'/memory-store/sorted-maps/EggControlResults_v2/items/'+quote(cid,safe=''),missing=True)
            return data.get('value')if data else None
        return self.cached('result:'+cid,4,fetch)

    def resolve(self,name):
        if name.isdecimal():return int(name)
        def fetch():
            try:r=self.http.post('https://users.roblox.com/v1/usernames/users',json={'usernames':[name],'excludeBannedUsers':False})
            except httpx.HTTPError:raise CloudError('Не удалось проверить ник')from None
            if r.status_code!=200:raise CloudError('Не удалось проверить ник')
            data=r.json().get('data',[])
            if not data:raise CloudError('Такого Roblox-ника нет')
            return int(data[0]['id'])
        return self.cached('user:'+name.lower(),300,fetch)

    def send(self,cid,action,args,target):
        payload={'id':cid,'action':action,'args':args,'target':target,'at':int(time.time()),'owner':OWNER_ROBLOX,'place':PLACE}
        message=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        if len(message.encode())>950:raise CloudError('Команда слишком длинная для Messaging')
        # Deliberately no automatic retry: a timeout may follow successful publication.
        self.call('POST',BASE+':publishMessage',message=True,json={'topic':TOPIC,'message':message})


cloud=Cloud()
