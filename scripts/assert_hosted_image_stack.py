from __future__ import annotations

"""Build-time guard: keep dangerous broad image decoder stacks out of hosted images."""

import ctypes.util
import importlib.util
import shutil

errors=[]
for command in ('magick','convert'):
    if shutil.which(command):
        errors.append(f'forbidden ImageMagick command present: {command}')
if ctypes.util.find_library('heif'):
    errors.append('forbidden libheif shared library is discoverable')
if importlib.util.find_spec('pillow_heif') is not None:
    errors.append('forbidden pillow-heif package is installed')
if errors:
    raise SystemExit('\n'.join(errors))
print('Hosted image decoder surface: PASS (no ImageMagick/libheif/pillow-heif)')
