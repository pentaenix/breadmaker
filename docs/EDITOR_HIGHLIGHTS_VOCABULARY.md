# English editing vocabulary: coverage expansion

Original curated language data; no downloaded dictionary, model, external service
or new dependency. This is a lexical review aid, **not** a part-of-speech parser or
a semantic assessment. Counts describe dictionary coverage, not measured recall or
precision on all English prose. Ambiguous matches still require author review.

| Category | Original | Expanded |
| --- | ---: | ---: |
| Telling word forms | 93 | 175 |
| Telling phrases | 2 | 19 |
| Filter word forms | 55 | 119 |
| Sensory/filter phrases | 7 | 66 |
| Explicit -ly adverbs | 355 | 1,015 |
| Standalone qualifiers | 10 | 44 |
| Qualifier disambiguation modifiers | 91 | 327 |
| Emotion nouns in constructions | 50 | 115 |
| Emotion adjectives in constructions | 47 | 125 |
| Wordy phrases | 32 | 91 |
| Possible redundancies | 35 | 90 |

Additionally: 13 compound adverbs, eight context-sensitive frequency adverbs,
and nine qualifier phrases. Sets/phrases are deduplicated. There is no generic
adjective search; the modifier list only helps distinguish qualifier uses.

## New matching support

- Explicit mental/sensory inflections and genuine US/UK variants: learnt/learned,
  forgot/forgotten, overheard, rationalise/rationalize, favourably/favorably,
  skilfully/skillfully, drily/dryly, etc. No crude stemming.
- Sensory framing includes taste, awareness, negated contractions and modal
  constructions: couldn't hear, can't taste, was able to see, became aware of.
- Literary/manner/stance/degree/connective adverbs, not just a short motion list.
  Hyphenated compounds are one complete hit, including ordinary and Unicode
  hyphens: self-consciously, matter-of-factly, half-heartedly, etc.
- Daily/weekly/etc. require conservative context: 'meet weekly' matches, while
  'weekly meeting' and 'the daily' do not. Some ambiguous adverb uses are knowingly
  missed rather than classifying every -ly ending as an adverb.
- Emotion frames handle negation, contractions and degrees, including wasn't happy,
  she's utterly crestfallen, was really not very happy, with overwhelming grief,
  in a state of panic. They do not infer the actual emotion or its polarity.
- Qualifier phrases handle variable whitespace, longer forms such as 'a little
  bit', and nominal counterexamples such as 'a little girl' or 'some kind of bird'.
- Noun/verb counterexamples reduce matches for her watches, a bitter taste, the
  main witness, her only hope and a fleeting thought. Causatives such as 'made her
  feel' still match; punctuation does not wrongly carry determiner context across
  sentence boundaries. These rules are deliberately limited, not a hidden parser.
- Original source case, code-point offsets, emoji, combining marks, whole-word
  boundaries and longest-overlapping-phrase behaviour remain unchanged.

## Tests and integration

`tests/unit/test_editing_vocabulary.py` contains positive/negative examples,
executable checks for every curated headword/emotion, spelling/compound variants,
source offsets, phrase boundaries, and a bounded synthetic-paragraph check. The
API suite additionally exercises the new vocabulary through persisted annotations
and Recheck, including emoji and smart apostrophes/hyphens. Existing Clear, Remove,
Undo, manual-highlight preservation, project/document boundaries and caps remain.

Recheck is explicit: expanding the dictionary does not silently alter saved
highlights or rescan manuscripts on startup. Users choose Recheck for each category.
The native source and pure matcher are shared with the cloud integration handoff;
no hosted adapter, deployment or infrastructure change is part of this expansion.
