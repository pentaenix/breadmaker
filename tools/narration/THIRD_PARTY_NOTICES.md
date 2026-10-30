# Optional narration language notices

Keep upstream license files with any redistributed runtime/dictionary/model payload. This source handoff is not a legal clearance for a paid hosted launch. Dependencies are installed explicitly; none of the dictionaries or model binaries is committed here.

- Kokoro/ONNX model and embeddings: Apache-2.0, [pinned model](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX-timestamped/tree/dd4401a9add81ac692d20e240d22ec9dda82cc29). Preserve model license/attribution. Voice quality and upstream training-data attribution: [Kokoro voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).
- Siwis French source: Junichi Yamagishi, Pierre-Edouard Honnet, Philip Garner, Alexandros Lazaridis (2017), *The SIWIS French Speech Synthesis Database, 2016*, University of Edinburgh, [doi:10.7488/ds/1705](https://doi.org/10.7488/ds/1705). Kokoro lists this voice under its CC BY attribution column; preserve this source citation and review the dataset's own license before redistributing source recordings (not included here).
- Gongitsune/Kumo Japanese source texts: Nankichi Niimi, *Gon, the Little Fox*; Ryūnosuke Akutagawa, *The Spider's Thread*. Kokoro's attribution links point to the [koniwa Gongitsune source](https://github.com/koniwa/koniwa/blob/master/source/tnc/tnc__gongitsune.txt) and [Kumo source](https://github.com/koniwa/koniwa/blob/master/source/tnc/tnc__kumonoito.txt). Review/preserve the upstream CC BY attribution trail for any packaged voices; source recordings/text corpora are not bundled here.
- @piper-plus/g2p 0.4.2: MIT; n2words 6.3.0: MIT. Installer preserves their complete license files (historically named SPANISH-G2P-LICENSE / SPANISH-NUMBERS-LICENSE; also applies to the new fr/pt/it submodules).
- Misaki 0.7.4: Apache-2.0, [upstream](https://github.com/hexgrad/misaki). Preserve its installed LICENSE. Its Japanese frontend contains code derived from Cutlet; MIT notice below. Preserve the licenses shipped by fugashi, jaconv, mojimoji, jieba, ordered-set, pypinyin, cn2an/proces and regex when packaging them.
- UniDic-lite 1.0.8 wrapper: MIT, Copyright 2020 Paul McCann. Dictionary is UniDic 2.1.2, Copyright 2011–2013 The UniDic Consortium; its COPYING offers BSD, GPL or LGPL. We select the BSD option, reproduced below. Retain dicdir/COPYING, BSD, AUTHORS and upstream notices. No OpenJTalk runtime is used.
- gruut-lang-it 2.0.1 / gruut CRF feature format: MIT, Copyright 2020 Michael Hansen, [upstream](https://github.com/rhasspy/gruut). Only the top-level, non-espeak lexicon/model is loaded. The Italian dictionary originates from Wiktionary-derived pronunciation data: [gruut provenance](https://github.com/rhasspy/gruut#intended-audience), [pron_dictionaries](https://github.com/Kyubyong/pron_dictionaries). Do not infer that MIT code metadata relicenses Wiktionary data; distribution must retain applicable source attribution/share-alike notices separately and review the exact bundled dictionary before release. The lexicon/CRF are not modified or committed here. python-crfsuite 0.9.12 and CRFsuite have their own installed license notices; retain them.

## MIT notices (gruut format adapter, Cutlet, UniDic-lite wrapper)

Copyright (c) 2020 Michael Hansen

Copyright (c) 2020 Paul O'Leary McCann (Cutlet)

Copyright 2020 Paul McCann (UniDic-lite)

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## UniDic dictionary BSD license

Copyright (c) 2011-2013, The UniDic Consortium
All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.
* Neither the name of the UniDic Consortium nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
