"""Bounded hosted recording metadata and lossless PCM validation."""
import io
import math
import wave

from hosted.firestore.contracts import HostedValidationError
from hosted.firestore.ids import require_opaque_id

MAX_AUDIO = 32 * 1024**2
MAX_METADATA = 2 * 1024**2
MAX_METADATA_CHARACTERS = 400_000


def normalize(data, catalog):
    try:
        if not isinstance(data, dict) or set(data) - {
            'block_id', 'chapter_id', 'target_type', 'voice', 'language', 'source_text',
            'text', 'start', 'end', 'timings', 'backend', 'generation_pace',
            'punctuation_pauses', 'replaces'}:
            raise ValueError()
        kind = data.get('target_type', 'block')
        if kind not in {'block', 'chapter_title'}:
            raise ValueError()
        if not isinstance(data.get('block_id'), str):
            raise ValueError()
        eid = require_opaque_id(data['chapter_id'] if kind == 'chapter_title' else data['block_id'])
        if kind == 'chapter_title' and data['block_id'] != 'chapter-title:' + eid:
            raise ValueError()
        voice = next(item for item in catalog['voices'] if item['id'] == data['voice'])
        source, text, start, end = (data[key] for key in ('source_text', 'text', 'start', 'end'))
        if (not isinstance(source, str) or not isinstance(text, str) or not 0 < len(text) <= 20_000
                or len(source) > 100_000 or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(source.encode('utf-16-le')) // 2
                or source.encode('utf-16-le')[start * 2:end * 2].decode('utf-16-le') != text
                or (kind == 'chapter_title' and (start != 0 or text != source))):
            raise ValueError()
        backend, pace = data.get('backend', 'wasm'), data.get('generation_pace', 1)
        punctuation = data.get('punctuation_pauses', False)
        if (backend not in {'wasm', 'webgpu'} or type(pace) not in (int, float)
                or pace not in (.85, .9, .95, 1, 1.05, 1.1, 1.15) or type(punctuation) is not bool
                or data.get('language', voice['language']) != voice['language']):
            raise ValueError()
        timings = data.get('timings', [])
        if not isinstance(timings, list) or len(timings) > 10_000:
            raise ValueError()
        clean = []
        for word in timings:
            if (not isinstance(word, dict) or set(word) != {'time', 'duration', 'start', 'end'}
                    or type(word['start']) is not int or type(word['end']) is not int
                    or not 0 <= word['start'] < word['end'] <= len(text.encode('utf-16-le')) // 2
                    or any(type(word[key]) not in (int, float) or not math.isfinite(word[key])
                           or not 0 <= word[key] < 36_000 for key in ('time', 'duration'))):
                raise ValueError()
            clean.append(dict(word))
        engine = 'kokoro-timestamped-' + ('fp32' if backend == 'webgpu' else 'q8')
        engine += '-es-piper-0.4.2-v1' if voice['language'] == 'es' else '-v1'
        if voice['language'] not in {'en-us', 'es'}:
            engine += '-' + voice['language'] + '-frontend-v1'
        if pace != 1:
            engine += f'-pace-{pace:g}'
        if punctuation:
            engine += '-punctuation-v1'
        record = dict(block_id=data['block_id'], source_text=source, text=text, start=start, end=end,
                      voice=voice['id'], language=voice['language'], timings=clean, engine=engine,
                      generation_pace=pace, punctuation_pauses=punctuation)
        if kind == 'chapter_title':
            record.update(target_type=kind, chapter_id=eid)
        replaces = data.get('replaces')
        if replaces is not None:
            if not isinstance(replaces, str):
                raise ValueError()
            require_opaque_id(replaces)
        return record, kind, eid, replaces
    except (KeyError, StopIteration, ValueError, TypeError, UnicodeError):
        raise HostedValidationError('NARRATION_METADATA_INVALID') from None


def validate_audio(payload, metadata):
    try:
        if not isinstance(payload, bytes) or not 44 < len(payload) <= MAX_AUDIO:
            raise ValueError()
        with wave.open(io.BytesIO(payload), 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 24000, 'NONE'):
                raise ValueError()
            frames = audio.getnframes()
            if frames <= 0 or len(audio.readframes(frames)) != frames * 2:
                raise ValueError()
            duration = frames / 24000
        if any(word['time'] + word['duration'] > duration + 1 for word in metadata['timings']):
            raise ValueError()
        return duration
    except (wave.Error, EOFError, KeyError, TypeError, ValueError):
        raise HostedValidationError('NARRATION_AUDIO_INVALID') from None
