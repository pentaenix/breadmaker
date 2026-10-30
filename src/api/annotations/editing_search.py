"""Bounded English lexical review passes. No model, network, mutation or rewrite."""
import re
from .editing_lexicon import (TELLING,FILTER,FILTER_PHRASES,TELLING_PHRASES,
    LY_ADVERBS,QUALIFIERS,CONTEXT_QUALIFIERS,MODIFIER_TARGETS,EMOTION_NOUNS,
    EMOTION_ADJECTIVES,WORDY_PHRASES,REDUNDANT_PHRASES)

WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*",re.UNICODE)

def phrase_pattern(phrases):
    alternatives=[r'\s+'.join(re.escape(part).replace("'","['’]") for part in phrase.split())
                  for phrase in sorted(phrases,key=len,reverse=True)]
    return re.compile(r"(?<![\w'’])(?:"+'|'.join(alternatives)+r")(?![\w'’])",re.I)

PHRASES={key:phrase_pattern(value) for key,value in {
    'telling':TELLING_PHRASES,'filter':FILTER_PHRASES,'wordy':WORDY_PHRASES,
    'redundancies':REDUNDANT_PHRASES,'qualifiers':('a little','a bit','sort of','kind of'),
}.items()}
EMOTIONS=re.compile(r'\b(?:(?:in|with)\s+(?:'+'|'.join(sorted(EMOTION_NOUNS,key=len,reverse=True))+r')|(?:feel|feels|felt|feeling|am|is|are|was|were|seem|seems|seemed|look|looks|looked)\s+(?:(?:very|really|quite|so|deeply|utterly)\s+)?(?:'+'|'.join(sorted(EMOTION_ADJECTIVES,key=len,reverse=True))+r'))\b',re.I)

def find_matches(text,kind):
    """Return original-source code-point ranges; longest overlapping phrase wins."""
    if kind not in {'telling','filter','adverbs','qualifiers','wordy','redundancies','emotions'}:
        raise ValueError('Unknown editing search')
    words=list(WORD.finditer(text)) if kind in {'telling','filter','adverbs','qualifiers'} else []
    candidates=[]
    for index,word in enumerate(words):
        value=word.group().lower()
        previous=words[index-1].group().lower() if index else ''
        following=words[index+1].group().lower() if index+1<len(words) else ''
        accepted=(kind=='telling' and value in TELLING or
                  kind=='filter' and value in FILTER or
                  kind=='adverbs' and value in LY_ADVERBS or
                  kind=='qualifiers' and value in QUALIFIERS)
        if kind=='adverbs' and value=='kindly' and previous in {'a','an','the'}:accepted=False
        if kind=='filter':
            if value in {'saw','watch','look','smell','sense','feel'} and previous in {'a','an','the','my','your','his','her','our','their'}:accepted=False
            if value in {'look','looks','looked','looking'} and following in {'after','for','forward'}:accepted=False
        if kind=='qualifiers':
            if value=='rather' and following=='than':accepted=False
            if value in CONTEXT_QUALIFIERS and index+1<len(words) and text[word.end():words[index+1].start()].isspace():
                accepted=following in MODIFIER_TARGETS or following in LY_ADVERBS
        if accepted:candidates.append((word.start(),word.end()))
    if kind in PHRASES:
        for match in PHRASES[kind].finditer(text):
            if kind=='qualifiers':
                before=WORD.findall(text[max(0,match.start()-40):match.start()]);after=WORD.search(text,match.end())
                if after and not text[match.end():after.start()].isspace():after=None
                if match.group().lower() in {'a little','a bit'} and (not after or after.group().lower() not in MODIFIER_TARGETS|LY_ADVERBS):continue
                if match.group().lower() in {'kind of','sort of'} and before and before[-1].lower() in {'a','an','some','this','that','another'}:continue
            candidates.append(match.span())
    if kind=='emotions':
        candidates.extend(match.span() for match in EMOTIONS.finditer(text))
    # Sort by start then longest: discard nested hits without losing source case.
    result=[]
    for start,end in sorted(set(candidates),key=lambda item:(item[0],-item[1])):
        if result and start<result[-1][1]:continue
        result.append((start,end))
    return result
