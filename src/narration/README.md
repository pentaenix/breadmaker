# Pronunciation ownership

`pronunciation.py` owns the existing native Kokoro frontend algorithms, shared
with the separately isolated private helper. Importing it loads no dictionaries,
runtime, identity, cloud SDK or inference model. Only explicit requests load a
language frontend. Private prose/audio persistence and runtime downloads do not
belong here.
