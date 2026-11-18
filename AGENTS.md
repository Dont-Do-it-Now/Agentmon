# agentmon 작업 안내

Ulanzi D200 덱(버튼 13개 + 458x196 넓은 패널)에 코딩 에이전트의 세션 상태와 한도를 띄우는 대시보드.

## 구조
- `agents/<이름>.py`: 에이전트 하나의 모든 것 (세션 감지, 한도 조회, 포커스, 색·이름). 서로 독립.
- `agents/base.py`: 공통 타입(`AgentSession`, `Window`, `Limit`)과 도우미. 모듈 형식이 맨 위에 적혀 있다.
- `agents/__init__.py`: 등록 목록 `MODULES`(= 덱·패널 표시 순서)와 세션 합치기 `list_sessions`.
- `dashboard.py`: 덱 화면·버튼 처리. `MODULES`만 돌며, 특정 에이전트 이름을 쓰지 않는다.
- `panel.py`: 넓은 패널 그림 (에이전트당 한 줄, 한도 바 2개).
- `d200.py`: 장치 통신.

## 에이전트 추가
1. `agents/<이름>.py`를 만든다.
   - 상수: `KEY, LABEL, SHORT, COLOR, BG, APP_TITLE`
   - `sessions() -> list[AgentSession]`: `state`는 `"running"` 또는 `"done"`, 눌러서 열 주소는 `focus_url`
   - `usage()`: `.limits -> list[Limit]`를 가진 객체. 앱이 꺼져 있으면 `None`
   - 선택 `focus(session)`: 딥링크만으로 세션이 안 열릴 때
2. `agents/__init__.py`의 `MODULES`에 추가한다.

`dashboard.py`와 `panel.py`는 고치지 않는다. 예외: 하단 한도 타일은 자리가 3개라 `MODULES` 앞의 3개만 나온다.

## 지킬 것
- 에이전트별 로직을 `dashboard.py`나 `panel.py`에 넣지 않는다. 해당 `agents/<이름>.py`에 둔다.
- 각 에이전트의 파일 위치·API·포커스 방식은 그 모듈 상단 설명과 주석에 있다. 다시 조사하기 전에 먼저 읽는다.
- 완료·미확인 판정(`seen`, `acks`, `baseline`)은 `list_sessions`와 `Dashboard`가 한다. 모듈은 앱이 알려주는 값만 `seen`에 넣는다.
- Claude 한도 API는 자주 부르면 429가 난다. `agents/claude.py`의 120초 캐시를 줄이지 않는다.
- `agents/claude.py`는 만료된 토큰을 갱신해 `~/.claude/.credentials.json`에 덮어쓴다 (`TOKEN_REFRESH`). 사용자가 켠 설정이다.
- 토큰·CSRF 값 등을 로그나 출력에 찍지 않는다.

## 덱 조작
- 세션 타일(0~8, 페이지당 9개): 누르면 확인 처리하고 해당 세션을 연다. 에이전트 색으로 채워지고, 완료·미확인은 누를 때까지 깜빡인다.
- 세션 타일의 모델·브랜치·컨텍스트 바는 `AgentSession`의 `model`, `branch`, `context`(0~1)를 쓴다.
- →(9): 다음 세션 페이지, 마지막 페이지에서 누르면 1페이지.
- 하단 3칸(10~12): 에이전트별 한도(5H·주간 바, 큰 숫자는 주간). 기본은 남은 %, 아무 칸이나 누르면 3칸 모두 남은 ↔ 사용 % 전환 + 한도 갱신.

## 실행
- 실행: `run_silent.vbs` (→ `run_log.bat` → `.venv\Scripts\python.exe dashboard.py`), 로그는 `logs/dashboard.log`
- 재시작: 명령줄에 `dashboard.py`가 있는 python 프로세스를 끝내고 `wscript.exe run_silent.vbs`
- 상태 전환(`running`/`done`)과 오류는 로그에 남는다.
