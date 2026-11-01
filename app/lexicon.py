# -*- coding: utf-8 -*-
"""Многоязычный словарь оскорблений для фильтра Евы.

Здесь ТОЛЬКО оскорбления человека. Обычный мат без адресата
(«блять», «пиздец», «fuck», «shit», «kurwa») в словарь не входит
и нарушением не считается.

Ссылки, домены, @упоминания и ники нарушением быть не могут —
они вырезаются до проверки (см. moder.strip_noise).

Формат записи:
    "слово"    — слово целиком, с границами
    "корень*"  — начало слова, любое окончание

Категории:
    HARD — оскорбление в любом контексте
    SOFT — грубое слово, нарушение только когда сказано в адрес человека
           («крыса в подвале» — чисто, «ты крыса» — нарушение)
    SUB  — языки без пробелов (китайский, японский, корейский, тайский …),
           ищем подстроку в тексте
    PHRASES — готовые фразы-оскорбления регулярками
    SECOND  — обращения «ты/вы» на разных языках
    CORE    — ядра для поиска опечаток (расстояние Левенштейна)
"""

# ---------------------------------------------------------------------------
# слова, внутри которых оскорбления искать нельзя (ложные срабатывания)
# ---------------------------------------------------------------------------
EXCEPTIONS = {
    # русский: обычные слова, похожие на оскорбления
    "тупик", "тупики", "тупика", "тупике", "тупику", "тупиков", "тупиком",
    "тупиковый", "тупиковая", "тупиковое", "тупиковые", "тупичок", "тупит",
    "тупить", "тупо", "тупость", "тупизм", "турусы",
    "мандарин", "мандарины", "мандарина", "мандаринка", "мандаринки",
    "мандариновый", "педикюр", "педикюра", "педикюре", "педикюрный",
    "педикюрша", "херсон", "херсона", "херсоне", "херувим", "херувимы",
    "херес", "хереса", "дурацкий", "дурацкая", "дурацкое", "дурацкие",
    "дурашка", "дурашкой", "дурочка", "дурочкой", "дурачок", "дурашка",
    "турник", "турника", "турнике", "мандала", "мандале",
    "хачапури", "хохолок", "хохолки", "хаммер", "хаммера",
    "педикюру", "педикюром", "педикюры", "педикюрами",
    "тупости", "тупостью", "тупизна", "тупицами", "тупицах",
    "турникет", "турникеты", "трактор", "ракетой",
    "козлик", "козлика", "козлики", "козлята", "козлёнок", "козленок",
    "козочка", "козлиный", "козловый", "козлов", "козла", "козлит",
    "баранина", "баранины", "баранка", "баранки", "баранок", "баранье",
    "бараний", "барашки", "барашка", "барашке",
    "свинина", "свинины", "свинка", "свинки", "свинтус", "свинство",
    "крысятник", "крысятника", "крысятничество", "крысиный", "крысиная",
    "крыска", "крыской", "крысонька", "крысята", "крысёнок",
    "оселки", "ослиный", "ослиная",
        "индюк", "индюка", "индюки", "индейка", "индейки",
    "лохматый", "лохматая", "лохматое", "лохматые", "лохмы", "лохмач",
    "чурбан", "чурбана", "чурбаны", "чурбачок",
    "турник", "рак", "раки", "рака", "раков", "раку",
    "ракета", "ракетка", "ракетки", "ракетка",
    "дно", "донный", "донная", "дна", "дном",
    "пеньки", "пенёк", "пенек",
    "мусор", "мусора", "мусорка", "мусорный", "мусорка",
    "позор", "позора", "позорище", "позорный", "позорно",
    "кринж", "кринжа", "кринжовый", "кринжово",
    "баба", "бабка", "бабушка", "бабой", "бабу", "бабы", "бабки",
    "девка", "девки", "девкой", "девчонка", "девчонки",
    "сынок", "сынка", "сынки", "сыночек", "доченька",
    "цель", "цели", "целью", "цели", "целей",
    "хер", "хера", "херу", "хером", "херня", "херни", "херню", "херовый",
    # другие языки
    "target", "targets", "safe", "safety", "duration", "champion",
    "champions", "chicken", "chickens", "creeper", "creepers",
    "punk", "punks", "spunk", "trunk", "drunk", "hunk", "chunk",
    "glass", "class", "brass", "grass", "pass", "mass", "ass" "assin",
    "idiotype", "idiotic", "idiotically",
    "dumbwaiter", "dumbbell", "dumbbells", "dumpling", "dumplings",
    "foolscap", "foolishness", "foolproof",
    "losers", "losership", "noob", "noobs", "noobish",
        "con", "cons", "conte", "contes", "connu", "conduite",
    "animal", "animals", "bestia", "bestias",
    "mensa", "mensal", "mental", "mentor",
    "kaka", "kakao", "kakashi",
    "hijo", "hija", "hijos", "hijas",
    "puta", "putas", "putain", "pute",
    "casse", "casser", "cassos", "cassette",
    "tanga", "tangerine", "tango", "tangled",
        "baca", "bacak", "bacaklar", "bacağı",
    "dada", "dadaism", "dado", "dados",
    "momo", "momok", "momos",
    "pipa", "pipas", "pipi", "pipo",
    "koko", "kokos", "kokoro", "kokosy",
}

# ---------------------------------------------------------------------------
# славянские языки
# ---------------------------------------------------------------------------
HARD_RU = [
    # прямые оскорбления личности — корни
    "дебил*", "дибил*", "дегенерат*", "кретин*", "критин*", "придур*",
    "недоум*", "тупоум*", "тупорыл*", "тупоголов*", "тупеньк*", "туповат*",
    "тупизн*", "туп*", "идиот*", "имбецил*", "олигофрен*", "слабоумн*",
    "ничтожеств*", "мраз*", "гнид*", "быдл*", "отброс*", "чмо*", "урод*",
    "тварь", "твари", "тварина", "тварюг*", "тварёныш", "тварей",
    "выродок*", "подон*", "сволоч*", "мерзав*", "паскуд*", "падл*",
    "лошар*", "лохуш*", "дебилоид*", "пидор*", "пидар*", "педераст*",
    "гомик*", "долбоёб*", "долбоеб*", "мудак*", "мудил*", "мудозвон*",
    "еблан*", "ублюд*", "выблядок*", "хуесос*", "хуеплёт*", "хуеплет*",
    "шлюх*", "шалав*", "проститут*", "дрян*", "шваль", "шавк*",
    "мразиш*", "негодя*", "стерв*", "поган*", "гадин*",
    "дебильн*", "кретинизм*", "идиотизм*", "аутист*", "даун*", "инвалид*",
    "калек*", "хромоног*", "косорук*", "криворук*", "бездар*", "скотин*",
    "шакал*", "гиен*", "падлюг*", "поганк*",
    "гандон*", "гондон*", "мандавошк*", "пидорас*", "педик*", "петух*",
    "опущен*", "чушпан*", "лох*", "чмош*", "шантрап*", "рвань*",
    "подстилк*", "прихвост*", "прилипал*", "подпевал*", "шестёрк*",
    "шестерк*", "холуй*", "холуя*", "прислуж*", "лакей*", "лизоблюд*",
    "жополиз*", "подхалим*", "хамл*", "хам", "хама", "хаму", "хамом",
    "хамка", "хамки", "наглец*", "нахал*",
    # расизм и травля по признаку — тоже оскорбления
    "чурк*", "хач", "хача", "хачи", "хачик*", "нигер*", "ниггер*", "нигга*",
    "узкоглаз*",
    "черножоп*", "черномаз*", "хохол*", "москал*", "бульбаш*", "пиндос*",
    "кацап*", "жид", "жида", "жиды", "жидов", "жидок", "цыган*", "армяшк*",
    "гомосек*", "педик", "педики", "педиков",
    # прилагательные-оскорбления
    "тупой", "тупая", "тупое", "тупые", "тупых", "тупому", "тупым",
    "тупыми", "тупом", "тупенький", "туповатый", "тупоголовый", "тупорылый",
    "глупый", "глупая", "глупые", "глупых", "жалкий", "жалкая", "жалкие",
    "убогий", "убогая", "убогие", "никчёмный", "никчемный", "ничтожный",
    "ничтожная", "жалок", "убог", "конченый", "конченая", "конченные",
    "отмороженный", "отморозок*", "дебильный", "днище", "днища", "днище*",
    # требования и пожелания смерти в адрес человека — считаются в любом виде
    "заткнись", "заткнитесь", "заткнулся", "заткнисьже", "закройся",
    "сдохни", "сдохните", "сдохниже", "умри", "умрите", "сгинь", "сгиньте",
    "проваливай", "проваливайте", "выметайся", "выматывайся", "исчезни",
    "отвали", "отвянь", "отвяжитесь", "свали", "свалите", "катись",
    "кукуй", "молчи", "помолчи", "замолчи", "цыц",
]

HARD_UK = [
    "ідіот*", "ідот*", "дебіл*", "придурок*", "кретин*", "мудак*", "гівнюк*",
    "падлюк*", "паскуд*", "покидьок*", "покидько", "нікчем*", "наволоч*",
    "мерзот*", "гнид*", "підонок*", "виродок*", "довбойоб*", "їблан*",
    "підар*", "педер*", "шльондр*", "курв*", "туп*", "дурн*", "дурень",
    "дурепа", "дурник*", "бидл*", "шваль", "лайдак*", "ледар*", "бовдур*",
    "божевільн*", "ненормальн*", "дегенерат*", "ідіотизм*", "тупак*",
    "пентюх*", "холоп*", "холуй*", "прихвос*", "шакал*", "сволот*",
    "заткнись", "замовкни", "здохни", "пішов геть", "геть звідси",
    "ідіот", "дурак", "дура", "дурень",
]

HARD_BE = [
    "ідот*", "дурань", "дурень", "прыдурак*", "дэбіл*", "крыцін*", "мудак*",
    "сволоч*", "падлюк*", "вылюдак*", "нягодн*", "туп*", "дурн*", "балбес*",
    "дур*", "заткніся", "здохні",
]

HARD_PL = [
    "idiot*", "debil*", "kretyn*", "głupek", "głupk*", "dureń", "tuman*",
    "ciemniak*", "imbecyl*", "głąb*", "pajac*", "błazen*", "nieudacznik*",
    "przegryw*", "frajer*", "łajza", "ciota*", "pedał*", "kurdupel*",
    "gnojek*", "szmata*", "słabeusz*", "głupi", "głupia", "głupie", "głupszy",
    "wieśniak*", "debilizm*", "kretynizm*", "zjełczały", "wsiok*",
    "odmieniec*", "menel*", "żul*", "pijak*", "spierdalaj", "zamknij się",
    "wypierdalaj", "zdechnij", "wygłup*",
]

HARD_CS = [
    "idiot*", "debil*", "kretén*", "kreten*", "blbec*", "blbá", "blbý",
    "blbá", "vůl", "vůl*", "hovado*", "svině", "prase", "sprosták*",
    "zmrd*", "dement*", "pitomec*", "mamlas*", "smejd*", "sráč*", "buzerant*",
    "teplouš*", "kripl*", "vypadni", "sklapni", "zdechni", "chcípni",
]

HARD_SK = [
    "idiot*", "debil*", "kretén*", "blbec*", "sprosták*", "kokot*", "zmrd*",
    "hovado*", "sviňa", "prasa", "dement*", "mamlas*", "smejd*", "buzerant*",
    "kripl*", "vypadni", "sklapni", "zdechni", "chcípni", "truľo", "trulo",
]

HARD_BG = [
    "идиот*", "дебил*", "кретен*", "глупак*", "тъпак*", "тъп*", "малоум*",
    "олух*", "простак*", "говедо*", "копеле*", "мръсник*", "боклук*",
    "п*дал*", "педераст*", "изрод*", "нищожеств*", "мерзав*", "нещастник*",
    "скот*", "животно*", "затваряй си устата", "млъкни", "млък", "умри",
    "изчезни", "махай се", "идиот", "глупак",
]

HARD_SR = [
    "идиот*", "дебил*", "кретен*", "глупан*", "будала*", "сељак*", "говно*",
    "смотан*", "шљука*", "курв*", "магарац*", "свиња", "крмак*", "пацов*",
    "злоћа*", "ништав*", "бедник*", "гадов*", "копиле*", "педер*",
    "затвори уста", "умукни", "цркни", "нестани",
]

HARD_HR = [
    "idiot*", "debil*", "kreten*", "glupan*", "budala*", "seljak*", "smeće",
    "šljam*", "kurv*", "magarac*", "svinja", "peder*", "kopile*", "gad*",
    "ništarij*", "bijednik*", "umukni", "začepi", "crkni", "gubi se",
]

HARD_SL = [
    "idiot*", "debil*", "kreten*", "bedak*", "butelj*", "tepec*", "gnoj*",
    "prase", "svinja", "peder*", "kurba*", "zapri usta", "utišaj se",
    "crkni", "izgini",
]

HARD_MK = [
    "идиот*", "дебил*", "кретен*", "глупак*", "будала*", "магаре*", "свиња",
    "ѓубре*", "ништожник*", "замолчи", "умри", "бегај",
]

HARD_LT = [
    "idiot*", "debil*", "kvailys*", "durnius*", "kretin*", "bukapročiai",
    "šūdžius*", "nenaudėl*", "bjauryb*", "atstumt*", "tylėk", "nutilk",
    "eik šalin",
]

HARD_LV = [
    "idiots*", "debil*", "muļķis*", "duraks*", "kretīns*", "stulbenis*",
    "nekaun*", "netikš*", "apklusti", "rāvi", "pazūdi",
]

HARD_ET = [
    "idioot*", "debiilik*", "lollakas*", "loll", "sитt", "sitapea*", "tobu*",
    "jobu*", "mölakas*", "väärakas*", "vaiki", "kao ära",
]

# ---------------------------------------------------------------------------
# латиница: германские, романские, финно-угорские, тюркские, ЮВА
# ---------------------------------------------------------------------------
HARD_EN = [
    "idiot*", "moron*", "imbecile*", "retard*", "dumbass*", "dumb", "stupid*",
    "loser*", "jerk*", "scumbag*", "jackass*", "blockhead*", "bonehead*",
    "numbskull*", "numskull*", "halfwit*", "nitwit*", "dimwit*", "simpleton*",
    "asshole*", "arsehole*", "bitch*", "bitches", "bastard*", "dipshit*", "douchebag*", "douche*",
    "dickhead*", "dickwad*", "shithead*", "dumbfuck*", "fucktard*", "fuckface*",
    "motherfucker*", "twat*", "prick*", "wanker*", "git", "berk", "plonker*",
    "pillock*", "bellend*", "knobhead*", "arse*", "bollock*", "prat*",
    "worthless*", "pathetic*", "freak*", "weirdo*", "wimp*", "sissy*",
    "coward*", "noob*", "newbie*", "scrub*", "cuck*", "simp*", "incel*",
    "neckbeard*", "fatass*", "lardass*", "tubby*", "brainless*",
    "mouthbreather*", "cretin*", "dullard*", "buffoon*", "clown*", "fool*",
    "foolish*", "clueless*", "hopeless*", "useless*", "garbage", "trash",
    "kys", "brainlet*", "spastic*", "spaz*", "sperg*", "autist*", "mongoloid*",
    "schizo*", "psycho*", "snowflake*", "manchild*", "crybaby*",
    "nigger*", "nigga*", "negro*", "coon*", "cracker*", "redneck*", "chink*",
    "gook*", "spic*", "wetback*", "kike*", "raghead*", "towelhead*",
    "gyppo*", "wop*", "beaner*", "sandnigger*", "transexual*", "tranny*",
    "faggot*", "fag*", "dyke*", "homo", "queer*", "shut up", "shutup",
    "kill yourself", "go die", "nobody cares", "you suck", "you're trash",
]

HARD_DE = [
    "idiot*", "trottel*", "dummkopf*", "vollidiot*", "spast*", "spasti*",
    "hirni*", "depp*", "blödmann*", "blöd*", "arschloch*", "arsch*",
    "wichser*", "hurensohn*", "hure*", "missgeburt*", "schwuchtel*", "fotze*",
    "spinner*", "versager*", "lutscher*", "lappen*", "pfeife*", "vollpfosten*",
    "hornochse*", "einfaltspinsel*", "dödel*", "flachzange*", "honk*",
    "dumm*", "dümm*", "blöde*", "penner*", "assi*", "opfer*", "huso*",
    "huan*", "schnauze", "halt die fresse", "verpiss dich", "verrecke",
]

HARD_NL = [
    "idioot*", "sukkel*", "dummkopf*", "pannenkoek*", "klootzak*", "eikel*",
    "hufter*", "mongool*", "debiel*", "achterlijk*", "dom", "domme", "dommer*",
    "stomme", "stommerd*", "lul*", "kut*", "tyfus*", "kankerlijer*", "hoer*",
    "flikker*", "mietje*", "loser*", "faalhaas*", "prutser*", "uilskuiken*",
    "ezel*", "varken*", "houd je mond", "oprotten", "krijg de tyfus",
]

HARD_SV = [
    "idiot*", "dumhuvud*", "dumbom*", "pucko*", "skitstövel*", "dum", "dumma",
    "dummare", "dummer*", "jävel*", "svin*", "kärring*", "fjant*", "tönt*",
    "nolla", "skit*", "fitta*", "kuk*", "hora*", "bög*", "mongo*",
    "efterbliven*", "håll käften", "dö", "dra åt helvete",
]

HARD_NO = [
    "idiot*", "drittsekk*", "tulling*", "dust*", "dum", "dumme", "dummere",
    "dumming*", "fjols*", "svin*", "jævel*", "faen*", "hore*", "bøg*",
    "mongo*", "tilbakestående*", "hold kjeft", "dø", "stikk av",
]

HARD_DA = [
    "idiot*", "dumrian*", "tosse*", "fjols*", "dum", "dumme", "dummere",
    "svin*", "luder*", "bøsserøv*", "mongol*", "hold kæft", "skrid",
]

HARD_IS = [
    "hálfviti*", "fáviti*", "bjáni*", "asni*", "durgur*", "kjáni*", "hálfvit*",
    "heimskingi*", "letihaugur*", "þegiðu", "farðu",
]

HARD_ES = [
    "idiota*", "imbecil*", "imbécil*", "estupido*", "estúpido*", "pendejo*",
    "pendeja*", "gilipollas*", "tarado*", "zopenco*", "menso*", "baboso*",
    "cabrón*", "cabron*", "puto*", "puta*", "hijo de puta", "hdp", "polla*",
    "capullo*", "maricón*", "marica*", "joto*", "culero*", "verga*",
    "chinga*", "naco*", "mamón*", "tonto*", "tonta*", "bruto*", "retrasado*",
    "bobo*", "burro*", "tarugo*", "zoquete*", "canalla*", "sinvergüenza*",
    "chupapollas*", "carepicha*", "malparido*", "gonorrea*", "pirobo*",
    "esclavo*", "muerto de hambre", "callate", "cállate", "vete a la mierda",
    "ojalá te mueras", "lárgate",
]

HARD_FR = [
    "idiot*", "imbécile*", "imbecile*", "crétin*", "cretin*", "abruti*",
    "débile*", "debile*", "taré*", "connard*", "connasse*", "salaud*",
    "salope*", "enculé*", "encule*", "bâtard*", "batard*", "merdeux*",
    "branleur*", "bouffon*", "clown*", "naze", "nul", "nulle", "minus*",
    "raté*", "trou du cul", "fdp", "tapette*", "pédé*", "pouffiasse*",
    "boudin*", "moche", "cassos*", "bolos*", "cruche*", "andouille*",
    "dégénéré*", "attardé*", "demeuré*", "espèce de", "ferme ta gueule",
    "ta gueule", "va te faire", "crève", "casse-toi",
]

HARD_IT = [
    "idiota*", "imbecille*", "stupido*", "stupida*", "cretino*", "coglione*",
    "deficiente*", "scemo*", "scema", "sciocco*", "cazzone*", "stronzo*",
    "bastardo*", "puttana*", "troia*", "zoccola*", "frocio*", "finocchio*",
    "ricchione*", "merdoso*", "testa di cazzo", "pezzo di merda", "mongolo*",
    "ritardato*", "handicappato*", "babbeo*", "tonto*", "fesso*",
    "coglionazzo*", "sfigato*", "pirla", "deficiente", "stai zitto", "taci",
    "vattene", "crepa",
]

HARD_PT = [
    "idiota*", "imbecil*", "imbécil*", "estúpido*", "estupido*", "otário*",
    "otario*", "burro*", "babaca*", "filho da puta", "fdp", "puta*",
    "viado*", "veado*", "bicha*", "corno*", "cornudo*", "escroto*",
    "trouxa*", "panaca*", "mané*", "retardado*", "débil*", "cretino*",
    "palerma*", "parvo*", "parva*", "arrombado*", "cuzão*",
    "cuzao*", "desgraçado*", "desgracado*", "cala a boca", "cai fora",
    "vai se ferrar",
]

HARD_RO = [
    "idiot*", "imbecil*", "cretin*", "dobitoc*", "nerod*", "tâmpit*",
    "tampit*", "bou*", "măgar*", "magar*", "curvă*", "curva*", "ticălos*",
    "nesimțit*", "nesimtit*", "secătură*", "nemernic*", "prăpădit*",
    "lepră*", "prost*", "proastă*", "handicapat*", "retardat*", "târfă*",
    "tarfă*", "poponar*", "bulangiu*", "muian*", "taci", "taci din gură",
    "du-te dracului", "mor", "sictir",
]

HARD_CA = [
    "idiota*", "imbècil*", "imbecil*", "estúpid*", "estupid*", "gilipollas*",
    "cabró*", "fill de puta", "burro*", "babau*", "totxo*", "capsigrany*",
    "tarambana*", "ximple*", "sòmines", "mocós*", "beneit*", "callа",
    "cala la boca", "ves a la merda",
]

HARD_FI = [
    "idiootti*", "tyhmä*", "typerys*", "älykääpiö*", "pelle*", "paskiainen*",
    "kusipää*", "mulkku*", "läski*", "vajaa*", "juntti*", "hölmö*",
    "tolvana*", "tyhmyri*", "paska*", "huora*", "homo", "vammainen*",
    "turpa kiinni", "häivy", "kuole",
]

HARD_HU = [
    "idióta*", "idiota*", "hülye*", "barom*", "marha*", "bunkó*", "gyökér*",
    "faszfej*", "seggfej*", "köcsög*", "kurva*", "geci*", "ribanc*", "buzi*",
    "csicska*", "tahó*", "balfasz*", "hülyegyerek*", "fogyatékos*", "kretén*",
    "fogd be a szád", "húzz el", "dögölj meg",
]

HARD_TR = [
    "aptal*", "salak*", "gerizekalı*", "gerizekali*", "ahmak*", "beyinsiz*",
    "ezik*", "şerefsiz*", "serefsiz*", "orospu*", "piç*", "gavat*", "kaltak*",
    "sürtük*", "ibne*", "dangalak*", "mankafa*", "kuşbeyinli*", "enayi*",
    "andaval*", "budala*", "öküz*", "eşek*", "yavşak*", "gerizekalı",
    "aptal", "salak", "sus", "kes sesini", "defol", "geber",
]

HARD_AZ = [
    "axmaq*", "səfeh*", "sərsəm*", "gic*", "kütbeyin*", "eşşək*", "öküz*",
    "fahişə*", "oğraş*", "gicbeyin*", "sus", "çıx get", "geber",
]

HARD_KK = [
    "ақымақ*", "ақымак*", "ақмақ*", "есуас*", "надан*", "ақылсыз*", "мисыз*",
    "мақау*", "оғаш*", "желөк*", "сөйлеме", "кет", "өл",
]

HARD_KY = [
    "акмак*", "ақмак*", "эси жок*", "эсижок*", "келесоо*", "макоо*",
    "акылсыз*", "сүйлөбө", "кет", "өл",
]

HARD_UZ = [
    "ahmoq*", "axmoq*", "esiz*", "aqlsiz*", "tentak*", "jinni*", "sassiq*",
    "harom*", "indamay", "ket", "o'l",
]

HARD_TK = [
    "akylsyz*", "samşyk*", "samsyk*", "aňkaw*", "haýwan*", "deňişme", "git",
]

HARD_ID = [
    "bodoh*", "goblok*", "tolol*", "bego*", "idiot*", "bangsat*", "anjing*",
    "kontol*", "memek*", "kimak*", "pukimak*", "setan*", "biadab*", "sialan*",
    "brengsek*", "keparat*", "bajingan*", "cacat*", "sarap*", "tai", "monyet*",
    "babi*", "kadal*", "kampungan*", "diam", "tutup mulut", "pergi",
    "mati",
]

HARD_MS = [
    "bodoh*", "bodo*", "bangang*", "sial*", "celaka*", "bajingan*", "keparat*",
    "pukimak*", "kimak*", "sundal*", "pelacur*", "haramjadah*", "binatang*",
    "monyet*", "babi*", "sampah*", "diam", "tutup mulut", "pi*ah",
]

HARD_VI = [
    "ngu", "ngốc", "ngoc", "đần", "dan", "dốt", "dot", "khốn", "khon",
    "điên", "dien", "chó", "cho", "đĩ", "di", "cặc", "lồn", "óc", "oc",
    "thằng", "đồ", "vô học", "mất dạy", "óc chó", "não chó", "đầu bò",
    "thần kinh", "dở hơi", "im đi", "cút", "chết đi",
]

HARD_TL = [
    "bobo", "tanga", "ulol", "gago", "gaga", "tarantado", "engot", "hayop",
    "pakyu", "peste", "bruha", "leche", "lintek", "inutil", "walang kwenta",
    "tumigil ka", "mamatay ka", "putangina*", "putang ina",
]

HARD_SW = [
    "mjinga*", "mpumbavu*", "pumbavu*", "mshenzi*", "malaya", "mnyama",
    "kenda", "kufa", "nyamaza", "toka",
]

HARD_AF = [
    "idioot*", "dom", "dwaas*", "poephol*", "bliksem*", "skelm*", "verraaier*",
    "jou ma", "hou jou bek", "vrek",
]

HARD_SQ = [
    "idiot*", "budalla*", "budalle*", "bythë*", "matrapik*", "gomar*",
    "kuçedër*", "palaco*", "hajvan*", "qyqe", "mbylle gojën", "ik",
]

HARD_EL = [
    "ηλίθιος", "ηλίθια", "βλάκας", "μαλάκας*", "μαλακισμένο*", "κόπανος",
    "χαζός*", "κουτός", "παλιάνθρωπος", "καθίκι", "πουτάνα*", "πούστης*",
    "μουνόπανο", "ζώο", "γουρούνι", "σκάσε", "άντε γαμήσου", "ξεκουμπίσου",
]

HARD_MN = [
    "тэнэг*", "ахьмаг*", "ахмад*", "нохой*", "муу*", "төөрөг*", "сармагчин*",
    "хөглөг*", "амаа хат", "замхар", "үх",
]

# ---------------------------------------------------------------------------
# грубые слова, которые ВСЕГДА оскорбление (человек назван кем-то плохим)
# ---------------------------------------------------------------------------
SOFT = [
    # русский
    "клоун*", "нуб*", "нубло*", "слабак*", "малолетк*", "сосунок*", "сопляк*", "соплячк*", "нытик*", "плакс*", "задрот*", "бездар*", "посредственност*",
    "пустышк*", "пустозвон*", "лошок*", "лох", "лохи", "лоха", "лоху",
    "лохом", "лохе", "лошки", "тугодум*", "недотёп*", "недотеп*", "растяп*",
    "разиня", "тюфяк*", "слюнтяй*", "маменькин*", "слабонервн*",
    "неудачник*", "неудачниц*", "аутсайдер*", "придурошн*", "дурашк*",
    "дурочк*", "дурачок*", "балбес*", "болван*", "олух*", "обалдуй*",
    "охламон*", "шалопай*", "пискляв*", "визглив*", "подкаблучник*",
    "чушпан*", "шутил*", "блазен*", "дурненьк*", "невдаха*", "недоумк*",
    "недалёк*", "недалек*", "туповат", "придурок", "дебилушка*",
    "лохушк*", "чмошн*", "недочеловек*", "полудурок*", "дебилоид",
    # латиница
    "noob*", "noobish*", "loser*", "crybaby*", "squeaker*", "clown*",
    "fool", "fools", "foolish", "punk", "brat*", "weirdo", "cringe",
    "dum", "dumme", "dumm*", "dimwit*",
    "dullard*", "pushover*", "doormat*", "snowflake*", "whiner*",
    "wannabe*", "tryhard*", "edgelord*", "kötü", "ezik", "beceriksiz",
    "korkak", "tembel", "doof", "feige", "memme", "heulsuse*", "tonto*",
    "bobo", "torpe", "cobarde*", "llorón*", "lloron*", "inútil", "inutil",
    "novato*", "faible", "lâche", "lache", "pleurnichard*",
    "nouille", "blaireau*", "sfigato*", "scemo*", "piagnucolone*",
    "imbranato*", "medroso*", "chorão*", "fracote", "bobalhão*", "slaboch*",
    "změkčil*", "ňouma*", "trouba", "břídil", "amatér*",
    "gerizekalı", "ahmak", "beceriksiz", ]

# ---------------------------------------------------------------------------
# грубые слова с двойным смыслом: нарушение ТОЛЬКО в адрес человека
# («крыса в подвале» — чисто, «ты крыса» — нарушение)
# ---------------------------------------------------------------------------
SOFT_TARGET = [
    # животные и предметы — сами по себе не оскорбление
    "баран*", "свинь*", "куриц*", "крыс*", "таракан*", "козёл", "козел",
    "козлы", "козла", "овца", "овцы", "овцой", "курица", "тюлен*", "пень",
    "пни", "индюк*", "хомяк*", "хомячок*", "попугай*", "телят*", "бичок*",
    "щупленьк*", "миршав*", "шакал", "шавка", "ишак*", "осел",
    "школьник*", "школьниц*", "жирн*", "жиртрест*", "карлик*",
    "тряпк*", "лицемер*", "подлиза*", "trash", "garbage",
    "kid", "kiddo", "paquete", "lama*",

    "ослы", "ослица", "ослик*", "баран", "бараны",
    "бомж*", "бомжара", "бомжатник",
    # двойные по смыслу слова
    "шкет*", "малышн*", "мелк*", "девк*", "баб*", "сынок*", "мамкин*",
    "мамсик*", "дно", "днище", "мусор*", "помойк*", "позор*", "кринж*",
    "шут*", "смешн*", "синяк*", "тормоз*", "читер*", "хакер*", "кемпер*",
    "тролль*", "проигравш*", "изгой*", "чужак*", "образин*", "уродина",
    "страхолюд*", "злюк*", "жадин*", "зануд*", "хвастун*", "врун*",
    "болтун*", "предател*", "ябед*", "подхалим", "лгун*", "клеветник*",
    "сплетник*", "ленив*", "соня", "обжор*", "грязнул*", "нерях*",
    # латиница
    "rat", "rats", "pig", "pigs", "sheep", "donkey", "monkey*", "chicken",
    "hendrix", "spinner", "angsthase*", "metomentodo*",
    "chismoso*", "llorona", "paquete", "poule mouillée", "tchórz",
    "ciamajda", "fajtłapa", "niezdara", "nowicjusz*", "bázlivec*", "srab*",
]

# ---------------------------------------------------------------------------
# письменности без пробелов: ищем подстроку
# ---------------------------------------------------------------------------
SUB_ZH = [
    "傻逼", "傻b", "傻x", "傻叉", "煞笔", "沙比", "白痴", "蠢货", "蠢猪",
    "蠢驴", "弱智", "智障", "脑残", "废物", "饭桶", "笨蛋", "呆子", "傻子",
    "二逼", "二货", "逗逼", "神经病", "精神病", "有病吧", "混蛋", "王八蛋",
    "狗东西", "狗杂种", "杂种", "贱人", "贱货", "婊子", "妓女", "妓",
    "畜生", "禽兽", "去死", "死全家", "你妈的", "你妈", "妈个", "操你妈",
    "草泥马", "尼玛", "滚蛋", "滚开", "闭嘴", "垃圾人", "垃圾东西",
    "狗娘养的", "猪脑子", "四眼", "脑瘫", "残废", "智障儿童", "低能",
]

SUB_JA = [
    "馬鹿", "ばか", "バカ", "アホ", "あほ", "阿呆", "間抜け", "まぬけ",
    "ボケ", "クズ", "カス", "ゴミ", "死ね", "しね", "くたばれ", "きもい",
    "キモい", "キモイ", "ブス", "デブ", "ハゲ", "チビ", "ドジ", "無能",
    "役立たず", "知恵遅れ", "池沼", "ガイジ", "キチガイ", "きちがい",
    "ボケナス", "ノロマ", "雑魚", "ザコ", "うんこ", "クソがき", "この野郎",
    "死んで", "失せろ", "黙れ",
]

SUB_KO = [
    "씨발", "시발", "씨팔", "개새끼", "개자식", "병신", "멍청이", "바보",
    "등신", "새끼", "지랄", "미친놈", "미친년", "죽어", "꺼져", "닥쳐",
    "뚱뚱이", "한심", "정신병자", "븅신", "머저리", "얼간이", "찐따", "돼지",
    "쓰레기", "좆", "밥맛", "극혐", "혐오", "아다", "벙어리",
]

SUB_TH = [
    "โง่", "ควาย", "ไอ้ควาย", "ห่า", "ไอ้สัส", "สัส", "กาก", "ขยะ",
    "หน้าด้าน", "อีดอก", "ไอ้เวร", "เวร", "ส้นตีน", "หน้าหมา", "ตุ๊ด",
    "กะหรี่", "มึงโง่", "ไอ้โง่", "อีโง่", "ตา", "เพี้ย", "ชั่ว", "เสือก",
]

SUB_AR = [
    "غبي", "غبية", "أغبياء", "أحمق", "احمق", "حمار", "كلب", "خنزير",
    "جاهل", "تافه", "حقير", "وضيع", "قذر", "وسخ", "مجنون", "معتوه",
    "أبله", "ابله", "خر", "زبالة", "قرد", "حشرة", "ابن الكلب", "ابن الحرام",
    "يا كلب", "شرموط", "قحبة", "خول", "معفن", "فاشل", "غثيث", "متخلف",
]

SUB_FA = [
    "احمق", "أحمق", "خنگ", "بی شعور", "بیعشور", "کثافت", "کله خر", "نادان",
    "ابله", "دیوانه", "پررو", "مادر جنده", "جنده", "لاشخور", "آشغال",
    "خرفت", "کودن", "کور مغز", "بی سواد", "کمی", "احمقانه", "زرنگ نبودن",
    "خری", "گاو", "خر", "خایه",
]

SUB_HE = [
    "אידיוט", "מטומטם", "מטומטמת", "טיפש", "טיפשה", "דביל", "מפגר",
    "חרא", "בן זונה", "זונה", "מניאק", "אפס", "חמור", "בהמה", "דפוק",
    "חלאה", "טינופת", "מכוער", "מכוערת", "כלב", "שתוק", "תסתום",
]

SUB_HI = [
    "मूर्ख", "बेवकूफ", "बेवकूफ़", "पागल", "गधा", "कुत्ता", "कुत्ते",
    "कमीना", "हरामी", "साला", "साली", "चूतिया", "भोसड़ी", "मादरचोद",
    "बहनचोद", "छक्का", "नीच", "हरामखोर", "उल्लू", "जाहिल", "बदतमीज़",
    "नालायक", "कमबख्त", "गंदा", "बकवास", "चुप",
]

SUB_BN = [
    "মূর্খ", "বোকা", "গাধা", "কুত্তা", "শালা", "হারামি", "নীচ", "ছাগল",
    "বেইমান", "নালায়েক", "চোপ", "নোংরা",
]

SUB_PA = [
    "ਮੂਰਖ", "ਬੇਵਕੂਫ", "ਗਧਾ", "ਕੁੱਤਾ", "ਹਰਾਮੀ", "ਨੀਚ", "ਬੇਈਮਾਨ",
]

SUB_UR = [
    "بیوقوف", "احمق", "گدھا", "کتا", "کمینہ", "حرامی", "سالا", "بے غیرت",
    "نالائق", "چپ",
]

SUB_TA = [
    "முட்டாள்", "முட்டாள", "மூடன்", "நாய்", "கழுதை", "அயோக்கியன்",
    "பேதை", "காட்டுமிராண்டி",
]

SUB_TE = [
    "మూర్ఖుడు", "వెర్రి", "గాడిద", "కుక్క", "దుష్టుడు", "నీచుడు",
]

SUB_MR = [
    "मूर्ख", "वेडा", "गाढव", "कुत्रा", "नीच", "बदमाष",
]

SUB_GU = [
    "મૂર્ખ", "ગધેડો", "કૂતરો", "નીચ", "બદમાશ",
]

SUB_NE = [
    "मूर्ख", "बेवकूफ", "गधा", "कुकुर", "नीच", "नालायक",
]

SUB_SI = [
    "මෝඩ", "ගොනා", "බල්ලා", "නීච", "මෝඩයා",
]

SUB_KA = [
    "იდიოტი", "სულელი", "ნაგავი", "ცხოველი", "დებილი", "შტერი", "გაჩერდი",
]

SUB_HY = [
    "հիմար", "ապուշ", "էշ", "շուն", "անասուն", "տխմար", "հիմարիկ",
]

SUB_AM = [
    "ደደብ", "ሞኝ", "ውሻ", "አህያ", "ጨርቅ",
]

SUB_MY = [
    "ငတုံး", "ခွေး", "မြေ", "အ", "နံ",
]

SUB_KM = [
    "ឆ្កួត", "ឆ្កែ", "ល្ងីល្ងើ", "អាសត្វ",
]

SUB_LO = [
    "ບ້າ", "ຫມາ", "ໂງ່", "ສັດ",
]

# ---------------------------------------------------------------------------
# фразы-оскорбления (регулярки, применяются к нормализованному тексту)
# ---------------------------------------------------------------------------
PHRASES = [
    # русский
    r"(?:ты|вы)\s+(?:туп\w*|дебил\w*|дибил\w*|идиот\w*|лох\w*|чмо\w*|урод\w*|"
    r"мраз\w*|ничтож\w*|клоун\w*|нуб\w*|дегенерат\w*|кретин\w*|дурак\w*|"
    r"придур\w*|даун\w*|быдл\w*|твар\w*|гнид\w*|крыс\w*|баран\w*|свинь\w*|"
    r"школьник\w*|малолетк\w*|жирн\w*|слабак\w*|позор\w*|дно|мусор\w*|"
    r"жалк\w*|убог\w*|кончен\w*)",
    r"(?:туп\w*|дебил\w*|дибил\w*|идиот\w*|лох\w*|чмо\w*|урод\w*|мраз\w*|"
    r"дегенерат\w*|кретин\w*)\s+(?:ты|вы)\b",
    r"(?:закрой|завали|заткни|прикрой|засунь)\s+"
    r"(?:рот|пасть|варежку|хлебало|будку|хавальник)",
    r"(?:иди|пош[её]л|катись|уйди|сваливай|проваливай|убирайся|исчезни)\s+"
    r"(?:ты\s+)?(?:отсюда|прочь|вон|подальше|куда\s+подальше|нахуй|нах)",
    r"(?:ты|вы)\s+(?:кто|что)\s+так(?:ой|ая|ое|ие)\b",
    r"(?:мозги|извилины|ум)\s+(?:у\s+тебя|у\s+вас)\s+(?:нет|отсутству\w+)",
    r"(?:тебе|вам)\s+(?:лечит\w*|к\s+врачу|к\s+психиатру)",
    r"умственно\s+отстал\w*",
    r"(?:иди|пош[её]л)\s+на\s+(?:х|хер|хуй|фиг)\b",
    r"(?:убей|у6ей)\s+себя",
    r"убейся",
    r"(?:пош[её]л|иди)\s+ты\b",
    r"пош[её]л\s+ты\b",
    # английский
    r"kill\s+your\s*self",
    r"\bkys\b",
    r"shut\s+(?:the\s+fuck\s+)?up",
    r"you\s+(?:are|r)\s+(?:an?\s+)?(?:idiot|moron|loser|noob|trash|garbage|"
    r"pathetic|useless|worthless|stupid|dumb|retard\w*)",
    r"nobody\s+(?:asked|cares)",
    r"screw\s+you",
    r"you\s+suck\b",
    r"get\s+a\s+life",
    r"get\s+(?:a\s+life|lost|out\s+of\s+here)",
    r"go\s+(?:die|kill\s+yourself|touch\s+grass)",
    # немецкий
    r"(?:du\s+bist|halt\s+die\s+fresse|verpiss\s+dich|verrecke)",
    r"(?:du)\s+(?:idiot|trottel|depp|spast\w*|vollidiot\w*)",
    # испанский / португальский
    r"(?:eres|tu\s+es)\s+(?:un\s+)?(?:idiota|imbecil|imbécil|pendejo|tonto|"
    r"estúpido|estupido|gilipollas|burro)",
    r"(?:hijo\s+de\s+puta|hija\s+de\s+puta|filho\s+da\s+puta)",
    r"(?:cállate|callate|cala\s+a\s+boca|cierra\s+la\s+boca)",
    r"(?:vete|vai)\s+(?:a\s+la\s+mierda|se\s+ferrar|tomar\s+no\s+cu)",
    # французский
    r"(?:tu\s+es|t'es)\s+(?:un\s+)?(?:idiot|imbécile|imbecile|abruti|con|"
    r"crétin|cretin)",
    r"(?:ferme\s+ta\s+gueule|ta\s+gueule|va\s+te\s+faire|nique\s+ta\s+mère)",
    # итальянский
    r"(?:sei\s+un|sei\s+una)\s+(?:idiota|imbecille|cretino|stupido|coglione)",
    r"(?:stai\s+zitto|taci|vattene|vaffanculo)",
    # турецкий
    r"(?:sen\s+)(?:aptal|salak|gerizekalı|ahmak|ezik)",
    r"(?:kes\s+sesini|sus\s+artık|defol|siktir\s+git)",
    # польский / чешский
    r"(?:jesteś|jestes)\s+(?:idiotą|idiota|głupk\w*|debil\w*)",
    r"(?:zamknij\s+się|zamknij\s+ryj|wypierdalaj|spierdalaj)",
    # арабский / персидский
    r"(?:أنت|انت|يا)\s+(?:غبي|أحمق|احمق|حمار|كلب|حقير)",
    r"(?:تو|شما)\s+(?:احمق|خنگ|خر|گاو)",
    # хинди / урду
    r"(?:तू|तुम|आप)\s+(?:मूर्ख|बेवकूफ|पागल|कमीना|गधा)",
    r"(?:تم|آپ)\s+(?:بیوقوف|احمق|گدھا|کمینہ)",
    # китайский / японский / корейский
    r"(?:你是|你就是|你这个)?(?:傻逼|傻B|白痴|弱智|脑残|废物|蠢货|智障)",
    r"(?:お前|きさま|てめえ|貴様)(?:は|が)?(?:バカ|馬鹿|アホ|ゴミ|クズ)",
    r"(?:너|니|넌)\s*(?:병신|멍청이|바보|새끼|쓰레기)",
    # вьетнамский / тайский / индонезийский
    r"(?:mày|may|đồ|do)\s+(?:ngu|ngốc|dốt|điên|chó)",
    r"(?:มึง|แก|ไอ้|อี)\s*(?:โง่|ควาย|สัส|เวร|กาก)",
    r"(?:kamu|lu|lo|anda)\s+(?:bodoh|goblok|tolol|bego|anjing)",
    # типичные конструкции «ты + оскорбление» на латинице
    r"(?:you\s+are|youre|you're|u\s+are|ur)\s+(?:a\s+)?(?:noob|loser|"
    r"idiot|moron|dumbass|trash|garbage|pathetic)",
    r"(?:du\s+bist|bist\s+du)\s+(?:ein\s+)?(?:idiot|trottel|depp)",
    r"(?:sen|siz)\s+(?:aptal|salak|ahmak|ezik|gerizekalı)",
    r"(?:ty|vy|jsi)\s+(?:jsi\s+)?(?:idiot|debil|blbec|kreten)",
]

# ---------------------------------------------------------------------------
# обращения «ты/вы» — по ним распознаётся направленное оскорбление
# ---------------------------------------------------------------------------
SECOND_PERSON = [
    # русский / украинский / белорусский
    "ты", "вы", "тебя", "тебе", "тобой", "тобою", "твой", "твоя", "твоё",
    "твое", "твои", "твоего", "твоей", "твоих", "вас", "вам", "вами", "ваш",
    "ваша", "ваше", "ваши", "вашего", "вашей", "ваших", "ти", "ви", "тебе",
    "тобі", "твій", "твоя", "ваш", "ваша", "цябе", "табе", "твой",
    # английский
    "you", "your", "yours", "youre", "you're", "u", "ur", "yall", "y'all",
    # немецкий
    "du", "dich", "dir", "dein", "deine", "deiner", "deinem", "deinen",
    "sie", "ihr", "euch", "euer",
    # нидерландский
    "jij", "je", "jou", "jouw", "u", "uw", "jullie",
    # скандинавские
    "du", "dig", "din", "dina", "ditt", "ni", "er", "jeres",
    # романские
    "tu", "toi", "ton", "ta", "tes", "vous", "votre", "vos", "tú", "usted",
    "ustedes", "te", "ti", "tuya", "tuyo", "su", "sus", "você", "voce",
    "seu", "sua", "vós", "vos", "voi", "tuo", "tua", "tuoi", "tue",
    # румынский
    "tu", "tine", "tău", "tau", "ta", "tăi", "voi", "vă", "va",
    # славянские западные
    "ty", "ciebie", "cię", "cie", "twój", "twoj", "twoja", "wy", "was",
    "wasz", "jsi", "tě", "tebe", "tvůj", "tuvuj", "vy", "váš", "vas",
    "si", "teba", "tvoj", "ваш", "вие", "ти", "те",
    # финно-угорские
    "sinä", "sina", "sä", "sa", "sinun", "te", "teidän", "teidan", "te",
    "ti", "teged", "tegedet",
    # тюркские
    "sen", "siz", "seni", "sana", "senin", "sən", "sizi", "sənin", "сен",
    "сіз", "саған", "сенің", "сени", "сага", "sening", "sizning",
    # другие
    "kamu", "anda", "kalian", "lo", "lu", "elu", "kau", "mày", "may",
    "bạn", "ban", "มึง", "แก", "คุณ", "তুই", "তুমি", "আপনি", "तू", "तुम",
    "आप", "تم", "آپ", "أنت", "انت", "أنتم", "تو", "شما", "אתה", "את",
    "אתם", "你", "您", "你们", "お前", "きさま", "てめえ", "君", "あなた",
    "너", "니", "네가", "wewe", "jy", "jou", "ti", "ju", "sen", "essi",
    "ikaw", "kayo", "nimo", "mo", "ka", "sila",
    "εσύ", "εσεις", "εσείς", "tu", "voi", "ты",
]

# ---------------------------------------------------------------------------
# ядра для поиска опечаток и подмен букв (только оскорбления)
# ---------------------------------------------------------------------------
CORE = [
    "дебил", "дибил", "дебилоид", "идиот", "идиотина", "кретин", "критин",
    "дегенерат", "придурок", "ничтожество", "мразь", "урод", "выродок",
    "ублюдок", "имбецил", "олигофрен", "тупица", "чмошник", "недоумок",
    "пидорас", "педераст", "мудак", "долбоеб", "лошара", "нищеброд",
    "отморозок", "аутист", "дауненок", "дебильный", "ничтожный",
    "idiot", "moron", "imbecile", "retard", "dumbass", "stupid", "loser",
    "bastard", "asshole", "scumbag", "jackass", "faggot", "nigger",
    "cretin", "dummkopf", "trottel", "vollidiot", "hurensohn", "arschloch",
    "idiota", "imbecil", "pendejo", "gilipollas", "estupido", "cabron",
    "cretino", "coglione", "stronzo", "puttana", "abruti", "connard",
    "salope", "debile", "kretyn", "głupek", "debil", "aptal", "salak",
    "gerizekali", "ahmak", "bodoh", "goblok", "tolol", "babaca", "otario",
    "trouxa", "debila", "budala", "idiotu", "prostaku", "tampit",
    "bodoh", "goblok", "tolol", "ahmoq", "aptal", "blbec", "kretyn",
    "kreten", "prostak", "budalla", "mjinga", "fool", "jerk",
    "asshole", "wanker", "bitch", "bobo", "tanga",
    "neger", "schwuchtel", "miststuck", "hurensohn",
]


# ---------------------------------------------------------------------------
# русские оскорбления латиницей (durak, zatknis, debil …)
# ---------------------------------------------------------------------------
HARD_TRANSLIT = [
    "durak", "durakh", "durachok", "debil", "dibil", "debiloid", "degen",
    "degenerat", "idiot", "idiotka", "imbecil", "kretin", "oligofren",
    "priydurok", "priduгok", "priydurok", "dolboeb", "dolboyeb", "eblan",
    "mudak", "mudilo", "mudozvon", "gandon", "gondon", "pidor", "pidar",
    "pidaras", "pederast", "petyh", "petuh", "ubludok", "ublyudok",
    "vyrodok", "podonok", "svoloch", "mraz", "mrazi", "gnida", "tvar",
    "tvarina", "bydlo", "chmo", "chmoshnik", "urod", "urodina", "loshara",
    "loshok", "lohushka", "zatknis", "zatknis", "sdohni", "sdoh", "ubey",
    "umri", "provalivay", "otvali", "konycheniy", "tupoy", "tupaya",
    "tupoy", "tupaя", "tupizna", "nitchevo", "nichego", "shval",
    "shkolnik", "maloletka", "sosunok", "soplyak", "zhirniy", "zhirnaya",
    "slabak", "kloun", "nub", "nublo", "zadrot", "chiter", "krysа",
    "paskuda", "merzavec", "podonok", "skotina",
]

# ---------------------------------------------------------------------------
# сборка: язык -> список
# ---------------------------------------------------------------------------
HARD_LANGS = {
    "ru": HARD_RU, "uk": HARD_UK, "be": HARD_BE, "pl": HARD_PL, "cs": HARD_CS,
    "sk": HARD_SK, "bg": HARD_BG, "sr": HARD_SR, "hr": HARD_HR, "sl": HARD_SL,
    "mk": HARD_MK, "lt": HARD_LT, "lv": HARD_LV, "et": HARD_ET,
    "en": HARD_EN, "de": HARD_DE, "nl": HARD_NL, "sv": HARD_SV, "no": HARD_NO,
    "da": HARD_DA, "is": HARD_IS, "es": HARD_ES, "fr": HARD_FR, "it": HARD_IT,
    "pt": HARD_PT, "ro": HARD_RO, "ca": HARD_CA, "fi": HARD_FI, "hu": HARD_HU,
    "tr": HARD_TR, "az": HARD_AZ, "kk": HARD_KK, "ky": HARD_KY, "uz": HARD_UZ,
    "tk": HARD_TK, "id": HARD_ID, "ms": HARD_MS, "vi": HARD_VI, "tl": HARD_TL,
    "sw": HARD_SW, "af": HARD_AF, "sq": HARD_SQ, "el": HARD_EL, "mn": HARD_MN,
}

SUB_LANGS = {
    "zh": SUB_ZH, "ja": SUB_JA, "ko": SUB_KO, "th": SUB_TH, "ar": SUB_AR,
    "fa": SUB_FA, "he": SUB_HE, "hi": SUB_HI, "bn": SUB_BN, "pa": SUB_PA,
    "ur": SUB_UR, "ta": SUB_TA, "te": SUB_TE, "mr": SUB_MR, "gu": SUB_GU,
    "ne": SUB_NE, "si": SUB_SI, "ka": SUB_KA, "hy": SUB_HY, "am": SUB_AM,
    "my": SUB_MY, "km": SUB_KM, "lo": SUB_LO,
}

HARD_LANGS["ru-translit"] = HARD_TRANSLIT

SOFT_WORDS = list(SOFT)
SOFT_TARGET_WORDS = list(SOFT_TARGET)

# ---------------------------------------------------------------------------
# статистика (используется в /moder и самопроверке)
# ---------------------------------------------------------------------------
def stats():
    hard_words = sum(len(v) for v in HARD_LANGS.values())
    soft_words = len(SOFT_WORDS)
    sub_words = sum(len(v) for v in SUB_LANGS.values())
    return {
        "languages": len(HARD_LANGS) + len(SUB_LANGS),
        "scripts": len(SUB_LANGS),
        "hard": hard_words,
        "soft": soft_words,
        "soft_target": len(SOFT_TARGET_WORDS),
        "sub": sub_words,
        "phrases": len(PHRASES),
        "cores": len(CORE),
        "total": hard_words + soft_words + sub_words,
    }
