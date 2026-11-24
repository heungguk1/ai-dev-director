# AI DEV DIRECTOR 프로젝트 분석

> 작성일: 2026-11-22 (2026-11-23 보강) · 기준 커밋: `be90907` (v3.2.0)
> 범위: 핵심 파일, 실행 방법, Claude / Codex / Antigravity CLI가 연결되는 위치와 방식, 대시보드 프런트엔드, CLI 진단

---

## 1. 한 줄 요약

로그인된 **구독형 AI 코딩 CLI**(Claude Code, OpenAI Codex, 선택적으로 Google Antigravity `agy`)를
**HEAD / BUILDER / REVIEWER** 세 역할로 배치해, 목표 한 줄을 **격리된 git worktree에서 검증·리뷰된 코드**로
만들어 주는 로컬 오케스트레이터입니다. API 키를 쓰지 않고, 각 CLI를 **서브프로세스로 호출**(stdin으로 프롬프트 전달,
stdout으로 JSON 수신)하는 방식으로 붙습니다.

- 언어: Python 3.9+ (의존성: `pyyaml`, `click`, `pydantic>=2`, `psutil`, `pytest`)
- 저장소: SQLite(`data/director.db`) + JSON 파일(run 결과, 미션)
- UI: 의존성 없는 정적 프런트엔드(`console/`) + Python `http.server` 기반 서버(포트 8081)

---

## 2. 디렉터리 구조와 핵심 파일

```
director.py              CLI 진입점 (click): run / mission / do / status / resume / clean-worktrees
companion_server.py      8081 콘솔 서버 (TRINITY 무대, /ops/*, /bridge/* API). dashboard_server를 감싸서 띄움
dashboard_server.py      (3,200줄) 작업실 화면(/classic), 채팅, 미션 스레드 실행, 상태 요약 — 서버 로직 대부분
config.yaml              역할별 공급자, 타임아웃, 안전 상한, 검증 명령, 자율 모드/프로필
companion.json           콘솔 포트(8081), 허용 프로젝트 목록

agents/                  AI CLI 어댑터 (← CLI가 "붙는" 곳)
  head.py                BaseHead 추상 클래스 + 공용 프롬프트(프록시 정책, ORDER 스키마, plan_next 등)
  claude_head.py         HEAD = Claude Code CLI
  codex_head.py          HEAD = Codex CLI
  google_roles.py        HEAD/REVIEWER = Antigravity(agy) (GoogleHead, GoogleReviewer)
  claude_reviewer.py     REVIEWER = Claude Code CLI
  codex_reviewer.py      REVIEWER = Codex CLI
  proposal_builder.py    BUILDER(proposal 모드, 기본) — codex/claude/agy 중 하나에게 "파일 전체 JSON"을 받아 Director가 씀
  antigravity_builder.py BUILDER(직접 편집 모드, 레거시) — agy가 worktree 안에서 직접 파일 편집. ProposalBuilder의 부모 클래스
  mock_head.py           테스트용 Mock HEAD

core/                    오케스트레이션 엔진
  orchestrator.py        (1,900줄) ★ 단일 run 파이프라인: ORDER → BUILD → VERIFY → REVIEW → DECIDE, 장애 조치
  mission.py             ★ 미션(여러 마일스톤 run을 HEAD가 계획하며 연속 실행), 정지/재개/자동 반영
  env_utils.py           ★ CLI 실행 파일 탐색(resolve_cli), 과금 환경 변수 제거, Claude 읽기 전용 플래그
  process_runner.py      ★ ProcessTreeRunner: 서브프로세스 실행, 타임아웃/무출력 감시, 프로세스 트리 강제 종료
  research_runner.py     ★ TriadResearchRunner: 3개 CLI 공통 호출기(도메인별 커맨드 조립). Builder·Google 역할도 이걸 씀
  provider_manager.py    공급자 상태(HEALTHY/RECOVERING/DOWN…), 에러 분류, NORMAL/DEGRADED/STOP 모드, 역할 교체
  role_settings.py       콘솔에서 바꾼 역할 배치(data/role_settings.json) 적용, 공급자 별칭/도메인 매핑
  safety.py              Scope Guard: 허용/보호 경로, 파일 수·diff 상한 검사
  verifier.py            빌드/테스트/린트 명령 실행(격리 worktree에서)
  git_manager.py         worktree 생성/정리, diff
  state_machine.py       run 상태 머신(SQLite): IDLE→PLANNING→ORDER_READY→BUILDING→VERIFYING→REVIEWING→DECIDING→PASS/FAIL/HUMAN_REQUIRED/TIMEOUT
  autopilot.py           사람에게 물어야 하는 정지 조건(결제, 실계좌 주문, 자격증명, 비가역 외부 행동)
  bootstrap.py / greenfield.py  빈 폴더·비 git 폴더 자동 준비, 스냅샷
  result_manager.py      결과 반영(apply) — 사람이 수정한 파일은 덮어쓰지 않음
  research_*.py          TRIAD 연구 모드(독립 연구 → 상호 비판 → 종합 → 실험 → 해석)
  usage.py               역할별 토큰/호출 집계
  solomon_bridge.py      외부 "CEO" 서비스가 미션을 보내는 /bridge 연동
  chat_manager.py        채팅 세션/메시지/provenance 저장
  db_path.py             SQLite 경로 단일화 (data/director.db)

schemas/                 pydantic 모델: OrderSchema, BuilderResultSchema, ReviewResultSchema, DecisionSchema, Provenance, Research
console/                 index.html + trinity.js/stage.js(무대형 대시보드) + console.js
scripts/                 cli_diagnostics.py(CLI 진단), test_role_cli_smoke.py, retry_transport_run.py, pick_folder.py
tests/                   ~625개 테스트 (스텁 CLI, DIRECTOR_TESTING=1). 실제 CLI 통합은 -m integration_cli 로 opt-in
docs/                    설계·마일스톤 문서
```

### 처음 읽을 때 추천 순서

1. `config.yaml` — 전체 동작을 좌우하는 설정
2. `director.py` — 진입점, 각 명령이 무엇을 호출하는지
3. `core/orchestrator.py` — `_init_head/_init_builder/_init_reviewer`(L52~104), `run()`(L1291~)
4. `core/env_utils.py` + `core/research_runner.py::_command` — CLI 호출 방식의 핵심
5. `agents/claude_head.py`, `agents/proposal_builder.py` — 실제 프롬프트와 응답 파싱
6. `core/mission.py` — 다단계 미션 흐름
7. `companion_server.py` → `dashboard_server.py` — UI/API 계층

---

## 3. 실행 방법

### 3.1 설치 / 테스트

```bash
pip install -r requirements.txt
python -m pytest -q                       # CLI 없이 스텁으로 동작 (README 기준 약 25~35분)
python -m pytest -m integration_cli       # 실제 로그인된 CLI로 도는 통합 테스트 (opt-in)
```

사전 조건: Git, 그리고 **구독 로그인된** `claude`(`--tools` 옵션 지원 버전), `codex`.
`agy`는 기본 비활성(아래 5.4 참고).

### 3.2 CLI로 직접 실행 (`director.py`)

```bash
# 단일 run: ORDER 1개를 만들고 BUILD→VERIFY→REVIEW→DECIDE
python director.py run -p /path/to/project -g "src/calc.py에 add(a,b) 추가하고 테스트 작성"
python director.py run -p ... -g ... --head-provider codex     # HEAD 공급자만 일시 변경 (claude|codex|mock)

# 미션: HEAD가 마일스톤을 계획하며 여러 run을 이어서 실행
python director.py mission -p /path/to/project -g "회원가입 흐름을 끝까지 구현" [--profile quick|standard|deep] [--max-steps N]
python director.py mission -p ... -g ... --mission-id <ID> [--replan "이유"]   # 기존 미션 이어가기

# do: 미션 + 자동 반영을 한 번에 (기본 프로젝트 = 현재 폴더가 git repo면 거기)
python director.py do "로그인 버그 고쳐" --profile quick [--no-apply]

python director.py status [-r RUN_ID]
python director.py resume RUN_ID
python director.py clean-worktrees -p /path/to/project --dry-run [--purge-reviewed]
```

### 3.3 콘솔(웹 UI) 실행

```bash
# macOS / Linux (코드상 지원, README상 "미검증")
chmod +x start-companion.sh stop-companion.sh
./start-companion.sh      # python3 companion_server.py를 nohup 백그라운드로, PID → data/companion.pid
./stop-companion.sh

# Windows
.\start-companion.ps1 / .\stop-companion.ps1
```

- 시작 시 `http://127.0.0.1:8081/?token=...` 형태의 **연결 주소**가 출력됩니다. 브라우저당 최초 1회 이 주소로 접속하면 세션이 생깁니다.
- 토큰 파일: `data/solomon-bridge.token` (최초 실행 시 생성). 로그: `data/companion.log`, `data/companion.err`.
- `/` = TRINITY 무대형 대시보드, `/classic` = 표 중심 작업실 화면.
- 서버는 항상 `127.0.0.1`에만 바인딩. 상태 변경 요청(POST)은 로컬 출처 검사 + 세션 또는 브리지 토큰 필요.

### 3.4 생성되는 런타임 데이터

| 위치 | 내용 |
|---|---|
| `data/director.db` | run 상태/전이/감사 로그, 채팅, 공급자 health (SQLite) |
| `data/role_settings.json` | 콘솔에서 바꾼 역할 배치 (있으면 config.yaml 역할을 덮어씀) |
| `data/` 하위 run/mission JSON | ORDER, builder 결과, 검증, 리뷰, 결정, provenance |
| `<대상 프로젝트>/.director_worktrees/` | 각 run의 격리 git worktree (원본 프로젝트는 직접 수정하지 않음) |

---

## 4. 전체 동작 흐름

### 4.1 단일 run (NORMAL 파이프라인) — `core/orchestrator.py::Orchestrator.run`

```
goal
 │
 ▼ HEAD.create_order()            → OrderSchema (allowed_paths, protected_paths, 상한, acceptance_criteria …)
 │   Scope Guard 상한 적용 (apply_safety_caps)
 ▼ git worktree 생성 (.director_worktrees/…) + 베이스라인 테스트
 ▼ BUILDER.run(order, worktree)   → (proposal 모드) CLI가 JSON으로 파일 전체/편집 제안 → Director가 검증 후 기록
 │   validate_post_build: 경로·파일 수·diff·삭제·제어면 파일 검사
 ▼ Verifier: build/test/lint 명령 (config.yaml 또는 projects.<root>.verification)
 ▼ REVIEWER.review()              → ReviewResultSchema (응답 없음/깨짐 = FAIL, fail-closed)
 ▼ HEAD.decide()                  → PASS / FAIL(수정 지시 next_steps) / HUMAN_REQUIRED
 │   FAIL이면 max_repairs 만큼 BUILDER 재시도 (마지막 시도는 다른 공급자로 교체)
 ▼ apply_final_pass_gate → 최종 상태 기록
```

각 단계 실패 시 `ProviderManager`가 에러를 분류(사용량 한도/인증/설정 오류 등)하고, 자율 모드에서는
`_swap_agent`로 다른 공급자에게 역할을 넘깁니다(`_run_builder_autonomous`, `_run_reviewer_autonomous`, `_run_decision_autonomous`).

### 4.2 미션 — `core/mission.py::MissionRunner`

- 채팅 메시지(또는 `director.py mission/do`)가 미션이 됨. HEAD `chat()`이 QUESTION/DEV_TASK를 판별하고 첫 ORDER를 만듦.
- `_advance` → `_plan`(HEAD `plan_next`) → `_execute`(Orchestrator.run, 직전 PASS worktree 스냅샷을 `base_worktree`로) 반복.
- `max_steps`, `max_consecutive_failures`로 제한. 완료 시 `auto_apply`(off / on_done / on_pass)에 따라 프로젝트에 반영.
- "중단/멈춰/stop" 또는 콘솔 정지 버튼 → 실행 중 CLI 프로세스 트리 종료, 미션 STOPPED 고정.

### 4.3 TRIAD 연구 모드 — `core/research_orchestrator.py`

세 공급자가 독립 연구 → 상호 비판 → 종합 가설 → NORMAL 파이프라인으로 실험 → 세 갈래 해석.
**3개 공급자가 모두 필요**하므로 Antigravity를 켜야 동작합니다. 상세: `docs/V3_TRIAD_RESEARCH_PLAN.md`.

---

## 5. Claude / Codex / Antigravity CLI 연결 상세

### 5.1 공통 구조

```
config.yaml (head/builder/reviewer.provider)
   + data/role_settings.json (콘솔 역할 교환, core/role_settings.py::apply)
        │
        ▼
Orchestrator._init_head / _init_builder / _init_reviewer   (core/orchestrator.py:52~104)
        │  provider 문자열로 어댑터 클래스 선택
        ▼
agents/*.py 어댑터  ──►  core/env_utils.resolve_cli(name)       실행 파일 경로 탐색
                    ──►  core/env_utils.get_sanitized_env()     과금 관련 환경 변수 제거
                    ──►  core/process_runner.ProcessTreeRunner.run(argv, stdin_data=prompt, ...)
                              (shell=False, 전체 타임아웃 + 무출력 타임아웃, 타임아웃 시 프로세스 트리 kill)
                    ◄──  stdout 파싱 → JSON 추출(_extract_json) → pydantic 스키마 검증
```

핵심 포인트:

- **API/SDK 호출은 전혀 없습니다.** 모두 공식 CLI를 서브프로세스로 실행합니다.
- **프롬프트는 stdin으로 전달**합니다(Windows 명령줄 길이 제한 회피). 모델 지정 플래그는 넘기지 않으며 각 CLI 계정의 기본 모델을 씁니다.
- 공급자 이름 별칭 (`core/role_settings.py`):

  | 별칭 | 내부 키 | 도메인(ProviderManager) | 실행 파일 |
  |---|---|---|---|
  | `claude`, `anthropic` | claude | ANTHROPIC | `claude` |
  | `codex`, `openai` | openai | OPENAI | `codex` |
  | `antigravity`, `google` | antigravity | GOOGLE | `agy` |

- 기본 역할 배치(`config.yaml`): **HEAD = Claude, BUILDER = Codex(proposal 모드), REVIEWER = Claude**.
  콘솔 역할 교환은 세 역할에 **서로 다른 AI**를 지정해야 합니다(`role_settings.validate`).

### 5.2 실행 파일 탐색 — `core/env_utils.py::resolve_cli`

1. (Windows) `%LOCALAPPDATA%/OpenAI/Codex/bin/*/codex.exe`, `%APPDATA%/Claude/claude-code/*/claude.exe` 중 최신
2. `shutil.which(name)` — PATH
3. 알려진 설치 위치: `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin`, `~/.claude/local/claude`, `%APPDATA%/npm/*.cmd`, `~/.agy/bin/agy` 등
4. `agy`는 `antigravity.enabled: false`면 **항상 None** (탐색조차 안 함)

명시적 경로 지정: `config.yaml`의 `head/builder/reviewer.executable`, 또는 환경 변수 `CLAUDE_BIN`, `CODEX_BIN`, `ANTIGRAVITY_BIN`(리뷰어/빌더 어댑터에서 사용).

### 5.3 실제 호출 커맨드 (역할별)

| CLI | 역할 | 위치 | 커맨드 | 비고 |
|---|---|---|---|---|
| **Claude Code** | HEAD | `agents/claude_head.py::_invoke_cli` | `claude --print --output-format json --tools Read,Glob,Grep --permission-mode default --strict-mcp-config --mcp-config '{"mcpServers":{}}'` | stdout JSON envelope의 `result` 필드 사용. cwd = 프로젝트/worktree |
| Claude Code | REVIEWER | `agents/claude_reviewer.py::_review_with_cli` | `claude --print <읽기 전용 플래그>` | |
| Claude Code | BUILDER / TRIAD | `core/research_runner.py::_command("ANTHROPIC")` | `claude --print --output-format json <읽기 전용 플래그>` | Builder 응답이 `{"type":"result"}` envelope면 한 번 더 풀어서 파싱 |
| **Codex** | HEAD | `agents/codex_head.py::_invoke_cli` | `codex exec --sandbox read-only -` | `-` = stdin에서 프롬프트 읽기. cwd = `git rev-parse --show-toplevel` 결과 |
| Codex | REVIEWER | `agents/codex_reviewer.py::_review_with_cli` | `codex exec --sandbox read-only -` | |
| Codex | BUILDER / TRIAD | `research_runner._command("OPENAI")` | `codex exec --sandbox read-only -` | |
| **Antigravity** | HEAD / REVIEWER | `agents/google_roles.py` → `TriadResearchRunner` | `agy --input-format stream-json --output-format stream-json --sandbox` | stdin에 NDJSON `{"event":"user","message":{...}}`. 빈 임시 폴더에서 "도구 쓰지 말 것" 지시로 실행 |
| Antigravity | BUILDER (proposal) | `proposal_builder.py` → `TriadResearchRunner` | 위와 동일 | |
| Antigravity | BUILDER (직접 편집, 레거시) | `agents/antigravity_builder.py::_execute_agy` | `agy --input-format stream-json --output-format stream-json --mode accept-edits --sandbox` | **worktree에서 직접 파일 편집**. 실행 전 범위 제한 권한 설정을 worktree에 설치하고 끝나면 복원. `result` 이벤트로 종료 판단, 출력 토큰 한도 시 1회 이어서 재시도 |

공통 읽기 전용 플래그(`env_utils.CLAUDE_READ_ONLY_FLAGS`)는 사용자의 개인 Claude 설정(허용 도구, MCP 서버)이 상속되지 않도록 막는 장치입니다.
Codex는 모든 역할에서 `--sandbox read-only`로만 실행됩니다.

### 5.4 BUILDER의 "proposal 모드" — 왜 CLI가 파일을 직접 안 쓰나

`config.yaml`의 `builder.mode: proposal`(기본, 역할 교환 시 강제)에서는:

1. `ProposalBuilder.run`이 worktree를 훑어 **편집 가능 파일**(Scope Guard 통과)과 **참고용 소스**를 골라 프롬프트에 직접 넣음
   (예산: 편집 가능 140KB, 참고 60KB, 비밀/토큰 관련 파일·100KB 이상 파일 제외).
2. CLI는 **빈 임시 디렉터리**(`director-builder-*`)에서 실행되고, `{"files":[...], "edits":[...], "summary":...}` JSON만 반환.
3. Director가 JSON을 파싱(깨진 이스케이프 자동 수리) → `resolve_changes`로 경로·앵커 검증 → 배치 전체가 유효할 때만 worktree에 기록.

즉 어떤 CLI가 Builder여도 **파일 쓰기 권한은 Director만** 갖습니다. (레거시 `AntigravityBuilder` 직접 편집 모드만 예외)

### 5.5 Antigravity 활성화 조건

- `config.yaml`의 `antigravity.enabled: false`가 기본. 구글 Antigravity FAQ상 소비자 플랜에서 다른 앱으로 감싸 쓰는 것은
  미지원이며 계정 정지 위험이 있다는 이유(README, config 주석).
- 꺼져 있으면: `resolve_cli('agy')` → None, `ProviderManager.check_health`에서 GOOGLE = UNAVAILABLE,
  "정상 모드" 기준이 3개가 아닌 **2개 공급자**로 바뀜(`_mode_for_usable`). TRIAD 연구 모드는 사용 불가.
- 테스트(`DIRECTOR_TESTING=1`)에서는 항상 켜진 것으로 간주.

### 5.6 과금(API 키) 차단 — `get_sanitized_env`

모든 CLI 호출과 검증 명령의 환경에서 다음을 제거:
- 이름이 `*_API_KEY(S)`로 끝나는 모든 변수
- `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY`, `OPENAI_BASE_URL`, `OPENAI_ORG_ID`,
  `GOOGLE_GENAI_USE_VERTEXAI` 등 엔드포인트·클라우드 과금 전환 변수

한계: CLI 자체를 API 키로 로그인했다면(예: `codex login --with-api-key`) 막을 수 없습니다. provenance의
`api_key_used: false`는 "환경 변수를 지우고 호출했다"는 선언값이지 측정값이 아닙니다.

### 5.7 장애 조치 — `core/provider_manager.py`

- 상태: HEALTHY / RECOVERING / DOWN / UNAVAILABLE 등, SQLite `provider_health`에 저장.
- `check_health`는 `--version` 프로브만 수행(추론 호출 아님). 프로브 성공은 RECOVERING까지만, HEALTHY는 실제 호출 성공(`record_success`) 후.
- 출력 텍스트로 에러 분류(`ErrorCategory.classify`): 사용량 한도, 인증, 설정 오류(신뢰 디렉터리 등)는 다르게 처리 — 설정/실행 오류는 공급자를 DOWN으로 만들지 않음.
- 모드: 사용 가능 공급자 수에 따라 NORMAL / DEGRADED / STOP. 자율 모드에서 실패한 역할은 다른 공급자로 교체.

---

## 6. 안전 장치 요약

| 장치 | 위치 |
|---|---|
| 원본 무수정 (worktree에서만 빌드) | `core/git_manager.py`, `orchestrator.run` |
| Scope Guard (경로/파일 수/diff 상한, 보호 경로) | `core/safety.py`, `orchestrator.validate_post_build` |
| 프로젝트별 검증 명령은 Director 설정에만 (빌더가 약화 불가) | `config.yaml: projects` |
| 리뷰어 fail-closed | `claude_reviewer.py`, `codex_reviewer.py` |
| 사람 확인 필요 조건 (결제/실계좌/자격증명/비가역) | `core/autopilot.py`, `BaseHead.PROXY_POLICY` |
| 프로세스 트리 타임아웃 | `core/process_runner.py` (psutil / Windows `taskkill /T`) |
| 콘솔 인증 (로컬 출처 + 세션/토큰) | `companion_server.py::authorized` |

**주의 (README 명시):** AI가 쓴 테스트/빌드 명령은 **샌드박스 없이 현재 OS 계정 권한으로 실행**됩니다.
worktree는 파일 변경 격리일 뿐 보안 격리가 아니므로 VM/별도 계정에서 사용을 권장합니다.

---

## 7. 대시보드 프런트엔드 구조

빌드 도구·외부 라이브러리·CDN 없이 **순수 HTML/CSS/JS**입니다. 화면은 두 종류입니다.

### 7.1 TRINITY 콘솔 (`/`) — `console/` 폴더의 정적 파일

| 파일 | 줄 수 | 역할 |
|---|---|---|
| `console/index.html` | 101 | 뼈대 페이지. `stage.css`, `trinity.js`, `stage.js`, `console.js`를 불러오고, 작업실 화면을 담는 `<iframe id="workspace">`를 가짐 |
| `console/stage.css` | 278 | 무대 화면 스타일 |
| `console/trinity.js` | 330 | **WebGL2 3D 무대**. 역할별 슬롯 0=HEAD(위), 1=BUILDER(왼쪽), 2=REVIEWER(오른쪽) 세 코어가 하나의 코어로 합쳐지는 장면. 속도·밝기·색은 실제 작업 기록(`/ops/activity`)에 따라 `Trinity.set()`으로 바뀜 |
| `console/stage.js` | 400 | 무대 위 정보 패널(HUD). `/ops/activity`, `/status`, `/chat/messages`, `/ops/replay`를 읽어 하나의 모델을 만들고, 그 모델로 패널과 3D 장면을 함께 갱신. 실시간 보기와 다시보기가 같은 모델을 씀. 채팅 전송(`/chat`), 역할 변경(`/ops/roles`), 미션 결정(`/ops/mission/decide`) |
| `console/console.js` | 199 | iframe 안의 작업실 화면에 **미션 컨트롤 패널**을 끼워 넣음. 작업 정지(`/ops/missions/stop`), 시스템 중지(`/ops/shutdown`), 폴더 선택(`/ops/pick-folder`), 역할 변경, 상태 조회(`/ops/state`, `/status`, `/project`) |
| `console/console.css` | 60 | `console.js`가 iframe 문서에 넣는 패널 스타일 |

- 이 파일들은 `companion_server.py`의 `STATIC_FILES` **허용 목록**에 있는 것만 제공됩니다. `console/` 밖의 파일은 서버가 내보내지 않습니다.
- 응답 헤더에 CSP(`default-src 'self'`)가 붙어 외부 스크립트나 리소스를 불러올 수 없습니다.
- `console.js`에는 원작자의 외부 서비스인 "솔로몬 집무실"(`http://127.0.0.1:8710`) 링크가 하드코딩되어 있습니다. 이 환경에서는 동작하지 않습니다.

### 7.2 작업실 화면 (`/classic`, 콘솔 안 iframe) — Python이 HTML을 직접 생성

`/classic`은 **파일이나 폴더가 아니라 URL 경로**입니다. 요청 처리 흐름은 다음과 같습니다.

```
브라우저 GET /classic
  → companion_server.py:160   if path == '/classic': self.path = '/'   (경로를 '/'로 바꿔 넘김)
  → dashboard_server.py:2980  if parsed.path == '/': body = render_html()
  → dashboard_server.py:2486  render_html()  ← HTML/CSS/JS 전체를 Python 문자열로 만들어 응답
```

- `dashboard_i18n.py`는 상태, 결과, 실패 이유 코드 같은 화면 문구를 한국어로 바꾸고 권장 조치 문구를 제공합니다.
- 작업실 화면을 고치려면 `dashboard_server.py`의 `render_html()`을 수정합니다.

### 7.3 화면별 수정 위치 정리

| 고치고 싶은 것 | 파일 |
|---|---|
| 3D 무대 연출 | `console/trinity.js` |
| 무대 위 패널, 채팅 UI | `console/stage.js`, `console/stage.css` |
| 작업실 상단 미션 컨트롤 패널 | `console/console.js`, `console/console.css` |
| 표 중심 작업실 화면 | `dashboard_server.py::render_html()` |
| 한국어 문구 | `dashboard_i18n.py` |
| 정적 파일 추가 | `console/`에 파일을 넣고 `companion_server.py`의 `STATIC_FILES`에 등록 |

---

## 8. 콘솔 서버 API 개요 (`companion_server.py`)

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/?token=...` | 최초 브라우저 연결(세션 발급) |
| GET | `/`, `/classic` | TRINITY 무대 / 작업실 화면 |
| GET/POST | `/ops/roles` | 역할 배치 조회/변경 → `data/role_settings.json` |
| GET | `/ops/state` | 전체 상태 |
| POST | `/ops/missions/stop` | 미션 정지 |
| POST | `/ops/shutdown` | 시스템 중지 |
| POST | `/ops/pick-folder` | 폴더 선택 창(tkinter) |
| GET/POST | `/bridge/status, /bridge/projects, /bridge/digest, /bridge/missions, /bridge/orders …` | 외부 Solomon(CEO) 서비스 연동, 브리지 토큰 필요 |

그 외 요청(`/status`, `/chat`, `/chat/messages`, `/project`, `/ops/activity`, `/ops/replay` 등)은 `companion_server.py`의
`CompanionHandler`가 상속한 `dashboard_server.py` 핸들러가 처리합니다(`handle_chat_message`, `_run_mission_thread`,
`MissionRunner` 등). `/ops/state` 응답에는 진단 결과(`data/cli_diagnostics.json`)도 포함됩니다.

---

## 9. CLI 진단 스크립트와 연습용 폴더

| 폴더 | 역할 | 쌓이는 것 |
|---|---|---|
| `~/projects/director_smoke` | `scripts/cli_diagnostics.py`가 CLI를 실행하는 **빈 git 저장소**. 기본값은 Director 폴더 옆이고 `DIRECTOR_SMOKE_PROJECT`로 바꿀 수 있음 | 없음. HEAD 역할(읽기 전용)만 시험하고 결과는 `data/cli_diagnostics.json`에 저장 |
| 작업 대상 프로젝트 (예: `~/projects/director_sandbox`) | `director.py run/mission/do`나 콘솔에서 고른 실제 작업 대상 | `.director_worktrees/RUN-…/` 아래 실행별 결과 코드 |

- 진단은 Codex와 Claude에게 실제로 `create_order`를 시켜서 `HEAD_VERIFIED` 또는 `FAILED`를 판정합니다. 화면에는 상태만 출력되고 **오류 원인은 JSON의 `summary`**에 있습니다.
- 빈 전용 저장소를 쓰는 이유: Codex 어댑터는 작업 위치가 git 저장소여야 하고, 실제 프로젝트 코드가 프롬프트에 섞이지 않아야 결과가 일정하기 때문입니다.
- 원래 코드는 smoke 폴더가 이미 있다고 가정해서, 폴더가 없으면 두 공급자 모두 FAILED가 났습니다. Codex 쪽 메시지는 "git executable not found"로 잘못 표시됐습니다. 지금은 진단 시작 시 `core/bootstrap.prepare_project()`로 폴더 생성, `git init`, 빈 시작 커밋을 자동으로 처리합니다.

### 자주 만나는 실패

| 증상 (`summary`) | 원인 | 조치 |
|---|---|---|
| `Not logged in · Please run /login` (Claude) | `ANTHROPIC_API_KEY` 환경 변수로만 인증된 상태. Director가 이 변수를 지우고 실행하므로 인증 정보가 없음 | `claude` 실행 후 `/login`으로 구독 계정 로그인 |
| `CONFIG_ERROR: ... not inside a git repository` (Codex) | 작업 위치가 git 저장소가 아님 | 대상 폴더에서 `git init` (run/mission은 자동 처리) |
| `CONFIG_ERROR: ... CLI not found` | 실행 파일을 찾지 못함 | PATH 확인, 또는 `config.yaml`의 `executable`에 경로 지정 |

---

## 10. macOS 환경 관련 메모

- README상 macOS/Linux는 **코드만 대응, 실기 미검증**(v3.1.8~).
- `config.yaml`의 `solomon.allowed_roots`가 `D:/work` 등 Windows 경로로 되어 있으므로, Solomon 브리지를 쓸 경우 맥 경로로 수정 필요.
- 폴더 선택 창은 tkinter 필요 (Homebrew Python이면 `brew install python-tk`). 없으면 콘솔에 경로 직접 입력.
- CLI 진단: `python scripts/cli_diagnostics.py`, 역할별 smoke: `python scripts/test_role_cli_smoke.py`.
- 시스템 파이썬(`/usr/bin/python3`)에는 `python` 명령이 없습니다. `python3 -m venv .venv && source .venv/bin/activate`로
  가상환경을 켜면 `python`이 생기고, 기본 검증 명령(`python -m pytest tests/`)도 동작합니다.
- 전체 흐름 다이어그램은 `docs/FLOW_DIAGRAM.md`를 참고하세요.

---

## 11. 에이전트 구조 관점: 이 프로젝트는 "에이전트"인가

정확히는 **AI 에이전트(CLI)들을 정해진 순서로 지휘하는 오케스트레이터**입니다. Director 자체는 에이전트가 아닙니다.
한 줄로 말하면 **"에이전트를 부품으로 쓰는 멀티 에이전트 워크플로 시스템"**입니다.

판단 기준은 흔히 쓰는 구분을 따릅니다.
- **워크플로**: 코드가 미리 정한 경로대로 AI를 호출함
- **에이전트**: AI가 스스로 다음 행동과 도구 사용을 정하며 반복함

| 구성 요소 | 성격 | 이유 |
|---|---|---|
| Director (Python 코드) | 에이전트 아님. 결정적인 오케스트레이터 | 순서(계획 → 구현 → 검증 → 리뷰 → 판정), 상태 전이, 안전 검사, 파일 쓰기, 테스트 실행을 모두 코드가 정함 |
| 단일 run (`Orchestrator.run`) | **워크플로**에 가까움 | 단계와 순서가 고정돼 있고, AI는 각 단계에서 JSON 하나만 만들어 돌려줌 |
| 각 역할 호출 (Claude / Codex) | 제한된 에이전트 | CLI 자체는 에이전트지만, 읽기 도구만 주고 한 번 호출해 JSON만 받음. BUILDER도 파일을 직접 쓰거나 명령을 실행하지 못함(proposal 모드) |
| 미션 루프 (`MissionRunner`) | **에이전트에 가장 가까움** | HEAD가 실제 결과를 보고 다음 단계, 완료 여부, 사람 확인 필요 여부를 스스로 결정하며 반복함 |

**설계 의도:** AI에게 직접 실행 권한을 주는 대신 **"AI는 제안하고 판단만 하고, 실행과 검증은 코드가 한다"**는 구조입니다.
이렇게 해서 Scope Guard, 원본 프로젝트 보호, fail-closed 리뷰를 코드로 강제합니다.

### 11.1 같은 CLI가 여러 역할을 맡을 때 (예: HEAD = Claude, REVIEWER = Claude)

**호출할 때마다 별개의 CLI 프로세스가 새로 뜹니다.**

- HEAD와 REVIEWER는 각각 `claude --print ...`를 새 프로세스로 실행하고, 응답을 받으면 프로세스가 끝납니다.
- 코드에 `--resume`, `--continue`, 세션 ID 재사용이 없습니다. 그래서 **역할 사이에 대화 기억을 공유하지 않습니다.**
  REVIEWER는 Director가 프롬프트에 넣어 준 ORDER, diff, 테스트 결과만 보고 판단합니다.
- 같은 HEAD라도 `create_order`, `decide`, `plan_next`, `chat`이 **각각 별도 호출**입니다. 필요한 이력은 매번 프롬프트로 다시 전달됩니다.
- 역할마다 받는 지시문(프롬프트)과 출력 스키마가 다릅니다.

다만 분리되지 않는 부분도 있습니다.

- **같은 계정, 같은 기본 모델**을 씁니다. 모델 지정 옵션을 넘기지 않기 때문입니다.
- 맥락은 독립적이지만 **판단 성향은 같아서**, 같은 유형의 실수를 함께 놓칠 수 있습니다. 독립 검토의 효과는 다른 AI가 검토할 때보다 약합니다.
- 사용량 한도를 같은 구독에서 함께 소모합니다.

즉 **"기억을 공유하지 않는 별개의 실행"이지만 "다른 두뇌"는 아닙니다.** 기본 구성(HEAD·REVIEWER = Claude, BUILDER = Codex)은
적어도 만드는 쪽과 검토하는 쪽을 다른 AI로 나눈 것입니다. 세 역할을 모두 다른 AI로 나누려면 Antigravity를 켜야 하고,
콘솔의 역할 교환 기능(`core/role_settings.py`)은 처음부터 세 역할에 서로 다른 AI를 지정하도록 강제합니다.

---

## 12. 대시보드에서 HEAD에게 새 작업 지시하기

콘솔 채팅창("HEAD에게 지시하기")에 입력한 메시지는 `POST /chat` → `submit_chat` → `dashboard_server.py::handle_chat_message`로
처리됩니다. 아래 조건이 갖춰져야 미션이 시작됩니다.

### 12.1 필요한 것

**1. 콘솔 서버 실행과 브라우저 연결**

```bash
cd ~/projects/ai-dev-director
source .venv/bin/activate      # 서버를 켜기 전에 가상환경부터 켜기
./start-companion.sh           # 출력된 http://127.0.0.1:8081/?token=... 로 처음 한 번 접속
```

- 토큰 주소로 접속해 세션 쿠키를 받은 브라우저만 채팅 같은 상태 변경 요청을 보낼 수 있습니다(`companion_server.py::authorized`).
- 가상환경을 먼저 켜야 하는 이유: 검증 명령 `python -m pytest tests/`가 서버 프로세스의 PATH를 그대로 씁니다.
  macOS 시스템 파이썬에는 `python` 명령이 없어서, 가상환경 없이 서버를 띄우면 검증 단계가 실패할 수 있습니다.
  대상 프로젝트 안에 `.venv`가 있으면 그 파이썬을 우선 사용합니다(`core/bootstrap.py::configure_runtime`).

**2. HEAD 역할의 CLI가 실제로 응답할 것**

- 채팅 메시지는 HEAD에게 바로 전달됩니다(`orch.head.chat`). HEAD CLI가 로그인돼 있지 않으면 `HEAD Error: ... Not logged in`이 납니다.
- HEAD가 Claude라면 `claude` → `/login`으로 구독 로그인이 필요합니다. 아니면 역할 버튼이나 `config.yaml`에서 HEAD를 다른 공급자로 바꿉니다.
- 호출 전 `--version` 확인 결과 쓸 수 있는 공급자가 부족하면 `SYSTEM STOP: insufficient healthy providers`로 멈춥니다.

**3. 프로젝트 폴더 선택**

- 콘솔의 폴더 선택 버튼(tkinter 창, `/ops/pick-folder`)을 쓰거나 경로를 직접 입력합니다(`POST /project` → `set_project`).
- 선택하지 않아도 HEAD는 답하지만, 개발 작업이면 `PROJECT_SELECTION_REQUIRED`(HUMAN_REQUIRED)로 멈춥니다.

**4. 지시 문장**

- 예: `캡션 글자 수 검사기를 만들어줘`
- HEAD가 **QUESTION(질문)인지 DEV_TASK(개발 작업)인지** 판단합니다. 질문이면 답만 하고, 개발 작업이면 첫 ORDER를 만듭니다.
- 그다음 경로 표기 자동 보정(`core/entry_repair.py`), 사전 검사(`autopilot.entry_gate`)를 거쳐 **미션**으로 시작합니다.

### 12.2 선택 사항

| 항목 | 방법 |
|---|---|
| 작업 규모(프로필) | 메시지 앞에 `[빠르게]`/`[quick]`, `[표준]`, `[깊게]`/`[deep]` 붙이기. 최대 단계 2 / 6 / 12 (`autopilot.PROFILE_TAGS`) |
| 정지 | 80자 이하의 짧은 정지 메시지(`멈춰`, `중단`, `stop` 등) 또는 작업 정지 버튼. HEAD에게 보내지 않고 바로 정지 처리됨 |
| 결과 반영 | 기본값 `autonomy.auto_apply: "off"`. 결과를 확인한 뒤 **반영** 버튼을 눌러야 원본에 적용 |

### 12.3 실행이 멈추는 경우

- 결제·유료 서비스, 실제 자격증명, 실계좌 주문, 되돌릴 수 없는 외부 행동이 포함되면 → `HUMAN_REQUIRED` (사람 승인 필요)
- 같은 프로젝트에 이미 실행 중인 작업이 있으면 → 바쁨 메시지
- 채팅은 한 번에 하나만 처리합니다. 앞 요청을 처리하는 중에는 `409 busy`를 돌려줍니다.
- 같은 `request_id`로 다시 보내면 HEAD를 또 부르지 않습니다(`duplicate`).

### 12.4 작업 대상 폴더 조건

꼭 **새** 경로일 필요는 없습니다. 필요한 것은 작업 대상이 될 프로젝트 폴더 하나입니다
(`set_project` → `bootstrap.prepare_project` → `validate_project_path`).

| 경우 | 가능 여부 | 비고 |
|---|---|---|
| 이미 있는 git 프로젝트 | ✅ | 가장 일반적인 경우. 원본은 그대로 두고 그 안의 `.director_worktrees/`에서 작업 |
| 이미 있지만 git이 아닌 폴더 | ✅ | 자동으로 `git init`과 빈 시작 커밋을 만듦. 그 폴더에 `.git`이 새로 생김 |
| 새 빈 폴더 (없는 경로 포함) | ✅ | 폴더 생성부터 자동. 새 프로젝트를 처음부터 만들 때 사용 |
| `ai-dev-director` 폴더 자체나 그 하위 | ❌ | Director 자기 자신은 대상으로 쓸 수 없음 |
| 홈 폴더(`~`), 루트(`/`), Director의 상위 폴더 | ❌ | 범위가 너무 넓은 경로라 막혀 있음 |

- 어떤 폴더를 고르든 결과는 `<폴더>/.director_worktrees/RUN-…`에 생기고, 반영 전까지 원본 파일은 바뀌지 않습니다.
- 실제로 쓰던 프로젝트를 고르면 HEAD와 BUILDER가 그 코드를 읽고, AI가 만든 테스트가 그 위에서 **현재 OS 계정 권한으로** 실행됩니다.
  처음에는 연습용 폴더(예: `~/projects/director_sandbox`)로 익숙해진 뒤 실제 프로젝트에 쓰는 것을 권장합니다.
