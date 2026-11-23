# 마감방 개인 비밀번호 v3 (단순화판, 사용 제한 없음) · 검증 보고서

## 대상
- 저장소 `https://github.com/zepzep10101-bueang/gogogo.git`, 기준 `ebeead5d7fff74f1599f3e76e33e73e0e737855d`.
- 전달 ref `refs/heads/delivery/gogogo-auth-v3`. 기준 위 커밋 한 개입니다. 커밋 값은 받은 번들에서 `git bundle list-heads <번들>`로 확인하십시오.
- 외부 배포나 운영 검증 실적은 없습니다.

## 이번 판의 범위 — 기본 조건만
기본 조건은 **«휴대폰과 컴퓨터 어디서든 같은 닉네임과 개인 비밀번호 두 가지로 입장»**이고, **비밀번호는 회원이 스스로 정하고 스스로 바꾸며 관리자가 나눠 주지 않습니다.** 이 판은 그 기능과 **사용을 막지 않는 안전장치**만 담았습니다. 입장 외의 사용 제한(시도 횟수·기기 수·세션 수·대기 시간)은 두지 않습니다.

| 남긴 것 | 이유 |
| --- | --- |
| 로그인 입력은 닉네임·개인 비밀번호 두 칸 | 기본 조건 |
| 기존 회원 최초 설정: 옛 기기 열쇠와 방 암호를 둘 다 확인 → 개인 비밀번호. 기록 유지 | 지금 회원이 기록을 잃지 않고 넘어오는 길 |
| 신규 가입: 방 주소만으로 스스로 가입(방 암호 요구는 방장 화면 체크 상자 한 번으로 켜고 끔, 처음 기본값은 `GOGOGO_SIGNUP_ROOM_PASSWORD`, 기본 꺼짐). 같은 이름·닮은 이름(띄어쓰기·기호 제외 모양) 검사는 기준과 같음 | 관리자가 아무것도 주지 않고 새 회원이 들어올 길 |
| 같은 이름으로 여러 기기·탭 동시 입장, 서로 끊지 않음 | 휴대폰과 컴퓨터를 함께 쓰기 |
| 분실·변경: 로그인된 기기에서 현재 비밀번호 없이 스스로 바꾸기(`change_password`). 다른 기기 연결은 유지. 바꿀 권한은 그 연결이 입장한 비밀번호 세대에만 있음 | 관리자 없이 잊은 비밀번호를 다시 정하는 길. 바뀌기 전 세션이 되돌려 바꾸지 못하게 |
| 로그인된 기기가 하나도 없을 때만: 방장이 발급하는 24시간 일회용 코드(기존 12자리 코드 재사용) | 드문 예외 |
| 한글 NFC 정규화, 자동완성·붙여넣기·표시 버튼, 끊김 뒤 자동 재연결 | 휴대폰·PC 차이 없이 편하게 |
| scrypt(회원별 salt) 해시 저장, 원문 미저장 | 사용을 막지 않는 안전장치 |
| 새 비밀번호를 정할 때만 최소 8자 확인(그 밖의 입력 규칙 없음). 값은 `member_auth.MIN_PASSWORD_CHARS` 한 곳, 로그인 때는 다시 보지 않음 | 사용을 막지 않는 안전장치. 값을 바꿔도 기존 비밀번호로 입장 |
| 인증 처리량 서버 전체 상한: 모든 인증 요청이 잠금 대기 전에 자리를 받음(18개), 잠금·계산 대기와 계산 시작까지 합쳐 20초(이미 시작한 계산은 끝까지 함, 응답 완료 상한 아님), 계산은 스레드 2개(scrypt·pbkdf2 모두). 넘으면 계산 없이 `busy` → 화면 «몇 초 뒤 다시 눌러 주세요». 자리는 요청과 계산 스레드가 모두 끝난 뒤 반납 | 서버 메모리 보호. 사람·출처별 제한이 아니고, 3~5명 방의 보통 사용에서는 닿지 않음 |
| 로그인 전 연결 서버 전체 200개 상한(첫 대기 전에 자리 예약, 모든 종료 경로에서 한 번만 반납, 출처 무관, 넘으면 `busy` 뒤 5초 안에 닫음) | 대량 연결로 서버가 무너지지 않게(시스템 보안). 정상 사용에서는 닿지 않음 |
| 조건부 단일 문서 쓰기와 majority·journal 쓰기 확인 | 동시 가입·덮어쓰기 방지 |
| 인증 필드를 저장·브로드캐스트·로그에서 제외. 익명에게는 `/`·`/healthz`·`/user_count`만 | 해시·세션 유출 방지 |
| 구버전 표식(`identity_keys`에 `personal-password:no-device-key`) | 구버전으로 돌아가도 남이 이름을 가로채지 못함 |
| 방장 강퇴·복구 코드 발급으로 회수한 세션은 카드 배경·설정 응답 직전에 다시 확인 | 회수가 실제로 지켜지게 |

| 뺀 것(직전 r2 대비) | 대신 |
| --- | --- |
| 닉네임별 5분 10회 잠금 | 없음 |
| 출처별 5분 10회·전체 1분 60회 실패 제한(기준에도 출처별 10회·전체 300회가 있었음) | 없음. 프록시 설정이 없으면 방 전체가 한 출처로 묶여 실제 회원이 막힐 수 있었습니다 |
| 같은 이름 새 입장 때 이전 WebSocket·HTTP 세션 회수(기준에도 있던 동작) | 동시 접속을 그대로 둡니다 |
| 해시 동시 2건 초과 시 즉시 `busy`, 인증 대기 12건 상한, 이름 잠금 5초 대기 초과 `busy`, 확인이 접속 60초를 넘겨 끝나면 `busy` | 위 «서버 전체 상한» 하나로 바꿨습니다(동시 2건은 기다리고, 18건을 넘거나 20초 넘게 기다린 경우만 `busy`) |
| 읽기 majority read concern | 쓰기 확인(majority·journal)은 유지합니다 |
| `MEMBER_AUTH_WRITES` 쓰기 동결 스위치 | 사고 때는 공개를 닫고 짱구애비와 상의합니다 |
| `reset_member_auth.py` 전체 폐기 도구와 복원 연습 | «과거 DB 백업으로 복원하지 말 것»(HANDOFF 1부 «적용 전에 확인하실 것» 2번) |
| 인증 사건 기록(`audit_event`) | 없음 |
| 배포된 적 없는 v1 중간 형식 호환 코드 | v2 레코드만 인정합니다 |
| r2의 비밀번호 변경(현재 비밀번호 필요, 바꾸면 모든 기기 로그아웃) | 현재 비밀번호 없이 로그인된 기기에서 바꾸고, 다른 기기는 끊지 않는 방식으로 바꿨습니다 |
| 가입 때 방 공용 암호(기준 동작) | 방 주소만으로 가입. 선택 설정으로 다시 켤 수 있음(기본 꺼짐) |
| 최소 15자 | 최소 8자 |
| 금지 비밀번호 규칙(닉네임과 같음·한 글자 반복·방 암호와 같음) | 길이 규칙만 둡니다 |
| 가입 정원 500(기준에도 있던 상한)과 가입 순간 닮은 이름 재검사(`name_shape`) | 가입 전 이름 검사만 둡니다. 3~5명 방이라 정원은 두지 않습니다 |

## JNB 격리 검증 (2026-11-22)
조건:
- MONGO_URI·운영 비밀 없이 실행했습니다. push는 막았습니다.
- 기준 `main.py`는 Git blob `2be801b9…`을 대조한 뒤 읽기 전용으로 두었습니다.
- 환경은 Windows 11, Python 3.12.10(전용 venv, 의존성 21개), Node 24.22.1, 설치된 Microsoft Edge(headless, 빈 프로필)입니다.

| 묶음 | 명령 | 결과 |
| --- | --- | --- |
| Python 단위·핸들러 | `python -B -X utf8 -m unittest discover -s tests -v` | 54건 실행, 52 통과, 2 건너뜀(일회용 `mongod`가 있어야 도는 T6·T7 실DB 시험), 실패 0 |
| 구버전 복귀 | `-p test_legacy_rollback.py` | 1/1 통과. 기준 `ebeead5` 앱에서 전환·가입·복구한 이름에 대해 비밀번호만으로 입장, 같은 이름 신규 등록, 쓴 코드가 모두 거절됩니다 |
| 화면 스크립트 | `node tests/client_login.cjs` | 12/12 통과 |
| 실제 브라우저 두 프로필 | `python tests/edge_smoke.py` | 25/25 PASS. 기존 프로필 전환 → 빈 프로필에서 틀린 비밀번호 거절 뒤 두 칸 로그인 → **두 기기 동시 로그인 유지**(공개 집계 2) → 전송이 끊기면 두 기기 모두 자동 재연결 → 재연결 뒤 둘 다 로그인 상태 유지 → **한 기기에서 현재 비밀번호 없이 비밀번호 바꾸기**, 다른 기기 연결 유지, 바뀌기 전에 들어온 기기는 다시 바꾸지 못함 → 새로 고친 기기에서 새 비밀번호로 입장 |
| scrypt 1회 | 7회 측정 중앙값 | 0.41~0.91초(같은 JNB에서 측정 때마다 다름, 다른 시험이 함께 돌 때 느려짐) |

시험으로 확인한 «사용 제한 없음»:
- 같은 출처에서 틀린 비밀번호를 25번 넣은 뒤에도 맞는 비밀번호와 방장 비밀번호로 입장됩니다(`test_no_attempt_limit_blocks_the_right_password`).
- 같은 이름으로 세 연결이 동시에 인증 상태를 유지합니다(`test_two_browsers_use_only_name_and_password`).
- 해시 요청 6건이 동시에 와도 거절 없이 모두 처리됩니다(`test_concurrent_requests_queue_instead_of_being_refused`).

시험으로 확인한 «관리자 없이 스스로»:
- 방 주소만으로 가입되고, 선택 설정을 켜면 방 암호가 있어야 가입됩니다. 화면도 설정에 따라 방 암호 칸을 보이거나 숨깁니다(`test_open_signup_needs_no_admin_and_optional_room_password`, `client_login.cjs`).
- 열린 가입에서도 활동 기록에 있는 이름·닮은 이름·방장 이름은 가입되지 않습니다(`test_invitation_historical_name_and_admin_boundaries`).
- 방장이 체크 상자로 가입 방 암호 요구를 켜고 끄며, 일반 회원은 바꾸지 못하고, 서버에 방 암호가 없으면 켜지지 않습니다(`test_admin_switches_signup_room_password_with_one_click`).
- 가입 방 암호 설정은 DB 저장 확인 뒤에만 성공으로 알리고, 저장이 실패해도 가입을 넓히지 않습니다(`test_signup_switch_reports_success_only_after_a_durable_write`, 화면 시험 «the admin switches the signup room password»).
- 일반 방 상태 저장은 가입 설정을 쓰지 않아, 옛 스냅숏이 늦게 저장돼도 방장이 고른 설정이 DB와 재시작 뒤에 그대로 남습니다(`test_room_state_save_never_writes_the_signup_policy`).
- 방장 변경이 겹쳐도 한 번에 하나씩 저장한 뒤 반영해, 메모리와 DB가 같은 값으로 끝납니다(`test_signup_policy_changes_are_saved_then_applied_one_at_a_time`).
- 로그인된 기기에서 현재 비밀번호 없이 바꾸고, 다른 기기는 연결을 유지하며, 이전 비밀번호는 거절되고 새 비밀번호로 입장됩니다(`test_member_changes_own_password_from_a_signed_in_device`, Edge).
- 다른 기기에서 바꾼 뒤에는, 그 전에 들어와 있던 연결이 다시 바꾸지 못하고(`stale`) 연결은 유지됩니다. 바꾼 기기와 새 비밀번호로 새로 들어온 기기는 바꿀 수 있습니다(`test_change_right_follows_the_password_generation`, Edge).
- 로그인하지 않은 연결과 방장 연결은 바꾸지 못합니다(`test_password_change_needs_a_signed_in_member_session`). 복구 코드가 발급된 이름은 바꾸지 못합니다(`test_change_writes_only_over_a_current_password_account`).

시험으로 확인한 «서버 전체 상한»:
- 자리가 다 차면 `busy`로 답하고, 계산이 끝나 자리가 돌아오면 다음 요청이 처리됩니다(`test_full_queue_answers_busy_and_frees_slots_when_workers_end`).
- 상한을 넘겨 기다린 요청은 계산하지 않고 `busy`로 끝납니다(`test_request_waiting_too_long_is_skipped_without_computing`).
- 호출이 취소된 대기 요청은 계산하지 않고, 자리는 계산 스레드가 끝난 뒤 돌아옵니다(`test_abandoned_request_is_not_computed_and_releases_its_slot`).
- 핸들러에서 `busy`면 세션을 주지 않고, 다시 시도하면 입장됩니다(`test_server_capacity_answers_busy_and_admits_after_retry`).
- 이름 잠금 대기도 같은 기한 안에서 끝나 `busy`가 되고, 방장·방 암호 확인도 같은 상한을 씁니다(`test_admission_precedes_lock_waits_and_covers_admin_and_room_proofs`, `test_lock_wait_counts_against_the_deadline_and_frees_the_slot`).
- 요청이 사라져도 계산 스레드가 끝나기 전에는 자리를 돌려주지 않습니다(`test_slot_stays_taken_until_the_worker_really_ends`).
- 로그인 전 연결이 상한에 닿으면 새 연결은 `busy`를 받고 1013으로 닫힙니다(`test_pending_signin_connections_have_one_high_server_wide_cap`).
- 동시에 몰린 연결도 상한을 넘지 못하고, 자리는 로그인·닫힘·접속 실패·취소 때 한 번만 돌아옵니다(`test_pending_slot_is_taken_before_the_first_await_and_returned_once`).
- 응답을 읽지 않는 연결을 거절하는 일도 정해진 시간 안에 끝납니다(`test_refusing_an_over_cap_connection_ends_in_bounded_time`).
- 최소 길이는 새 비밀번호에만 적용됩니다. 설정값을 올려도 이전 값으로 정한 비밀번호로 입장됩니다(`test_length_rule_applies_only_to_new_passwords`).

기존 비밀번호 호환: 기준 `ebeead5`의 `identity_enroll`에는 일회용 코드 해시(`{h, exp}`)만 있고 비밀번호 검증 레코드가 없습니다(기준 `main.py` 248~274, 2882~2890줄). 따라서 적용 시점에 이미 있는 짧은 비밀번호나 v1 레코드는 없습니다. 모든 회원은 이 판에서 처음 비밀번호를 정합니다. 배포되지 않았다는 근거: 운영은 앱 저장소 main을 자동 배포하고, 시험 스크립트는 실행 때마다 `origin/main`이 기준 `ebeead5`와 같은지 확인합니다(2026-11-23 실행에서도 같음). 이전 후보들은 번들로만 전달·검토되었고 push된 적이 없습니다. 운영 DB 자체는 열어 보지 않았으므로, 적용 전 백업(HANDOFF 적용 순서 1번)에서 `identity_enroll`에 `v` 필드가 없는지 한 번 보는 것을 권합니다.

남겨 둔 것: 로그인 메시지를 한 번도 보내지 않은 연결은 60초 뒤 닫습니다(기준 동작). 화면은 «입장하기»를 누를 때 연결을 열기 때문에, 회원 사용에는 영향이 없습니다.

## 남는 위험
- **시도 제한이 없으니 쉬운 비밀번호는 맞혀질 수 있습니다.** 최소 길이는 8자이고, scrypt 해시(1회 약 0.4~0.8초, 서버 전체 동시 2건)는 추측을 느리게 할 뿐 시도 제한을 대신하지는 못합니다. 시도 제한을 두지 않은 것은 소유자 판단입니다.
- **방 주소를 아는 누구나 가입할 수 있습니다.** 원치 않는 가입자는 방장 강퇴로 처리하고, 필요하면 `GOGOGO_SIGNUP_ROOM_PASSWORD=1`로 방 암호 요구를 켭니다.
- **로그인된 채 잠기지 않은 기기를 남이 쓰면 비밀번호를 바꿀 수 있습니다.** 현재 비밀번호를 묻지 않는 것은 잊었을 때 쓰는 길이기 때문입니다. 바꿔도 다른 기기는 끊지 않으므로, 남이 이미 들어와 있었다면 방장 강퇴가 필요합니다. 다만 바뀌기 전에 들어와 있던 연결은 다시 바꿀 수 없습니다.
- 위 위험들과 공개 가입, 현재 비밀번호 없이 바꾸기, 다른 기기 연결 유지, 대량 요청 때의 일시 `busy`는 소유자가 사용 편의를 위해 받아들인 계정 단위 위험입니다. 위험마다 대처와 코드 위치는 START_HERE 에이전트 지시문 «0-2. 의도적으로 감수한 위험»에 있습니다.
- 연결이 끊긴 것은 확인 계산 중에는 알 수 없습니다. 그래서 끊긴 연결의 요청도 계산될 수 있지만, 서버 전체 상한(18건, 대기 20초) 안에서만 남습니다.

## 전달 뒤 확인 항목 (이번 전달의 차단 조건 아님)
1. **T12:** 짱구 Linux Docker 이미지 안에서 같은 시험을 돌립니다(`--network none`, 합성 자료).
2. **VM Edge 실기:** 짱구 Win11 VM의 Edge로 정상 흐름과 두 기기 동시 접속을 확인합니다.
3. **T6·T7 실DB:** 일회용 MongoDB로 확인합니다(`GOGOGO_TEST_MONGOD`).
4. **짱구 입장 시간:** 실제 서버에서 scrypt를 포함해 입장까지 걸리는 시간을 측정합니다.
