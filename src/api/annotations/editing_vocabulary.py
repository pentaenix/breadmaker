"""Original curated English vocabulary supplement, not a grammatical verdict.

Keep explicit inflections and genuine spellings. Avoid deriving adverbs from every
adjective, or adding weak phrase matches merely to make the dictionary larger.
"""

def _forms(rows):
    return frozenset(word for row in rows for word in row.split())


TELLING_EXTRA = _forms([
    'ponder ponders pondered pondering', 'contemplate contemplates contemplated contemplating',
    'reflect reflects reflected reflecting', 'reason reasons reasoned reasoning',
    'deduce deduces deduced deducing', 'infer infers inferred inferring',
    'presume presumes presumed presuming', 'anticipate anticipates anticipated anticipating',
    'doubt doubts doubted doubting', 'reckon reckons reckoned reckoning',
    'recall recalls recalled recalling', 'forget forgets forgot forgotten forgetting',
    'learn learns learned learnt learning', 'discover discovers discovered discovering',
    'comprehend comprehends comprehended comprehending',
    'perceive perceives perceived perceiving', 'appreciate appreciates appreciated appreciating',
    'acknowledge acknowledges acknowledged acknowledging',
    'rationalize rationalizes rationalized rationalizing',
    'rationalise rationalises rationalised rationalising',
])
FILTER_EXTRA = _forms([
    'taste tastes tasted tasting', 'detect detects detected detecting',
    'discern discerns discerned discerning', 'glimpse glimpses glimpsed glimpsing',
    'spot spots spotted spotting', 'overhear overhears overheard overhearing',
    'witness witnesses witnessed witnessing', 'listen listens listened listening',
    'realize realizes realized realizing', 'realise realises realised realising',
    'recognize recognizes recognized recognizing', 'recognise recognises recognised recognising',
    'remember remembers remembered remembering', 'recall recalls recalled recalling',
    'think thinks thought thinking', 'wonder wonders wondered wondering',
])
FILTER_FRAMES = tuple(
    f'{prefix} {sense}'
    for prefix in ('can','cannot',"can't",'could',"couldn't",'was able to','were able to','am able to','is able to','are able to')
    for sense in ('see','hear','feel','smell','taste')
) + (
    'the sight of','the taste of','was aware of','were aware of','is aware of',
    'became aware of','becoming aware of','was conscious of','were conscious of',
    'it seemed that','it seemed as if','it appeared that','it appeared as though',
)
TELLING_FRAMES = (
    'it occurred to','it dawned on','came to the conclusion','come to the conclusion',
    'comes to the conclusion','coming to the conclusion','came to realise',
    'came to realize','had a feeling that','have a feeling that','has a feeling that',
    'there was no doubt that','there is no doubt that','it was obvious that',
    'it was clear that','it is obvious that','it is clear that',
)

# Manner, stance, degree and connective adverbs, including literary vocabulary
# and real UK/US variants. Adjectives such as stately/bodily/deadly stay out.
ADVERBS_EXTRA = frozenset('''
abnormally abominably abstractly absurdly abundantly academically accurately
acutely admirably admiringly admittedly adorably adoringly adversely agreeably
aimlessly airily allegedly alternatively amazingly ambiguously ambitiously amiably
amply amusingly analytically annually anonymously annoyingly anticlimactically
appallingly apprehensively approximately aptly arbitrarily arguably arrogantly
artfully artificially assertively astonishingly astutely attentively audibly
auspiciously authentically automatically avidly avoidably avowedly awfully
barefacedly begrudgingly belatedly belligerently beneficially benignly
beseechingly bewilderedly bleakly blithely boastfully boisterously boyishly
brazenly breathily brilliantly brutally buoyantly callously capriciously cattily
causally ceaselessly challengingly chaotically charitably childishly civilly
cleanly clinically coarsely coherently coincidentally collectively colloquially
colorfully colourfully combatively comically comparatively compassionately
competently competitively complacently comprehensively compulsively conceivably
concisely conclusively condescendingly conditionally congenially conscientiously
considerably considerately consistently conspicuously constructively contemptuously
contentedly contentiously convincingly coolly cordially courageously
courteously coyly credibly crisply critically crossly cunningly cynically daintily
dauntlessly deafeningly deceptively decisively decorously defensively dejectedly
delicately deliciously deliriously demonstrably densely derisively
despondently destructively deviously dexterously diagonally diametrically
diffidently diligently diplomatically disapprovingly disastrously disbelievingly
disconcertingly disconsolately discontentedly disdainfully disgustedly disjointedly
dispassionately disproportionately disrespectfully disruptively distractedly
distrustfully doggedly dolefully domestically dourly drastically dreadfully drearily
dully dumbly dutifully earnestly ecstatically eerily elaborately elegantly
embarrassingly eminently encouragingly enduringly energetically engagingly
enigmatically enthusiastically enviously erratically essentially
eternally euphorically evasively evenly exceptionally excessively exhaustively
exotically expansively experimentally explosively expressively extravagantly
exuberantly fabulously facetiously factually fanatically fantastically fatally
favorably favourably feebly ferociously fervently feverishly fiendishly figuratively
financially fitfully flatly flawlessly fleetingly flexibly fluently forcibly
forlornly fortuitously fortunately frugally fruitlessly furtively
fussily gallantly garishly gaudily gingerly girlishly glibly globally glowingly
graphically gravely grossly gruesomely gruffly guardedly haltingly handsomely
harmlessly harshly hauntingly heartily heartlessly heatedly hideously
hoarsely hollowly homogeneously honorably honourably horizontally hungrily
idiotically ignominiously illicitly imaginatively immaculately immensely
imminently immovably impartially impassively impeccably imperceptibly imperiously
impertinently implausibly implicitly imploringly impossibly impressively
improbably inaccurately inadequately inappropriately incoherently incompletely
incongruously inconspicuously inconveniently indecisively indefinitely
indifferently indiscreetly indiscriminately individually industriously
ineffectively inefficiently inexorably infrequently ingeniously initially
injudiciously inquisitively insanely insecurely insensitively insistently
insolently intimately intolerably intriguingly intuitively invariably
involuntarily irrationally irrelevantly irreverently jealously jubilantly
judgmentally judgementally laboriously languidly lavishly legitimately leniently
lethargically liberally limply logically ludicrously lugubriously luminously
luridly lustily magnanimously majestically maliciously marginally markedly
marvelously marvellously masterfully materially maternally maturely meekly
melodramatically mercifully mercilessly methodically meticulously mightily
mindfully mindlessly miraculously monotonously morally morbidly morosely
motionlessly munificently mutely naively needlessly negligently nimbly
nonchalantly nominally nostalgically notably noticeably obnoxiously obstinately
ominously optimistically ordinarily ostentatiously overly overwhelmingly
painstakingly palely palpably paradoxically partially passively peculiarly
peevishly pensively perceptibly peremptorily permanently persistently persuasively
pessimistically petulantly phenomenally philosophically piercingly piously
piteously pitifully plausibly pleadingly poetically poignantly pointedly
ponderously pragmatically precariously predominantly prematurely preposterously
presumptuously primly principally profanely professionally profoundly profusely
progressively prohibitively proportionately protectively provocatively prudently
publicly pugnaciously punctually quaveringly querulously questioningly rationally
reassuringly regretfully regrettably reliably remarkably remotely reproachfully
resolutely resoundingly responsibly reverently rhythmically richly ridiculously
righteously rightfully rigorously robustly romantically ruefully ruthlessly
sardonically savagely scathingly scientifically scornfully sedately sensibly
sensitively sentimentally serenely shabbily shakily shamefully shamelessly
significantly simultaneously skilfully skillfully sloppily sluggishly soberly
socially soothingly sorrowfully soundlessly sourly spaciously sparsely specially
spectacularly speechlessly speedily spiritually splendidly spontaneously
sportingly starkly stealthily stoically stolidly stonily strategically
strenuously structurally studiously stunningly suavely subsequently substantially
subtly sufficiently sulkily superciliously superficially superfluously supremely
surlily tactfully tantalizingly tantalisingly tautly tensely
tentatively theatrically thirstily threateningly tonelessly
tragically transparently tremendously triumphantly trustingly truthfully
tunefully unaccountably unashamedly unavoidably unbearably uncannily
unceremoniously uncharacteristically uncomfortably unconcernedly unconditionally
unconsciously uncontrollably unconvincingly undeniably underhandedly uneasily
unenthusiastically unevenly unfaithfully ungraciously uniformly
unintentionally uninterestedly uniquely unjustly unknowingly unnaturally
unnecessarily unobtrusively unpleasantly unpredictably unreasonably unreservedly
unsteadily unsuccessfully unusually unwittingly usefully uselessly vacantly
valiantly verbally vertically victoriously vigilantly vigorously virtually
vocally voraciously vulnerably wanly watchfully weightily whimsically wholly
wilfully willfully wishfully witlessly wonderfully woodenly wordlessly
wryly zealously zestfully wholeheartedly lightheartedly singlehandedly
ably affably agitatedly agonizingly agonisingly amusedly antagonistically blandly
broodingly calculatingly cheerlessly confusedly disappointedly disinterestedly
dismissively drily dubiously duly exasperatedly exultantly glumly graciously humbly
idly insidiously irresolutely irritatingly jauntily joylessly listlessly obliquely
offhandedly passionlessly prosaically quizzically relievedly resignedly resentfully
sarcastically severely shrewdly sociably solely steadfastly surreptitiously tacitly
tauntingly tenaciously tremulously unblinkingly unflinchingly unhurriedly
unquestionably unthinkingly untruthfully venomously relatively moderately
exceedingly unbelievably extraordinarily naïvely cooperatively
'''.split())
ADVERB_COMPOUNDS=(
    'self-consciously','self-confidently','self-importantly','self-righteously',
    'half-heartedly','whole-heartedly','good-naturedly','ill-naturedly',
    'light-heartedly','matter-of-factly','absent-mindedly','single-mindedly',
    'single-handedly',
)
AMBIGUOUS_LY=frozenset('daily weekly monthly yearly hourly quarterly nightly fortnightly'.split())

QUALIFIERS_EXTRA=frozenset('virtually practically relatively moderately considerably exceptionally exceedingly excessively terribly awfully unbelievably incredibly utterly completely totally absolutely immensely enormously extraordinarily particularly especially sufficiently notably remarkably noticeably barely scarcely mildly partially predominantly largely mostly approximately roughly'.split())
QUALIFIER_PHRASES=('a little','a little bit','a tiny bit','a bit','sort of','kind of','to some extent','to a certain extent','more or less')
MODIFIERS_EXTRA=frozenset('''
able absurd accurate adequate alive alone amused amusing annoyed annoying
appropriate attractive available aware awful awkward bitter boring brave brief
busy calm capable cheerful chilly clean cloudy coarse complete delightful
complex concerned considerable content convincing correct cruel curious damp
dead deaf decent delicate dense depressed depressing desperate determined dirty
disgusting distant doubtful dry dull efficient elaborate embarrassed
empty enormous entertaining essential evil exact excellent excessive
exhausted exhausting familiar famous fascinating fierce filthy fine firm flat
fluent foolish formal fragile fresh frightened frightening full funny generous
gentle genuine glad gloomy gorgeous graceful grateful great healthy heavy
helpful honest horrible hungry hurt ill impatient impressive inadequate insecure
intense jealous keen lazy light limited lively logical loose lucky mad massive
meaningful miserable modern modest moist muddy narrow nasty natural neat necessary
normal obscure ordinary original painful pale patient peaceful perfect pleasant
polite poor popular powerful practical precious precise predictable prepared
pretty private probable proper pure puzzled quick rare ready realistic reasonable
recent relaxed reluctant remote responsible rich ridiculous rigid ripe rough round
rural rusty satisfied satisfying selfish sensitive serious sharp shocked
shocking sick silent silly similar sincere skilled sleepy slim smart smooth soft
solid sore sorry specific stable steep stiff strange strict successful
suitable suspicious tense tender terrible thick thin thirsty tight
tiny tolerant tough tricky troubled true trustworthy unfair unhappy unique
unpleasant urgent vague valuable vast violent visible vivid wealthy wet wide
wild willing wonderful worse worst worthy wrong
'''.split())

EMOTION_NOUNS_EXTRA=frozenset('''
agitation amazement ambivalence anguish animosity apathy apprehension astonishment
bewilderment boredom calm compassion concern confidence consternation contentment
dejection desolation desperation determination devotion disapproval discontent
distress distrust eagerness ecstasy elation empathy enthusiasm exasperation
exhilaration fondness gratitude helplessness homesickness humiliation impatience
indifference indignation insecurity inspiration loathing longing melancholy
nervousness nostalgia optimism outrage pessimism repulsion satisfaction
serenity shyness skepticism scepticism sympathy tenderness trepidation triumph
unease uncertainty unhappiness vulnerability yearning
'''.split())
EMOTION_ADJECTIVES_EXTRA=frozenset('''
agitated amazed ambivalent anguished apathetic apprehensive astonished bewildered
bored calm compassionate concerned confident content crestfallen dejected
desolate desperate despondent distressed distrustful eager ecstatic elated
empathetic enthusiastic exasperated exhilarated forlorn grateful helpless
homesick humiliated impatient indifferent indignant insecure inspired irate
melancholy nostalgic optimistic outraged overwhelmed pessimistic reassured
satisfied serene shy skeptical sceptical stunned suspicious sympathetic tense
troubled triumphant uneasy uncertain unhappy unsettled vulnerable wistful
happier happiest sadder saddest angrier angriest calmer calmest lonelier loneliest
prouder proudest gloomier gloomiest shyer shyest
'''.split())

WORDY_EXTRA=(
    'owing to the fact that','despite the fact that','regardless of the fact that',
    'in view of the fact that','by virtue of the fact that','on the grounds that',
    'for the simple reason that','for the reason that','in the event that',
    'in the eventuality that','at such time as','at the present time',
    'at the present moment','at this moment in time','at a later date',
    'at an earlier point in time','during the course of','in the course of',
    'over the course of','for a period of','in a timely manner','in close proximity to',
    'with respect to','with reference to','with regards to','in relation to',
    'in connection with','as a consequence of','for the purpose of',
    'with the intention of','with the aim of','in an attempt to',
    'a sufficient number of','a total of','a period of time','the vast majority of',
    'in actual fact','as a matter of fact','as far as I am concerned',
    'as far as we are concerned','from my point of view','from our point of view',
    'it goes without saying that','it should be noted that','it must be noted that',
    'it is worth noting that',"it's worth noting that",'the question as to whether',
    'has the ability to','had the ability to','have the ability to',
    'make a decision','made a decision','making a decision',
    'reach a conclusion','reached a conclusion','take into consideration',
    'took into consideration','taking into consideration',
)
REDUNDANCIES_EXTRA=(
    'reverts back','reverting back','advance warning','advance reservations',
    'previous history','new innovation','original prototype',
    'completely empty','entirely empty','absolutely essential','absolutely necessary',
    'completely destroyed','totally finished','fully complete','entirely complete',
    'completely unique','totally unique','very unique',
    'completely eliminate','completely eliminated','completely eliminates',
    'completely eliminating','completely surrounded','entirely surrounded',
    'unanimous consensus','joint collaboration','mutual cooperation',
    'combine together','combines together','combined together','combining together',
    'merge together','merges together','merged together','merging together',
    'join together','joins together','joined together','joining together',
    'mix together','mixed together','mixing together','gather together','gathered together',
    'assemble together','assembled together','surrounded on all sides',
    'small in size','large in size','few in number','many in number',
    'exact duplicate','consensus of opinion','sum total','final conclusion',
    'ultimate outcome',
)
