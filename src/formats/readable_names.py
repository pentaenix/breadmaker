"""Portable, collision-resistant filenames for human-readable exports."""
import re
import unicodedata

_RESERVED=re.compile(r'^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)',re.I)

def file_stem(value):
    value=unicodedata.normalize('NFC',str(value or ''))
    value=''.join(c for c in value if not unicodedata.category(c).startswith('C'))
    value=re.sub(r'[\\/:*?"<>|]+','-',value).strip(' .').encode('utf-8')[:160].decode('utf-8',errors='ignore').rstrip(' .')
    if not value or _RESERVED.match(value):value='Breadmaker'+('-'+value if value else '')
    return value
