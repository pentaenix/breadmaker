"""Shared offline Kokoro frontends; no runtime, identity, model or storage IO."""
from __future__ import annotations

import base64
import re
import sqlite3
import unicodedata
from functools import lru_cache
from pathlib import Path


class PronunciationError(ValueError):
    """Structural codes only; never put private input into errors."""


def validate_input(language, text, words):
    if not isinstance(language, str) or language not in {'it', 'ja', 'zh-CN'} or not isinstance(text, str) or len(text) > 20000:
        raise PronunciationError('NARRATION_PRONUNCIATION_INVALID')
    if (not isinstance(words, list) or len(words) > 10000
            or any(not isinstance(word, str) or not word or len(word) > 300 for word in words)):
        raise PronunciationError('NARRATION_PRONUNCIATION_INVALID')
    if language == 'it':
        if text or not words or sum(map(len, words)) > 30000:
            raise PronunciationError('NARRATION_PRONUNCIATION_INVALID')
    elif not text.strip() or words:
        raise PronunciationError('NARRATION_PRONUNCIATION_INVALID')

_punctuation = str.maketrans({'。':'.','、':',','，':',','！':'!','？':'?','；':';','：':':','「':'"','」':'"','『':'"','』':'"','«':'"','»':'"','（':'(','）':')','【':'(','】':')',"'":'"','–':'—'})
# Kokoro v1.0's token inventory. Fail visibly rather than silently dropping IPA.
_vocabulary = set(';:,.!?—…"()“” ̃ʣʥʦʨᵝꭧAIOQSTWYᵊabcdefhijklmnopqrstuvwxyzɑɐɒæβɔɕçɖðʤəɚɛɜɟɡɥɨɪʝɯɰŋɳɲɴøɸθœɹɾɻʁɽʂʃʈʧʊʋʌɣɤχʎʒʔˈˌːʰʲ↓→↗↘ᵻ')

def _ipa(value):
    value = unicodedata.normalize('NFC', value.translate(_punctuation)).replace('t͡ʃ','ʧ').replace('d͡ʒ','ʤ').replace('t͡s','ʦ').replace('d͡z','ʣ').replace('ɦ','h').replace('g','ɡ')
    if not value or len(value)>=350 or any(char not in _vocabulary for char in value):
        raise PronunciationError('NARRATION_PRONUNCIATION_UNSUPPORTED')
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
    if not any(item['subtitles'] for item in items):raise PronunciationError('NARRATION_TEXT_EMPTY')
    return items

def convert(language, text='', words=None):
    words = [] if words is None else words
    validate_input(language, text, words)
    frontend = _frontend(language)
    return {'phonemes': [_italian(word, frontend) for word in words]} if language == 'it' else {'items': _items(text, language, frontend)}
