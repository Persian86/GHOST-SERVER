"""Only complete, unambiguous timer phrases take the provider-free command path."""
from __future__ import annotations
import re

_WORDS = dict(zip('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split(),range(1,21)))
_NUMBER = r'(?:\d+(?:\.\d+)?|' + '|'.join(_WORDS) + ')'
_PART = re.compile(rf'({_NUMBER})\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b')


def _duration(text: str) -> dict | None:
    pos=0;args={}
    for part in _PART.finditer(text):
        if text[pos:part.start()].strip() not in ('','and',','):
            return None
        raw,unit=part.groups()
        amount=_WORDS.get(raw)
        if amount is None:amount=float(raw)
        key='hours' if unit.startswith('h') else 'minutes' if unit.startswith('m') else 'seconds'
        args[key]=args.get(key,0)+amount
        pos=part.end()
    return args if args and not text[pos:].strip() else None


def timer_request(text: str) -> dict | None:
    if not isinstance(text,str) or len(text)>300:
        return None
    text=text.strip().lower().rstrip('.?!').strip()
    text=re.sub(r'^please\s+','',text)
    text=re.sub(r',?\s+please$','',text)
    if re.search(r'\b(?:if|unless|then|when)\b',text):
        return None
    match=re.fullmatch(r'(?:set|start)\s+(?:a\s+)?timer\s+(?:for\s+)?(.+)',text)
    if match:
        duration=match[1];label=''
        named=re.fullmatch(r'(.+?)\s+(?:called|named)\s+([\w ]{1,40})',match[1])
        if named:duration,label=named.groups()
        if re.search(r'\b(?:then|if|unless|when|before|after)\b|\band\s+(?:turn|unlock|lock|open|close|set|start|stop|cancel)\b',label):
            return None
        args=_duration(duration)
        if args is None:return None
        return {'action':'set',**args,**({'label':label} if label else {})}
    if re.fullmatch(r'(?:list|show)(?: me)?(?: the)? timers',text):
        return {'action':'list','kind':'timer'}
    match=re.fullmatch(r'(?:stop|cancel|dismiss)(?: the)? (all timers|timer|my timer|[\w ]{1,40} timer)',text)
    if match:
        label=match[1]
        if label=='my timer':label='timer'
        if label not in ('timer','all timers'):label=label[:-6].strip()
        return {'action':'stop','label':label,'kind':'timer'}
    match=re.fullmatch(r'snooze(?: for (.+))?',text)
    if match:
        args=_duration(match[1]) if match[1] else {}
        return None if args is None else {'action':'snooze',**args}
    match=re.fullmatch(r'add (.+) to (?:the )?([\w ]{1,40}) timer',text)
    if match:
        args=_duration(match[1])
        return None if args is None else {'action':'add','label':match[2],'kind':'timer',**args}
    return None
