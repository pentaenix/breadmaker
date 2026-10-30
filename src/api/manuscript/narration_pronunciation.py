"""Optional offline pronunciation only. No inference, persistence, or network IO."""
from __future__ import annotations

import base64
import re
import sqlite3
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from backend.environment import runtime
from .narration_boundary import NarrationRoute

router = APIRouter(route_class=NarrationRoute)
_lock = threading.Lock()
_punctuation = str.maketrans({'。':'.','、':',','，':',','！':'!','？':'?','；':';','：':':','「':'"','」':'"','『':'"','』':'"','«':'"','»':'"','（':'(','）':')','【':'(','】':')',"'":'"','–':'—'})
# Kokoro v1.0's token inventory. Fail visibly rather than silently dropping IPA.
_vocabulary = set(';:,.!?—…"()“” ̃ʣʥʦʨᵝꭧAIOQSTWYᵊabcdefhijklmnopqrstuvwxyzɑɐɒæβɔɕçɖðʤəɚɛɜɟɡɥɨɪʝɯɰŋɳɲɴøɸθœɹɾɻʁɽʂʃʈʧʊʋʌɣɤχʎʒʔˈˌːʰʲ↓→↗↘ᵻ')

class PronunciationInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    language: Literal['it','ja','zh-CN']
    text: str = Field(default='', max_length=20000)
    words: list[str] = Field(default_factory=list, max_length=10000)

def _ipa(value):
    value = unicodedata.normalize('NFC', value.translate(_punctuation)).replace('t͡ʃ','ʧ').replace('d͡ʒ','ʤ').replace('t͡s','ʦ').replace('d͡z','ʣ').replace('ɦ','h').replace('g','ɡ')
    if not value or len(value)>=350 or any(char not in _vocabulary for char in value):
        raise HTTPException(422,'NARRATION_PRONUNCIATION_UNSUPPORTED')
    return value

@lru_cache(maxsize=3)
def _frontend(language):
    # Deliberately lazy: writing, startup and English/Spanish need none of these.
    if language=='ja':
        from misaki.ja import JAG2P
        return JAG2P()
    if language=='zh-CN':
        from misaki.zh import ZHG2P
        import jieba
        jieba.setLogLevel(40)
        return ZHG2P()
    import gruut_lang_it
    import pycrfsuite
    root=Path(gruut_lang_it.__file__).parent
    with sqlite3.connect(f'{(root/"lexicon.db").as_uri()}?mode=ro',uri=True) as db:
        lexicon={}
        for word,phonemes in db.execute('SELECT word,phonemes FROM word_phonemes ORDER BY pron_order'):
            lexicon.setdefault(word,phonemes.replace(' ',''))
    tagger=pycrfsuite.Tagger();tagger.open(str(root/'g2p'/'model.crf'))
    return lexicon,tagger

def _italian(word,frontend):
    lexicon,tagger=frontend;word=unicodedata.normalize('NFC',word).lower()
    if word in lexicon:return _ipa(lexicon[word])
    # Feature encoding follows gruut's MIT CRF format (Michael Hansen, 2020).
    # No espeak-derived dictionary or general gruut/SSML dependency is loaded.
    encode=lambda value:base64.b64encode(value.encode()).decode('ascii')
    features=[]
    for index,char in enumerate(word):
        row={'bias':1.0,'grapheme':encode(char)}
        if index==0:row['begin']=True
        if index==len(word)-1:row['end']=True
        for distance in range(1,4):
            if index>=distance:row[f'grapheme-{distance}']=encode(word[index-distance])
            if index+distance<len(word):row[f'grapheme+{distance}']=encode(word[index+distance])
        features.append(row)
    phonemes=[phoneme for coded in tagger.tag(features) for phoneme in base64.b64decode(coded).decode().split('|') if phoneme!='_']
    return _ipa(''.join(phonemes))

def _items(text,language,frontend):
    items=[]
    def add(original,value,spoken=True):
        value=_ipa(value)
        if spoken:items.append({'type':'phonetic','value':value+' ','subtitles':original})
        elif items:items[-1]['value']+=value
        else:items.append({'type':'phonetic','value':value,'subtitles':''})
    if language=='ja':
        import jaconv
        from misaki.ja import Word
        # Tag the original phrase at once so particles retain contextual readings.
        for token in frontend.tagger(text):
            original=token.surface
            if re.search(r'\w',original):
                if original.isdigit():value=frontend(original)
                else:value=frontend._romaji_word(Word(original,jaconv.kata2hira(token.feature.pron or token.feature.kana or original),6 if token.char_type==7 or not token.is_unk else token.char_type))
                add(original,value)
            else:add('',original.translate(_punctuation),False)
    else:
        import jieba
        import cn2an
        for original in jieba.lcut(text,cut_all=False):
            if original.isspace():continue
            # Misaki 0.7.4 classifies the first character before normalizing
            # digits. Normalize first so a standalone number gets IPA, not Hanzi.
            if re.search(r'\w',original):add(original,frontend(cn2an.transform(original,'an2cn')))
            else:add('',original.translate(_punctuation),False)
    if not any(item['subtitles'] for item in items):raise HTTPException(422,'NARRATION_TEXT_EMPTY')
    return items

@router.post('/api/narration-pronunciation')
def prepare_narration_pronunciation(data:PronunciationInput):
    if runtime.mode!='local':raise HTTPException(404)
    if runtime.context.current() is None:raise HTTPException(409,'PROJECT_CONTEXT_REQUIRED')
    if data.language=='it':
        if data.text or not data.words or any(not word or len(word)>300 for word in data.words) or sum(map(len,data.words))>30000:raise HTTPException(400,'NARRATION_PRONUNCIATION_INVALID')
    elif not data.text.strip() or data.words:raise HTTPException(400,'NARRATION_PRONUNCIATION_INVALID')
    # Do not let concurrent requests park every API worker behind one dictionary.
    if not _lock.acquire(blocking=False):raise HTTPException(429,'NARRATION_PRONUNCIATION_BUSY',headers={'Retry-After':'1'})
    try:
        frontend=_frontend(data.language)
        return {'phonemes':[_italian(word,frontend) for word in data.words]} if data.language=='it' else {'items':_items(data.text,data.language,frontend)}
    except ImportError:raise HTTPException(503,'NARRATION_LANGUAGE_NOT_INSTALLED')
    except (ValueError,AssertionError,KeyError):raise HTTPException(422,'NARRATION_PRONUNCIATION_UNSUPPORTED')
    finally:_lock.release()
