# 🏢 나만의 AI 오피스

AI 직원 6명이 출근 → 회의 → 업무 → 보고까지 하는 나만의 AI 오피스입니다.

💬 **관리자 한결**: 궁금한 건 무엇이든 물어보세요 (상단 '관리자에게 질문' 버튼).
- 무료 AI(Ollama) 연결 시: 직접 생각해서 대답
- 기본 모드: 오늘 보고서 · 위키백과 · 최신 뉴스를 찾아서 대답

## 실행 방법 (Windows)
1. Python 설치: https://www.python.org/downloads (설치 시 "Add python.exe to PATH" 체크)
2. 이 폴더의 `start.bat` 더블클릭
3. 브라우저에서 http://localhost:8000 이 열리면 "오늘 업무 시작하기" 클릭!

Mac: 터미널에서 `python3 ai_office.py`

## 무료 AI 두뇌 (선택)
https://ollama.com 설치 후 `ollama pull exaone3.5:2.4b` → 자동 연결 (요금 0원)

## 파일
- `ai_office.py` — 실행 파일 (모든 기능 포함)
- `start.bat` — Windows 더블클릭 실행
- `src/` — 원본 소스 (`python src/build.py` 로 ai_office.py 다시 만들기)
