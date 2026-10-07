# 원랜디(ORDR 2.323[R]) AI 딥러닝 효율화 레퍼런스 조사 — 2026-09-27

## 한눈에

- 지금 병목은 모델이 아니라 **데이터**다. 실게임 기록 기준 시간당 약 900전이이고, A2C는 모은 전이를 한 번 쓰고 버린다. 전이 기록에는 상태와 마스크가 없어서 나중에 다시 학습에 쓸 수도 없다.
- 무작위로 시작한 정책은 11판 모두 3~4라운드에서 졌다. 소환은 전체 결정의 4%뿐이었고, 57%는 스토리↔라인 왕복이었다. 이미 있는 규칙 교사(`teacher.py`)와 모방학습 함수(`Learner.imitate`)를 학습 루프에 연결하는 것이 가장 싸고 큰 개선이다.
- 가장 효과가 큰 3가지는 ① 교사로 시작(BC → JSRL/Kickstarting) ② 전이를 재사용 가능한 데이터로 저장하고 PPO/오프라인 RL로 여러 번 쓰기 ③ 매크로 행동·이벤트 시점 결정·학습된 거부 마스크로 결정 수와 행동 공간 줄이기다. OrandSimulator 위 사전학습은 그다음 단계로 권장한다.

우선순위 표기: **지금 당장**은 게임 없이 코드·데이터만으로 가능한 것, **실게임 연결 후**는 브리지가 안정된 뒤 수집이 필요한 것, **나중**은 시뮬레이터나 대규모 작업이 필요한 것이다.

---

## 1. 현재 구조 (이번 세션에서 파일을 직접 읽어 확인)

### ORD Picker `src/ord_ai/`

- **모델** (`learning.py`): 128×2 tanh MLP 공유 인코더에 정책·가치 헤드를 둔다. 마스크된 Categorical 분포를 쓰는 A2C다(γ=0.99, 엔트로피 0.01, 그래디언트 클립 0.5, Adam 3e-4). 전이 32개 또는 에피소드 종료마다 1회 업데이트하고 그 전이는 버린다. 순수 on-policy라 재사용이 없다.
- **관측** (`observation.py`): 스칼라 10개(라운드·골드·목재·식량·적 수/제한·데스카운트·스토리·보스·신세계 여부)와 유닛 467종의 개수를 log로 정규화해 합친 477차원이다. 위치, 체력, 쿨타임, 라인 적 구성은 없다.
- **행동**: 체크포인트 기준 244개(기본 7개 + 원본 조합 237개)다. 조사 도중 같은 폴더의 다른 작업(st_01a0e256)이 `summon_all_random_wisps`(코드 7)를 추가했고, 19:35에 `build/catalog.json`이 245개로 바뀌었다. 그래서 기존 `runs/ordr` 체크포인트는 이제 `load_checkpoint` 호환성 검사에서 거부된다.
- **마스크** (`catalog.available_actions`): 골드·목재·재료 유닛만 검사한다. 조합 237개 중 약 232개는 마스크가 보지 않는 추가 조건(UPUN 145개, KING+UBAN 54개 등)이 있고, 이 조건은 맵이 최종 판정한다. 추가 조건이 없는 조합은 5개뿐이다.
- **통신** (`channel.py`, `jass/bridge_functions.j`): 동기식으로 한 번에 명령 1개만 기다린다.
  - 순서: Python이 명령마다 새 파일(`cmd_<ep>_<seq>.txt`)을 쓴다 → 맵이 0.25초 주기 타이머로 Preloader를 읽는다 → `BlzSendSyncData` → 행동 실행 → 1.0 게임초 뒤 `state.txt`를 쓴다 → Python watchdog 이벤트가 받는다.
  - 에피소드 리셋은 Battle.net을 거쳐 Warcraft를 종료하고 다시 실행하는 방식이다(`launcher.GameHost.relaunch`).
- **보상**: 조사 중 다른 작업(st_01a0e254)이 19:21에 v2로 바꿨다. 이전 실행은 v1(패배 −50, 승리 +50, 거부 −0.1)이었다.

  | 항목 | v2 보상 |
  |---|---|
  | 새 라운드 | +1 |
  | 스토리 단계 | +5 |
  | 보스 처치 | +3 |
  | 적 압력 변화 | 0.2×(이전 압력 − 이후 압력) |
  | 거부된 행동 | −0.05 |
  | 패배 | −10 |
  | 승리 | +20 |

- **이미 있지만 연결 안 된 것**: 규칙 교사 `teacher.py`(19:06), `Learner.imitate`·`Demonstration`·어드밴티지 정규화·`discounted_returns`(19:30). `training.py`는 아직 교사도 모방학습도 호출하지 않는다.
- **기록** (`transitions.jsonl`): step·episode·sequence·action·accepted·reward·round·story·enemies·status만 남긴다. 관측 벡터, 골드, 유닛, 마스크, 벽시계 시각이 없어서 이 기록으로는 오프라인 RL이나 BC 재학습을 할 수 없다.
- **환경**: Windows, RTX 3080 Ti 12GB, PyTorch 2.11.0+cu128, Python 3.12. gymnasium은 쓰지 않는다.

### 브리프와 다른 점 (확인)

- "실게임에서는 학습이 안 돈다"는 사실과 다르다. 18:01~19:14 사이 실게임 전이 1,096개, 11에피소드(4~14번)로 학습했다(`runs/ordr/metadata.json` steps=1096).
  - 11판 모두 3~4라운드에서 패배했다.
  - 에피소드 보상은 −48~−49로, −50 종료 보상이 대부분을 차지했다.
  - 종료 업데이트 때 value_loss가 약 1,900~2,100까지 튀었다.
- 19:14 이후로는 전이가 늘지 않았다. `reset_15.txt`(19:14:29)가 소비되지 않은 채 남아 있고, `boot.txt`·`state.txt`는 19:32에 갱신됐다. 다음 리셋에서 멈춘 것으로 보인다. 원인은 wB:p9 세션 범위라 조사하지 않았다.
- 훈련 맵 로드 중 게임이 종료된 기록은 `.debug-journal.md`의 16:24 건이다.
- 행동 분포(1,096회): 전체 거부율은 18%다. 스토리↔라인 왕복이 57%이고, 소환은 3.8%뿐이다.

  | 행동 | 횟수 | 그중 거부 |
  |---|---:|---:|
  | 대기 | 339 | 1 |
  | 스토리 보내기 | 321 | 89 |
  | 라인 회수 | 306 | 104 |
  | 소환 | 42 | 0 |
  | 마나 | 34 | 0 |
  | 목재 | 29 | 0 |
  | 조합 | 14 | 4 |
  | 진화 | 11 | 0 |

### 속도 추정 (파일 시각으로 계산한 추정)

- 73분에 1,096전이를 모았으니 전이당 약 4초, 에피소드당 약 6.6분이다.
- 라운드(35초)당 결정이 약 30회라 결정 하나는 약 1.2 게임초다. 그러면 93스텝은 약 2분이고, 에피소드마다 약 4.5분이 재실행·로딩·인트로 비용이라는 계산이 나온다.
- 로그에 타임스탬프가 없어서 정확히 나누지는 못했다.
- 쉬움 클리어(40라운드 종료) 한 판은 약 1,200결정, 게임 시간 약 25분이다.
- 결론: 실게임만으로는 시간당 약 900전이이고 100만 전이에 약 1,100시간이 걸린다. 처음부터 순수 RL로 배우게 하는 것은 비현실적이다.

### OrandSimulator (폴더 밖 `D:/OrandOverlay/orand-simulator`, 확인)

- **빌드**: C# .NET 10 WPF, `win-x86`/x86 WinExe다. 맵 **2.314[R]** 해시에 고정돼 있다(ORD Picker는 2.323[R]).
- **API**: Gym 형태와 거의 같다.
  - `RunState`는 불변이고 RNG 상태를 값으로 포함해서, 시드와 행동 순서만으로 재현된다.
  - `OrandGameEngine.NewGame(seed, difficulty)`, `Apply(state, action) → ActionOutcome(Success, Reason, State)`, `ValidActions(state)`가 있다.
  - `--engine-run` 헤드리스 실행, 행동 900개 한도의 `BatchRunner`, 규칙 정책 2개(즉시소비·희귀보존)가 있다.
- **충실도**:
  - 엔진 규칙은 맵 근거 20 / 수동 주입 5 / 단순화 8이다. `runtime-simulation=partial`로 표시된다.
  - seed42 로그 기준 능력 규칙은 modeled 401, partial 146, unmodeled 1,097이다.
  - 스킬 피해는 클리어 샘플로 역산한 ×4.5 보정이다. 이 보정으로 악몽 24시드를 돌리면 30라운드 도달 100%, 40라운드 도달 79%, 평균 47.9라운드다.
- **시간 해상도 차이**: 전투는 라운드 단위 DPS/처치시간 판정이다. 실게임 브리지는 1초 단위로 라인 압력이 변한다. 행동 어휘도 다르다(`CombineAction`, `DeployUnitAction`, `ResolveRoundAction`, 수동 `Grant*` 등).
- **참고 데이터**: `RandyPick/Data`에 tmo.gg 클리어 샘플 40,026판(최종 편성 10기 + 난이도)과 오버레이 기록 2,179건이 있다. 둘 다 행동 궤적이 아니라 **최종 편성** 데이터다.

---

## 2. 주제별 레퍼런스

### 2-1. 느리고 긴 게임에서 샘플 효율 (오프라인 RL·모방학습·모델 기반·보상 설계)

- [Levine et al. 2020 — Offline RL 튜토리얼](https://arxiv.org/abs/2005.01643)
  - 무엇: 이미 모은 데이터만으로 정책을 배우는 문제와 어려움을 정리한 개관이다.
  - 적용: 전이 기록을 완전한 (s, mask, a, r, s′, done) 데이터셋으로 바꾸는 설계 근거로 쓴다.
  - 우선: **지금 당장**
- [DAgger — Ross et al.](https://arxiv.org/abs/1011.0686)
  - 무엇: 학생이 방문한 상태에 전문가가 행동 라벨을 다는 반복 모방학습이다.
  - 적용: `teacher.py`는 GameState만 받는 결정적 함수다. 학생 정책이 모은 상태를 추가 게임 없이 재라벨할 수 있다(원시 상태를 저장해야 함).
  - 우선: **실게임 연결 후**
- [DQfD](https://arxiv.org/abs/1704.03732)
  - 무엇: 소량 시연에 TD 학습, 지도 분류 손실, 우선 재생을 결합한다.
  - 적용: 교사 시연을 버퍼에 영구 보관하고 RL 손실과 섞는다.
  - 우선: **실게임 연결 후**
- [Kickstarting](https://arxiv.org/abs/1803.03835)
  - 무엇: 교사 정책 증류 손실을 가중치를 자동 조절하며 RL에 더한다. 학생이 교사를 넘어설 수 있다.
  - 적용: A2C/PPO 손실에 λ·교차엔트로피(교사 행동)를 넣고 λ를 점점 줄인다.
  - 우선: **실게임 연결 후**
- [JSRL — Jump-Start RL](https://arxiv.org/abs/2204.02372)
  - 무엇: 가이드 정책이 앞부분을 플레이하고 탐색 정책이 뒤를 맡는다. 전환 지점을 점점 앞당기는 커리큘럼이다.
  - 적용: 교사로 N라운드까지 진행한 뒤 학습 정책에 넘긴다. "3~4라운드 조기 사망" 루프를 벗어나 뒤쪽 보상(보스·스토리)을 처음부터 경험하게 한다.
  - 우선: **실게임 연결 후 (효과 큼)**
- [AWAC](https://arxiv.org/abs/2006.09359)
  - 무엇: 시연·준최적 사전 데이터로 오프라인 학습한 뒤 빠르게 온라인 미세조정한다.
  - 적용: BC 뒤 RL로 넘어갈 때 정책이 무너지는 것을 막는다.
  - 우선: **실게임 연결 후**
- [RLPD](https://arxiv.org/abs/2302.02948)
  - 무엇: 기존 off-policy 알고리즘에 오프라인 데이터를 최소 변경으로 섞는다.
  - 적용: 교사·과거 전이와 새 전이를 섞어 샘플링한다.
  - 우선: **나중** (off-policy로 바꿀 때)
- [CQL](https://arxiv.org/abs/2006.04779)
  - 무엇: 데이터 밖 행동의 Q값을 보수적으로 낮춰 과대평가를 막는다.
  - 적용: 쌓인 실게임 전이로 이산 CQL 오프라인 학습을 한다(d3rlpy 이산 지원 확인).
  - 우선: **실게임 연결 후** (수천~수만 전이 이후)
- [IQL](https://arxiv.org/abs/2110.06169)
  - 무엇: 데이터에 없는 행동을 평가하지 않는 오프라인 RL이다.
  - 적용: 행동 244개 대부분이 한 번도 관측되지 않은 상황에 개념적으로 맞는다. d3rlpy는 IQL을 연속 행동만 지원하므로(확인) 직접 구현해야 한다.
  - 우선: **나중**
- [AlphaStar Unplugged](https://arxiv.org/abs/2308.03526)
  - 무엇: SC2 사람 데이터만으로 BC, 오프라인 actor-critic, MuZero 기준선을 만들었다. 이전 BC 에이전트 상대로 90% 승률이다.
  - 적용: "BC 먼저, 오프라인 개선 다음" 순서의 근거다.
  - 우선: **나중** (리플레이 데이터 확보 시)
- [Decision Transformer](https://arxiv.org/abs/2106.01345)
  - 무엇: 목표 리턴을 조건으로 행동을 시퀀스 모델링한다.
  - 적용: 지금 데이터 규모에서는 과하다. 기록이 크게 쌓이면 후보가 된다.
  - 우선: **나중**
- [MBPO](https://arxiv.org/abs/1906.08253)
  - 무엇: 실제 데이터에서 갈라진 짧은 모델 롤아웃으로 샘플 효율을 높인다.
  - 적용: 1초 전이(골드·적 수·라운드) 예측 모델을 만들어 짧은 가상 롤아웃을 돌린다.
  - 우선: **나중**
- [MuZero](https://arxiv.org/abs/1911.08265) / [EfficientZero](https://arxiv.org/abs/2111.00210)
  - 무엇: 학습된 모델과 탐색을 결합한다. EfficientZero는 Atari 100k(실시간 약 2시간 분량)에서 사람 수준을 넘었다.
  - 적용: 적은 실경험으로 어디까지 가능한지 보여 주는 상한선이다. 구현 부담이 크다.
  - 우선: **나중**
- [DreamerV3](https://arxiv.org/abs/2301.04104) / [DreamerV2](https://arxiv.org/abs/2010.02193)
  - 무엇: 세계 모델 안의 상상 롤아웃으로 학습한다.
  - 적용: 모델 기반으로 갈 때의 기준 구현이다.
  - 우선: **나중**
- [Expert Iteration](https://arxiv.org/abs/1705.08439)
  - 무엇: 트리 탐색이 만든 행동을 신경망이 일반화하고, 그 신경망이 다시 탐색을 돕는다.
  - 적용: OrandSimulator에서 조합·스토리 순서를 탐색하고 결과를 정책에 증류한다.
  - 우선: **나중**
- [Ng·Harada·Russell 1999 — 잠재 함수 보상 설계](https://mlanthology.org/icml/1999/ng1999icml-policy/)
  - 무엇: γΦ(s′)−Φ(s) 꼴의 보상을 더해도 최적 정책은 바뀌지 않는다.
  - 적용: 현재 압력 항 0.2×(전−후)는 이미 이 꼴이다. 유닛 전력(추정 DPS)이나 메타 편성 유사도(클리어 샘플 40,026판)를 Φ 후보로 삼는다.
  - 우선: **지금 당장**
- [RUDDER](https://arxiv.org/abs/1806.07857)
  - 무엇: 지연 보상을 기여도 분석으로 앞쪽 행동에 재분배한다.
  - 적용: 패배 원인이 몇 라운드 전 결정일 때 쓸 수 있다.
  - 우선: **나중**
- [PopArt](https://arxiv.org/abs/1602.07714)
  - 무엇: 가치 목표의 크기를 적응적으로 정규화한다.
  - 적용: −50 종료 보상 때문에 생긴 value_loss 약 2,000 급등 같은 스케일 문제를 다룬다. v2로 줄었지만 승리·스토리 보상이 커지면 다시 생길 수 있다. 우선 리턴 정규화부터 한다.
  - 우선: **지금 당장**
- [GAE](https://arxiv.org/abs/1506.02438)
  - 무엇: 지수 가중 어드밴티지로 분산을 줄인다.
  - 적용: 현재 n-step 리턴을 λ=0.95 GAE로 바꾼다.
  - 우선: **지금 당장**
- [PPO](https://arxiv.org/abs/1707.06347)
  - 무엇: 같은 배치로 여러 에폭 미니배치 업데이트를 한다.
  - 적용: A2C의 "한 번 쓰고 버림"을 같은 데이터 4~10회 재사용으로 바꾼다.
  - 우선: **지금 당장**
- [What Matters in On-Policy RL](https://arxiv.org/abs/2006.05990) / [PPO 구현 디테일 37가지](https://iclr-blog-track.github.io/2022/03/25/ppo-implementation-details/)
  - 무엇: 구현 선택이 성능을 크게 좌우한다는 대규모 실험과 체크리스트다.
  - 적용: 관측 정규화, 직교 초기화, 가치 클리핑, 학습률 감소 등을 점검한다.
  - 우선: **지금 당장**

### 2-2. RTS·디펜스 게임 AI 사례에서 가져올 구조

- [AlphaStar (Nature 2019)](https://www.nature.com/articles/s41586-019-1724-z)
  - 무엇: 사람 리플레이 지도학습에서 RL로 넘어가고 리그 학습을 한다. 보충자료에 SL/RL 의사코드가 있다.
  - 적용: "지도학습으로 시작" 구조를 따르고, 행동 종류를 먼저 고른 뒤 대상을 고르는 순차 출력을 참고한다.
  - 우선: **실게임 연결 후** (BC 단계)
- [OpenAI Five](https://arxiv.org/abs/1912.06680)
  - 무엇: 4프레임마다 행동(한 판 약 20,000스텝)하며 10개월 연속 학습했다. 구조가 바뀔 때 가중치를 이어받는 "surgery"를 약 2주마다 했다.
  - 적용: 지금 행동 사전이 244에서 245로 바뀌었다. 체크포인트를 거부하는 대신 새 행동 행만 추가해 이어받는다.
  - 우선: **지금 당장**
- [SC2LE 논문](https://arxiv.org/abs/1708.04782) + [PySC2](https://github.com/google-deepmind/pysc2)
  - 무엇: 매 스텝 사용 가능한 행동 목록을 준다. 행동은 함수 ID와 인자로 나뉜다.
  - 적용: "조합 ID 하나" 대신 (행동 종류 → 대상 조합/유닛) 두 단계로 출력한다.
  - 우선: **실게임 연결 후**
- [SMAC 논문](https://arxiv.org/abs/1902.04043) + [저장소](https://github.com/oxwhirl/smac)
  - 무엇: 에이전트별 `get_avail_agent_actions`를 주는 SC2 마이크로 환경이다.
  - 적용: 마스크를 관측과 함께 돌려주는 환경 API 설계를 참고한다. 멀티에이전트 자체는 해당 없다.
  - 우선: **지금 당장** (인터페이스)
- [TStarBots](https://arxiv.org/abs/1809.07193)
  - 무엇: 사전 정의한 매크로 행동 위에서 RL을 하는 TStarBot1과, 규칙 기반 계층 구조의 TStarBot2가 SC2 내장 AI 1~10단계를 이겼다.
  - 적용: 원랜디 행동을 "전부 소환", "가능한 최선 조합", "스토리 보내기/회수" 같은 매크로로 묶는다.
  - 우선: **지금 당장**
- [Gym-μRTS 논문](https://arxiv.org/abs/2105.13807) + [MicroRTS-Py](https://github.com/Farama-Foundation/MicroRTS-Py)
  - 무엇: 무효 행동 마스킹과 PPO로 GPU 1장·약 60시간 만에 강한 RTS 에이전트를 만들었다. 저장소는 2025-08에 사용 중단됐다.
  - 적용: 단일 PC 예산에서 마스킹 PPO를 설계할 때 코드를 참고한다.
  - 우선: **지금 당장**
- [Tencent Solo (Honor of Kings)](https://arxiv.org/abs/1912.09729)
  - 무엇: 제어 의존성 분리, 행동 마스크, 타깃 어텐션, dual-clip PPO를 쓴다.
  - 적용: 행동 머리를 나누고 dual-clip으로 안정화한다.
  - 우선: **나중**
- [타워디펜스 고수준 전략 RL (CoG 2024)](https://arxiv.org/abs/2406.07980)
  - 무엇: RL과 스크립트 AI를 결합한 쪽이 휴리스틱 단독보다 성공률이 높았다(40레벨에서 57.12% vs 47.95%).
  - 적용: 원랜디도 "RL은 전략, 규칙은 실행" 혼합이 현실적이라는 근거다.
  - 우선: **지금 당장** (설계 방향)
- [TowerDefenseRL (Jacky8703)](https://github.com/Jacky8703/TowerDefenseRL)
  - 무엇: 자체 TD 게임을 Gymnasium 환경으로 감싸 SB3 MaskablePPO로 학습한 예제다.
  - 적용: 작은 규모 구성 참고용이다.
  - 우선: **나중**
- [TowerMind](https://arxiv.org/abs/2601.05899)
  - 무엇: LLM 에이전트용 타워디펜스 벤치마크다(텍스트·구조 상태).
  - 적용: 직접 효용은 낮다.
  - 우선: **나중**
- [Warsmash](https://github.com/Retera/WarsmashModEngine)
  - 무엇: LibGDX 기반 Warcraft III 에뮬레이터다. 원본 에셋이 필요하고, 1.22~1.29 데이터로 테스트됐으며 jassparser 모듈을 포함한다.
  - 적용: 이론상 헤드리스·다중 실행 대안이다. ORDR 2.323(Reforged) 맵이 실행되는지는 미확인이다.
  - 우선: **나중** (탐색만)
- [AMAI](https://github.com/SMUnlimited/AMAI)
  - 무엇: JASS로 짠 규칙 기반 근접전 AI로, Reforged 3.0.0을 지원한다.
  - 적용: 맵 안 규칙 AI 구조 참고 정도다.
  - 우선: **나중**
- [w3gjs](https://github.com/PBug90/w3gjs)
  - 무엇: TypeScript .w3g 리플레이 파서로, 저수준 이벤트(`gamedatablock`)를 제공한다.
  - 적용: 사람의 원랜디 리플레이에서 행동 궤적을 뽑아 BC 데이터로 쓴다. 채팅 조합, 선택, 이동 명령을 우리 행동 사전에 매핑하는 작업이 필요하다.
  - 우선: **나중** (잠재 효과 큼)
- [w3champions](https://github.com/w3champions)
  - 무엇: 래더 플랫폼(백엔드·런처·flo hostbot)이다. AI 레퍼런스가 아니다.
  - 적용: 없다.
  - 우선: 해당 없음
- [lep/jassdoc](https://github.com/lep/jassdoc)
  - 무엇: JASS 네이티브 문서 DB다.
  - 적용: 브리지가 쓰는 네이티브(Preloader, SetGameSpeed, BlzSendSyncData)의 동작을 확인한다.
  - 우선: **지금 당장**

### 2-3. 여러 게임 동시 실행·배속·헤드리스, 시뮬레이터 → 실게임 (sim-to-real)

확인한 제약: 지금은 게임 1개, 명령 1개, 동기식이고 에피소드마다 Warcraft를 재실행한다. 배속은 다른 작업(st_01a0e256)이 맵 틱에 `SetGameSpeed(MAP_SPEED_FASTEST)`를 넣었고 19:35에 다시 빌드됐다. 실제 효과는 아직 측정되지 않았다.

- [Hive: SetGameSpeed가 battle.net에서 되나](https://www.hiveworkshop.com/threads/does-setgamespeed-work-for-battle-net-games.206738/)
  - 무엇: LAN에서는 속도 변경이 됐다는 사용자 보고가 1건 있다.
  - 적용: Reforged 오프라인에서의 실제 배율은 미확인이다. `GetGameSpeed`와 라운드 실제 초를 재서 확인한다.
  - 우선: **실게임 연결 후**
- [Hive: 게임 속도 토론](https://www.hiveworkshop.com/threads/at-what-game-speed-do-you-play.240739/)
  - 무엇: 기본값이 fast라는 커뮤니티 설명이다.
  - 적용: 배속만으로 얻는 이득은 수 배 이하일 것으로 추정한다. 그래서 병렬화나 시뮬레이터가 필요하다.
  - 우선: 참고
- [Hive: 한 PC에서 LAN 다중 실행](https://www.hiveworkshop.com/threads/lan-hosting-multiple-players-single-pc.308171/)
  - 무엇: ForceBindIP로 127.0.0.x마다 Warcraft 인스턴스를 띄우는 옛 패치용 방법이다.
  - 적용: Reforged·Battle.net 경유 실행에서 여러 개가 뜨는지는 미확인이다. 된다면 인스턴스별 CustomMapData 폴더와 에피소드 번호 공간을 나눠야 한다(지금은 `ORD_AI` 폴더 하나).
  - 우선: **나중**
- [WC3 Replay Speed Unlocker](https://github.com/UnryzeC/WC3_ReplaySpeedUnlocker)
  - 무엇: 리플레이 8배속 제한을 푼다. 1.24e~1.28f 전용이다.
  - 적용: Reforged에는 해당 없다. 리플레이는 상호작용이 안 돼서 학습 환경도 아니다.
  - 우선: 해당 없음
- [Gymnasium Vector API](https://gymnasium.farama.org/api/vector/)
  - 무엇: AsyncVectorEnv와 자동 리셋을 제공한다.
  - 적용: OrandSimulator 여러 개를 벡터 환경으로 묶는다.
  - 우선: **나중**
- [pythonnet](https://github.com/pythonnet/pythonnet)
  - 무엇: Python에서 .NET CLR을 직접 호출한다.
  - 적용: 시뮬레이터 엔진을 같은 프로세스에서 부르면 스텝 비용이 파일 IPC보다 훨씬 작다. 다만 시뮬레이터는 x86 WinExe/WPF이고 CUDA PyTorch 파이썬은 64비트라 그대로는 로드할 수 없다(추정). Core를 AnyCPU 클래스 라이브러리로 분리하거나 표준입출력 JSON 프로세스로 감싸야 한다.
  - 우선: **나중**
- [Domain Randomization](https://arxiv.org/abs/1703.06907)
  - 무엇: 시뮬레이터 파라미터를 무작위로 흔들어 실제를 변형의 하나처럼 보이게 한다.
  - 적용: 스킬 배율 ×4.5, 보스 체력, DPS를 범위 안에서 무작위화해 불확실한 규칙에 강하게 만든다.
  - 우선: **나중**
- [Sim-to-Real 서베이](https://arxiv.org/abs/2009.13303)
  - 무엇: 도메인 랜덤화, 도메인 적응, 모방, 메타러닝, 증류를 정리한 개관이다.
  - 우선: **나중**
- [H2O](https://arxiv.org/abs/2206.13464)
  - 무엇: 불완전한 시뮬레이터와 고정된 실데이터를 함께 쓰고, 동역학 차이가 큰 시뮬 샘플의 Q에 벌점을 준다.
  - 적용: OrandSimulator(2.314, partial)와 실게임 기록을 섞는 우리 설정에 가장 가깝다.
  - 우선: **나중**
- [DARC (Off-Dynamics RL)](https://arxiv.org/abs/2006.13916)
  - 무엇: 시뮬 전이와 실제 전이를 구분하는 분류기로 보상을 보정한다.
  - 적용: 위와 같은 설정이다.
  - 우선: **나중**
- [Reverse Curriculum](https://arxiv.org/abs/1707.05300)
  - 무엇: 목표 근처 시작 상태부터 점점 멀리 가는 커리큘럼이다.
  - 적용: 시뮬레이터에서는 중반 라운드 상태에서 시작할 수 있어 후반 경험을 싸게 얻는다. 실게임은 시작 상태를 고를 수 없으니 JSRL로 대신한다.
  - 우선: **나중**

### 2-4. 행동 공간이 크고 대부분 무효일 때 (마스킹·계층형·매크로)

- [Huang & Ontañón — Invalid Action Masking](https://arxiv.org/abs/2006.14171)
  - 무엇: 마스킹이 타당한 정책 경사임을 보이고, 무효 행동이 많을수록 중요하다는 것을 실험했다. 마스크를 떼는 실험도 있다.
  - 적용: 현재 방식(logit −1e9)은 타당하다. 핵심은 마스크의 정확도다.
  - 우선: **지금 당장**
- [AE-DQN (Action Elimination)](https://arxiv.org/abs/1809.02121)
  - 무엇: 환경이 주는 "무효" 신호로 무효 행동 예측망을 따로 학습해 행동을 제거한다. 행동 1,000개 이상인 텍스트 게임에서 실험했다.
  - 적용: `action_ok=0`이 바로 그 신호다. (상태, 행동) → 거부 확률 모델을 학습해 UPUN·KING 같은 미검사 조건을 보완한다.
  - 우선: **실게임 연결 후** (기록에 상태·마스크가 있어야 함)
- [Growing Action Spaces](https://arxiv.org/abs/1906.12266)
  - 무엇: 작은 행동 공간부터 점점 키우는 커리큘럼이다(SC2 마이크로 실험).
  - 적용: 처음엔 기본 8개와 교사가 실제로 쓰는 조합 몇 개로 시작해 점차 237개로 연다.
  - 우선: **지금 당장**
- 매크로와 함수+인자 구조는 2-2의 [TStarBots](https://arxiv.org/abs/1809.07193)와 [SC2LE](https://arxiv.org/abs/1708.04782)를 참고한다.
- [Dulac-Arnold — 대규모 이산 행동](https://arxiv.org/abs/1512.07679) / [Chandak — 행동 표현 학습](https://arxiv.org/abs/1902.00183)
  - 무엇: 행동을 임베딩해서 행동끼리 일반화한다.
  - 적용: 조합을 (재료, 비용, 결과 등급) 특징 벡터로 표현하면 한 번도 안 해 본 조합의 가치도 추정할 수 있다.
  - 우선: **나중**
- [Option-Critic](https://arxiv.org/abs/1609.05140) / [FeUdal Networks](https://arxiv.org/abs/1703.01161)
  - 무엇: 계층형 RL(옵션, 관리자/작업자 구조)이다.
  - 적용: 상위 정책이 "전략 모드"(소환/조합/스토리 러시/방어)를 고르고, 하위는 규칙이나 작은 정책이 맡는다.
  - 우선: **나중**
- [FiGAR (Fine Grained Action Repetition)](https://arxiv.org/abs/1702.06054)
  - 무엇: 행동과 그 행동을 반복할 시간을 함께 고른다.
  - 적용: 결정의 31%가 대기다. "대기 k초" 행동을 두거나, 상태가 바뀌는 이벤트(위습 획득, 조합 가능, 압력 임계)에서만 결정하게 한다. 그러면 한 판 결정 수(약 1,200)와 신용 할당 거리가 줄어든다.
  - 우선: **지금 당장**
- [Net2Net](https://arxiv.org/abs/1511.05641)
  - 무엇: 신경망을 함수가 보존되게 넓히거나 깊게 만든다.
  - 적용: 관측·행동 사전이 커질 때 기존 가중치를 살려 확장한다(OpenAI Five surgery와 같은 목적).
  - 우선: **지금 당장**

### 2-5. 파일 기반 느린 IPC에서 비동기 수집·재생 버퍼

현재 매 스텝을 동기로 기다리고, 병목은 게임 시간(1 게임초 관측 지연 + 0.25초 폴링)과 리셋이다. MLP 추론 1회는 무시할 만하다. 그래서 비동기로 얻는 이득은 스텝 속도가 아니라 **수집을 멈추지 않는 것**과 **모은 데이터를 여러 번 쓰는 것**이다.

- [Real-Time RL](https://arxiv.org/abs/1911.04448) / [Thinking While Moving](https://arxiv.org/abs/2004.06089)
  - 무엇: 행동을 고르는 동안에도 상태가 변하는 설정과, 지연을 고려한 벨만식이다.
  - 적용: 1초 관측 지연 동안 게임이 계속 진행되는 원랜디에 해당한다. 관측에 "직전 행동, 수락 여부, 경과 게임시간"을 넣는 정도가 실용적이다.
  - 우선: **지금 당장**
- [IMPALA](https://arxiv.org/abs/1802.01561)
  - 무엇: 행동자와 학습자를 분리하고, V-trace로 약간 지난 정책 데이터를 보정한다.
  - 적용: 학습 스레드를 수집에서 떼어낼 때와, 여러 인스턴스·시뮬레이터를 병렬로 돌릴 때의 기본 구조다.
  - 우선: **나중**
- [Ape-X](https://arxiv.org/abs/1803.00933)
  - 무엇: 분산 행동자와 공유 우선 재생 버퍼 구조다.
  - 적용: 실게임과 시뮬레이터 행동자가 한 버퍼에 쓰는 구조로 쓴다.
  - 우선: **나중**
- [SEED RL](https://arxiv.org/abs/1910.06591)
  - 무엇: 추론을 중앙 가속기에 모은다.
  - 적용: 행동자가 1개라 효용이 작다.
  - 우선: **나중**
- [Prioritized Experience Replay](https://arxiv.org/abs/1511.05952)
  - 무엇: 중요한 전이를 더 자주 재생한다.
  - 적용: 드문 사건(보스 처치, 스토리 진행, 패배 직전) 전이를 우선한다.
  - 우선: **실게임 연결 후**
- [Sample Factory 논문](https://arxiv.org/abs/2006.11751)
  - 무엇: 단일 머신 비동기 샘플러와 off-policy 보정으로 초당 10^5 프레임을 낸다.
  - 적용: 설계 개념만 참고한다. 라이브러리는 Windows를 지원하지 않는다.
  - 우선: **나중**
- [RLlib External Environments](https://docs.ray.io/en/latest/rllib/external-envs.html)
  - 무엇: 게임 엔진처럼 자체 루프를 가진 외부 시뮬레이터가 학습 서버에 접속하고, 클라이언트 쪽에서 추론한다.
  - 적용: 게임이 루프를 주도하는 우리 구조와 같은 패턴이다.
  - 우선: **나중** (개념 참고)
- [TorchRL](https://github.com/pytorch/rl)
  - 무엇: `LazyMemmapStorage`, 우선 재생 등 디스크 기반 버퍼를 제공한다.
  - 적용: 전이를 memmap 버퍼로 영구 저장해 재시작 뒤에도 다시 쓴다.
  - 우선: **지금 당장**
- [Minari](https://minari.farama.org/)
  - 무엇: 오프라인 RL 데이터셋 포맷과 API(Gymnasium 기반)다.
  - 적용: 기록 스키마(관측·행동·보상·종료·info)를 참고한다.
  - 우선: **지금 당장** (스키마만)

제안하는 기록·버퍼 설계 (추정, 코드에는 적용하지 않음):

1. 스텝마다 다음을 한 줄로 저장한다(JSONL 또는 memmap). 원시 상태를 저장해 두면 나중에 특징 설계가 바뀌어도 다시 계산할 수 있다.
   - 원시 GameState 전체(유닛 개수 포함)
   - 마스크, 선택한 행동, 교사 행동
   - 정책 확률(logp), 가치 추정
   - 벽시계 시각과 게임 시각
2. 수집 루프는 파일 교환만 하고, 학습자는 별도 스레드에서 버퍼를 샘플링한다. logp를 저장해 두면 조금 지난 데이터도 중요도 비율(V-trace 개념)로 쓸 수 있다.
3. 에피소드 경계에 종료 종류를 플래그로 저장한다. terminated(승패)와 truncated(리셋 실패·타임아웃)를 구분해야 가치 부트스트랩이 맞다.

### 2-6. 작은 GPU·단일 PC용 라이브러리 비교 (Windows, RTX 3080 Ti 12GB, 환경은 느린 외부 게임)

| 라이브러리 | 확인한 사실 | 우리에게 맞는가 | 판정 |
|---|---|---|---|
| [CleanRL](https://github.com/vwxyzjn/cleanrl) ([논문](https://arxiv.org/abs/2111.08819), [PPO 문서](https://docs.cleanrl.dev/rl-algorithms/ppo/)) | 알고리즘당 단일 파일, PPO 변형·EnvPool 변형 | A2C를 PPO+GAE로 바꿀 때 그대로 옮겨 쓸 기준 코드 | **지금 당장** (참고 구현) |
| [Stable-Baselines3](https://stable-baselines3.readthedocs.io/en/master/) ([설치](https://stable-baselines3.readthedocs.io/en/master/guide/install.html)) + [MaskablePPO](https://sb3-contrib.readthedocs.io/en/master/modules/ppo_mask.html) | PyTorch 기반, Python 3.10+·PyTorch ≥2.8(우리 2.11 충족), Windows는 miniforge 권장. MaskablePPO는 Discrete·MultiDiscrete 마스크, env에 `action_masks()` 구현 | 브리지를 Gymnasium Env로 감싸면 바로 마스킹 PPO 기준선. gymnasium·sb3 의존성 추가 필요 | **실게임 연결 후** (비교 기준선) |
| [imitation](https://github.com/HumanCompatibleAI/imitation) ([BC 문서](https://imitation.readthedocs.io/en/latest/algorithms/bc.html)) | BC·DAgger·GAIL·AIRL, 이산 지원 | `Learner.imitate`가 이미 있어 필수 아님. DAgger 루프 참고 | 선택 |
| [d3rlpy](https://github.com/takuseno/d3rlpy) | 이산: BC·NFQ·DQN·Double DQN·SAC·BCQ·CQL·Decision Transformer. IQL은 연속만 | 쌓인 실게임 전이로 오프라인 이산 RL 실험. 행동 마스크 처리 방식은 미확인 | **실게임 연결 후** |
| [Tianshou](https://github.com/thu-ml/tianshou) | v2, 이산 CQL·BCQ·CRR, 모방학습 포함 | d3rlpy 대안 | 선택 |
| [TorchRL](https://github.com/pytorch/rl) | 재생 버퍼(memmap·우선순위)·수집기·목표 함수 모듈 | 버퍼만 가져다 쓰기 좋음 | **지금 당장** 후보 |
| [CORL](https://github.com/corl-team/CORL) | 오프라인 RL 단일 파일(IQL·CQL·AWAC, offline→online) | 알고리즘 읽기용. 주로 연속 제어 벤치마크(추정) | 참고 |
| [LeanRL](https://github.com/pytorch-labs/LeanRL) | CleanRL을 torch.compile·cudagraphs로 가속 | 병목이 게임이라 효과 없음 | 불필요 |
| [RLlib](https://docs.ray.io/en/latest/rllib/external-envs.html) ([설치](https://docs.ray.io/en/latest/ray-overview/installation.html), [오프라인](https://docs.ray.io/en/latest/rllib/rllib-offline.html)) | Windows는 beta. 외부 환경, 오프라인(BC·MARWIL) 지원 | 개념은 맞지만 무겁고 Windows beta | 보류 |
| [Sample Factory](https://www.samplefactory.dev/01-get-started/installation/) | 공식 문서상 "There is no Windows support" | 사용 불가 | 제외 |
| [EnvPool](https://github.com/sail-sg/envpool) | Windows 휠(py3.12~3.14) 있음. 새 환경은 C++로 구현 | Warcraft 파일 브리지엔 이득 없음 | 제외 |
| [PufferLib](https://github.com/PufferAI/PufferLib) ([문서](https://puffer.ai/docs.html)) | 5.0은 CUDA C 네이티브 백엔드, 환경은 C, bash 설치 스크립트/도커 | Windows 네이티브 언급 없음(추정: WSL/리눅스). 시뮬레이터를 C로 다시 쓰지 않는 한 무의미 | 제외 |

결론: 지금의 작고 타입이 엄격한 자체 PyTorch 루프를 유지한다. CleanRL의 PPO/GAE 세부를 옮기고, 버퍼만 TorchRL이나 numpy memmap으로 붙인다. 오프라인 실험은 d3rlpy나 Tianshou로 별도 스크립트를 만든다. SB3 MaskablePPO는 실게임 연결 뒤 "자체 구현이 맞는지" 비교하는 기준선으로 쓴다.

---

## 3. 지금 구조에서 가장 효과 큰 3가지

### ① 교사로 시작하기: BC 사전학습 → JSRL/Kickstarting 미세조정 → DAgger 재라벨

- **근거**: 무작위 초기 정책은 11판 모두 3~4라운드에서 전멸했고, 1,096전이 중 소환은 42회뿐이다. `teacher.py`와 `Learner.imitate`는 이미 있지만 `training.py`가 부르지 않는다.
- **방법**:
  1. 교사가 플레이하면서 (상태, 마스크, 교사 행동, 이후 리턴)을 수집한다.
  2. `imitate`로 정책과 가치를 사전학습한다.
  3. 교사가 플레이하는 구간을 점점 줄이며 RL로 넘긴다(JSRL). 이때 교사 교차엔트로피 항을 λ를 줄여 가며 유지한다(Kickstarting).
  4. 학생이 방문한 상태는 교사로 재라벨한다(DAgger).
- **기대 효과 (추정)**: 초반 전멸 구간을 건너뛰어 라운드·보스·스토리 보상을 처음부터 경험한다. 같은 수집 시간에 얻는 학습 신호가 크게 늘어난다.
- **레퍼런스**: [JSRL](https://arxiv.org/abs/2204.02372), [Kickstarting](https://arxiv.org/abs/1803.03835), [DAgger](https://arxiv.org/abs/1011.0686), [DQfD](https://arxiv.org/abs/1704.03732), [AWAC](https://arxiv.org/abs/2006.09359), [AlphaStar](https://www.nature.com/articles/s41586-019-1724-z)

### ② 전이를 재사용 가능한 데이터로 남기고, 재사용하는 학습으로 바꾸기

- **근거**: 시간당 약 900전이밖에 못 모으는데 A2C는 한 번 쓰고 버린다. 기록에는 상태와 마스크가 없어 다시 쓸 수도 없다. 행동 사전이 244에서 245로 바뀌어 기존 체크포인트도 버려질 상황이다.
- **방법**:
  1. 원시 GameState, 마스크, 행동, logp, 교사 행동, 시각을 저장한다(2-5의 설계).
  2. A2C를 PPO(GAE, 리턴 정규화, 여러 에폭)로 바꿔 같은 데이터를 여러 번 쓴다.
  3. 데이터가 쌓이면 이산 CQL 오프라인 학습과 우선 재생을 붙인다.
  4. 행동·관측이 추가될 때 가중치를 이어받는다(surgery/Net2Net).
  5. 로그에 벽시계 시각을 넣어 리셋 비용과 스텝 비용을 실제로 나눠 잰다.
- **레퍼런스**: [Offline RL 튜토리얼](https://arxiv.org/abs/2005.01643), [PPO](https://arxiv.org/abs/1707.06347), [GAE](https://arxiv.org/abs/1506.02438), [PPO 구현 디테일](https://iclr-blog-track.github.io/2022/03/25/ppo-implementation-details/), [PopArt](https://arxiv.org/abs/1602.07714), [CQL](https://arxiv.org/abs/2006.04779), [PER](https://arxiv.org/abs/1511.05952), [TorchRL](https://github.com/pytorch/rl), [OpenAI Five](https://arxiv.org/abs/1912.06680), [Net2Net](https://arxiv.org/abs/1511.05641)

### ③ 결정 수와 행동 공간 줄이기: 매크로 행동 + 이벤트 시점 결정 + 학습된 거부 마스크

- **근거**:
  - 결정의 31%가 대기이고, 57%가 스토리↔라인 왕복이다(거부 193회).
  - 조합 237개 중 약 232개는 마스크가 보지 않는 조건을 가진다.
  - 쉬움 한 판이 약 1,200결정이라 γ=0.99로는 몇 라운드 앞 결과를 신용 할당하기 어렵다.
- **방법**:
  1. 매크로 행동을 만든다: 전부 소환(진행 중), 가능한 최선 조합, 스토리 보내기/회수 규칙.
  2. "대기 k초" 행동을 두거나 이벤트가 생길 때만 결정하게 한다.
  3. `action_ok`로 거부 예측 모델을 학습해 마스크를 보강한다.
  4. 행동 공간을 작게 시작해 점점 넓힌다.
  5. 전략은 RL이, 실행은 규칙이 맡는 혼합 구조로 간다.
- **레퍼런스**: [TStarBots](https://arxiv.org/abs/1809.07193), [FiGAR](https://arxiv.org/abs/1702.06054), [AE-DQN](https://arxiv.org/abs/1809.02121), [Growing Action Spaces](https://arxiv.org/abs/1906.12266), [Invalid Action Masking](https://arxiv.org/abs/2006.14171), [TD 하이브리드 (CoG 2024)](https://arxiv.org/abs/2406.07980)

이 순서를 추천하는 이유:

- ①과 ③은 게임 없이 코드만으로 준비할 수 있고, ②는 ①의 수집과 함께 들어간다.
- OrandSimulator 위 사전학습(sim-to-real)은 세 가지 차이 때문에 이 셋 다음으로 권장한다.
  - 맵 버전이 다르다(2.314 vs 2.323).
  - 시간 해상도가 다르다(라운드 단위 판정 vs 1초 단위).
  - 능력 규칙 1,097개가 모델링되지 않았다.
- 시뮬레이터는 우선 파이프라인 대량 검증과 조합 순서 탐색(Expert Iteration)에 쓰고, 실게임 정책에는 H2O/DARC처럼 차이를 고려하는 방식으로 섞는다.

---

## 4. 확인한 것 / 추정 / 확인 못 한 것

- **확인한 것**
  - ORD Picker의 README, `.omo` 계약·브리지 노트, `src/ord_ai/*.py`, `jass/bridge_functions.j`, `runs/*`(메타데이터·로그·전이 1,096줄), `build/catalog.json`, CustomMapData/ORD_AI 파일 시각을 직접 읽었다.
  - OrandSimulator의 README, csproj, Engine/RunState/BatchRunner/HeadlessSandboxRunner/Provenance, artifacts 로그와 RandyPick Data 구조를 직접 읽었다.
  - 이 문서의 링크는 모두 이번 세션에서 열어 제목과 초록 또는 README 내용을 확인했다.
- **추정**
  - 에피소드당 리셋 비용 약 4.5분(로그에 시각이 없어 계산으로 추정)
  - 배속으로 얻는 이득의 크기
  - pythonnet x86/x64 문제
  - Reforged에서 다중 인스턴스가 가능한지
  - CORL·PufferLib의 대상 플랫폼, d3rlpy의 마스크 처리
  - 교사·매크로 도입 효과의 크기
- **확인 못 한 것**
  - Reforged에서 `MAP_SPEED_FASTEST`의 실제 배율
  - Warsmash가 ORDR 2.323을 실행할 수 있는지
  - 19:14 이후 리셋이 멈춘 원인(wB:p9 세션 범위라 손대지 않음)
  - R2D2(OpenReview 인증 페이지)와 Blizzard 포럼 다중 클라이언트 글(본문이 비어 있음)은 열어 확인하지 못해 링크에서 뺐다.
- **읽기 전용 경계**
  - ORD Picker와 OrandSimulator 파일은 하나도 수정하지 않았다.
  - 조사 중 바뀐 `observation.py`(19:21), `learning.py`(19:30), `catalog.py`(19:34), `build/`(19:35), `tests/test_catalog_macros.py`는 같은 폴더의 다른 작업(st_01a0e254 보상·모방학습, st_01a0e256 매크로·배속)이 만든 변경이다.
