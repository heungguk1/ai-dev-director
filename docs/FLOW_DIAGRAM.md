# AI DEV DIRECTOR 전체 플로우 (Mermaid)

> 기준 커밋: `be90907` (v3.2.0) · 함께 보기: [PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md)
> GitHub, VS Code(Markdown Preview Mermaid Support 확장), Obsidian 등에서 바로 렌더링됩니다.

목차
1. 전체 구조: 진입점 → 엔진 → 어댑터 → CLI
2. 단일 run 파이프라인 (시퀀스)
3. run 상태 머신
4. 미션 루프
5. 역할 배치와 공급자 장애 조치
6. 콘솔 요청 흐름

---

## 1. 전체 구조

```mermaid
flowchart TB
    subgraph ENTRY["진입점"]
        CLI["director.py<br/>run / mission / do / status / resume"]
        BROWSER["브라우저<br/>http://127.0.0.1:8081"]
        DIAG["scripts/cli_diagnostics.py"]
    end

    subgraph SERVER["콘솔 서버"]
        COMP["companion_server.py<br/>CompanionHandler: 인증, 정적 파일, /ops, /bridge"]
        DASH["dashboard_server.py<br/>DashboardHandler: /classic, /chat, /status, 미션 스레드"]
        STATIC["console/<br/>index.html, trinity.js, stage.js, console.js, css"]
    end

    subgraph ENGINE["오케스트레이션 엔진 core/"]
        MISSION["mission.py<br/>MissionRunner"]
        ORCH["orchestrator.py<br/>Orchestrator.run"]
        PM["provider_manager.py<br/>상태, 장애 조치"]
        SAFE["safety.py<br/>Scope Guard"]
        VER["verifier.py<br/>build / test / lint"]
        GIT["git_manager.py<br/>worktree"]
        AUTO["autopilot.py<br/>사람 확인 조건"]
        SM["state_machine.py<br/>run 상태"]
    end

    subgraph AGENTS["어댑터 agents/"]
        HEADA["HEAD<br/>claude_head / codex_head / google_roles"]
        BUILDA["BUILDER<br/>proposal_builder / antigravity_builder"]
        REVA["REVIEWER<br/>claude_reviewer / codex_reviewer / google_roles"]
    end

    subgraph RUNNER["CLI 실행 계층"]
        ENV["env_utils.py<br/>resolve_cli, get_sanitized_env"]
        TRIAD["research_runner.py<br/>TriadResearchRunner._command"]
        PTR["process_runner.py<br/>ProcessTreeRunner"]
    end

    subgraph CLIS["구독 로그인 CLI"]
        CLAUDE["claude --print<br/>--tools Read,Glob,Grep"]
        CODEX["codex exec<br/>--sandbox read-only -"]
        AGY["agy stream-json<br/>기본 꺼짐"]
    end

    subgraph STORE["저장소"]
        DB[("data/director.db")]
        FILES[("data/ run, mission JSON")]
        WT[("대상 프로젝트/.director_worktrees/RUN-…")]
    end

    CLI --> ORCH
    CLI --> MISSION
    BROWSER --> COMP
    COMP --> STATIC
    COMP --> DASH
    DASH --> MISSION
    DASH --> HEADA
    DIAG --> HEADA

    MISSION --> ORCH
    MISSION --> HEADA
    ORCH --> PM
    ORCH --> SAFE
    ORCH --> VER
    ORCH --> GIT
    ORCH --> AUTO
    ORCH --> SM
    ORCH --> HEADA
    ORCH --> BUILDA
    ORCH --> REVA

    HEADA --> ENV
    REVA --> ENV
    BUILDA --> ENV
    HEADA --> PTR
    REVA --> PTR
    BUILDA --> TRIAD
    TRIAD --> PTR
    PTR --> CLAUDE
    PTR --> CODEX
    PTR -.-> AGY

    SM --> DB
    PM --> DB
    ORCH --> FILES
    MISSION --> FILES
    GIT --> WT
    VER --> WT
```

---

## 2. 단일 run 파이프라인

`core/orchestrator.py::Orchestrator.run` 기준입니다. 기본 역할: HEAD=Claude, BUILDER=Codex(proposal 모드), REVIEWER=Claude.

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant O as Orchestrator
    participant PM as ProviderManager
    participant H as HEAD
    participant G as GitManager
    participant B as BUILDER
    participant S as Safety
    participant V as Verifier
    participant R as REVIEWER

    U->>O: run(project, goal)
    O->>O: bootstrap.prepare_project<br/>폴더 생성, git init, 빈 시작 커밋
    O->>PM: check_health (--version 확인)
    PM-->>O: 사용 가능한 공급자
    O->>O: 역할 배치, 사용할 수 없는 역할은 교체
    Note over O: IDLE → PLANNING
    O->>H: create_order(goal, project_context)
    H-->>O: ORDER JSON (OrderSchema 검증)
    O->>S: apply_safety_caps (파일 수, diff 상한)
    Note over O: PLANNING → ORDER_READY
    O->>G: worktree 생성 (.director_worktrees/RUN-…)
    O->>V: 기존 테스트 실행 (베이스라인)

    loop 수정 반복 (최대 max_repairs)
        Note over O: → BUILDING
        O->>B: run(order, worktree)
        Note right of B: 빈 임시 폴더에서 CLI 실행<br/>파일 내용을 JSON으로 제안
        B->>S: 경로, 앵커 검증 후 worktree에 기록
        B-->>O: BuilderResult
        O->>S: validate_post_build<br/>경로, 파일 수, diff, 삭제 검사
        Note over O: BUILDING → VERIFYING
        O->>V: build / test / lint (API 키 변수를 지운 환경)
        V-->>O: 검증 결과, git diff
        Note over O: VERIFYING → REVIEWING
        O->>R: review(order, diff, verification)
        R-->>O: ReviewResult (응답 없거나 깨지면 FAIL)
        Note over O: REVIEWING → DECIDING
        O->>H: decide(order, builder, verification, review)
        H-->>O: PASS / FAIL + next_steps / HUMAN_REQUIRED
        alt FAIL이고 수정 기회가 남음
            O->>O: next_steps를 ORDER context에 넣고 다시 BUILDING<br/>마지막 시도는 다른 공급자로 교체
        end
    end

    O->>O: apply_final_pass_gate
    O-->>U: 최종 상태 (PASS / FAIL / HUMAN_REQUIRED / TIMEOUT)<br/>원본 프로젝트는 그대로
```

---

## 3. run 상태 머신

`core/state_machine.py`의 `VALID_NORMAL_TRANSITIONS`와 `can_transition` 기준입니다.

```mermaid
stateDiagram-v2
    [*] --> IDLE: create_run
    IDLE --> PLANNING
    PLANNING --> ORDER_READY: ORDER 생성, 검증
    ORDER_READY --> BUILDING
    BUILDING --> VERIFYING
    VERIFYING --> REVIEWING
    REVIEWING --> DECIDING
    DECIDING --> PASS: HEAD PASS + 최종 게이트 통과
    DECIDING --> BUILDING: FAIL, 수정 반복

    state "비종료 상태 어디서나" as ANY
    ANY --> FAIL: 수정 기회 소진, 오류
    ANY --> HUMAN_REQUIRED: 범위 초과, 공급자 없음,<br/>사람 확인 필요
    ANY --> TIMEOUT: CLI 시간 초과

    PASS --> [*]
    FAIL --> [*]
    HUMAN_REQUIRED --> [*]
    TIMEOUT --> [*]

    note right of HUMAN_REQUIRED
        승인(approve_and_resume) 시
        reopen_after_human_approval로 재개
    end note
```

---

## 4. 미션 루프

`core/mission.py::MissionRunner._advance` 기준입니다. 미션 상태: `RUNNING`, `DONE`, `BLOCKED`, `INCOMPLETE`, `FAILED`, `STOPPED`.

```mermaid
flowchart TD
    START(["채팅 메시지 또는<br/>director.py mission / do"]) --> CHAT["HEAD.chat<br/>QUESTION인지 DEV_TASK인지 판별"]
    CHAT -->|QUESTION| ANSWER(["답변만 반환"])
    CHAT -->|DEV_TASK + 첫 ORDER| CREATE["MissionRunner.create<br/>status = RUNNING"]
    CREATE --> EXEC

    EXEC["단계 실행<br/>Orchestrator.run<br/>base_worktree = 직전 PASS 작업본"] --> RES{"run 결과"}
    RES -->|PASS| BASE["base_worktree 갱신"]
    RES -->|FAIL / TIMEOUT| PLAN
    RES -->|HUMAN_REQUIRED 등| NEED{"사람 확인이 정말 필요한가<br/>결제, 자격증명, 실계좌,<br/>되돌릴 수 없는 외부 행동, 보안"}
    NEED -->|예, HEAD 재검토도 ESCALATE| BLOCKED(["BLOCKED<br/>사람 결정 대기"])
    NEED -->|아니오| PLAN
    BASE --> PLAN

    PLAN["HEAD.plan_next<br/>실제 증거로 다음 단계 계획"] --> PS{"계획 결과"}
    PS -->|DONE| DONE(["DONE"])
    PS -->|BLOCKED| BLOCKED
    PS -->|계획 실패| INC(["INCOMPLETE"])
    PS -->|CONTINUE| BUDGET{"단계 수가 max_steps 미만?"}
    BUDGET -->|아니오| INC
    BUDGET -->|예| STREAK{"연속 실패가<br/>max_consecutive_failures 미만?"}
    STREAK -->|아니오| FAILED(["FAILED"])
    STREAK -->|예| GATE["autopilot.entry_gate<br/>다음 ORDER 사전 검사"]
    GATE -->|에스컬레이션| BLOCKED
    GATE -->|통과| EXEC

    DONE --> APPLY{"auto_apply"}
    APPLY -->|off| KEEP(["worktree 유지<br/>콘솔에서 반영 버튼"])
    APPLY -->|on_done / on_pass| MERGE(["프로젝트에 반영<br/>사람이 바꾼 파일은 덮어쓰지 않음"])

    STOP(["작업 정지 버튼 / '중단' 채팅"]) -.->|CLI 프로세스 트리 종료| STOPPED(["STOPPED<br/>재시작해도 재개 안 함"])
```

---

## 5. 역할 배치와 공급자 장애 조치

```mermaid
flowchart TD
    CFG["config.yaml<br/>head / builder / reviewer .provider"] --> RS{"data/role_settings.json<br/>있음?"}
    RS -->|예: 콘솔에서 역할 교환| APPLY["role_settings.apply<br/>세 역할 모두 다른 AI, builder는 proposal 모드"]
    RS -->|아니오| INIT
    APPLY --> INIT["Orchestrator._init_head / _init_builder / _init_reviewer"]

    INIT --> MAP{"provider 별칭"}
    MAP -->|claude, anthropic| C["ClaudeHead / ClaudeReviewer<br/>또는 ProposalBuilder claude"]
    MAP -->|codex, openai| X["CodexHead / CodexReviewer<br/>또는 ProposalBuilder openai"]
    MAP -->|antigravity, google| G["GoogleHead / GoogleReviewer<br/>또는 ProposalBuilder antigravity"]
    MAP -->|mock| M["MockHead (테스트용)"]

    C --> RESOLVE
    X --> RESOLVE
    G --> RESOLVE
    RESOLVE["env_utils.resolve_cli<br/>PATH → 알려진 설치 경로"] --> AGYQ{"agy이고<br/>antigravity.enabled = false?"}
    AGYQ -->|예| NONE["None, GOOGLE = UNAVAILABLE"]
    AGYQ -->|아니오| HEALTH

    HEALTH["ProviderManager.check_health<br/>--version 확인"] --> MODE{"사용 가능한 공급자 수<br/>agy 꺼짐: 기준 2개 / 켜짐: 3개"}
    MODE -->|전부| NORMAL["NORMAL"]
    MODE -->|2개| DEGRADED["DEGRADED"]
    MODE -->|1개 이하| STOPM["STOP"]

    NORMAL --> ASSIGN
    DEGRADED --> ASSIGN
    STOPM --> ASSIGN
    ASSIGN["_autonomous_role_assignment<br/>사용 불가 역할만 다른 공급자로 교체"] --> CAN{"모든 역할에<br/>공급자 있음?"}
    CAN -->|예| RUN(["run 진행"])
    CAN -->|아니오| HR(["HUMAN_REQUIRED<br/>PROVIDER_FAILURE"])

    RUN --> ERR{"실행 중 CLI 오류"}
    ERR -->|사용량 한도, 인증, 시간 초과| SWAP["_swap_agent<br/>다른 공급자로 재시도"]
    ERR -->|CONFIG_ERROR, 실행 오류| NOSWAP["공급자를 DOWN 처리하지 않음"]
```

---

## 6. 콘솔 요청 흐름

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자 브라우저
    participant C as companion_server.py<br/>CompanionHandler
    participant D as dashboard_server.py<br/>DashboardHandler
    participant F as console/ 정적 파일
    participant MR as MissionRunner 스레드

    U->>C: GET /?token=… (최초 1회)
    C-->>U: 세션 발급
    U->>C: GET /
    C->>F: STATIC_FILES 허용 목록 확인
    F-->>U: index.html, trinity.js, stage.js, console.js, css
    U->>C: GET /classic (iframe)
    C->>D: 경로를 '/'로 바꿔 전달
    D-->>U: render_html() 결과 HTML

    loop 주기적 조회
        U->>D: GET /ops/activity, /status, /chat/messages
        D-->>U: 작업 기록, 상태, 채팅
    end

    U->>D: POST /chat 「기능 만들어 줘」
    D->>MR: handle_chat_message → _run_mission_thread
    MR-->>D: 단계별 알림 (채팅 메시지로 기록)

    U->>C: POST /ops/missions/stop
    C->>MR: stop_request → 실행 중 CLI 종료, STOPPED
    U->>C: POST /ops/roles
    C->>C: role_settings.save → 다음 run부터 적용
```
