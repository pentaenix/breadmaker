"""Offline optional dictionary checks; no project, server, network or audio IO."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from api.manuscript.narration_pronunciation import _frontend,_items,_italian,_vocabulary
vocab=set(json.loads((ROOT/'static/vendor/narration/models/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/tokenizer.json').read_text())['model']['vocab'])
assert _vocabulary<=vocab
for language,text in [('ja','彼は「パンを12個」と言った。（本当？）'),('zh-CN','他说：“面包有12个。”（真的吗？）'),('zh-CN','一共3.14元，有100个。')]:
    items=_items(text,language,_frontend(language))
    if '12' in text:assert any('12'==item['subtitles'] for item in items)
    for item in items:
        assert item['subtitles'] in text
        assert all(char in vocab for char in item['value'])
    assert not any(any(char.isdigit() for char in item['value']) for item in items)
    print('PASS:',language,'quoted text, punctuation, numbers and original subtitles',flush=True)
for word in ['La','panetteria','è','aperta','dodici','fantasiosamente']:
    assert all(char in vocab for char in _italian(word,_frontend('it')))
assert _italian('dodici',_frontend('it'))=='ˈdodiʧi'
print('PASS: Italian lexicon/unknown-word prediction and Kokoro affricates.',flush=True)
