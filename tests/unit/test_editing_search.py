"""English review suggestions must not behave like a crude suffix search."""
import pytest
from api.annotations.editing_search import find_matches


def hits(text, kind):
    return [text[start:end] for start,end in find_matches(text,kind)]


@pytest.mark.parametrize('kind,text,expected',[
    ('telling','She realised he knew because she had thought about it.',['realised','knew','because','thought']),
    ('telling','I recognized her; she was recognising me.',['recognized','recognising']),
    ('filter','I could see the gate and heard the sound of rain.',['could see','heard','the sound of']),
    ('adverbs','She replied quietly, then hurriedly left.',['quietly','hurriedly']),
    ('qualifiers','A pretty good and very quiet place, a little tired.',['pretty','very','a little']),
    ('wordy','Due to the fact that it’s important to note that all of the rain fell.',['Due to the fact that','it’s important to note that','all of the']),
    ('redundancies','An unexpected surprise: they collaborated together.',['unexpected surprise','collaborated together']),
    ('emotions','She was angry and cried with relief, then felt very anxious.',['was angry','with relief','felt very anxious']),
])
def test_each_search(kind,text,expected):
    assert hits(text,kind)==expected


@pytest.mark.parametrize('kind,text',[
    ('adverbs','A friendly family felt lonely in a lovely likely reply. Apply supply early only.'),
    ('filter','The saw was beside my watch. They looked after it and looked for a tool.'),
    ('qualifiers','A pretty dress. Me too. Rather than leave a little girl with some kind of bird.'),
    ('telling','The unknown forethought overshadowed her hopeful eyes.'),
    ('emotions','Joy is her name. The fear of heights. Happiness matters. A sad song.'),
    ('wordy','In order, tomorrow. All of them.'),
])
def test_common_false_positives(kind,text):
    assert hits(text,kind)==[]


def test_original_unicode_offsets_and_flexible_whitespace():
    text='😀 Cafe\u0301 — IN\nORDER\tTO leave quietly.'
    assert hits(text,'wordy')==['IN\nORDER\tTO']
    assert hits(text,'adverbs')==['quietly']
    start,end=find_matches(text,'adverbs')[0]
    assert start==text.index('quietly') and end==len(text)-1


def test_longest_phrase_is_one_hit_and_word_boundaries_are_respected():
    assert hits('on account of the fact that; the fact that','wordy')==['on account of the fact that','the fact that']
    assert hits('foreseen unseen overseen see seeing','filter')==['see','seeing']


def test_qualifiers_do_not_cross_punctuation():
    assert hits('pretty. Good. A little, tired. too! Quiet.','qualifiers')==[]


def test_unknown_search_rejected():
    with pytest.raises(ValueError):find_matches('quietly','anything')


def test_large_paragraph_remains_bounded():
    text=('The friendly family walked quietly. '*5000)
    assert len(find_matches(text,'adverbs'))==5000
