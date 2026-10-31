"""Bounded English lexical review passes. No model, network, mutation or rewrite."""
import re
from .editing_lexicon import (TELLING,FILTER,FILTER_PHRASES,TELLING_PHRASES,
    LY_ADVERBS,QUALIFIERS,CONTEXT_QUALIFIERS,MODIFIER_TARGETS,EMOTION_NOUNS,
    EMOTION_ADJECTIVES,WORDY_PHRASES,REDUNDANT_PHRASES,ADVERB_COMPOUNDS,
    AMBIGUOUS_LY,QUALIFIER_PHRASES)

WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*",re.UNICODE)

def phrase_pattern(phrases):
    alternatives=[r'\s+'.join(re.escape(part).replace("'","['’]").replace(r'\-', '[-‐‑]') for part in phrase.split())
                  for phrase in sorted(phrases,key=len,reverse=True)]
    return re.compile(r"(?<![\w'’])(?:"+'|'.join(alternatives)+r")(?![\w'’])",re.I)

PHRASES={key:phrase_pattern(value) for key,value in {
    'telling':TELLING_PHRASES,'filter':FILTER_PHRASES,'wordy':WORDY_PHRASES,
    'redundancies':REDUNDANT_PHRASES,'qualifiers':QUALIFIER_PHRASES,
    'adverbs':ADVERB_COMPOUNDS,
}.items()}
_NOUNS='|'.join(sorted(EMOTION_NOUNS,key=len,reverse=True))
_ADJECTIVES='|'.join(sorted(EMOTION_ADJECTIVES,key=len,reverse=True))
_FEELING_VERBS=r"(?:feel|feels|felt|feeling|am|is|are|was|were|be|been|being|seem|seems|seemed|seeming|look|looks|looked|looking|become|becomes|became|becoming|remain|remains|remained|remaining|i['’]m|you['’]re|he['’]s|she['’]s|it['’]s|we['’]re|they['’]re|isn['’]t|aren['’]t|wasn['’]t|weren['’]t)"
_DEGREE=r'(?:(?:very|really|quite|so|deeply|utterly|extremely|incredibly|terribly|particularly|especially|slightly|rather|completely|absolutely|entirely|remotely|exactly|fully|even|more|most|less|least|visibly|obviously|clearly|noticeably|unusually|surprisingly|increasingly|desperately|painfully|sincerely|profoundly|genuinely|truly|secretly|almost|nearly|overwhelmingly|unexpectedly|at\s+all)\s+){0,2}'
_NEGATION=r'(?:(?:not|never|no\s+longer)\s+)?'
_NOUN_FRAME=r'(?:in\s+a\s+state\s+of|a\s+sense\s+of|a\s+feeling\s+of|filled\s+with|full\s+of|overcome\s+with|overcome\s+by|out\s+of|in|with)\s+(?:(?:a|an|the)\s+)?(?:(?:deep|great|sudden|intense|overwhelming|sheer|utter|pure)\s+)?'
_FELT_NOUN=r'(?:feel|feels|felt|feeling|experience|experiences|experienced|experiencing)\s+(?:(?:a|an|the|no|any|some)\s+)?(?:(?:deep|great|sudden|intense|overwhelming|sheer|utter|pure|real|genuine|profound)\s+)?'
EMOTIONS=re.compile(r"(?<![\w'’])(?:"+_NOUN_FRAME+r'(?:'+_NOUNS+r')|'+_FELT_NOUN+r'(?:'+_NOUNS+r')|'+_FEELING_VERBS+r'\s+'+_DEGREE+_NEGATION+_DEGREE+r'(?:'+_ADJECTIVES+r"))(?![\w'’])",re.I)

_DETERMINERS={'a','an','the','my','your','his','her','its','our','their','this','that','these','those','some','any','another','each','every','no'}
_FILTER_NOUNS={'saw','watch','watches','look','looks','smell','smells','sense','senses','feel','felt','taste','tastes','spot','spots','witness','witnesses','thought','wonder','wonders'}
_TELLING_NOUNS={'thought','hope','wish','doubt','reason','suspect'}
_CAUSATIVES={'make','makes','made','making','let','lets','letting','help','helps','helped','helping'}
_FREQUENCY_FOLLOWERS={'and','or','but','because','during','throughout','in','at','on','for','to','from','until','since','with','without','after','before','when','while','where','as','except','unless','then','i','he','she','we','they','you','it'}
_NOUN_MODIFIERS=MODIFIER_TARGETS|{'own','keen','fleeting','main','sole','only','key','primary','potential','independent','reliable','credible','material','direct','last','first','same','mere','very'}

def _nominal_use(words,index,text,previous):
    """Conservative noun exclusions, not a pretend part-of-speech parser."""
    if index>=2 and previous in {'her','him','them','us','me','you','it'}:
        before=words[index-2]
        if before.group().lower() in _CAUSATIVES and text[before.end():words[index-1].start()].isspace():return False
    if previous in _DETERMINERS:return True
    if index>=2 and previous in _NOUN_MODIFIERS:
        before=words[index-2]
        return before.group().lower() in _DETERMINERS and text[before.end():words[index-1].start()].isspace()
    return False

def find_matches(text,kind):
    """Return original-source code-point ranges; longest overlapping phrase wins."""
    if kind not in {'telling','filter','adverbs','qualifiers','wordy','redundancies','emotions'}:
        raise ValueError('Unknown editing search')
    words=list(WORD.finditer(text)) if kind in {'telling','filter','adverbs','qualifiers'} else []
    candidates=[]
    for index,word in enumerate(words):
        value=word.group().lower()
        previous=words[index-1].group().lower() if index and text[words[index-1].end():word.start()].isspace() else ''
        following=words[index+1].group().lower() if index+1<len(words) and text[word.end():words[index+1].start()].isspace() else ''
        accepted=(kind=='telling' and value in TELLING or
                  kind=='filter' and value in FILTER or
                  kind=='adverbs' and value in LY_ADVERBS or
                  kind=='qualifiers' and value in QUALIFIERS)
        if kind=='adverbs':
            if value=='kindly' and previous in _DETERMINERS and following:accepted=False
            if value in AMBIGUOUS_LY:accepted=previous not in _DETERMINERS and (not following or following in _FREQUENCY_FOLLOWERS)
        if kind=='telling' and value in _TELLING_NOUNS and _nominal_use(words,index,text,previous):accepted=False
        if kind=='filter':
            if value in _FILTER_NOUNS and _nominal_use(words,index,text,previous):accepted=False
            if value in {'look','looks','looked','looking'} and following in {'after','for','forward'}:accepted=False
        if kind=='qualifiers':
            if value=='rather' and following=='than':accepted=False
            if value in CONTEXT_QUALIFIERS and index+1<len(words) and text[word.end():words[index+1].start()].isspace():
                accepted=following in MODIFIER_TARGETS or following in LY_ADVERBS
        if accepted:candidates.append((word.start(),word.end()))
    if kind in PHRASES:
        for match in PHRASES[kind].finditer(text):
            if kind=='qualifiers':
                before=list(WORD.finditer(text[max(0,match.start()-40):match.start()]));after=WORD.search(text,match.end())
                if after and not text[match.end():after.start()].isspace():after=None
                phrase=' '.join(match.group().lower().split())
                if phrase in {'a little','a little bit','a tiny bit','a bit'} and (not after or after.group().lower() not in MODIFIER_TARGETS|LY_ADVERBS):continue
                prefix=text[max(0,match.start()-40):match.start()]
                previous=before[-1].group().lower() if before and prefix[before[-1].end():].isspace() else ''
                if phrase in {'kind of','sort of'} and previous in _DETERMINERS:continue
            candidates.append(match.span())
    if kind=='emotions':
        candidates.extend(match.span() for match in EMOTIONS.finditer(text))
    # Sort by start then longest: discard nested hits without losing source case.
    result=[]
    for start,end in sorted(set(candidates),key=lambda item:(item[0],-item[1])):
        if result and start<result[-1][1]:continue
        result.append((start,end))
    return result
