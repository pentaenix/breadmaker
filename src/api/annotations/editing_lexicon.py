"""Curated English editing-review vocabulary, not a grammar/error dictionary.

Original lists built for Breadmaker from common English usage. The course guide
inspired the categories, not an automatic rewrite rule. Keep inflections explicit
so proper word boundaries and irregular verbs never depend on crude stemming.
"""

def forms(rows):
    return frozenset(word for row in rows for word in row.split())


TELLING = forms([
    'wonder wonders wondered wondering', 'realize realizes realized realizing',
    'realise realises realised realising', 'decide decides decided deciding',
    'hope hopes hoped hoping', 'consider considers considered considering',
    'believe believes believed believing', 'think thinks thought thinking',
    'pray prays prayed praying', 'assume assumes assumed assuming',
    'know knows knew known knowing', 'understand understands understood understanding',
    'remember remembers remembered remembering', 'recognize recognizes recognized recognizing',
    'recognise recognises recognised recognising', 'suppose supposes supposed supposing',
    'conclude concludes concluded concluding', 'determine determines determined determining',
    'expect expects expected expecting', 'intend intends intended intending',
    'wish wishes wished wishing', 'suspect suspects suspected suspecting',
    'imagine imagines imagined imagining',
])
FILTER = forms([
    'see sees saw seen seeing', 'hear hears heard hearing',
    'feel feels felt feeling', 'watch watches watched watching',
    'notice notices noticed noticing', 'look looks looked looking',
    'know knows knew known knowing', 'smell smells smelled smelt smelling',
    'seem seems seemed seeming', 'appear appears appeared appearing',
    'observe observes observed observing', 'perceive perceives perceived perceiving',
    'sense senses sensed sensing',
])
FILTER_PHRASES = ('could see','could hear','could feel','could smell',
                  'the sound of','the feel of','the smell of')
TELLING_PHRASES = ('because','in order to')

# An allowlist deliberately excludes adjectives/nouns/verbs such as friendly,
# lovely, lonely, likely, family, reply, supply and apply. Unknown -ly spellings
# are not guessed. Expand with positive AND negative regression examples.
LY_ADVERBS = frozenset('''
abruptly absently absentmindedly absolutely accidentally accusingly actively actually
adamantly affectionately aggressively angrily anxiously apologetically apparently
appreciatively approvingly awkwardly badly barely bashfully beautifully bitterly
blankly blindly blissfully boldly bravely breathlessly briefly brightly briskly
broadly busily calmly candidly carefully carelessly casually cautiously certainly
cheerfully clearly cleverly closely clumsily coldly comfortably completely confidently
consciously consequently constantly continually conveniently correctly covertly
cruelly curiously currently dangerously darkly dearly decidedly deeply
defiantly definitely deliberately desperately determinedly devotedly differently
dimly directly discreetly distantly distinctly doubtfully dramatically dreamily
dryly eagerly easily effortlessly eagerly eloquently emotionally emphatically
endlessly enormously entirely equally especially eventually evidently exactly
excitedly exclusively expectantly expertly explicitly extremely faintly fairly
faithfully falsely famously fearfully fiercely finally firmly fondly foolishly
formally frankly frantically freely frequently freshly fully furiously generally
generously gently genuinely gladly gleefully gracefully gratefully greatly greedily
grimly grudgingly guiltily habitually halfheartedly happily hardly hastily hatefully
haughtily heavily helpfully helplessly hesitantly honestly hopefully hopelessly
hurriedly hysterically icily immediately impatiently importantly impulsively
inadvertently incredulously increasingly incredibly independently indignantly
inevitably innocently instantly instinctively intentionally intently intensely
inwardly ironically irritably jokingly joyfully justly keenly kindly knowingly
largely lately lazily lightly literally longingly loosely loudly lovingly loyally
luckily madly mainly meaningfully mechanically mentally merely mildly miserably
mistakenly mockingly momentarily mostly mournfully mysteriously naturally nearly
neatly necessarily nervously nicely noisily normally obediently obviously occasionally
oddly openly originally outwardly painfully particularly passionately patiently
perfectly personally physically plainly playfully pleasantly politely poorly
positively possibly potentially powerfully practically precisely presently presumably
previously primarily privately probably promptly properly proudly purely purposely
quickly quietly rapidly rarely readily really reasonably recently recklessly
regularly reluctantly repeatedly reportedly respectfully restlessly rigidly roughly
rudely sadly safely scarcely searchingly secretly seemingly seriously sharply
sheepishly shrilly shyly silently simply sincerely skeptically sceptically sleepily
slightly slowly slyly smartly smoothly smugly softly solemnly solidly
sparingly specifically spitefully steadily sternly stiffly strangely strictly
strongly stubbornly stupidly successfully suddenly supposedly surely surprisingly
suspiciously sweetly swiftly sympathetically tearfully technically tenderly terribly
thankfully thickly thinly thoroughly thoughtfully thoughtlessly tightly timidly
tiredly totally truly typically ultimately unanimously unabashedly undoubtedly
unexpectedly unfortunately unhappily unwillingly urgently usually utterly vaguely
vainly vastly vehemently viciously violently visibly voluntarily warmly warily
weakly wearily wickedly wildly willingly wisely wistfully worriedly wrongly
'''.split())

QUALIFIERS = frozenset('very really somewhat fairly quite rather extremely slightly almost nearly'.split())
CONTEXT_QUALIFIERS = frozenset('pretty too more most less least'.split())
# Only used to disambiguate qualifiers, not to condemn adjectives themselves.
MODIFIER_TARGETS = frozenset('''
good bad big small little large happy sad angry afraid scared anxious worried tired
late early soon often fast slow hard easy difficult clear obvious certain sure
important interesting unusual different beautiful ugly hot cold warm cool bright
dark loud quiet strong weak old young new long short tall deep shallow close far
kind rude nice sweet bitter clever stupid expensive cheap simple complicated
comfortable uncomfortable dangerous safe useful useless careful careless eager
pleased disappointed nervous excited lonely lovely friendly likely unlikely
confident uncertain surprised confused delighted upset proud ashamed guilty
'''.split())

EMOTION_NOUNS = frozenset('''
anger sadness joy despair fear terror horror grief sorrow rage fury delight shame
guilt disgust contempt envy jealousy pride relief surprise dismay disbelief alarm
panic frustration embarrassment excitement anticipation anxiety worry happiness
misery loneliness affection love hatred resentment bitterness hope hopelessness
regret remorse confusion amusement pleasure disappointment irritation dread awe
'''.split())
EMOTION_ADJECTIVES = frozenset('''
angry sad happy afraid scared terrified horrified joyful miserable furious enraged
ashamed guilty disgusted envious jealous proud relieved surprised dismayed alarmed
panicked frustrated embarrassed excited anxious worried lonely resentful bitter
hopeful hopeless regretful remorseful confused amused pleased disappointed irritated
delighted heartbroken nervous frightened apprehensive sorrowful devastated upset
'''.split())

WORDY_PHRASES = (
    'in order to','due to the fact that','at this point in time','at that point in time',
    'at the same time as','make an effort','made an effort','making an effort',
    'all of the','whether or not','for all intents and purposes',
    "it's important to note that",'it is important to note that','needless to say',
    'first and foremost','in the process of','in my opinion','in the event of',
    'by means of','until such a time as','with regard to','in the near future',
    'as the case may be','sooner rather than later','the fact that','in the midst of',
    'off of','with the exception of','on account of the fact that',
    'a large number of','a small number of','in spite of the fact that',
)
REDUNDANT_PHRASES = (
    'collaborate together','collaborates together','collaborated together','collaborating together',
    'past history','advance planning','free gift','end result','final outcome',
    'unexpected surprise','basic fundamentals','personal opinion','true fact',
    'close proximity','completely finished','totally destroyed','repeat again',
    'repeats again','repeated again','repeating again','return back','returns back',
    'returned back','returning back','revert back','reverted back','each and every',
    'the reason why','added bonus','new innovation','general consensus','exact same',
    'necessary prerequisite','unintentional mistake','completely unanimous',
)

from .editing_vocabulary import (TELLING_EXTRA,FILTER_EXTRA,FILTER_FRAMES,TELLING_FRAMES,
    ADVERBS_EXTRA,ADVERB_COMPOUNDS,AMBIGUOUS_LY,QUALIFIERS_EXTRA,QUALIFIER_PHRASES,
    MODIFIERS_EXTRA,EMOTION_NOUNS_EXTRA,EMOTION_ADJECTIVES_EXTRA,WORDY_EXTRA,
    REDUNDANCIES_EXTRA)

TELLING |= TELLING_EXTRA
FILTER |= FILTER_EXTRA
FILTER_PHRASES=tuple(dict.fromkeys(FILTER_PHRASES+FILTER_FRAMES))
TELLING_PHRASES=tuple(dict.fromkeys(TELLING_PHRASES+TELLING_FRAMES))
LY_ADVERBS |= ADVERBS_EXTRA
QUALIFIERS |= QUALIFIERS_EXTRA
MODIFIER_TARGETS |= MODIFIERS_EXTRA
EMOTION_NOUNS |= EMOTION_NOUNS_EXTRA
EMOTION_ADJECTIVES |= EMOTION_ADJECTIVES_EXTRA
WORDY_PHRASES=tuple(dict.fromkeys(WORDY_PHRASES+WORDY_EXTRA))
REDUNDANT_PHRASES=tuple(dict.fromkeys(REDUNDANT_PHRASES+REDUNDANCIES_EXTRA))

PASSES = {
    'telling': ('Telling words','#e8b86b'),
    'filter': ('Filter words','#77b8db'),
    'adverbs': ('-ly adverbs','#c599d8'),
    'qualifiers': ('Qualifiers','#a5c878'),
    'wordy': ('Wordy phrases','#e898ae'),
    'redundancies': ('Possible redundancies','#83c7b4'),
    'emotions': ('Named emotions','#c7bc77'),
}
