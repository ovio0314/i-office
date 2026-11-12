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


def ask_ollama(model, system, prompt):
    body = json.dumps({
        "model": model, "stream": False,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
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


def ask_claude(system, prompt):
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
            messages=[{"role": "user", "content": prompt}],
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


def collect(lang="ko", keyword=""):
    feeds = FEEDS["ko"] + FEEDS["en"] if lang == "all" else list(FEEDS.get(lang, FEEDS["ko"]))
    if keyword:
        feeds = [(f"검색: {keyword}", google_news(f"{keyword} AI", "en" if lang == "en" else "ko"))] + feeds
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = ex.map(lambda f: fetch_feed(*f), feeds)
    seen, articles = set(), []
    for items in results:
        for a in items:
            key = re.sub(r"\W", "", a["title"].lower())[:60]
            if not a["link"] or key in seen:
                continue
            if keyword and keyword.lower() not in (a["title"] + a["summary"]).lower() \
                    and not a["source"].startswith("검색"):
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
        text = ask_ollama(model, system, prompt) if kind == "ollama" else ask_claude(system, prompt)
        if text:
            return {"text": text, "ai": True}
    return {"text": template_work(role, articles, outputs), "ai": False}


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
EMBEDDED = {'index.html': '<!DOCTYPE html>\n<html lang="ko">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>나만의 AI Office</title>\n<style>\n  :root{\n    --bg:#fbf3f6; --ink:#3b2f3a; --muted:#8c7a88; --line:#ecd9e2; --card:#fff;\n    --accent:#e48bb0; --accent2:#f6d76b; --floor:#f7eef0; --floor2:#f2e6ea;\n  }\n  *{box-sizing:border-box}\n  body{margin:0;background:var(--bg);color:var(--ink);\n       font-family:-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}\n  .wrap{max-width:1240px;margin:0 auto;padding:18px 16px 60px}\n\n  /* ── 헤더 ── */\n  header{display:flex;gap:16px;align-items:center;flex-wrap:wrap;background:var(--card);\n         border:1px solid var(--line);border-radius:18px;padding:14px 18px}\n  .logo{width:46px;height:46px;border-radius:12px;background:linear-gradient(135deg,#e48bb0,#b38bea);\n        display:grid;place-items:center;color:#fff;font-weight:800;font-size:22px}\n  .live{font-size:11px;letter-spacing:.12em;color:var(--muted);font-weight:700}\n  .live b{color:#e2557f}\n  h1{margin:2px 0 0;font-size:24px;cursor:pointer}\n  h1:hover{color:var(--accent)}\n  .head-right{margin-left:auto;display:flex;gap:10px;align-items:center;flex-wrap:wrap}\n  .badge{padding:6px 12px;border-radius:999px;font-size:13px;border:1px solid var(--line);background:#fff}\n  .badge.on{background:#e9f8ef;border-color:#a7e0bd;color:#227a47}\n  .badge.off{background:#fff6e5;border-color:#f3d39b;color:#9a6a12}\n  .clock{font-variant-numeric:tabular-nums;font-weight:700;font-size:18px}\n\n  /* ── 컨트롤 ── */\n  .controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0}\n  .btn{border:1px solid var(--line);background:#fff;color:var(--ink);padding:11px 16px;border-radius:12px;\n       font-size:15px;font-weight:600;cursor:pointer;transition:.15s}\n  .btn:hover{transform:translateY(-1px);box-shadow:0 4px 10px rgba(228,139,176,.2)}\n  .btn.main{background:var(--accent2);border-color:#e9c443}\n  .btn:disabled{opacity:.5;cursor:not-allowed;transform:none;box-shadow:none}\n  .seg{display:flex;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#fff}\n  .seg button{border:0;background:none;padding:11px 13px;cursor:pointer;font-weight:600;color:var(--muted)}\n  .seg button.on{background:var(--accent);color:#fff}\n  select{padding:10px 12px;border-radius:12px;border:1px solid var(--line);background:#fff;font-size:14px;color:var(--ink)}\n\n  /* ── 메인 그리드 ── */\n  .main{display:grid;grid-template-columns:1fr 330px;gap:14px}\n  @media (max-width:980px){.main{grid-template-columns:1fr}}\n\n  /* ── 사무실 지도 ── */\n  .office{position:relative;aspect-ratio:16/10;border-radius:18px;overflow:hidden;border:1px solid var(--line);\n          background:\n            repeating-linear-gradient(0deg,transparent 0 39px,rgba(0,0,0,.025) 39px 40px),\n            repeating-linear-gradient(90deg,transparent 0 39px,rgba(0,0,0,.025) 39px 40px),var(--floor)}\n  .room{position:absolute;border:3px solid #e4cdd8;border-radius:14px}\n  .room .label{position:absolute;top:6px;left:10px;font-size:12px;font-weight:700;color:var(--muted);\n               background:rgba(255,255,255,.8);padding:2px 8px;border-radius:8px}\n  .r-work{left:2%;top:4%;width:61%;height:68%;background:rgba(255,255,255,.45)}\n  .r-meet{left:66%;top:4%;width:32%;height:68%;background:#fdf6e8;border-color:#efdcae}\n  .r-lounge{left:66%;top:80%;width:32%;height:17%;background:#eef7f1;border-color:#c7e6d2}\n  .door{position:absolute;left:36%;bottom:0;width:8%;height:2.2%;background:#c99c7a;border-radius:6px 6px 0 0}\n  .door::after{content:"입구";position:absolute;top:-20px;left:50%;transform:translateX(-50%);font-size:11px;color:var(--muted)}\n\n  .desk{position:absolute;width:12%;height:9%;transform:translate(-50%,-50%);background:#e8c9a8;border-radius:8px;\n        box-shadow:0 3px 0 #cfa883}\n  .desk .mon{position:absolute;left:50%;top:-38%;width:46%;height:70%;transform:translateX(-50%);\n             background:#4b4453;border-radius:5px;border:3px solid #6b6274}\n  .desk.working .mon{background:#9fe3ff;box-shadow:0 0 14px #9fe3ff;animation:screen 1s steps(2) infinite}\n  @keyframes screen{50%{background:#c6f0ff}}\n  .desk .plate{position:absolute;bottom:-26px;left:50%;transform:translateX(-50%);font-size:11px;white-space:nowrap;\n               color:var(--muted)}\n  .table{position:absolute;left:82%;top:37%;width:16%;height:22%;transform:translate(-50%,-50%);\n         background:#f1d9a6;border-radius:50%;box-shadow:0 4px 0 #d9bd84}\n  .board{position:absolute;left:73%;top:9%;width:18%;height:6%;background:#fff;border:3px solid #ddd;border-radius:6px;\n         font-size:10px;display:grid;place-items:center;color:#999}\n  .deco{position:absolute;font-size:clamp(14px,2.2vw,26px);transform:translate(-50%,-50%);user-select:none}\n\n  /* ── 직원 캐릭터 ── */\n  .emp{position:absolute;transform:translate(-50%,-92%);width:5.2%;cursor:pointer;z-index:5;\n       transition-property:left,top;transition-timing-function:linear}\n  .emp svg{width:100%;display:block;filter:drop-shadow(0 3px 2px rgba(0,0,0,.15))}\n  .emp:hover svg{filter:drop-shadow(0 0 6px var(--accent))}\n  .emp.sel svg{filter:drop-shadow(0 0 7px #e2557f)}\n  .emp .tag{position:absolute;left:50%;top:100%;transform:translateX(-50%);font-size:11px;font-weight:700;\n            background:#fff;border:1px solid var(--line);border-radius:8px;padding:1px 6px;white-space:nowrap}\n  .emp.walking .leg-l{animation:legL .3s infinite alternate} .emp.walking .leg-r{animation:legR .3s infinite alternate}\n  @keyframes legL{to{transform:translateY(-3px)}} @keyframes legR{from{transform:translateY(-3px)}}\n  .emp.typing svg{animation:type .25s infinite alternate}\n  @keyframes type{to{transform:translateY(-2px)}}\n  .emp.hidden{opacity:0;pointer-events:none}\n  .emp .say{position:absolute;bottom:104%;left:50%;transform:translateX(-50%);background:#fff;border:1px solid var(--line);\n            border-radius:10px;padding:4px 8px;font-size:11.5px;white-space:nowrap;max-width:220px;overflow:hidden;\n            text-overflow:ellipsis;box-shadow:0 3px 8px rgba(0,0,0,.08);opacity:0;transition:opacity .2s;pointer-events:none}\n  .emp .say.show{opacity:1}\n\n  .phase{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);background:rgba(59,47,58,.85);color:#fff;\n         padding:14px 26px;border-radius:14px;font-size:20px;font-weight:700;opacity:0;transition:.3s;pointer-events:none;z-index:20}\n  .phase.show{opacity:1}\n\n  /* ── 사이드 패널 ── */\n  .side{display:flex;flex-direction:column;gap:14px;min-width:0}\n  .panel{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:14px 16px}\n  .panel h3{margin:0 0 10px;font-size:15px}\n  #log{height:300px;overflow:auto;font-size:13px;line-height:1.55}\n  #log div{padding:3px 0;border-bottom:1px dashed #f1e4ea}\n  #log .t{color:var(--muted);font-variant-numeric:tabular-nums;margin-right:6px}\n  .staff-card .top{display:flex;gap:12px;align-items:center}\n  .staff-card .avatar{width:56px;flex:none}\n  .staff-card .role{color:var(--muted);font-size:13px}\n  .staff-card .out{max-height:260px;overflow:auto;font-size:13px;margin-top:10px;background:#fcf8fa;border-radius:10px;padding:8px 10px}\n  .order{display:flex;gap:6px;margin-top:10px}\n  .order input{flex:1;min-width:0;padding:9px 10px;border:1px solid var(--line);border-radius:10px;font-size:13px}\n  .order button{padding:9px 12px;border:0;border-radius:10px;background:var(--accent);color:#fff;font-weight:700;cursor:pointer}\n  .hint{color:var(--muted);font-size:13px}\n\n  /* ── 결과물 ── */\n  .results{margin-top:14px;display:grid;grid-template-columns:1fr 280px;gap:14px}\n  @media (max-width:980px){.results{grid-template-columns:1fr}}\n  .main>*,.results>*{min-width:0}\n  #report,.staff-card .out{overflow-wrap:anywhere}\n  #report{font-size:14.5px;line-height:1.65;min-height:120px}\n  #report h1{font-size:22px;cursor:auto} #report h2{font-size:18px;margin-top:22px} #report h3{font-size:16px;margin:18px 0 6px}\n  #report a{color:#c2457a} #report blockquote{margin:6px 0;padding:6px 12px;background:#fdf3f7;border-left:3px solid var(--accent);border-radius:6px}\n  #reports button{display:block;width:100%;text-align:left;border:0;background:#fcf8fa;padding:8px 10px;border-radius:8px;\n                  margin-bottom:6px;cursor:pointer;font-size:13px;color:var(--ink)}\n  #reports button:hover{background:#f7e5ed}\n</style>\n</head>\n<body>\n<div class="wrap">\n  <header>\n    <div class="logo">AI</div>\n    <div>\n      <div class="live"><b>●</b> LIVE OFFICE · <span id="staffCount">6</span> AI STAFF</div>\n      <h1 id="company" title="클릭해서 회사 이름 바꾸기"></h1>\n    </div>\n    <div class="head-right">\n      <span class="badge" id="brain">🧠 확인 중...</span>\n      <span class="clock" id="clock">08:50</span>\n    </div>\n  </header>\n\n  <div class="controls">\n    <button class="btn main" id="startBtn">▶ 오늘 업무 시작하기</button>\n    <button class="btn" id="pauseBtn">⏸ 일시정지</button>\n    <div class="seg" id="speed">\n      <button data-s="1" class="on">1x</button><button data-s="2">2x</button><button data-s="4">4x</button>\n    </div>\n    <select id="lang" title="리서치 범위">\n      <option value="ko">🇰🇷 국내 뉴스</option>\n      <option value="all">🌏 국내+해외</option>\n      <option value="en">🌎 해외 뉴스</option>\n    </select>\n    <button class="btn" id="homeBtn">🏠 퇴근</button>\n    <a class="btn" href="/nuri" style="text-decoration:none">🤖 누리 뉴스창</a>\n  </div>\n\n  <div class="main">\n    <div class="office" id="office">\n      <div class="room r-work"><span class="label">💻 업무 공간</span></div>\n      <div class="room r-meet"><span class="label">🗂 회의실</span></div>\n      <div class="room r-lounge"><span class="label">☕ 라운지</span></div>\n      <div class="board" id="board">오늘의 안건</div>\n      <div class="table"></div>\n      <div class="door"></div>\n      <div class="deco" style="left:4.5%;top:76%">🪴</div>\n      <div class="deco" style="left:60%;top:76%">🪴</div>\n      <div class="deco" style="left:70%;top:89%">☕</div>\n      <div class="deco" style="left:84%;top:90%">🛋️</div>\n      <div class="deco" style="left:95%;top:89%">🪴</div>\n      <div class="deco" style="left:95%;top:64%">📚</div>\n      <div class="phase" id="phase"></div>\n    </div>\n\n    <div class="side">\n      <div class="panel staff-card" id="staffCard">\n        <h3>👥 직원 정보</h3>\n        <p class="hint">사무실의 직원을 클릭하면 프로필과 오늘 한 일을 볼 수 있고, 따로 업무를 지시할 수도 있어요.</p>\n      </div>\n      <div class="panel">\n        <h3>📝 업무 일지</h3>\n        <div id="log"></div>\n      </div>\n    </div>\n  </div>\n\n  <div class="results">\n    <div class="panel">\n      <h3>📄 오늘의 보고서</h3>\n      <div id="report" class="hint">아직 보고서가 없어요. \'오늘 업무 시작하기\'를 눌러보세요!</div>\n    </div>\n    <div class="panel">\n      <h3>📁 업무결과 폴더</h3>\n      <div id="reports" class="hint">불러오는 중...</div>\n    </div>\n  </div>\n</div>\n\n<script>\n/* ───────── 직원 명단 (이름·색·업무를 마음대로 바꿔보세요) ───────── */\nconst STAFF = [\n  {id:\'researcher\', name:\'누리\', team:\'리서치팀\', emoji:\'🔎\', hair:\'#6b4a3a\', shirt:\'#8fb8ff\',\n   job:\'여러 뉴스 사이트에서 최신 AI 기사를 수집해요.\', plan:\'최신 AI 기사 모아올게요!\', desk:[13,24]},\n  {id:\'analyst\', name:\'하나\', team:\'분석팀\', emoji:\'📊\', hair:\'#2f2a36\', shirt:\'#9be0c2\',\n   job:\'오늘 가장 많이 언급된 키워드와 트렌드를 분석해요.\', plan:\'오늘의 트렌드 분석할게요.\', desk:[32,24]},\n  {id:\'editor\', name:\'도담\', team:\'편집팀\', emoji:\'✍️\', hair:\'#c9853f\', shirt:\'#ffc79b\',\n   job:\'중요한 뉴스 5개로 오늘의 AI 브리핑을 작성해요.\', plan:\'브리핑 기사 쓸게요.\', desk:[51,24]},\n  {id:\'marketer\', name:\'별\', team:\'마케팅팀\', emoji:\'📣\', hair:\'#e46f9b\', shirt:\'#ffe08a\',\n   job:\'오늘 뉴스로 SNS 게시물 초안을 만들어요.\', plan:\'SNS 게시물 만들게요!\', desk:[13,52]},\n  {id:\'planner\', name:\'가온\', team:\'기획팀\', emoji:\'💡\', hair:\'#3d5a80\', shirt:\'#c7b3ff\',\n   job:\'대표님이 해볼 만한 일과 아이디어를 제안해요.\', plan:\'아이디어 기획할게요.\', desk:[32,52]},\n  {id:\'secretary\', name:\'미소\', team:\'비서실\', emoji:\'📋\', hair:\'#8a5a44\', shirt:\'#ffb3c7\',\n   job:\'팀원들의 보고를 모아 최종 보고서를 정리하고 저장해요.\', plan:\'보고서 정리해서 드릴게요.\', desk:[51,52]},\n];\nconst DOOR = [40, 99], HALL_Y = 76;\nconst MEET_SEATS = [[74,28],[82,22],[90,28],[74,47],[82,53],[90,47]];\nconst LOUNGE = [[70,93],[75,93],[80,93],[87,93],[91,93],[78,88]];\n\n/* ───────── 상태 ───────── */\nlet speed = 1, paused = false, running = false, inOffice = false;\nlet articles = [], outputs = {}, selected = null, officeMin = 8*60+50;\nconst $ = id => document.getElementById(id);\nconst office = $(\'office\');\n\n/* ───────── 회사 이름 ───────── */\nfunction setCompany(n){ $(\'company\').textContent = n; document.title = n; }\nsetCompany(localStorage.getItem(\'company\') || \'나만의 AI Office\');\n$(\'company\').onclick = () => {\n  const n = prompt(\'회사 이름을 정해주세요\', $(\'company\').textContent);\n  if (n && n.trim()) { localStorage.setItem(\'company\', n.trim()); setCompany(n.trim()); }\n};\n\n/* ───────── 캐릭터 그리기 ───────── */\nfunction charSVG(s){\n  return `<svg viewBox="0 0 40 56">\n    <rect class="leg-l" x="13" y="44" width="5" height="10" rx="2" fill="#5b5266"/>\n    <rect class="leg-r" x="22" y="44" width="5" height="10" rx="2" fill="#5b5266"/>\n    <rect x="8" y="28" width="24" height="19" rx="8" fill="${s.shirt}"/>\n    <circle cx="20" cy="18" r="12" fill="#ffe0c7"/>\n    <path d="M8 18 Q8 4 20 5 Q32 4 32 18 Q28 10 20 11 Q12 10 8 18Z" fill="${s.hair}"/>\n    <circle cx="15.5" cy="19" r="1.7" fill="#3b2f3a"/><circle cx="24.5" cy="19" r="1.7" fill="#3b2f3a"/>\n    <circle cx="12.5" cy="23" r="2" fill="#ffadc6" opacity=".7"/><circle cx="27.5" cy="23" r="2" fill="#ffadc6" opacity=".7"/>\n    <path d="M17.5 24 Q20 26.5 22.5 24" stroke="#3b2f3a" stroke-width="1.3" fill="none" stroke-linecap="round"/>\n  </svg>`;\n}\n\nSTAFF.forEach((s, i) => {\n  const d = document.createElement(\'div\');\n  d.className = \'desk\'; d.style.left = s.desk[0]+\'%\'; d.style.top = s.desk[1]+\'%\';\n  d.innerHTML = `<div class="mon"></div><div class="plate">${s.emoji} ${s.team}</div>`;\n  office.appendChild(d); s.deskEl = d;\n\n  const e = document.createElement(\'div\');\n  e.className = \'emp hidden\';\n  e.innerHTML = `<div class="say"></div>${charSVG(s)}<div class="tag">${s.name}</div>`;\n  office.appendChild(e); s.el = e; s.pos = [...DOOR]; s.status = \'출근 전\';\n  place(s, DOOR);\n  e.onclick = () => showStaff(s);\n});\n$(\'staffCount\').textContent = STAFF.length;\n\n/* ───────── 이동·시간 도구 ───────── */\nfunction place(s, [x, y]){ s.el.style.transitionDuration = \'0s\'; s.el.style.left = x+\'%\'; s.el.style.top = y+\'%\'; s.pos=[x,y]; }\nasync function gate(){ while (paused) await new Promise(r => setTimeout(r, 150)); }\nasync function wait(ms){\n  await gate();\n  const real = ms / speed;\n  await new Promise(r => setTimeout(r, real));\n  tickClock(ms / 1000 * 4);\n}\nfunction tickClock(min){\n  officeMin = Math.min(officeMin + min, 23*60+59);\n  const h = Math.floor(officeMin/60), m = Math.floor(officeMin%60);\n  $(\'clock\').textContent = `${String(h).padStart(2,\'0\')}:${String(m).padStart(2,\'0\')}`;\n}\nfunction setClock(h, m){ officeMin = Math.max(officeMin, h*60+m); tickClock(0); }\n\nasync function step(s, [x, y]){\n  await gate();\n  const [x0, y0] = s.pos, dist = Math.hypot(x-x0, y-y0);\n  if (dist < .3) return;\n  const sec = dist / 22 / speed;\n  s.el.style.transitionDuration = sec + \'s\';\n  s.el.style.left = x+\'%\'; s.el.style.top = y+\'%\'; s.pos = [x, y];\n  await new Promise(r => setTimeout(r, sec*1000));\n}\nasync function walk(s, to){            // 복도를 따라 걷기\n  s.el.classList.add(\'walking\'); s.el.classList.remove(\'typing\');\n  if (Math.abs(s.pos[1]-HALL_Y) > 1) await step(s, [s.pos[0], HALL_Y]);\n  await step(s, [to[0], HALL_Y]);\n  await step(s, to);\n  s.el.classList.remove(\'walking\');\n}\nconst seat = s => [s.desk[0], s.desk[1]+8];\n\nfunction say(s, text, ms = 2600){\n  const b = s.el.querySelector(\'.say\');\n  b.textContent = text; b.classList.add(\'show\');\n  clearTimeout(s.sayT);\n  if (ms) s.sayT = setTimeout(() => b.classList.remove(\'show\'), ms / speed);\n}\nfunction setStatus(s, st){ s.status = st; if (selected === s) showStaff(s); }\nfunction log(text){\n  const d = document.createElement(\'div\');\n  d.innerHTML = `<span class="t">${$(\'clock\').textContent}</span>${text}`;\n  $(\'log\').prepend(d);\n}\nasync function phase(text){\n  const p = $(\'phase\'); p.textContent = text; p.classList.add(\'show\');\n  await wait(1400); p.classList.remove(\'show\');\n}\nconst byId = id => STAFF.find(s => s.id === id);\n\n/* ───────── 서버 통신 ───────── */\nasync function api(path, body){\n  const r = await fetch(path, body ? {method:\'POST\', headers:{\'Content-Type\':\'application/json\'},\n                                      body: JSON.stringify(body)} : undefined);\n  if (!r.ok) throw new Error(r.status);\n  return r.json();\n}\nasync function checkBrain(){\n  try {\n    const s = await api(\'/api/status\');\n    const b = $(\'brain\');\n    if (s.kind === \'ollama\') {\n      b.className = \'badge on\'; b.textContent = \'🧠 무료 AI 연결됨\';\n      b.title = `내 컴퓨터의 Ollama (${s.model}) — 요금 0원`;\n    } else if (s.kind === \'claude\') {\n      b.className = \'badge on\'; b.textContent = \'🧠 Claude 연결됨 (유료)\'; b.title = `모델: ${s.model}`;\n    } else {\n      b.className = \'badge off\'; b.textContent = \'🧠 기본 모드 (무료)\';\n      b.title = \'Ollama(무료)를 설치하고 모델을 받으면 AI 두뇌가 자동으로 켜져요 → ollama.com\';\n    }\n  } catch { $(\'brain\').textContent = \'⚠️ 서버 꺼짐\'; $(\'brain\').className = \'badge off\'; }\n}\nsetInterval(() => { if (!running) checkBrain(); }, 20000);   // Ollama를 나중에 켜도 자동 감지\n\n/* ───────── 하루 일과 ───────── */\nasync function startDay(){\n  if (running) return;\n  running = true; $(\'startBtn\').disabled = true; $(\'homeBtn\').disabled = true;\n  outputs = {}; articles = [];\n  officeMin = inOffice ? 9*60+25 : 8*60+55; tickClock(0);   // 매번 새로운 하루\n  try {\n    // 1) 출근\n    if (!inOffice) {\n      await phase(\'☀️ 출근 시간\');\n      log(\'🏢 오피스 문이 열렸어요.\');\n      await Promise.all(STAFF.map(async (s, i) => {\n        await wait(i * 700);\n        place(s, DOOR); s.el.classList.remove(\'hidden\');\n        say(s, \'좋은 아침이에요!\');\n        await walk(s, seat(s));\n        setStatus(s, \'자리에서 대기 중\');\n        log(`${s.emoji} <b>${s.name}</b> 출근했습니다.`);\n      }));\n      inOffice = true;\n    }\n\n    // 2) 아침 회의\n    setClock(9, 30);\n    await phase(\'🗂 아침 회의\');\n    $(\'board\').textContent = \'📌 오늘의 AI 뉴스\';\n    await Promise.all(STAFF.map((s, i) => walk(s, MEET_SEATS[i])));\n    const sec = byId(\'secretary\');\n    say(sec, \'대표님, 오늘 회의 시작할게요!\'); log(\'🗂 아침 회의가 시작됐어요.\');\n    await wait(1800);\n    for (const s of STAFF) { say(s, s.plan, 2000); setStatus(s, \'회의 중\'); await wait(1100); }\n\n    // 3) 업무 시작\n    setClock(10, 0);\n    await phase(\'💻 업무 시작\');\n    await Promise.all(STAFF.map(s => walk(s, seat(s))));\n\n    // 3-1) 리서치\n    const res = byId(\'researcher\');\n    startWork(res, \'기사 수집 중... 🔎\');\n    articles = await api(`/api/news?lang=${$(\'lang\').value}`);\n    stopWork(res);\n    outputs.researcher = `### 🔎 수집한 기사 ${articles.length}건\\n\\n` +\n      articles.slice(0, 10).map(a => `- [${mdt(a.title)}](${a.link}) — ${a.source}`).join(\'\\n\');\n    say(res, `기사 ${articles.length}개 모았어요!`); setStatus(res, \'수집 완료\');\n    log(`🔎 <b>누리</b>가 기사 ${articles.length}건을 수집했어요.`);\n    if (!articles.length) throw new Error(\'기사가 없어요\');\n\n    // 누리가 자료를 나눠줌\n    await walk(res, [41, 38]); say(res, \'자료 공유했어요! 📎\'); await wait(1200);\n    walk(res, seat(res));\n\n    // 3-2) 팀별 업무 (동시에 진행)\n    const team = [\'analyst\', \'editor\', \'marketer\', \'planner\'].map(byId);\n    await Promise.all(team.map(async s => {\n      startWork(s, \'작업 중... ⌨️\');\n      const r = await api(\'/api/work\', {role: s.id, articles});\n      stopWork(s);\n      outputs[s.id] = r.text;\n      say(s, \'완료했어요! ✅\'); setStatus(s, \'업무 완료\');\n      log(`${s.emoji} <b>${s.name}</b> 업무 완료${r.ai ? \' (AI)\' : \'\'}.`);\n    }));\n\n    // 3-3) 비서 최종 정리\n    startWork(sec, \'보고서 정리 중... 📋\');\n    const r = await api(\'/api/work\', {role: \'secretary\', articles, outputs});\n    outputs.secretary = r.text;\n    const md = buildReport();\n    const saved = await api(\'/api/save\', {markdown: md});\n    stopWork(sec); setStatus(sec, \'보고서 저장 완료\');\n    log(`📋 <b>미소</b>가 보고서를 저장했어요: <i>${saved.name}</i>`);\n\n    // 4) 보고\n    setClock(17, 0);\n    await phase(\'📣 업무 보고\');\n    $(\'board\').textContent = \'✅ 오늘 업무 완료\';\n    await Promise.all(STAFF.map((s, i) => walk(s, MEET_SEATS[i])));\n    say(sec, \'대표님, 오늘 보고서 완성했어요! 💖\', 3500);\n    renderReport(md); loadReports();\n    await wait(2500);\n    STAFF.forEach(s => say(s, \'수고하셨습니다!\', 2000));\n    await wait(1500);\n\n    // 5) 휴식\n    await Promise.all(STAFF.map((s, i) => walk(s, LOUNGE[i])));\n    STAFF.forEach(s => setStatus(s, \'라운지에서 휴식 중\'));\n    log(\'☕ 모두 라운지에서 쉬는 중이에요. 직원을 클릭해 추가 업무를 지시할 수 있어요.\');\n  } catch (e) {\n    log(`⚠️ 문제가 생겼어요: ${e.message}. 서버(assistant.py)가 켜져 있는지 확인해 주세요.`);\n    STAFF.forEach(stopWork);\n  }\n  running = false; $(\'startBtn\').disabled = false; $(\'homeBtn\').disabled = false;\n  $(\'startBtn\').textContent = \'▶ 다시 업무 시작하기\';\n}\n\nfunction startWork(s, text){ s.el.classList.add(\'typing\'); s.deskEl.classList.add(\'working\'); say(s, text, 0); setStatus(s, text); }\nfunction stopWork(s){ s.el.classList.remove(\'typing\'); s.deskEl.classList.remove(\'working\'); }\n\nasync function goHome(){\n  if (running || !inOffice) return;\n  running = true;\n  setClock(18, 0); await phase(\'🌙 퇴근 시간\');\n  await Promise.all(STAFF.map(async (s, i) => {\n    await wait(i * 400); say(s, \'내일 봬요! 👋\'); await walk(s, DOOR);\n    s.el.classList.add(\'hidden\'); setStatus(s, \'퇴근\');\n  }));\n  log(\'🌙 모두 퇴근했어요. 오늘도 수고하셨습니다!\');\n  inOffice = false; running = false;\n}\n\n/* ───────── 보고서 ───────── */\nconst mdt = t => (t || \'\').replace(/\\[/g, \'(\').replace(/\\]/g, \')\');\nfunction buildReport(){\n  const today = new Date().toLocaleDateString(\'ko-KR\', {year:\'numeric\', month:\'long\', day:\'numeric\', weekday:\'long\'});\n  const part = (id) => outputs[id] ? `\\n\\n${outputs[id]}\\n` : \'\';\n  return `# ${$(\'company\').textContent} 업무 보고서\\n_${today}_\\n` +\n    part(\'secretary\') + part(\'editor\') + part(\'analyst\') + part(\'marketer\') + part(\'planner\') +\n    `\\n\\n## 📎 수집 기사 목록\\n` + articles.slice(0, 20).map(a => `- [${mdt(a.title)}](${a.link}) — ${a.source}`).join(\'\\n\');\n}\n\nfunction md2html(md){\n  const esc = s => s.replace(/[&<>"]/g, c => ({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\'}[c]));\n  const inline = s => esc(s)\n    .replace(/\\[([^\\]]+)\\]\\((https?:[^)\\s]+)\\)/g, \'<a href="$2" target="_blank" rel="noopener">$1</a>\')\n    .replace(/\\*\\*([^*]+)\\*\\*/g, \'<b>$1</b>\')\n    .replace(/(^|\\s)_([^_]+)_(?=\\s|$)/g, \'$1<i>$2</i>\');\n  let html = \'\', list = null;\n  const close = () => { if (list) { html += list === \'bq\' ? \'</blockquote>\' : `</${list}>`; list = null; } };\n  for (const raw of md.split(\'\\n\')) {\n    const line = raw.trimEnd();\n    let m;\n    if ((list === \'ol\' || list === \'ul\') && /^\\s{2,}\\S/.test(line)) {          // 들여쓴 줄 = 항목의 이어지는 내용\n      html = html.replace(/<\\/li>$/, `<div>${inline(line.trim().replace(/^[-*]\\s+/, \'\'))}</div></li>`);\n    }\n    else if ((m = line.match(/^(#{1,4})\\s+(.*)/))) { close(); html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; }\n    else if ((m = line.match(/^[-*]\\s+(.*)/))) { if (list !== \'ul\') { close(); html += \'<ul>\'; list = \'ul\'; } html += `<li>${inline(m[1])}</li>`; }\n    else if ((m = line.match(/^\\d+\\.\\s+(.*)/))) { if (list !== \'ol\') { close(); html += \'<ol>\'; list = \'ol\'; } html += `<li>${inline(m[1])}</li>`; }\n    else if ((m = line.match(/^>\\s?(.*)/))) { if (list !== \'bq\') { close(); html += \'<blockquote>\'; list = \'bq\'; } else html += \'<br>\'; html += inline(m[1]); }\n    else if (!line.trim()) { close(); }\n    else { close(); html += `<p>${inline(line.trim())}</p>`; }\n  }\n  close(); return html;\n}\nfunction renderReport(md){ $(\'report\').className = \'\'; $(\'report\').innerHTML = md2html(md); }\n\nasync function loadReports(){\n  try {\n    const list = await api(\'/api/reports\');\n    $(\'reports\').className = list.length ? \'\' : \'hint\';\n    $(\'reports\').innerHTML = list.length ? \'\' : \'아직 저장된 보고서가 없어요.\';\n    list.forEach(n => {\n      const b = document.createElement(\'button\'); b.textContent = \'📄 \' + n.replace(\'_AI오피스_보고서.md\', \'\');\n      b.onclick = async () => {\n        const r = await fetch(\'/api/report?name=\' + encodeURIComponent(n));\n        renderReport(await r.text()); $(\'report\').scrollIntoView({behavior:\'smooth\'});\n      };\n      $(\'reports\').appendChild(b);\n    });\n  } catch { $(\'reports\').textContent = \'서버에 연결할 수 없어요.\'; }\n}\n\n/* ───────── 직원 카드 & 업무 지시 ───────── */\nfunction showStaff(s){\n  selected = s;\n  STAFF.forEach(x => x.el.classList.toggle(\'sel\', x === s));\n  const out = outputs[s.id];\n  $(\'staffCard\').innerHTML = `\n    <h3>👥 직원 정보</h3>\n    <div class="top"><div class="avatar">${charSVG(s)}</div>\n      <div><b style="font-size:17px">${s.name}</b> <span class="role">${s.emoji} ${s.team}</span>\n      <div class="role">${s.job}</div><div class="role">상태: <b>${s.status}</b></div></div></div>\n    ${out ? `<div class="out">${md2html(out)}</div>` : \'<p class="hint">아직 오늘 한 업무가 없어요.</p>\'}\n    <form class="order" id="orderForm">\n      <input id="orderInput" placeholder="${s.id === \'researcher\' ? \'찾을 주제 (예: 로봇)\' : \'업무 지시 (예: 반도체 관련만 정리해줘)\'}">\n      <button>지시</button>\n    </form>`;\n  $(\'orderForm\').onsubmit = e => { e.preventDefault(); order(s, $(\'orderInput\').value.trim()); };\n}\n\nasync function order(s, text){\n  if (!text) return;\n  if (!inOffice) { log(\'🏢 직원들이 출근 전이에요. 먼저 업무를 시작해 주세요.\'); return; }\n  if (s.busy) return;\n  s.busy = true;\n  log(`👑 대표님 → <b>${s.name}</b>: "${text}"`);\n  say(s, \'네, 대표님! 바로 할게요 🫡\');\n  const back = s.pos;\n  if (!running) await walk(s, seat(s));\n  startWork(s, \'지시 업무 처리 중...\');\n  try {\n    if (s.id === \'researcher\') {\n      const found = await api(`/api/news?lang=${$(\'lang\').value}&q=${encodeURIComponent(text)}`);\n      if (found.length) articles = found;\n      outputs.researcher = `### 🔎 "${text}" 관련 기사 ${found.length}건\\n\\n` +\n        found.slice(0, 12).map(a => `- [${mdt(a.title)}](${a.link}) — ${a.source}`).join(\'\\n\');\n    } else {\n      if (!articles.length) articles = await api(`/api/news?lang=${$(\'lang\').value}`);\n      const r = await api(\'/api/work\', {role: s.id, articles, outputs, instruction: text});\n      outputs[s.id] = r.text;\n    }\n    say(s, \'다 했어요! 확인해 주세요 ✅\'); setStatus(s, \'지시 업무 완료\');\n    log(`${s.emoji} <b>${s.name}</b>가 지시 업무를 끝냈어요.`);\n  } catch (e) { say(s, \'앗, 실패했어요 😢\'); log(`⚠️ ${s.name}: 오류 (${e.message})`); }\n  stopWork(s);\n  if (!running) await walk(s, back);\n  s.busy = false;\n  showStaff(s);\n}\n\n/* ───────── 버튼 연결 ───────── */\n$(\'startBtn\').onclick = startDay;\n$(\'homeBtn\').onclick = goHome;\n$(\'pauseBtn\').onclick = () => {\n  paused = !paused;\n  $(\'pauseBtn\').textContent = paused ? \'▶ 계속하기\' : \'⏸ 일시정지\';\n  office.style.filter = paused ? \'grayscale(.6)\' : \'\';\n  log(paused ? \'⏸ 잠시 멈췄어요.\' : \'▶ 다시 일을 시작해요.\');\n};\ndocument.querySelectorAll(\'#speed button\').forEach(b => b.onclick = () => {\n  speed = +b.dataset.s;\n  document.querySelectorAll(\'#speed button\').forEach(x => x.classList.toggle(\'on\', x === b));\n});\n\ncheckBrain(); loadReports();\nlog(\'👋 대표님, 환영합니다! "오늘 업무 시작하기"를 눌러주세요.\');\n</script>\n</body>\n</html>\n', 'nuri.html': '<!DOCTYPE html>\n<html lang="ko">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>나의 개인 비서 - 누리</title>\n<style>\n  :root { --bg:#0f1220; --card:#1a1e33; --text:#e8eaf6; --muted:#9aa0c3; --accent:#7c6cff; }\n  * { box-sizing: border-box; }\n  body { margin:0; font-family: -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;\n         background: radial-gradient(circle at 50% 0%, #22264a 0%, var(--bg) 55%); color:var(--text); min-height:100vh; }\n  .wrap { max-width: 860px; margin: 0 auto; padding: 24px 20px 60px; }\n\n  /* ---------- 캐릭터 영역 ---------- */\n  .stage { display:flex; flex-direction:column; align-items:center; margin-top: 10px; }\n  .bubble { position:relative; background:#fff; color:#222; padding:14px 20px; border-radius:18px;\n            max-width: 460px; text-align:center; font-size:16px; line-height:1.5; min-height: 50px;\n            box-shadow: 0 6px 20px rgba(0,0,0,.3); margin-bottom: 14px; }\n  .bubble::after { content:""; position:absolute; bottom:-10px; left:50%; transform:translateX(-50%);\n            border:10px solid transparent; border-top-color:#fff; border-bottom:0; }\n  .bubble.pop { animation: pop .3s ease; }\n  @keyframes pop { 0%{transform:scale(.85);opacity:.4} 100%{transform:scale(1);opacity:1} }\n\n  #buddy { width: 190px; height: 190px; cursor:pointer; user-select:none;\n           animation: bob 3s ease-in-out infinite; transition: transform .15s;\n           filter: drop-shadow(0 12px 18px rgba(124,108,255,.35)); }\n  #buddy:hover { transform: scale(1.06); }\n  #buddy:active { transform: scale(.95); }\n  @keyframes bob { 0%,100%{translate:0 0} 50%{translate:0 -10px} }\n  .thinking #buddy { animation: wiggle .5s ease-in-out infinite; }\n  @keyframes wiggle { 0%,100%{rotate:-4deg} 50%{rotate:4deg} }\n  .happy #buddy { animation: jump .45s ease 2; }\n  @keyframes jump { 0%,100%{translate:0 0} 50%{translate:0 -28px} }\n\n  /* 표정 전환 */\n  .face { display:none; }\n  .idle .f-idle, .thinking .f-think, .happy .f-happy, .sad .f-sad { display:inline; }\n  .blink { animation: blink 4s infinite; transform-origin: center 88px; }\n  @keyframes blink { 0%,94%,100%{transform:scaleY(1)} 97%{transform:scaleY(.1)} }\n  .antenna-light { animation: glow 1.6s ease-in-out infinite; }\n  .thinking .antenna-light { animation-duration: .35s; }\n  @keyframes glow { 0%,100%{fill:#ffd166} 50%{fill:#fff3c4} }\n\n  .hint { color: var(--muted); font-size: 13px; margin-top: 4px; }\n\n  /* 빠른 메뉴 */\n  .menu { display:flex; gap:8px; flex-wrap:wrap; justify-content:center; margin: 18px 0 6px; }\n  .chip { padding: 9px 15px; border-radius: 999px; border:1px solid #3a4170; background: var(--card);\n          color: var(--text); font-size: 14px; cursor:pointer; transition:.15s; }\n  .chip:hover { border-color: var(--accent); background:#262b4d; }\n  .ask { display:flex; gap:8px; max-width: 520px; margin: 8px auto 0; }\n  .ask input { flex:1; padding: 11px 14px; border-radius: 10px; border: 1px solid #333a5c;\n               background: var(--card); color: var(--text); font-size: 15px; }\n  .ask button { padding: 11px 18px; border:0; border-radius:10px; background:var(--accent);\n                color:#fff; font-weight:600; cursor:pointer; }\n\n  /* 기사 카드 */\n  #list { margin-top: 22px; }\n  .card { background: var(--card); border-radius: 12px; padding: 16px 18px; margin: 12px 0;\n          border-left: 4px solid var(--accent); animation: fadeUp .35s ease both; }\n  @keyframes fadeUp { from{opacity:0; transform:translateY(8px)} to{opacity:1; transform:none} }\n  .card a { color: var(--text); text-decoration: none; font-size: 17px; font-weight: 600; line-height: 1.4; }\n  .card a:hover { color: #b3a9ff; text-decoration: underline; }\n  .meta { color: var(--muted); font-size: 13px; margin-top: 6px; }\n  .summary { color: #c5c9e6; font-size: 14px; margin: 8px 0 0; line-height: 1.5; }\n  .tag { background:#2a2f52; padding:2px 8px; border-radius:6px; margin-right:6px; }\n</style>\n</head>\n<body>\n<div class="wrap">\n  <div class="stage idle" id="stage">\n    <div class="bubble" id="bubble"></div>\n\n    <!-- 캐릭터 \'누리\' (클릭하세요!) -->\n    <svg id="buddy" viewBox="0 0 200 200" role="button" aria-label="비서 누리를 클릭하면 AI 기사를 찾아줘요">\n      <!-- 안테나 -->\n      <line x1="100" y1="38" x2="100" y2="18" stroke="#b9b2ff" stroke-width="5" stroke-linecap="round"/>\n      <circle class="antenna-light" cx="100" cy="14" r="9"/>\n      <!-- 귀 -->\n      <rect x="22" y="78" width="18" height="40" rx="9" fill="#5a4de0"/>\n      <rect x="160" y="78" width="18" height="40" rx="9" fill="#5a4de0"/>\n      <!-- 머리 -->\n      <rect x="36" y="36" width="128" height="112" rx="44" fill="#8f82ff"/>\n      <rect x="50" y="56" width="100" height="70" rx="30" fill="#1b1f3b"/>\n      <!-- 눈: 기본 -->\n      <g class="face f-idle blink">\n        <ellipse cx="78" cy="88" rx="9" ry="12" fill="#7ef0ff"/>\n        <ellipse cx="122" cy="88" rx="9" ry="12" fill="#7ef0ff"/>\n        <path d="M86 108 Q100 118 114 108" stroke="#7ef0ff" stroke-width="4" fill="none" stroke-linecap="round"/>\n      </g>\n      <!-- 눈: 생각 중 -->\n      <g class="face f-think">\n        <path d="M68 90 L88 90" stroke="#7ef0ff" stroke-width="5" stroke-linecap="round"/>\n        <ellipse cx="122" cy="86" rx="9" ry="12" fill="#7ef0ff"/>\n        <circle cx="100" cy="111" r="5" fill="#7ef0ff"/>\n      </g>\n      <!-- 눈: 기쁨 -->\n      <g class="face f-happy">\n        <path d="M68 92 Q78 78 88 92" stroke="#7ef0ff" stroke-width="5" fill="none" stroke-linecap="round"/>\n        <path d="M112 92 Q122 78 132 92" stroke="#7ef0ff" stroke-width="5" fill="none" stroke-linecap="round"/>\n        <path d="M84 104 Q100 122 116 104 Z" fill="#7ef0ff"/>\n      </g>\n      <!-- 눈: 실패 -->\n      <g class="face f-sad">\n        <path d="M70 84 L86 92 M130 84 L114 92" stroke="#7ef0ff" stroke-width="5" stroke-linecap="round"/>\n        <path d="M86 114 Q100 104 114 114" stroke="#7ef0ff" stroke-width="4" fill="none" stroke-linecap="round"/>\n      </g>\n      <!-- 볼 -->\n      <circle cx="62" cy="110" r="7" fill="#ff8fb1" opacity=".55"/>\n      <circle cx="138" cy="110" r="7" fill="#ff8fb1" opacity=".55"/>\n      <!-- 몸 -->\n      <rect x="70" y="150" width="60" height="38" rx="16" fill="#8f82ff"/>\n      <circle cx="100" cy="168" r="7" fill="#ffd166"/>\n    </svg>\n    <div class="hint">👆 저를 눌러주세요!</div>\n  </div>\n\n  <div class="menu">\n    <button class="chip" data-lang="ko">🇰🇷 한국 AI 뉴스</button>\n    <button class="chip" data-lang="en">🌎 해외 AI 뉴스</button>\n    <button class="chip" data-lang="all">📚 전부 다</button>\n    <button class="chip" data-q="챗GPT">챗GPT</button>\n    <button class="chip" data-q="클로드">클로드</button>\n    <button class="chip" data-q="반도체">반도체</button>\n  </div>\n  <form class="ask" id="askForm">\n    <input id="kw" placeholder="누리에게 궁금한 AI 주제를 말해보세요 (예: 로봇)">\n    <button>부탁해!</button>\n  </form>\n\n  <div id="list"></div>\n</div>\n\n<script>\nconst stage = document.getElementById(\'stage\'), bubble = document.getElementById(\'bubble\'),\n      list = document.getElementById(\'list\');\nconst NAME = localStorage.getItem(\'myName\') || \'\';\nlet busy = false, lastLang = \'ko\';\n\nfunction setMood(m) { stage.className = \'stage \' + m; }\nfunction say(text) {\n  bubble.textContent = text;\n  bubble.classList.remove(\'pop\'); void bubble.offsetWidth; bubble.classList.add(\'pop\');\n}\nconst pick = arr => arr[Math.floor(Math.random() * arr.length)];\n\n// 첫 인사\n(() => {\n  const h = new Date().getHours();\n  const hi = h < 6 ? \'늦은 밤이네요\' : h < 12 ? \'좋은 아침이에요\' : h < 18 ? \'좋은 오후예요\' : \'좋은 저녁이에요\';\n  const d = new Date().toLocaleDateString(\'ko-KR\', { month:\'long\', day:\'numeric\', weekday:\'long\' });\n  say(`${hi}${NAME ? \', \' + NAME + \'님\' : \'\'}! 저는 비서 누리예요 🤖 오늘은 ${d}. 저를 누르면 최신 AI 기사를 찾아드릴게요!`);\n})();\n\nfunction ago(iso) {\n  if (!iso) return \'\';\n  const m = Math.floor((Date.now() - new Date(iso)) / 60000);\n  if (m < 60) return `${Math.max(m,1)}분 전`;\n  if (m < 1440) return `${Math.floor(m/60)}시간 전`;\n  return `${Math.floor(m/1440)}일 전`;\n}\nconst esc = s => (s || \'\').replace(/[&<>"]/g, c => ({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\'}[c]));\n\nasync function find(lang = \'ko\', q = \'\') {\n  if (busy) { say(\'지금 열심히 찾는 중이에요! 잠깐만요 🏃\'); return; }\n  busy = true; lastLang = lang;\n  setMood(\'thinking\');\n  say(q ? `"${q}" 관련 AI 소식을 찾아볼게요... 🔎`\n        : pick([\'뉴스 사이트를 둘러보는 중이에요... 🔎\', \'따끈한 AI 소식 가져올게요! 🏃\',\n                \'잠시만요, 기사들을 모으고 있어요... 📰\']));\n  list.innerHTML = \'\';\n  try {\n    const res = await fetch(`/api/news?lang=${lang}&q=${encodeURIComponent(q)}`);\n    const items = await res.json();\n    if (!items.length) {\n      setMood(\'sad\');\n      say(\'앗, 기사를 못 찾았어요 😢 다른 주제로 다시 부탁해 주세요.\');\n    } else {\n      setMood(\'happy\');\n      say(`짜잔! ${items.length}개의 기사를 찾았어요 🎉 제일 최신 소식은 "${items[0].title.slice(0, 40)}..." 이에요.`);\n      list.innerHTML = items.map((a, i) => `\n        <div class="card" style="animation-delay:${Math.min(i, 15) * 40}ms">\n          <a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.title)}</a>\n          <div class="meta"><span class="tag">${esc(a.source)}</span>${ago(a.date)}</div>\n          ${a.summary && !a.summary.startsWith(a.title.slice(0,20)) ? `<p class="summary">${esc(a.summary)}</p>` : \'\'}\n        </div>`).join(\'\');\n      setTimeout(() => stage.classList.contains(\'happy\') && setMood(\'idle\'), 2500);\n    }\n  } catch (e) {\n    setMood(\'sad\');\n    say(\'연결에 문제가 생겼어요 😢 비서 프로그램(assistant.py)이 켜져 있는지 확인해 주세요.\');\n  }\n  busy = false;\n}\n\n// 캐릭터 클릭 → 기사 찾기\ndocument.getElementById(\'buddy\').addEventListener(\'click\', () => find(lastLang));\n\n// 빠른 메뉴\ndocument.querySelectorAll(\'.chip\').forEach(c => c.addEventListener(\'click\', () =>\n  find(c.dataset.lang || \'ko\', c.dataset.q || \'\')));\n\n// 직접 부탁하기 ("내 이름은 ○○" 이라고 말하면 이름을 기억해요)\ndocument.getElementById(\'askForm\').addEventListener(\'submit\', e => {\n  e.preventDefault();\n  const input = document.getElementById(\'kw\'), text = input.value.trim();\n  input.value = \'\';\n  const m = text.match(/(?:내 이름은|나는)\\s*([^\\s.!]+?)(?:이야|야|입니다|이에요|예요)?[.!]?$/);\n  if (m) {\n    localStorage.setItem(\'myName\', m[1]);\n    setMood(\'happy\'); say(`반가워요, ${m[1]}님! 이제 이름을 기억할게요 😊`);\n    return;\n  }\n  find(lastLang, text);\n});\n</script>\n</body>\n</html>\n'}  # build.py가 화면 파일을 여기에 넣어 하나의 파일로 만듭니다


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
