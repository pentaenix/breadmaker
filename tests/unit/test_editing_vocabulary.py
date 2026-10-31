"""Broader coverage with counterexamples, original offsets and bounded matching."""
import pytest
from api.annotations.editing_search import find_matches
from api.annotations import editing_lexicon as vocabulary


def hits(text,kind):
    return [text[a:b] for a,b in find_matches(text,kind)]


@pytest.mark.parametrize('word',[
    'tasted','tasting','smelt','overheard','overhearing','discerned','glimpsed',
    'spotted','spotting','witnessed','listened','detected','recognised','realized',
    'remembered','recalled','thought','wondered',
])
def test_filter_families(word):
    assert hits('She '+word+' something.','filter')==[word]


@pytest.mark.parametrize('phrase',[
    'cannot see',"couldn't hear",'couldn’t taste','was able to smell',
    'were able to feel','the sight of','the taste of','became aware of',
    'was conscious of','it seemed as if','it appeared as though',
])
def test_sensory_frames_are_single_complete_hits(phrase):
    assert hits(phrase+' something.','filter')==[phrase]


@pytest.mark.parametrize('word',[
    'pondered','contemplating','inferred','deduced','presumed','anticipated',
    'forgot','forgotten','learnt','learned','comprehended','rationalised','rationalized',
])
def test_telling_families(word):
    assert hits('She '+word+' it.','telling')==[word]


@pytest.mark.parametrize('word',[
    'nonchalantly','lugubriously','wryly','coyly','haltingly','breathily',
    'gingerly','gruffly','fervently','furtively','tensely','ruefully','wordlessly',
    'favourably','favorably','colourfully','colorfully','honourably','honorably',
    'marvellously','marvelously','skilfully','skillfully','wilfully','willfully',
    'judgmentally','judgementally','tantalisingly','tantalizingly',
    'wholeheartedly','lightheartedly','subtly','wholly','publicly',
    'idly','duly','drily','disappointedly','sarcastically','surreptitiously',
    'resentfully','severely','exceedingly','moderately','extraordinarily','naïvely',
])
def test_adverb_coverage(word):
    assert hits('She answered '+word+'.','adverbs')==[word]


@pytest.mark.parametrize('phrase',[
    'self-consciously','self‐consciously','self‑consciously','matter-of-factly',
    'good-naturedly','single-handedly','absent-mindedly','half-heartedly',
])
def test_compounds_are_not_partial_word_hits(phrase):
    assert hits('😀 She answered '+phrase+'.','adverbs')==[phrase]


@pytest.mark.parametrize('text,expected',[
    ('They meet weekly.',['weekly']),('She checked hourly and waited nightly.',['hourly','nightly']),
    ('The weekly meeting and her nightly walks.',[]),('Monthly costs rose.',[]),
    ('The daily arrived.',[]),('A kindly stranger helped.',[]),
    ('Her kindly mother smiled.',[]),('They treated her kindly.',['kindly']),
])
def test_ambiguous_ly_uses_need_context(text,expected):
    assert hits(text,'adverbs')==expected


@pytest.mark.parametrize('kind,text',[
    ('filter','His new watch lay beside the saw and her keen sense of timing impressed me.'),
    ('filter','A bitter taste and a strange smell lingered near that spot.'),
    ('filter','The main witness kept her watches beside the felt. His senses and her tastes changed.'),
    ('telling','His hope and her fleeting thought gave the suspect a reason.'),
    ('telling','The mere thought was his only hope. The main reason was her wish.'),
    ('qualifiers','A little bit of cake and some kind of bird. A tiny bit of paper.'),
    ('qualifiers','This sort of behaviour is common. A\tlittle girl waved.'),
    ('adverbs','A lovely friendly family with a lively elderly neighbour. A costly scholarly book.'),
    ('adverbs','A deadly weapon and bodily harm. A stately motherly figure seemed lonely.'),
    ('adverbs','July reply supply apply imply multiply butterfly jelly bully early only likely.'),
    ('emotions','The blue lamp was cold. Happiness is a word. Joy is her name.'),
])
def test_counterexamples(kind,text):
    assert hits(text,kind)==[]


def test_object_pronouns_and_punctuation_do_not_hide_real_sensory_verbs():
    assert hits('She made her feel uneasy. He helped her feel confident.','filter')==['feel','feel']
    assert hits('The saw. Saw it again.','filter')==['Saw']


@pytest.mark.parametrize('phrase',[
    'felt despondent','was not happy','wasn’t particularly happy',"weren't anxious",
    'she’s utterly crestfallen',"I'm not at all pleased",'became very uneasy',
    'was really not very happy',"wasn't exactly happy",'was not even angry',
    'was visibly nervous','felt more hopeful','was less happy','became angrier',
    'was genuinely happier','felt unexpectedly proud',
    'felt fear','felt no anger','experienced genuine relief','feeling overwhelming grief',
    'remained deeply suspicious','with overwhelming grief','in a state of panic',
    'a sense of dread','out of jealousy','full of hope','overcome by shame',
])
def test_emotion_constructions(phrase):
    assert hits(phrase+'.','emotions')==[phrase]


@pytest.mark.parametrize('text,expected',[
    ('pretty exhausted',['pretty']),('most delightful',['most']),
    ('a little bit tired',['a little bit']),('a\ttiny\nbit anxious',['a\ttiny\nbit']),
    ('rather than leave',[]),('to some extent',['to some extent']),
    ('A. Kind of upset.',['Kind of']),('virtually impossible',['virtually']),
])
def test_qualifier_ranges_and_context(text,expected):
    assert hits(text,'qualifiers')==expected


@pytest.mark.parametrize('kind,phrase',[
    ('wordy','owing to the fact that'),('wordy','at the present moment'),
    ('wordy','during the course of'),('wordy','with the intention of'),
    ('wordy','take into consideration'),('wordy','it’s worth noting that'),
    ('redundancies','combined together'),('redundancies','entirely empty'),
    ('redundancies','completely eliminating'),('redundancies','unanimous consensus'),
])
def test_additional_phrases(kind,phrase):
    assert hits(phrase+'.',kind)==[phrase]


def test_vocabulary_is_unique_and_does_not_guess_suffixes():
    for name in ('FILTER_PHRASES','TELLING_PHRASES','WORDY_PHRASES','REDUNDANT_PHRASES'):
        entries=getattr(vocabulary,name)
        assert len(entries)==len(set(entries))
    assert all(word.endswith('ly') for word in vocabulary.LY_ADVERBS)
    assert hits('unlistedly fictitiouslymadeuply','adverbs')==[]


def test_every_curated_headword_and_emotion_has_an_executable_match():
    for kind,name in [('telling','TELLING'),('filter','FILTER'),('adverbs','LY_ADVERBS'),('qualifiers','QUALIFIERS')]:
        words=sorted(getattr(vocabulary,name))
        assert hits(', '.join(words),kind)==words
    assert {word for word in vocabulary.QUALIFIERS if word.endswith('ly')}<=vocabulary.LY_ADVERBS
    for word in vocabulary.EMOTION_NOUNS:
        assert hits('with '+word,'emotions')==['with '+word]
    for word in vocabulary.EMOTION_ADJECTIVES:
        assert hits('felt '+word,'emotions')==['felt '+word]


def test_expanded_matcher_preserves_original_unicode_ranges_and_performance():
    phrase='😀 Cafe\u0301: She answered self‑consciously, then wasn’t particularly happy.'
    assert hits(phrase,'adverbs')==['self‑consciously','particularly']
    assert hits(phrase,'emotions')==['wasn’t particularly happy']
    text=('She answered nonchalantly and was not really happy. '*1500)
    assert len(find_matches(text,'adverbs'))==3000
    assert len(find_matches(text,'emotions'))==1500
