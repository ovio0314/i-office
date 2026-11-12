#!/usr/bin/env python3
"""
🏢 나만의 AI 오피스
- 실행: python3 ai_office.py  (Windows는 start.bat 더블클릭)  →  브라우저에서 http://localhost:8000 이 열립니다.
- '오늘 업무 시작하기'를 누르면 AI 직원들이 출근해서 회의하고, 각자 일한 뒤 보고서를 남깁니다.
- 결과물은 '업무결과' 폴더에 날짜별 파일로 저장됩니다.

[AI 두뇌 — 우선순위대로 자동 선택]
  ① 무료 AI (Ollama, 내 컴퓨터에서 실행 · 요금 0원)
     1) https://ollama.com 에서 Ollama 설치
     2) 명령 프롬프트에서:  ollama pull exaone3.5:2.4b   (한국어에 강한 작은 모델)
     3) 이 프로그램을 실행하면 자동으로 연결됩니다.
  ② Claude API (유료, 선택) : pip install anthropic 후 api_key.txt 에 키 저장
  ③ 기본 모드 (무료) : 아무것도 없어도 기사 수집·키워드 분석·템플릿 보고서로 동작
"""
import json
import os
import re
import threading
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8000
HERE = Path(__file__).parent
RESULTS = HERE / "업무결과"
MODEL = "claude-opus-5-5"   # (유료 Claude를 쓸 때만) 더 저렴하게: "claude-haiku-5-5"

# 무료 AI(Ollama) 설정 — 원하는 모델 이름을 적으면 그 모델을 씁니다. 비워두면 설치된 모델 중 자동 선택.
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")
PREFERRED = ["exaone", "qwen", "gemma", "llama", "mistral"]   # 한국어를 잘하는 순서

# ───────────────────────── 무료 AI 두뇌 (Ollama) ─────────────────────────
def ollama_model():
    """Ollama가 켜져 있으면 사용할 모델 이름, 아니면 None."""
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=1.5) as r:
            models = [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return None
    if not models:
        return None
    if OLLAMA_MODEL:
        return OLLAMA_MODEL if any(m.startswith(OLLAMA_MODEL) for m in models) else None
    for pref in PREFERRED:
        for m in models:
            if m.startswith(pref) and "embed" not in m:
                return m
    return next((m for m in models if "embed" not in m), None)


def ask_ollama(model, system, messages):
    body = json.dumps({
        "model": model, "stream": False,
        "messages": [{"role": "system", "content": system}] + messages,
        "options": {"num_ctx": 8192, "temperature": 0.4},
    }).encode()
    req = urllib.request.Request(OLLAMA_URL + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            text = json.load(r).get("message", {}).get("content", "")
    except Exception as e:
        print(f"[무료 AI] Ollama 응답 실패: {e}")
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()   # 생각 과정 제거
    return text or None


# ───────────────────────── 유료 AI 두뇌 (Claude, 선택) ─────────────────────────
def _load_client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    keyfile = HERE / "api_key.txt"
    if not key and keyfile.exists():
        key = keyfile.read_text(encoding="utf-8").strip()
    if not key:
        return None, "API 키 없음"
    try:
        import anthropic
    except ImportError:
        return None, "anthropic 패키지 미설치 (pip install anthropic)"
    return anthropic.Anthropic(api_key=key), "연결됨"


CLIENT, AI_STATUS = _load_client()


def ask_claude(system, messages):
    """Claude에게 일을 시키고 텍스트를 돌려받습니다. 실패하면 None."""
    import anthropic
    try:
        resp = CLIENT.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",            # 거절 시 서버가 다른 모델로 자동 재시도
            output_config={"effort": "low"},
            system=system,
            messages=messages,
        )
    except anthropic.AuthenticationError:
        print("[AI] API 키가 올바르지 않습니다.")
        return None
    except anthropic.RateLimitError:
        print("[AI] 요청이 너무 많습니다. 잠시 후 다시 시도하세요.")
        return None
    except anthropic.APIStatusError as e:
        print(f"[AI] API 오류 {e.status_code}: {e.message}")
        return None
    except anthropic.APIConnectionError:
        print("[AI] 인터넷 연결을 확인하세요.")
        return None
    if resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if b.type == "text").strip() or None


# ───────────────────────── 뉴스 수집 ─────────────────────────
def google_news(query, lang="ko"):
    q = urllib.parse.quote(query + " when:7d")
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


FEEDS = {
    "ko": [
        ("구글 뉴스 (인공지능)", google_news("인공지능 OR 생성형AI OR LLM")),
        ("구글 뉴스 (AI)", google_news("AI 챗GPT OR 클로드 OR 제미나이")),
    ],
    "en": [
        ("Google News (AI)", google_news("artificial intelligence OR LLM", "en")),
        ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
        ("MIT Tech Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
        ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ],
}
ATOM = "{http://www.w3.org/2005/Atom}"


def clean(text, limit=220):
    text = unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def fetch_feed(source, url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AI-Office)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            root = ET.fromstring(r.read())
    except Exception as e:
        print(f"[경고] {source} 불러오기 실패: {e}")
        return []
    items = []
    is_google = "구글" in source or "Google" in source or source.startswith("검색")
    for it in root.iter("item"):
        items.append({
            "title": clean(it.findtext("title"), 200),
            "link": it.findtext("link"),
            "summary": clean(it.findtext("description")),
            "date": parse_date(it.findtext("pubDate")),
            "source": (it.findtext("source") or source) if is_google else source,
        })
    for e in root.iter(ATOM + "entry"):
        link = e.find(ATOM + "link")
        items.append({
            "title": clean(e.findtext(ATOM + "title"), 200),
            "link": link.get("href") if link is not None else None,
            "summary": clean(e.findtext(ATOM + "summary") or e.findtext(ATOM + "content")),
            "date": parse_date(e.findtext(ATOM + "updated") or e.findtext(ATOM + "published")),
            "source": source,
        })
    return items[:15]


def collect(lang="ko", keyword="", ai=True):
    """기사 모으기. keyword가 있으면 구글 뉴스 검색 결과만 사용 (ai=True면 AI 관련으로 좁힘)"""
    if keyword:
        q = f"{keyword} AI" if ai else keyword
        if lang == "all":
            feeds = [(f"검색: {keyword}", google_news(q, "ko")), (f"검색: {keyword}", google_news(q, "en"))]
        else:
            feeds = [(f"검색: {keyword}", google_news(q, "en" if lang == "en" else "ko"))]
    else:
        feeds = FEEDS["ko"] + FEEDS["en"] if lang == "all" else list(FEEDS.get(lang, FEEDS["ko"]))
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = ex.map(lambda f: fetch_feed(*f), feeds)
    seen, articles = set(), []
    for items in results:
        for a in items:
            key = re.sub(r"\W", "", a["title"].lower())[:60]
            if not a["link"] or key in seen:
                continue
            seen.add(key)
            articles.append(a)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    articles.sort(key=lambda a: a["date"] or epoch, reverse=True)
    for a in articles:
        a["date"] = a["date"].isoformat() if a["date"] else None
    return articles[:40]


# ───────────────────────── 직원별 업무 ─────────────────────────
STAFF = {
    "analyst": ("하나", "분석팀",
                "기사 목록에서 오늘 가장 많이 언급된 기업·기술·이슈 TOP 5를 뽑고, 각각 왜 주목받는지 한 줄로 분석해."),
    "editor": ("도담", "편집팀",
               "기사 목록에서 가장 중요한 뉴스 5개를 골라 '오늘의 AI 브리핑'을 써. 각 뉴스는 제목, 3줄 요약, 출처 링크로."),
    "marketer": ("별", "마케팅팀",
                 "오늘의 AI 뉴스로 인스타그램/스레드에 올릴 게시물 초안 3개를 써. 친근한 말투, 이모지, 해시태그 포함."),
    "planner": ("가온", "기획팀",
                "오늘 뉴스를 보고 대표님이 해볼 만한 일(공부할 것, 써볼 AI 도구, 콘텐츠·사업 아이디어) 3가지를 제안해."),
    "secretary": ("미소", "비서실",
                  "팀원들의 보고를 읽고 대표님께 드릴 '오늘의 핵심 3줄 요약'만 써. 다른 내용은 쓰지 마."),
}

KEYWORDS = {
    "오픈AI·챗GPT": r"오픈ai|openai|챗gpt|chatgpt|gpt-?\d",
    "구글·제미나이": r"구글|google|제미나이|gemini|딥마인드|deepmind",
    "앤트로픽·클로드": r"앤트로픽|anthropic|클로드|claude",
    "엔비디아": r"엔비디아|nvidia|젠슨|jensen",
    "마이크로소프트": r"마이크로소프트|microsoft|코파일럿|copilot",
    "메타": r"메타|meta\b|라마|llama",
    "애플": r"애플|apple",
    "삼성": r"삼성|samsung",
    "SK하이닉스": r"하이닉스|hynix",
    "네이버": r"네이버|naver|하이퍼클로바",
    "반도체·HBM": r"반도체|hbm|칩|chip|gpu",
    "AI 에이전트": r"에이전트|agent",
    "로봇·피지컬AI": r"로봇|robot|휴머노이드|humanoid|피지컬",
    "규제·정책": r"규제|법안|정책|regulat|기본법|정부",
    "투자·인수": r"투자|인수|펀딩|funding|valuation|기업가치|ipo",
    "교육·일자리": r"교육|일자리|채용|job|인재",
    "의료·바이오": r"의료|병원|바이오|헬스|health",
}


def top_keywords(articles, n=5):
    c = Counter()
    for a in articles:
        text = (a["title"] + " " + a["summary"]).lower()
        for name, pat in KEYWORDS.items():
            if re.search(pat, text):
                c[name] += 1
    return c.most_common(n)


def mdt(t):
    return (t or "").replace("[", "(").replace("]", ")")


def link_line(a):
    return f"[{mdt(a['title'])}]({a['link']}) — {a['source']}"


def template_work(role, articles, outputs):
    """AI 두뇌 없이도 할 수 있는 기본 업무"""
    top = articles[:5]
    kws = top_keywords(articles)
    if role == "analyst":
        lines = ["### 📊 오늘의 AI 키워드 TOP 5", ""]
        for i, (k, cnt) in enumerate(kws, 1):
            ex = next((a for a in articles if re.search(KEYWORDS[k], (a["title"] + a["summary"]).lower())), None)
            lines.append(f"{i}. **{k}** — 기사 {cnt}건" + (f"\n   - 예: {link_line(ex)}" if ex else ""))
        lines.append(f"\n총 {len(articles)}건의 기사를 분석했어요.")
        return "\n".join(lines)
    if role == "editor":
        lines = ["### 📰 오늘의 AI 브리핑", ""]
        for i, a in enumerate(top, 1):
            lines.append(f"**{i}. {mdt(a['title'])}**")
            if a["summary"] and not a["summary"].startswith(a["title"][:20]):
                lines.append(f"   {a['summary']}")
            lines.append(f"   🔗 [{a['source']}]({a['link']})\n")
        return "\n".join(lines)
    if role == "marketer":
        tags = " ".join("#" + re.sub(r"[·\s]", "", k.split("·")[0]) for k, _ in kws[:3])
        lines = ["### 📣 SNS 게시물 초안", ""]
        for i, a in enumerate(top[:3], 1):
            lines.append(f"**초안 {i}**\n> 🤖 오늘의 AI 소식!\n> {a['title']}\n> "
                         f"여러분은 어떻게 생각하세요? 👇\n> #AI #인공지능 {tags}\n")
        return "\n".join(lines)
    if role == "planner":
        k1 = kws[0][0] if kws else "AI"
        k2 = kws[1][0] if len(kws) > 1 else "생성형 AI"
        return ("### 💡 대표님께 드리는 제안\n\n"
                f"1. **'{k1}' 공부하기** — 오늘 가장 많이 언급된 주제예요. 관련 기사 2~3개를 읽어보세요.\n"
                f"2. **'{k2}' 콘텐츠 만들기** — 블로그나 SNS에 내 생각을 한 단락 정리해 보세요.\n"
                "3. **AI 도구 하나 써보기** — 오늘 뉴스에 나온 서비스를 10분만 직접 써보세요.\n\n"
                "_(AI 두뇌를 연결하면 더 구체적인 제안을 드릴 수 있어요)_")
    if role == "secretary":
        k = ", ".join(k for k, _ in kws[:3]) or "AI"
        first = top[0]["title"] if top else "-"
        return (f"### 📋 오늘의 핵심 요약\n\n"
                f"- 오늘 수집한 AI 기사는 **{len(articles)}건**이에요.\n"
                f"- 가장 뜨거운 키워드는 **{k}** 입니다.\n"
                f"- 가장 최신 소식: {first}")
    return "알 수 없는 업무예요."


def articles_text(articles, n=25):
    return "\n".join(
        f"{i}. {a['title']} | {a['source']} | {a['date'] or ''} | {a['link']}\n   {a['summary']}"
        for i, a in enumerate(articles[:n], 1))


def current_brain():
    """지금 쓸 수 있는 AI 두뇌: ('ollama', 모델) / ('claude', 모델) / (None, None)"""
    m = ollama_model()
    if m:
        return "ollama", m
    if CLIENT:
        return "claude", MODEL
    return None, None


def do_work(role, articles, outputs, instruction=""):
    if role not in STAFF:
        return {"text": "알 수 없는 직원이에요.", "ai": False}
    name, team, job = STAFF[role]
    kind, model = current_brain()
    if kind:
        system = (f"너는 '나만의 AI 오피스'에서 일하는 {team} 직원 '{name}'이야. "
                  "대표님께 한국어로, 친근하지만 프로답게 보고해. 마크다운을 쓰고 간결하게 써. "
                  "기사 내용에 없는 사실은 지어내지 마.")
        task = instruction or job
        n = 15 if kind == "ollama" else 25          # 내 컴퓨터 AI는 조금 가볍게
        prompt = f"## 오늘의 업무\n{task}\n\n## 수집된 AI 기사\n{articles_text(articles, n)}"
        if outputs:
            prompt += "\n\n## 팀원 보고\n" + "\n\n".join(f"[{k}]\n{v}" for k, v in outputs.items())
        msgs = [{"role": "user", "content": prompt}]
        text = ask_ollama(model, system, msgs) if kind == "ollama" else ask_claude(system, msgs)
        if text:
            return {"text": text, "ai": True}
    return {"text": template_work(role, articles, outputs), "ai": False}


# ───────────────────────── 관리자 (질문에 대답) ─────────────────────────
UA = {"User-Agent": "AI-Office/1.0 (personal desktop app)"}
QUESTION_WORDS = (r"어때요?|뭐\s*하는|무슨|어떤|에\s*대해서?|에\s*관해서?|이?란\s*(무엇|뭐)\S*|무엇인가요|뭔가요|뭐예요|뭐야|무엇|뭐|"
                  r"알려\s*주세요|알려\s*줘|설명해\s*주세요|설명해\s*줘|궁금해요?|어떻게|좀|해\s*줘|요\?|\?")


PARTICLE = r"(이에요|예요|인가요|이란|이야|에서|에게|으로|은|는|이|가|을|를|의|에|로|와|과|도|만|야|란)$"
FILLER = {"하는", "요즘", "최근", "지금", "정말", "진짜", "그", "이", "저", "것", "거"}


def search_candidates(question):
    """질문에서 검색어 후보 만들기: '엔비디아가 뭐하는 회사야?' → ['엔비디아 회사', '엔비디아']"""
    text = re.sub(QUESTION_WORDS, " ", question)
    words = []
    for w in re.findall(r"[\w.+#-]+", text):
        w2 = re.sub(PARTICLE, "", w) if len(w) > 2 else w
        if w2 and w2 not in FILLER:
            words.append(w2)
    cands = []
    for k in (" ".join(words[:4]), " ".join(words[:2]), words[0] if words else ""):
        if k and k not in cands:
            cands.append(k[:40])
    return cands or [question[:40]]


def get_json(url, timeout=8):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def wiki_summary(keyword):
    """한국어 위키백과에서 요약을 찾습니다(무료). 실패하면 None."""
    try:
        q = urllib.parse.quote(keyword)
        hits = get_json(f"https://ko.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}"
                        "&format=json&srlimit=1")["query"]["search"]
        if not hits:
            return None
        title = urllib.parse.quote(hits[0]["title"].replace(" ", "_"))
        d = get_json(f"https://ko.wikipedia.org/api/rest_v1/page/summary/{title}")
        if not d.get("extract"):
            return None
        return {"title": d.get("title"), "text": d["extract"],
                "url": d.get("content_urls", {}).get("desktop", {}).get("page", "")}
    except Exception as e:
        print(f"[관리자] 위키백과 검색 실패: {e}")
        return None


def office_context(articles, outputs):
    parts = []
    if outputs:
        parts.append("## 오늘 팀원들의 보고\n" + "\n\n".join(f"[{k}]\n{v[:1500]}" for k, v in outputs.items()))
    if articles:
        parts.append("## 오늘 수집한 기사\n" + articles_text(articles, 15))
    return "\n\n".join(parts) or "(아직 오늘 업무 전이라 수집한 자료가 없음)"


def clean_history(history):
    """user/assistant 가 번갈아 오도록 정리 (첫 메시지는 user)"""
    msgs = []
    for m in history[-10:]:
        role, text = m.get("role"), (m.get("text") or "")[:2000]
        if role not in ("user", "assistant") or not text:
            continue
        if not msgs and role != "user":
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + text
        else:
            msgs.append({"role": role, "content": text})
    return msgs


def basic_answer(question, articles, outputs):
    """AI 두뇌 없이: 오늘 보고서 / 위키백과 / 뉴스 검색으로 대답"""
    q = question.strip()
    if outputs and re.search(r"보고서|오늘.*(요약|뉴스|소식|일)|키워드|트렌드|브리핑", q):
        ans = "대표님, 오늘 팀 보고 내용이에요.\n\n" + outputs.get("secretary", "")
        if "analyst" in outputs:
            ans += "\n\n" + outputs["analyst"]
        return ans
    cands = search_candidates(q)
    keyword, parts, wiki, news = cands[0], [], None, []
    for k in cands:                       # 긴 검색어부터 시도하고, 없으면 핵심 단어로
        wiki = wiki or wiki_summary(k)
        news = collect("ko", k, ai=False)[:5]
        if news or wiki:
            keyword = k
            break
    if wiki:
        parts.append(f"### 📚 {wiki['title']}\n{wiki['text']}\n\n🔗 [위키백과에서 더 보기]({wiki['url']})")
    if news:
        parts.append(f"### 📰 '{keyword}' 관련 최신 뉴스\n" + "\n".join(f"- {link_line(a)}" for a in news))
    if not parts:
        return (f"죄송해요 대표님, '{keyword}'에 대한 자료를 찾지 못했어요. 😢\n"
                "다른 단어로 물어봐 주시겠어요?")
    return (f"대표님, '{keyword}'에 대해 찾아봤어요.\n\n" + "\n\n".join(parts) +
            "\n\n_💡 무료 AI(Ollama)를 연결하면 제가 직접 생각해서 더 자연스럽게 대답해 드려요._")


def answer_question(question, history, articles, outputs):
    kind, model = current_brain()
    if kind:
        system = ("너는 '나만의 AI 오피스'의 관리자 '한결'이야. 대표님의 어떤 질문이든 한국어로 친절하고 정확하게 대답해. "
                  "모르는 것은 모른다고 솔직히 말하고, 지어내지 마. 필요하면 마크다운으로 정리해. "
                  "오늘 회사 자료가 질문과 관련 있으면 활용해.\n\n" + office_context(articles, outputs))
        msgs = clean_history(history) + [{"role": "user", "content": question}]
        if len(msgs) > 1 and msgs[-2]["role"] == "user":      # 연속 user 방지
            msgs[-2]["content"] += "\n" + msgs.pop()["content"]
        text = ask_ollama(model, system, msgs) if kind == "ollama" else ask_claude(system, msgs)
        if text:
            return {"answer": text, "source": kind}
    return {"answer": basic_answer(question, articles, outputs), "source": "basic"}


# ───────────────────────── 결과물 저장 ─────────────────────────
def save_report(markdown):
    RESULTS.mkdir(exist_ok=True)
    name = datetime.now().strftime("%Y-%m-%d_%H%M") + "_AI오피스_보고서.md"
    (RESULTS / name).write_text(markdown, encoding="utf-8")
    return name


def list_reports():
    if not RESULTS.exists():
        return []
    return sorted((p.name for p in RESULTS.glob("*.md")), reverse=True)[:30]


# ───────────────────────── 웹 서버 ─────────────────────────
PAGES = {"/": "index.html", "/nuri": "nuri.html"}
EMBEDDED = {}  # build.py가 화면 파일을 여기에 넣어 하나의 파일로 만듭니다


def page_bytes(name):
    return EMBEDDED[name].encode("utf-8") if name in EMBEDDED else (HERE / name).read_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)
        if url.path in PAGES:
            return self._send(200, "text/html; charset=utf-8", page_bytes(PAGES[url.path]))
        if url.path == "/api/status":
            kind, model = current_brain()
            return self._json({"ai": kind is not None, "kind": kind, "model": model,
                               "free": kind != "claude"})
        if url.path == "/api/news":
            return self._json(collect(qs.get("lang", ["ko"])[0], qs.get("q", [""])[0].strip()[:50]))
        if url.path == "/api/reports":
            return self._json(list_reports())
        if url.path == "/api/report":
            name = Path(qs.get("name", [""])[0]).name  # 폴더 밖 접근 방지
            p = RESULTS / name
            if p.suffix == ".md" and p.exists():
                return self._send(200, "text/plain; charset=utf-8", p.read_bytes())
        self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, "text/plain", b"bad json")
        if self.path == "/api/work":
            articles = body.get("articles") or []
            instruction = (body.get("instruction") or "").strip()[:500]
            if instruction and current_brain()[0] is None:   # 기본 모드: 지시를 키워드 검색으로 처리
                articles = collect("ko", instruction[:30]) or articles
            return self._json(do_work(body.get("role"), articles, body.get("outputs") or {}, instruction))
        if self.path == "/api/ask":
            q = (body.get("question") or "").strip()[:1000]
            if not q:
                return self._json({"answer": "무엇이 궁금하신가요?", "source": "basic"})
            return self._json(answer_question(q, body.get("history") or [], body.get("articles") or [],
                                              body.get("outputs") or {}))
        if self.path == "/api/save":
            return self._json({"name": save_report(body.get("markdown", ""))})
        self._send(404, "text/plain", b"Not found")

    def _json(self, obj):
        self._send(200, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode())

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    addr = f"http://localhost:{PORT}"
    print(f"🏢 AI 오피스가 문을 열었습니다: {addr}  (종료: Ctrl+C)")
    kind, model = current_brain()
    if kind == "ollama":
        print(f"🧠 AI 두뇌: 무료 AI 연결됨 (Ollama · {model})")
    elif kind == "claude":
        print(f"🧠 AI 두뇌: Claude 연결됨 (유료 · {model})")
    else:
        print("🧠 AI 두뇌: 기본 모드 (무료). Ollama를 설치하면 무료 AI 두뇌가 켜져요 → https://ollama.com")
    threading.Timer(1, lambda: webbrowser.open(addr)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n오늘도 수고하셨습니다. 👋")
