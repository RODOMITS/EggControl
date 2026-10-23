import re
from pathlib import Path
import json

CATALOG=json.loads((Path(__file__).parent/'game_catalog.json').read_text(encoding='utf-8'))
def field(name,label,kind='text',**kw):return dict(name=name,label=label,kind=kind,**kw)
def spec(name,label,fields=(),target=False):return dict(name=name,label=label,fields=list(fields),target=target)
SPEC=[
 spec('announcement','Объявление',[field('message','Текст',maxBytes=500)]),
 spec('globalevent','Глобальный ивент',[field('action','Действие','select',options=['start','stop']),field('name','Ивент','select',options=CATALOG['events']),field('duration','Секунды','integer',default=60,min=0,max=86400)]),
 spec('globalmorph','Глобальный морф',[field('petname','Пет','pet',random=True),field('duration','Секунды','integer',default=120,min=1,max=3600),field('withboard','Табличка','boolean',default=False),field('slowdown','Замедлить','boolean',default=False)]),
 spec('globalunmorph','Отменить глобальный морф'),
 spec('globalgive','Глобальная выдача',[field('kind','Предмет','select',options=['pet','egg']),field('name','Название','reward'),field('amount','Количество','integer',default=1,min=1,max=20),field('mutation','Мутация','select',options=list(CATALOG['mutations']),default='Default')]),
 spec('globalgivecandy','Выдать конфеты всем',[field('amount','Количество','integer',default=5,min=1,max=10000)]),
 spec('globalskipfuse','Завершить фьюз всем'),
 spec('globalpoll','Глобальный опрос',[field('question','Вопрос',maxBytes=160),field('answer1','Первый ответ',maxBytes=80),field('answer2','Второй ответ',maxBytes=80),field('duration','Секунды','integer',default=30,min=1,max=600)]),
 spec('globalcode','Глобальный код',[field('name','Код',maxBytes=32),field('activations','Активаций','integer',default=100,min=1,max=10000),field('type','Награда','select',options=['pet','egg','money','speed']),field('amount','Количество','integer',default=1,min=1,max=10**12),field('rewardname','Название пета / яйца','reward',optional=True)]),
 spec('fakeupgrade','Фейковый апгрейд',target=True),
 spec('fakedrop','Фейковый дроп',[field('petname','Пет','pet')],target=True),
 spec('fakegrab','Включить прыгающее яйцо',target=True),
 spec('unfakegrab','Выключить прыгающее яйцо',target=True),
 spec('isplayeronline','Проверить онлайн',target=True),
 spec('remoteannounce','Личное уведомление',[field('announce','Текст',maxBytes=400)],target=True),
]
BY_NAME={s['name']:s for s in SPEC}

def validate(action,args):
    if action not in BY_NAME:raise ValueError('Неизвестная команда')
    fields=BY_NAME[action]['fields'];out={}
    if set(args)-{f['name']for f in fields}:raise ValueError('Неизвестный аргумент')
    pets={p['name']for p in CATALOG['pets']}
    for f in fields:
        name=f['name'];v=args.get(name,f.get('default',''))
        if f['kind']=='integer':
            if type(v)is not int or not f['min']<=v<=f['max']:raise ValueError(f['label']+': неверное число')
        elif f['kind']=='boolean':
            if type(v)is not bool:raise ValueError('Нужен переключатель '+f['label'])
        else:
            if not isinstance(v,str):raise ValueError('Неверный аргумент '+f['label'])
            v=v.strip()
            if not v and not f.get('optional'):raise ValueError('Заполни '+f['label'])
            if len(v.encode())>f.get('maxBytes',200):raise ValueError('Слишком длинное поле: '+f['label'])
            if f['kind']=='select'and v not in f['options']:raise ValueError('Недопустимое значение '+f['label'])
            if f['kind']=='pet'and v not in pets and not(f.get('random')and v=='random'):raise ValueError('Неизвестный пет')
        out[name]=v
    if action=='globalgive'and out['name']not in (pets if out['kind']=='pet'else CATALOG['eggs']):raise ValueError('Неизвестный предмет')
    if action=='globalcode':
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',out['name']):raise ValueError('Код: только латиница, цифры, _ и -')
        if out['type']in ('pet','egg'):
            if out['amount']>20:raise ValueError('Максимум 20 предметов')
            if out['rewardname']not in(pets if out['type']=='pet'else CATALOG['eggs']):raise ValueError('Неизвестная награда')
    if action=='globalpoll'and out['answer1']==out['answer2']:raise ValueError('Ответы должны различаться')
    return out
