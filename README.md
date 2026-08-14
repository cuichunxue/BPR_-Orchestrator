# BPR Orchestrator

企業改革（BPR）全体を統括する **Transformation Brain** の基盤実装です。
Orchestrator 自身は統計解析・プロセスマイニング・自動化方式選定などを行いません。
専門エージェント（Process / Data / Root Cause / Automation / ... Agent）へタスクを委譲し、

> Evidence を集める → 矛盾を検出する → Gate を通す → 意思決定する → Pilot する → 効果を学習する

というループを構造的に統制することに専念します。

このリポジトリは **Orchestrator 基盤フェーズ**（設計書 3〜21 章に対応するコアエンジン）です。
製造品質向け `Quality Incident Agent` などの専門エージェント本体は次フェーズで追加します。

## モジュール構成

```
bpr_orchestrator/
  models.py        Problem / Hypothesis / Evidence / Initiative / Decision /
                    KPIRecord / Learning / TransformationCase などの構造化オブジェクト
  contracts.py      AgentTask / AgentResponse / BaseAgent — 全専門エージェントが守る契約
  state_machine.py  DISCOVER→UNDERSTAND→DIAGNOSE→VERIFY→REDESIGN→EVALUATE→
                    PILOT→MEASURE→SCALE→MONITOR（→DIAGNOSE に回帰）
  gates.py          Gate 0(Scope)〜Gate 7(Scale) の判定ロジック
  redesign.py       「廃止→統合→簡素化→並行化→即時化→例外管理→権限委譲→自動化」の順序強制
  value_engine.py   工数削減などの生の資源を実現可能な価値カテゴリへ変換
  opportunity.py    Opportunity = Impact×Evidence×Feasibility×Urgency×StrategicFit
                    （Gate 不合格時は無条件に 0）
  memory.py         CaseMemory(orchestrator.py が保持) / EnterpriseMemory / GeneralPatternLibrary
  orchestrator.py   BPROrchestrator 本体：dispatch・矛盾検出・Next Best Investigation・
                    Gate 判定・Human-in-the-loop 強制・Learning Loop
  prompts.py        Orchestrator のコアプロンプトと Agent 契約プロンプト
  agents/
    base.py          Anthropic API を呼ぶ専門エージェントの基底クラス
    mock_agents.py   API 不要でオーケストレーションを一気通貫で検証できる決定論的スタブ
    bpmn_agent.py    Camunda Modeler対応 BPMN 2.0 XML生成エージェント（CamundaBPMNAgent）と
                      その出力を機械的に検証する validate_bpmn_xml()
examples/run_demo.py DISCOVER〜SCALE までの全フェーズを一気通貫で動かすデモ
tests/               gates / state_machine / redesign / orchestrator / bpmn_agent の単体テスト
```

## 設計原則との対応

- **Agent は必ず契約に従う**（`contracts.AgentResponse.is_well_formed`）
  finding だけを返すことは許されず、evidence または assumptions を伴わない
  bare な主張は Orchestrator が `dispatch()` の時点で拒否します。
- **原因未確認の施策は確定案にならない**
  `Initiative.target_root_cause` が指す Hypothesis が `UNCONFIRMED` のままだと
  `gate_blocked_reasons` が付き、`propose_initiative()` は `CONDITIONAL` 止まりです。
- **自動化はいつも最後**
  `redesign.validate_intervention_order()` が、AUTOMATE（や他の後段介入）を
  提案する前に、それより前段の介入タイプが `considered_alternatives` として
  検討済みであることを要求します（Gate 2 として強制）。
- **Safety / Legal / Quality は点数で相殺されない**
  `opportunity.compute_opportunity_score()` は Gate 不合格時に問答無用でスコア 0 を返します。
- **Human-in-the-loop の境界**
  `models.HUMAN_ONLY_DOMAINS`（法令適合・安全・品質最終判断・出荷判断・人事評価・
  採用解雇・重大投資・経営戦略）に触れる、または不可逆な施策は、
  `Decision.decision_owner` に実在する人間の名前が入るまで `ready_to_scale()` が
  Scale を許可しません。Orchestrator 自身や Agent 名を owner にした
  `record_decision()` はエラーになります。
- **Pilot後の学習**
  `run_learning_loop()` は期待 KPI と実績 KPI の差分を記録し、
  Adoption 不足・対象範囲不足・原因仮説誤り・例外率増加・データ誤認といった
  再調査候補を提示します。Gate 7（Scale）も「測定さえされていればよい」ではなく、
  baseline からの改善が期待値の 50% 以上実現しているかを確認します。

## BPMN 生成エージェント（Camunda Modeler対応）

`agents/bpmn_agent.py` は、業務説明から Camunda Modeler で開ける BPMN 2.0 XML を生成する
専門エージェントです。他のエージェントと異なり、出力そのもの（生XML）が成果物なので、
`AgentResponse` には JSON ではなく `artifact` / `artifact_type="bpmn_xml"` フィールドで
XML を格納します（`contracts.AgentResponse` に追加済み）。

このエージェントのプロンプトは「出力前に自己検証する」という内部ルールを持ちますが、
LLM の自己申告は信用しません。`validate_bpmn_xml()` が以下を **プログラムで機械的に**
再検証し、通らなければ 1 回だけ具体的な指摘つきで再生成を要求します（それでも失敗すれば
例外を投げ、Orchestrator に壊れた XML を渡しません）。

- スマートクォート・不可視/ゼロ幅文字の混入なし
- 整形式XML（`xml.etree.ElementTree` でパース可能）かつ `BPMNDI` あり
- ID の一意性・命名規則（英数字+アンダースコア、数字始まり禁止）
- `sourceRef`/`targetRef` の参照整合性、`incoming`/`outgoing` タグの完全対応
- 孤立ノードなし、`default` Flow に `conditionExpression` が付いていないこと

`MockCamundaBPMNAgent` は API キー不要の決定論的スタブで、デモ・テストで
`CamundaBPMNAgent`（実際に Claude API を呼ぶ本番実装）の代わりに使えます。

```python
from bpr_orchestrator.agents.bpmn_agent import CamundaBPMNAgent  # ANTHROPIC_API_KEY が必要

orch.agents["bpmn_agent"] = CamundaBPMNAgent()
response = orch.dispatch(
    "bpmn_agent", "To-Beプロセスを図示して",
    problem_id=problem_id, initiative_id=initiative_id,
)
xml = response.artifact  # Camunda Modeler にそのまま読み込める BPMN 2.0 XML
```

生成された XML は呼び出し元が受け取るだけでなく、Evidence や Decision と同じように
`case.design_artifacts`（`DesignArtifact`：artifact_type / content / phase / problem_id /
initiative_id / source_agent を保持）へ自動的に永続化されます。`dispatch()` に
`problem_id=`/`initiative_id=` を渡しておけば、「どの Problem・どの Initiative の
Redesign 時点で生成された図か」を後から追跡できます（1 回の dispatch につき 1 件追加、
上書きはされません）。

## 実行方法

```bash
pip install -r requirements.txt
python -m examples.run_demo   # Mock Agent だけで DISCOVER〜SCALE を一気通貫デモ
pytest                        # 単体テスト
```

## 専門エージェントの追加方法

`bpr_orchestrator.contracts.BaseAgent` を実装するか、Claude API を使うなら
`bpr_orchestrator.agents.base.AnthropicAgent` を継承して `system_prompt` を
差し替えるだけです。返り値は必ず `AgentResponse` の契約フィールドを満たす必要があります
（`ANTHROPIC_API_KEY` 環境変数が必要）。

```python
from bpr_orchestrator.agents.base import AnthropicAgent

class ProcessAgent(AnthropicAgent):
    name = "process_agent"
    system_prompt = "You are a process-mining specialist. ..."
```

作成したエージェントは `BPROrchestrator(case, agents={"process_agent": ProcessAgent()})`
のように `agents` 辞書へ登録して `orch.dispatch("process_agent", "...")` で呼び出します。

## 次フェーズ

製造品質領域の `Quality Incident Agent`（Beachhead）を、この Orchestrator 基盤の下に
専門 Agent として実装する予定です。
