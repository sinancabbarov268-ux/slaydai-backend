# -*- coding: utf-8 -*-
"""
3 merheleli pipeline: tedqiqat -> Gamma ucun strukturlasdirilmis metn (generasiya Gamma-da olur).
Bu fayl evvelki generate_slides.py-in eyni menteqidir, sadece
funksiya kimi backend-den cagirila bilecek sekilde yenidenqurulub.
"""

import os
import re
import json
import random

import anthropic

MODEL = "claude-sonnet-5"


def get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY tapilmadi (server mühit dəyişənlərində qeyd olunmalıdır)")
    return anthropic.Anthropic(api_key=api_key)


def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    start = min(starts) if starts else -1
    end = max(text.rfind("}"), text.rfind("]"))
    if start == -1 or end == -1:
        raise ValueError("Cavabda JSON tapilmadi:\n" + text[:500])
    return json.loads(text[start:end + 1])


KURS_DILI = {
    "1": "Dil sadə, izahedici olsun, mürəkkəb terminlər izah edilməklə istifadə olunsun, ümumi/giriş səviyyəli tələbə auditoriyasına uyğun.",
    "2": "Dil bir az daha akademik, əsas terminlər izahsız istifadə oluna bilər, orta səviyyə.",
    "3": "Dil akademik, sahə terminologiyası sərbəst istifadə olunsun, analitik dərinlik artsın.",
    "4": "Dil ən yüksək akademik səviyyədə, tədqiqat məqaləsi səviyyəsində terminologiya və dərin analitik yanaşma, bitirmə işi səviyyəsinə uyğun.",
}


def kurs_dili(project: dict) -> str:
    kurs = str(project.get("kurs", "")).strip()[:1]
    return f"\nDIL SEVIYYESI ({kurs}-ci kurs): {KURS_DILI[kurs]}" if kurs in KURS_DILI else ""


# ---------------- 1) TEDQIQAT ----------------

def research_topic(client, project: dict) -> dict:
    if project.get("menbe_secimi") == "ozum_verirem" and project.get("istifadeci_menbeleri"):
        menbe_teliamti = (
            "Yalniz asagida verilen menbelerden istifade et:\n"
            + "\n".join(f"- {m}" for m in project["istifadeci_menbeleri"])
        )
        tools = []
    else:
        menbe_teliamti = (
            "Movzunu internetden (web_search aleti ile) arasdir. YALNIZ etibarli menbelerden "
            "istifade et: elmi meqaleler, derslikler, resmi statistik hesabatlar, dovlet/rasmi "
            "saytlar. Forum, blog, sosial media ve tesdiqlenmemis saytlardan istifade etme."
        )
        tools = [{"type": "web_search_20250305", "name": "web_search"}]

    system = f"""Sen akademik tedqiqat mutexessisisen. Verilen movzunu diqqetle arasdirib,
strukturlasdirilmis JSON formatinda netice cixarirsan. {menbe_teliamti}

Cavabini YALNIZ asagidaki JSON formatinda ver, basqa hec ne yazma:
{{
  "movzunun_aktualligi": "...",
  "meqsed": "...",
  "bolmeler": [
    {{"basliq": "...", "esas_fikirler": ["..."], "faktlar_ve_reqemler": ["..."], "menbe": "..."}}
  ],
  "netice": "...",
  "edebiyyat_siyahisi": ["..."]
}}"""

    system += kurs_dili(project)
    user = f"Movzu: {project['movzu']}\nFenn: {project.get('fenn','')}\nIxtisas: {project.get('ixtisas','')}"

    resp = client.messages.create(
        model=MODEL, max_tokens=16000, system=system, tools=tools,
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Model cavabi max_tokens limitinde kesildi")
    text = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return extract_json(text)


# ---------------- 2) DIZAYN ----------------

FIXED_SLIDE_COUNT = 3  # titul + plan + (edebiyyat+tesekkur birlesdirilmis)


def slide_count_plan(n_slides: int) -> tuple:
    """(sabit, deyisen) qaytarir: sabit=3 (titul/plan/edebiyyat+tesekkur), deyisen =
    n_slides-3 (giris+esas+netice arasinda mezmuna gore bolunur)."""
    variable = max(n_slides - FIXED_SLIDE_COUNT, 1)
    return FIXED_SLIDE_COUNT, variable


def build_slides_markdown_rules(n_slides: int) -> str:
    fixed, variable = slide_count_plan(n_slides)
    return f"""SEN AKADEMIK TEQDIMAT MEZMUN REDAKTORUSAN. Sende IKI MESULIYYET var ve onlari
QARISDIRMA:

1) MEZMUN (sen yazirsan, qeti ve dəyişməz):
   - Her slayd ucun TAM, hazir metn: basliq + bullet-lar. Uydurma fakt YOX - yalniz
     asagida verilen TEDQIQAT materialindan istifade et.
   - Vizual YALNIZ lazim olduqda elave et (esas hisse slaydlarinda adeten lazimdir):
     novunu (cədvəl / qrafik / müqayisə / statistika / ikon-siyahı) VE onun DƏQİQ
     datasini (konkret reqemler, setirler, kateqoriyalar) yaz.

2) VİZUAL İCRA (sen YAZMA - bu Presenton-a buraxilir):
   - Rəng kodu, piksel/mövqe koordinatı, şrift ölçüsü, layout detalları YAZMA. Bunlar
     promptda olmamalidir, cunki vizual icrasini Presenton oz alqoritmi ile qurur.

CAVAB FORMATI - YALNIZ bu JSON, basqa hec ne yazma:
Tam {n_slides} elementden ibaret JSON massivi. Her element - bir slaydin Markdown metni (string).
Slaydlarin SABIT hissesi 3-dur, qalan {variable} slayd giris+esas+netice arasinda
mezmunun mentiqi axinina gore SEN qerarlasdirirsan (mecburi deyil beraber bolunsun):
  1. Titul (SABIT)
  2. Plan (SABIT)
  3..{n_slides - 1}. Giriş + Əsas hissə + Nəticə - CƏMİ {variable} slayd (bölgüsünü
     mövzunun məntiqi axınına görə təbii şəkildə sən qərarlaşdır, məsələn
     1 giriş + ({variable - 2 if variable >= 2 else variable}) əsas + 1 nəticə,
     ya da məzmuna görə başqa bölgü)
  {n_slides}. Ədəbiyyat + Təşəkkür (SABIT, birləşdirilmiş son slayd)

Her elementin daxili formati:
# Slayd basligi
- bullet 1 (tam cumle, tedqiqatdan)
- bullet 2
- bullet 3
**Vizual:** [növ] — [dəqiq data, meselen "Sütun qrafik: 2024=200, 2025=234, 2026=389 (mlrd. $)"]

Qaydalar:
- Vizual setri YALNIZ vizual lazimdirsa yazilsin (titul/plan/edebiyyat slaydlarinda adeten yoxdur).
- TITUL SLAYDI (DƏQİQ BU SIRA İLƏ, başqa cür YOX):
    1) Birinci sətir: universitet adı - HƏMİŞƏ BÖYÜK/QALIN vurğu ilə (məs. "**Bakı Biznes
       Universiteti**"), ƏN YUXARIDA.
    2) DƏRHAL onun altında, ikinci sətir - dəyişməz, sabit mətn: "Azərbaycan Respublikası
       Elm və Təhsil Nazirliyi". Bu mətn HƏR ZAMAN eynidir, dəyişdirilmir.
    Bu iki sətirdən (universitet, nazirlik) başqa, onlar haqqında HEÇ BİR əlavə
    tərifləyici/təbliğ edici cümlə YAZMA - yalnız ad, başqa heç nə.
    Bu iki sətirdən SONRA, qalan sahələr öz sətrində davam edir: Fakültə, Kafedra,
    İxtisas, Fənn, Mövzu, Kurs/Qrup, Müəllim, Tələbə - hər biri "Ad: dəyər" formatında.
    Universitet/nazirlik adları mövzu adı ilə QARIŞDIRILMASIN.
- Plan slaydı: giriş+əsas+nəticə bölmələrinin başlıqlarının siyahısı.
- Aralıq (giriş/əsas/nəticə) slaydları: hər biri 3-5 bullet, mümkünsə bir **Vizual:**
  sətri konkret rəqəmlərlə.
- Son slayd (Ədəbiyyat+Təşəkkür): ƏN ÇOX 5 mənbə (nömrələnmiş) - YALNIZ ən etibarlı,
  ən vacib mənbələri seç (elmi jurnal, rəsmi statistika hesabatları üstünlük təşkil
  etsin, ikinci dərəcəli/blog mənbələri arxaya at) + son sətirdə "Diqqətiniz üçün
  təşəkkür edirik!".
- Yeni fakt uydurma - yalniz verilen TEDQIQAT materialindan istifade et."""


def _validate_slides_markdown(data, n_slides: int) -> bool:
    if not isinstance(data, list) or len(data) != n_slides:
        return False
    return all(isinstance(item, str) and item.strip().startswith("#") for item in data)


def design_slides(client, project: dict, research: dict) -> list:
    """Mövcud tedqiqat materialindan (yeni web_search YOXDUR) hər biri bir slaydın Markdown
    mətni olan, project['slayd_sayi_hedefi']-e uygun N elementli JSON massivi qaytarır
    (sabit=titul+plan+edebiyyat/tesekkur=3, qalani mezmuna gore bolunur).
    generated_files/slides_markdown.json kimi saxlanılır."""
    n_slides = int(project.get("slayd_sayi_hedefi") or 10)
    n_slides = max(n_slides, FIXED_SLIDE_COUNT + 1)

    user = (
        f"LAYIHE:\n{json.dumps(project, ensure_ascii=False)}\n\n"
        f"TEDQIQAT (mövcud material - yeni tədqiqat aparma):\n{json.dumps(research, ensure_ascii=False)}\n\n"
        f"Dil: {project.get('dil','Azərbaycan dili')}."
    )
    messages = [{"role": "user", "content": user}]
    slides = None
    system = build_slides_markdown_rules(n_slides) + kurs_dili(project)
    # 10 slaydlik xerc hedefi (6000-8000) ~700 token/slayd-a bereberdir - n_slides
    # deyisken oldugu ucun bu nisbeti saxlayib mueyyen tavan altinda skalalasdiririq.
    max_tokens = min(14000, max(8000, n_slides * 900))
    for attempt in (1, 2):  # format/say sehv olarsa bir defe avtomatik duzelis isteyirik
        resp = client.messages.create(
            model=MODEL, max_tokens=max_tokens, system=system, messages=messages,
        )
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("Model cavabi max_tokens limitinde kesildi")
        text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        try:
            data = extract_json(text)
        except ValueError:
            data = None
        if _validate_slides_markdown(data, n_slides):
            slides = data
            break
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"Cavab yanlis formatda idi. YALNIZ tam {n_slides} stringden ibaret JSON massivi qaytar, her string bir slaydin Markdown metni ('#' basliqla baslamali). Basqa hec ne yazma."},
        ]
    if slides is None:
        raise RuntimeError(f"Dizayn tekrar cehdden sonra da duzgun formatda ({n_slides} elementli JSON massivi) qayitmadi")

    os.makedirs("generated_files", exist_ok=True)
    with open("generated_files/slides_markdown.json", "w", encoding="utf-8") as f:
        json.dump(slides, f, ensure_ascii=False, indent=2)
    return slides


# ---------------- 3) PRESENTON (PPTX GENERASİYASI) ----------------

PRESENTON_API_BASE = "https://api.presenton.ai"


def get_presenton_key() -> str:
    key = os.environ.get("PRESENTON_API_KEY")
    if not key:
        raise RuntimeError("PRESENTON_API_KEY tapilmadi (server mühit dəyişənlərində qeyd olunmalıdır)")
    return key


def build_presenton_payload(slides_markdown: list, project: dict) -> dict:
    """Presenton /api/v1/ppt/presentation/generate ucun request body-ni qurur.
    Hec bir setwork cagirisi ETMIR - yalniz payload-i qaytarir (gonderilmeden evvel review ucun)."""
    return {
        "content": "",  # slides_markdown istifade olunduqda bos setir kifayetdir
        "slides_markdown": slides_markdown,
        "instructions": (
            "Akademik, rəsmi üslub, Azərbaycan Respublikası dövlət universiteti "
            "standartlarına uyğun."
        ),
        "tone": "educational",
        "export_as": "pptx",
        "language": project.get("dil", "Azərbaycan dili"),
        # slides_markdown-da titul (1) ve plan (2) slaydlari artiq var - Presenton
        # bunlari yeniden avtomatik yaratmasin, eks halda 10 slayd 12-ye cixar:
        "include_title_slide": False,
        "include_table_of_contents": False,
        "web_search": False,  # yalniz bizim verdigimiz tesdiqlenmis mezmun istifade olunsun
    }


def send_to_presenton(slides_markdown: list, project: dict, async_mode: bool = False) -> dict:
    """DIQQƏT: bu, real Presenton kreditini xərcləyir. YALNIZ istifadəçi payload-u gördükdən
    və təsdiqlədikdən sonra çağırılmalıdır - avtomatik pipeline-a bağlanmayıb."""
    import requests

    key = get_presenton_key()
    path = "/api/v1/ppt/presentation/generate/async" if async_mode else "/api/v1/ppt/presentation/generate"
    resp = requests.post(
        PRESENTON_API_BASE + path,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=build_presenton_payload(slides_markdown, project),
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------- 4) 2SLIDES (ALTERNATIV PPTX/PDF GENERASİYASI) ----------------

TWOSLIDES_API_BASE = "https://2slides.com/api/v1"


def get_2slides_key() -> str:
    key = os.environ.get("2SLIDES_API_KEY")
    if not key:
        raise RuntimeError("2SLIDES_API_KEY tapilmadi (server mühit dəyişənlərində qeyd olunmalıdır)")
    return key


def _2slides_headers() -> dict:
    return {"Authorization": f"Bearer {get_2slides_key()}", "Content-Type": "application/json"}


def search_2slides_theme(query: str):
    """Verilen sorguya uygun ilk tema id-sini qaytarir (tapilmasa None).
    NEZERE AL: 2Slides-in real cavabi {"success":true,"data":{"themes":[...]}}
    seklindedir (senedlesdirilmis {"themes":[...]} deyil)."""
    import requests

    resp = requests.get(
        f"{TWOSLIDES_API_BASE}/themes/search",
        headers=_2slides_headers(),
        params={"query": query, "limit": 5},
        timeout=30,
    )
    resp.raise_for_status()
    themes = resp.json().get("data", {}).get("themes", [])
    return themes[0]["id"] if themes else None


def build_2slides_user_input(slides_markdown: list) -> str:
    """Bizim 10-elementli slides_markdown massivini 2Slides-in gozlədiyi tək
    mətn (userInput) formatına çevirir - hər slayd öz Markdown mətni ilə,
    aralarında ayırıcı ilə."""
    return "\n\n---\n\n".join(slides_markdown)


def send_to_2slides(slides_markdown: list, project: dict, mode: str = "sync") -> dict:
    """DIQQƏT: bu, real 2Slides kreditini xərcləyir (Fast PPT: 10 kredit/səhifə).
    themeId real API-de MECBURIDIR (senedlerdeki "bos burax" qeydine baxmayaraq)."""
    import requests

    theme_id = search_2slides_theme("simple clean business")
    if not theme_id:
        raise RuntimeError("2Slides temasi tapilmadi (themeId mecburidir)")
    payload = {
        "userInput": build_2slides_user_input(slides_markdown),
        # Azərbaycan dili dəstəklənən dillər siyahısında yoxdur - avtomatik aşkarlama
        # istifadə edirik ki, mətn olduğu kimi (Azərbaycan dilində) qalsın.
        "responseLanguage": "Auto",
        "mode": mode,
        "themeId": theme_id,
    }

    resp = requests.post(
        f"{TWOSLIDES_API_BASE}/slides/generate",
        headers=_2slides_headers(),
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------- 5) GAMMA (developers.gamma.app Generate API) ----------------

GAMMA_API_BASE = "https://public-api.gamma.app"

# GET /v1.0/themes CAVABININ TAM STRUKTURU (canli sorgu ile yoxlanilib, 2026-09-27):
# her elementde YALNIZ bu 5 sahe var: id, name, type, colorKeywords, toneKeywords.
# Ayrica "tags"/"category"/"mood"/"description"/"colorPalette" sahesi YOXDUR - lakin
# colorKeywords + toneKeywords ozu de-fakto tag/filtr metadatasi kimi istifade edile
# biler (ve asagida mehz bele istifade olunur). Buna gore "2-ci ssenari" (filtrleme
# ucun metadata VAR) tetbiq edilib, "3-cu ssenari" (mecburi genis+tesadufi siyahi) YOX.
#
# GAMMA_THEME_POOL - 102 standard temadan colorKeywords/toneKeywords-e gore
# secilmis, professional/formal/serious/corporate/clean/modern/elegant/classic
# tonlu (playful/loud/childish OLMAYAN) ~19 tema, MUXTELIF renk aileleri uzre
# (yalniz tund/qara ustunluk teskil etmesin deye): blue/teal, green, purple,
# warm(gold/orange/brown), az sayda neutral(b&w/grey). Her elementde real
# colorKeywords/toneKeywords saxlanilir ki, select_gamma_theme() movzuya gore
# xal (score) hesablaya bilsin - bu siyahi lokal/statik olduğu üçün
# build_gamma_payload YENƏ DƏ heç bir şəbəkə çağırışı etmir.
GAMMA_THEME_POOL = [
    {"id": "ash", "family": "neutral", "colorKeywords": ["black", "white", "light gray", "b&w", "high contrast", "light"], "toneKeywords": ["serious", "formal", "professional", "clean", "subtle", "bold", "modern", "mature"]},
    {"id": "chimney-smoke", "family": "neutral", "colorKeywords": ["white", "light", "light gray", "silver", "chrome", "cool", "bright", "gray", "dark gray"], "toneKeywords": ["serious", "corporate", "formal", "elegant", "professional", "minimalist", "clean", "quiet", "modern", "mature"]},
    {"id": "gleam", "family": "neutral", "colorKeywords": ["cool gray", "silver", "pearl gray", "slate gray", "charcoal gray", "white", "black", "gray", "light"], "toneKeywords": ["serious", "classy", "professional", "corporate", "tech", "minimalist", "clean", "modern"]},
    {"id": "default-light", "family": "blue", "colorKeywords": ["light", "blue", "white", "black", "navy", "cool"], "toneKeywords": ["serious", "corporate", "formal", "professional", "tech", "bold", "modern"]},
    {"id": "consultant", "family": "blue", "colorKeywords": ["light", "blue", "white", "cool"], "toneKeywords": ["consulting", "corporate", "formal", "business", "professional", "modern", "serious"]},
    {"id": "icebreaker", "family": "blue", "colorKeywords": ["light", "blue", "white", "navy", "gradient", "cool"], "toneKeywords": ["serious", "professional", "friendly", "clean", "modern", "fresh"]},
    {"id": "tranquil", "family": "blue", "colorKeywords": ["light", "blue", "sky", "white", "glassy", "cool"], "toneKeywords": ["serious", "corporate", "professional", "quiet"]},
    {"id": "founder", "family": "blue", "colorKeywords": ["dark", "black", "gray", "blue", "cool", "purple"], "toneKeywords": ["serious", "modern", "professional", "minimalist"]},
    {"id": "blue-steel", "family": "blue", "colorKeywords": ["dark", "black", "gray", "blue", "navy", "gradient", "cool"], "toneKeywords": ["corporate", "clean", "modern", "futuristic", "mature"]},
    {"id": "marine", "family": "blue", "colorKeywords": ["dark", "blue", "navy", "white", "cool", "royal blue", "dark blue"], "toneKeywords": ["professional", "bold", "classic", "fresh", "inspirational"]},
    {"id": "petrol", "family": "blue", "colorKeywords": ["light", "petrol blue", "denim blue", "grayish blue", "ecru", "off-white", "blue"], "toneKeywords": ["professional", "formal", "corporate", "quiet", "classic", "traditional", "serious"]},
    {"id": "ashrose", "family": "purple", "colorKeywords": ["white", "silver", "gray", "light purple", "mauve", "dusty lavender", "ivory", "warm gray"], "toneKeywords": ["elegant", "quiet", "serious", "clean", "modern", "professional"]},
    {"id": "iris", "family": "purple", "colorKeywords": ["purple", "lavender"], "toneKeywords": ["modern", "soft", "inviting"]},
    {"id": "commons", "family": "green", "colorKeywords": ["light", "gray", "white", "green"], "toneKeywords": ["professional", "clean", "modern", "tech", "minimal"]},
    {"id": "sage", "family": "green", "colorKeywords": ["light", "beige", "white", "green", "sage", "forest green", "pastel green", "cool"], "toneKeywords": ["serious", "corporate", "formal", "professional", "minimalist", "clean", "modern", "classic", "fresh"]},
    {"id": "lux", "family": "green", "colorKeywords": ["green", "dark green", "forest green", "deep green", "salmon", "peach", "dark", "colorful"], "toneKeywords": ["serious", "classy", "corporate", "formal", "elegant", "professional", "minimalist", "clean", "luxury", "modern"]},
    {"id": "dialogue", "family": "warm", "colorKeywords": ["light", "orange", "white", "warm"], "toneKeywords": ["bright", "modern", "simple", "professional"]},
    {"id": "clementa", "family": "warm", "colorKeywords": ["pumpkin orange", "chocolate brown", "golden tan", "burnt umber", "cream", "warm beige"], "toneKeywords": ["warm", "bold", "optimistic", "professional", "earthy"]},
    {"id": "gold-leaf", "family": "warm", "colorKeywords": ["gold", "champagne", "ivory", "white", "cream", "light beige", "luxurious gold", "warm tones", "elegant"], "toneKeywords": ["classy", "formal", "elegant", "professional", "minimalist", "clean", "luxury", "modern", "mature", "inspirational"]},
]

# Movzu/fenn metninde bu sozlerden biri olsa mövzu "reqemsal/data-yönümlü" sayilir
# ve o zaman canli renk aileleri (blue/green/warm) neutral/b&w-dan ustun tutulur.
GAMMA_DIGITAL_TOPIC_HINTS = [
    "sosial media", "sosial şəbəkə", "rəqəmsal", "media", "marketinq", "reklam",
    "texnologiya", "data", "statistika", "analitika", "informasiya", "it ",
    "biznes", "iqtisad",
]


def select_gamma_theme(project: dict) -> str:
    """GAMMA_THEME_POOL-dan project['movzu']/project['fenn']-e uygun ACAR SOZLERLE
    xallanmis temalar arasindan TESADUFI secim edir (en yuksek xalli temalar
    arasinda random.choice - beleliklə hem movzuya uygunluq, hem de her
    generasiyada muxteliflik qorunur). HEC BIR seback cagirisi ETMIR (statik
    GAMMA_THEME_POOL uzerinde islenilir)."""
    topic_text = f"{project.get('movzu', '')} {project.get('fenn', '')}".lower()
    is_digital = any(hint in topic_text for hint in GAMMA_DIGITAL_TOPIC_HINTS)

    scored = []
    for theme in GAMMA_THEME_POOL:
        tones = [w.lower() for w in theme["toneKeywords"]]
        score = 0
        if any(w in tones for w in ("professional", "formal", "serious", "corporate", "academic")):
            score += 1  # akademik/rəsmi minimum tələb - butun havuz onsuz da bunu qismen tesdiqleyir
        if is_digital:
            if theme["family"] in ("blue", "green", "warm"):
                score += 2  # reqemsal/data movzular ucun canli renk aileleri ustunluk
            if any(w in tones for w in ("modern", "fresh", "bold", "tech")):
                score += 1
        else:
            if theme["family"] == "neutral":
                score += 1  # qeyri-reqemsal/enenevi movzular ucun neytral da uygundur
        scored.append((score, theme["id"]))

    max_score = max(s for s, _ in scored)
    top_candidates = [tid for s, tid in scored if s == max_score]
    return random.choice(top_candidates)


def build_gamma_payload(slides_markdown: list, project: dict) -> dict:
    """Gamma POST /v1.0/generations ucun request body qurur. HEC BIR seback
    cagirisi ETMIR - yalniz payload-i qaytarir (review ucun).

    cardSplit="inputTextBreaks" + hər slaydın arasında dəqiq "\\n---\\n" ayırıcısı
    istifadə olunur ki, bizim 10 elementin hər biri TAM BİR karta uyğun gəlsin
    (9 ayırıcı = 10 kart). textMode="preserve" secilib ki, Gamma bizim tesdiqlenmis
    metni yenidən yazıb genişləndirməsin. NEZERE AL: cardSplit="inputTextBreaks"
    seçildikde numCards Gamma terefinden tamamile e'tibarsiz sayilir (senede gore),
    ona gore bu payload-da numCards YOXDUR.

    themeId select_gamma_theme() ile project['movzu']/['fenn']-e uygun ACAR SOZLERLE
    xallanmis GAMMA_THEME_POOL-dan (en yuksek xalli temalar arasinda TESADUFI) secilir -
    beleliklə hər çağırışda fərqli, amma mövzuya uyğun VƏ həmişə akademik/rəsmi
    görünüşlü dizayn alınır."""
    return {
        "inputText": "\n---\n".join(slides_markdown),
        "format": "presentation",
        "textMode": "preserve",
        "cardSplit": "inputTextBreaks",
        "themeId": select_gamma_theme(project),
        "additionalInstructions": (
            "VACIB: Heç bir slaydda mətn üst-üstə düşməsin, hərflər bir-birinin "
            "içinə girməsin — bu, ƏN BÖYÜK PRİORİTETDİR. Mətn sığmırsa, bullet "
            "sayını azalt, qısalt, amma HEÇ VAXT sıxışdırma və ya üst-üstə yerləşdirmə. "
            "Heç bir slaydda məzmun slaydın sərhədlərindən kənara çıxmasın, standart "
            "16:9 ölçüsü daxilində qal.\n\n"
            "Hər slayd mümkün qədər STATİSTİK, VİZUAL və CƏLBEDİCİ olsun: rəqəmləri "
            "böyük, vurğulanmış şəkildə göstər, cədvəl/qrafik/ikon istifadəsini "
            "maksimuma çıxar, hər slaydın öz fərqli vizual xarakteri olsun (bütün "
            "slaydlar eyni şablonun təkrarı olmasın).\n\n"
            "Universitet adı böyük şriftlə ən yuxarıda, dərhal altında dəyişməz "
            "'Azərbaycan Respublikası Elm və Təhsil Nazirliyi' mətni olsun, başqa "
            "təbliğ mətni yazma. Ağ, boş fon istifadə etmə.\n\n"
            "Arxa fon rəngi mövzuya uyğun canlı, akademik tonlarda olsun (təkcə "
            "qara/tündqara yox) — mavi, yaşıl, bənövşəyi, isti tonlar arasından "
            "mövzuya uyğun birini seç, gradient də ola bilər.\n\n"
            "Slayd başlıqları çox uzun olarsa, avtomatik kiçik şriftlə sıxışdırmaq "
            "əvəzinə, başlığı 2 sətirə böl və ya qısalt ki, slaydın sağ kənarından "
            "KEÇMƏSİN. Xüsusilə uzun başlıqlarda (3-4 sözdən çox) buna diqqət et.\n\n"
            "Hər slaydın bütün sahəsindən SƏMƏRƏLİ istifadə et — böyük boş sahələr "
            "qalmasın, məzmun slaydın yalnız kiçik bir hissəsində sıxılıb qalan yer "
            "boş buraxılmasın. Mətn, vizual elementlər (qrafik, cədvəl, ikon) və boş "
            "sahə arasında BALANSLI nisbət saxla: məzmun azdırsa, onu böyüdərək və ya "
            "əlavə vizual dəstəklə (böyük rəqəmlər, geniş ikonlar, əlavə kontekst "
            "bloku) səhifəni tarazlı doldur; məzmun çoxdursa, sıxışdırmaq əvəzinə "
            "iki sütuna və ya kart formatına böl. Məqsəd: hər slayd vizual olaraq "
            "'tam', 'dolu' və tarazlı görünsün — nə boş, nə də sıxılmış hiss olunsun.\n\n"
            "XÜSUSİLƏ İKİ SÜTUNLU (mətn + ikon siyahısı) layoutlarda: hər iki sütun "
            "TAXMİNƏN eyni hündürlükdə bitməlidir, biri digərindən çox qısa olub "
            "aşağıda boş sahə buraxmamalıdır. Əgər bir sütunun məzmunu az olduğu üçün "
            "digərindən qısa qalırsa: (a) o sütunun elementlərini şaquli olaraq bərabər "
            "paylaşdır (aralarında daha çox boşluq qoyaraq sütunu doldurmağa çalış), "
            "və ya (b) həmin sütuna əlavə uyğun vizual element (məsələn kiçik statistika "
            "bloku, sitat, əlavə kontekst) əlavə et ki, hər iki sütun vizual olaraq "
            "TARAZ bitsin. 'L formalı', bir tərəfi dolu, digər tərəfi yarımçıq boş "
            "qalan slayd YARADILMASIN."
        ),
        # NEZERE AL: Azerbaycan dili textOptions.language-in dеstekledigi kodlar
        # siyahisinda YOXDUR (400 Input validation errors ile teyid olundu) - ona
        # gore bu sahe qesden atlanilib; textMode="preserve" onsuz da metni
        # tercume etmeden oldugu kimi saxlayir.
        "exportAs": "pptx",
    }


def get_gamma_key() -> str:
    key = os.environ.get("GAMMA_API_KEY")
    if not key:
        raise RuntimeError("GAMMA_API_KEY tapilmadi (server mühit dəyişənlərində qeyd olunmalıdır)")
    return key


def send_to_gamma(slides_markdown: list, project: dict, poll_interval: int = 5, max_attempts: int = 60) -> dict:
    """DIQQƏT: bu, real Gamma kreditini xərcləyir.
    POST /v1.0/generations ile generasiyani basladir, sonra senedin tovsiye etdigi
    kimi hər 5 saniyədə GET /v1.0/generations/{id} ile status="completed"/"failed"
    olana qeder (maks. 5 deqiqe) polling edir ve neticeni (gammaUrl, exportUrl,
    credits) qaytarir.

    NEZERE AL: polling GET sorgusu keçici şəbəkə xətası (məs. ReadTimeout) versə,
    generasiyanin özü buna görə DAYANMIR (server terefinde davam edir) - ona görə
    burda polling xetalarini "generasiya ugursuz oldu" kimi qebul ETMIRIK, sadece
    novbeti cehde kecirik ki, generationId-ni itirib yeniden kredit xercleyen
    tekrar POST/generations cagirmayaq."""
    import time
    import requests

    headers = {"X-API-KEY": get_gamma_key(), "Content-Type": "application/json"}

    payload = build_gamma_payload(slides_markdown, project)
    chosen = next((t for t in GAMMA_THEME_POOL if t["id"] == payload["themeId"]), None)
    family = chosen["family"] if chosen else "?"
    print(f"[gamma] secilen tema (themeId)={payload['themeId']} | renk ailesi={family}")

    create_resp = requests.post(
        f"{GAMMA_API_BASE}/v1.0/generations",
        headers=headers,
        json=payload,
        timeout=60,
    )
    create_resp.raise_for_status()
    generation_id = create_resp.json()["generationId"]
    print(f"[gamma] generationId={generation_id} (polling gedir...)")

    for _ in range(max_attempts):
        try:
            status_resp = requests.get(
                f"{GAMMA_API_BASE}/v1.0/generations/{generation_id}",
                headers=headers,
                timeout=60,
            )
            status_resp.raise_for_status()
            data = status_resp.json()
        except requests.exceptions.RequestException as e:
            print(f"[gamma] polling xetasi ({e}) - tekrar cehd edilir...")
            time.sleep(poll_interval)
            continue
        if data.get("status") in ("completed", "failed"):
            return data
        time.sleep(poll_interval)

    raise RuntimeError(f"Gamma generasiyasi {max_attempts * poll_interval} saniyede tamamlanmadi (generationId={generation_id})")


def run_full_pipeline(project: dict, output_path: str) -> dict:
    """Tedqiqat + Presenton ucun slaydlarin Markdown mezmunu (generasiya Presenton-da olur).
    output_path: .json fayl (10 elementli Markdown massivi bura da yazilir)."""
    client = get_client()
    research = research_topic(client, project)
    research_path = re.sub(r"\.json$", "", output_path) + "_research.json"
    with open(research_path, "w", encoding="utf-8") as f:
        json.dump(research, f, ensure_ascii=False, indent=1)
    slides = design_slides(client, project, research)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(slides, f, ensure_ascii=False, indent=2)
    return {"research": research, "slides": slides}
