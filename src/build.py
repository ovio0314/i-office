"""src/ 의 파일들을 합쳐 하나의 실행 파일(../ai_office.py)을 만듭니다."""
from pathlib import Path
src = Path(__file__).parent
code = (src / "assistant.py").read_text(encoding="utf-8")
pages = {n: (src / n).read_text(encoding="utf-8") for n in ("index.html", "nuri.html")}
marker = "EMBEDDED = {}"
assert marker in code
code = code.replace(marker, "EMBEDDED = " + repr(pages), 1)
(src.parent / "ai_office.py").write_text(code, encoding="utf-8")
print("built ai_office.py", len(code), "chars")
