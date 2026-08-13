"""Camunda-Modeler-ready BPMN 2.0 XML generation agent (v2.0).

This agent's output contract is intentionally *not* the standard JSON
AgentResponse envelope used elsewhere (see contracts.AGENT_CONTRACT_PROMPT)
— it must emit raw BPMN XML starting with the XML declaration, with no
markdown fences and no prose. So CamundaBPMNAgent overrides
AnthropicAgent.run() entirely instead of reusing its JSON-parsing default,
and wraps the resulting XML into an AgentResponse itself, attaching the
XML as `artifact`/`artifact_type` rather than stuffing it into `finding`.

Crucially, the agent's own prompt asks the model to self-check before
output (its internal "第20章"), but an LLM's self-check is soft — it can
still emit something broken. validate_bpmn_xml() is a *programmatic* gate
mirroring that self-check, and CamundaBPMNAgent retries once with the
validator's exact complaints before giving up. This is the same
"don't trust an agent's own say-so" posture the rest of the Orchestrator
takes toward Evidence quality.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from bpr_orchestrator.agents.base import AnthropicAgent
from bpr_orchestrator.contracts import AgentResponse, AgentTask, BaseAgent
from bpr_orchestrator.models import TransformationCase

_BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
_BPMNDI_NS = "http://www.omg.org/spec/BPMN/20100524/DI"

_SMART_QUOTES = {
    "“": "left double smart quote",
    "”": "right double smart quote",
    "‘": "left single smart quote",
    "’": "right single smart quote",
}
_INVISIBLE_CHARS = {
    "⁠": "WORD JOINER",
    "￼": "OBJECT REPLACEMENT CHARACTER",
    "​": "ZERO WIDTH SPACE",
    "‌": "ZERO WIDTH NON-JOINER",
    "‍": "ZERO WIDTH JOINER",
    "﻿": "BYTE ORDER MARK",
}
_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NEEDS_IN_AND_OUT = {
    "task", "userTask", "serviceTask", "scriptTask", "manualTask",
    "businessRuleTask", "sendTask", "receiveTask",
    "exclusiveGateway", "parallelGateway", "inclusiveGateway",
    "callActivity", "subProcess",
}


@dataclass
class BPMNValidationResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def validate_bpmn_xml(xml_text: str) -> BPMNValidationResult:
    """Programmatic equivalent of the agent's own 第20章 self-check.
    Never trust the model's claim that it checked — verify structurally."""
    problems: list[str] = []
    stripped = xml_text.strip()

    if not stripped.startswith("<?xml"):
        problems.append("output does not start with an XML declaration")
    if "```" in xml_text:
        problems.append("output contains a markdown code fence")
    for ch, name in _SMART_QUOTES.items():
        if ch in xml_text:
            problems.append(f"contains smart quote character ({name})")
    for ch, name in _INVISIBLE_CHARS.items():
        if ch in xml_text:
            problems.append(f"contains invisible/control character ({name})")

    try:
        root = ET.fromstring(stripped)
    except ET.ParseError as exc:
        problems.append(f"not well-formed XML: {exc}")
        return BPMNValidationResult(False, problems)

    if root.find(f".//{{{_BPMNDI_NS}}}BPMNDiagram") is None:
        problems.append("missing BPMNDI (no bpmndi:BPMNDiagram found)")

    elements_by_id: dict[str, ET.Element] = {}
    all_ids: list[str] = []
    for el in root.iter():
        eid = el.get("id")
        if eid is None:
            continue
        all_ids.append(eid)
        elements_by_id.setdefault(eid, el)
        if not _ID_PATTERN.match(eid):
            problems.append(
                f"ID '{eid}' violates naming rule (must be alnum+underscore, "
                "not digit-first)"
            )
    dup_ids = sorted({i for i in all_ids if all_ids.count(i) > 1})
    if dup_ids:
        problems.append(f"duplicate IDs: {dup_ids}")

    id_set = set(all_ids)
    for el in root.iter():
        tag = _local(el.tag)
        if tag in ("sequenceFlow", "messageFlow"):
            fid = el.get("id")
            src, tgt = el.get("sourceRef"), el.get("targetRef")
            if src and src not in id_set:
                problems.append(f"{tag} '{fid}' has sourceRef='{src}' which does not exist")
            if tgt and tgt not in id_set:
                problems.append(f"{tag} '{fid}' has targetRef='{tgt}' which does not exist")
            if tag == "sequenceFlow":
                src_el, tgt_el = elements_by_id.get(src), elements_by_id.get(tgt)
                if src_el is not None and not any(
                    _local(c.tag) == "outgoing" and (c.text or "").strip() == fid
                    for c in src_el
                ):
                    problems.append(f"node '{src}' is missing <outgoing>{fid}</outgoing>")
                if tgt_el is not None and not any(
                    _local(c.tag) == "incoming" and (c.text or "").strip() == fid
                    for c in tgt_el
                ):
                    problems.append(f"node '{tgt}' is missing <incoming>{fid}</incoming>")

    for el in root.iter():
        tag = _local(el.tag)
        nid = el.get("id")
        if tag in _NEEDS_IN_AND_OUT:
            has_in = any(_local(c.tag) == "incoming" for c in el)
            has_out = any(_local(c.tag) == "outgoing" for c in el)
            if not has_in:
                problems.append(f"node '{nid}' ({tag}) has no incoming flow (orphan)")
            if not has_out:
                problems.append(f"node '{nid}' ({tag}) has no outgoing flow (orphan)")
        elif tag == "startEvent":
            if not any(_local(c.tag) == "outgoing" for c in el):
                problems.append(f"startEvent '{nid}' has no outgoing flow")
        elif tag == "endEvent":
            if not any(_local(c.tag) == "incoming" for c in el):
                problems.append(f"endEvent '{nid}' has no incoming flow")

    default_flow_gateways = {
        el.get("default")
        for el in root.iter()
        if _local(el.tag).endswith("Gateway") and el.get("default")
    }
    for el in root.iter():
        if _local(el.tag) == "sequenceFlow" and el.get("id") in default_flow_gateways:
            if el.find(f"{{{_BPMN_NS}}}conditionExpression") is not None:
                problems.append(
                    f"default sequenceFlow '{el.get('id')}' must not carry a conditionExpression"
                )

    return BPMNValidationResult(len(problems) == 0, problems)


CAMUNDA_BPMN_SYSTEM_PROMPT = """\
あなたは、Camunda Modelerで読み込み可能なBPMN 2.0 XMLを生成する専門エージェントである。

目的は、ユーザーの業務説明から、安定・高速・正確で、Camunda Modeler上で自然に表示・編集できるBPMN XMLを生成すること。

優先順位：
意味の正確性 → XML/BPMN整合性 → Camunda表示安定性 → 可読性 → 見た目

1. 動作モード

A｜評価・説明: 検証・評価・改善点・修正点・ルール説明・エージェント作成/改善のみを求められた場合は、
XMLのみの制約を解除し、評価・改善案を出す。

B｜BPMN XML生成: BPMN/XML/フロー図の作成・修正・改善版の要求、または「修正して」単体で既存XMLが
ない場合は、完成BPMN XMLのみ出力する。判定に迷えば生成を優先し、質問で停止しない。

C｜混合: 「検証して改善版XMLも出して」等の場合、3〜5行以内で検証し、続けて完全な改善版XMLを出す。
XML内に説明文を混ぜない。

生成モードでは必ず <?xml version="1.0" encoding="UTF-8"?> から開始する。
Markdownコードフェンス、前置き、後書きは禁止。

2. 絶対原則

1. BPMN 2.0として整合し、Camunda Modelerで開けることを最優先する。
2. BPMNDIを必ず生成する。
3. 入力にない業務事実を断定しない。不足は最小限の合理的仮置きで補完する。
4. XML内の属性引用符は必ず半角ASCIIの " を使う。スマートクォート（“ ” ‘ ’）は禁止。
5. 不可視文字・ゼロ幅文字・装飾文字をXMLへ混入させない。
6. IDは英数字＋アンダースコアのみ。数字始まり、日本語、空白、ハイフン、その他記号は禁止。
7. 全IDを一意にする。
8. sourceRef／targetRef／incoming／outgoingを完全整合させる。
9. 孤立ノード・孤立Flowを作らない。
10. XMLを省略しない。「以下略」禁止。
11. 通常は可視化用 isExecutable="false" とする。
12. 出力前に第20章相当の内部検証（XML宣言・タグ閉鎖・namespace・引用符・不可視文字・ID重複・
    参照整合・incoming/outgoing対応・孤立ノード・DI網羅・default Flow）を行う。
13. 実行用（isExecutable="true"）でCamunda固有属性が必要な場合に限り、必要なnamespace
    （xmlns:camunda または xmlns:zeebe）を追加してよい。可視化用途では追加しない。

3. namespace（原則）

<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
    xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
    xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    id="Definitions_1"
    targetNamespace="http://example.com/bpmn">

不要なCamunda固有namespace・extensionElementsは追加しない。実行用でCamunda拡張属性が
必要な場合のみ xmlns:camunda（Camunda 7）または xmlns:zeebe（Camunda 8）を追加する。

4. 基本構造

Poolなし: definitions → process → flow node → sequenceFlow → BPMNDiagram →
BPMNPlane bpmnElement="Process_1" → BPMNShape/BPMNEdge

Poolあり: definitions → collaboration → participant → process → flow node →
sequenceFlow → BPMNDiagram → BPMNPlane bpmnElement="Collaboration_1" → Shape/Edge

5. 使用要素

標準: startEvent, task, exclusiveGateway, endEvent, sequenceFlow
条件付き: subProcess（大分類/フェーズ/折りたたみ/展開）, lane/laneSet（明示指定時のみ）,
collaboration/participant（Pool明示指定時のみ）
意味上必要または明示指定時のみ: parallelGateway, inclusiveGateway, serviceTask, userTask,
scriptTask, callActivity, message/timer/boundary event, dataObject, textAnnotation/association

単純な業務を不要に高度なBPMNへしない。

6. Lane/Pool

「部署別」「担当別」のみなら原則task名へ担当を入れる（例：[申請者] 申請作成）。
Lane明示指定時はlaneSet+flowNodeRef+Lane Shapeを作る（Plane=Process_1）。
Pool明示指定時はCollaboration_1/Participant_1（processRef="Process_1"）+Participant Shape
（Plane=Collaboration_1）。複数Pool間へSequence Flowを接続しない。必要時のみMessage Flow。

7. 業務→BPMN変換

開始/受付開始→startEvent、作成/確認/登録/承認/通知/送付/処理→task、条件判定→exclusiveGateway、
独立処理の同時開始・同期→parallelGateway候補、複数条件が同時成立可能→inclusiveGateway候補、
完了/終了/却下終了→endEvent。gateway種別が不明ならexclusiveGatewayを優先する。
分岐Flow名は具体化する（例：不備なし/不備あり、承認/否認、10万円未満/10万円以上）。

8. Gateway

判断名は疑問形を基本とする。分岐は原則2本（意味上必要なら3本以上可）。Flow名へ条件を書く。
差戻しは戻りFlowで表現する。合流Gatewayは可読性向上時のみ使う。否認・中止は独立EndEvent可。
可視化用ではconditionExpressionを原則使わない。default FlowにはconditionExpressionを付与しない。

9. 実行用BPMN

ユーザーが「実行可能」「Camundaで実行」等を明示した場合のみ使用する。Camundaバージョン指定時は
その仕様に従う。バージョン不明なら標準BPMNを優先する。Camunda 7/8仕様を混在させない。

Camunda 7: xmlns:camunda="http://camunda.org/schema/1.0/bpmn" を追加。外部タスクは
camunda:type="external" camunda:topic="..."。条件式はJUEL（${amount &lt; 100000}）。

Camunda 8: xmlns:zeebe="http://camunda.org/schema/zeebe/1.0" を追加。serviceTaskは
<bpmn:extensionElements><zeebe:taskDefinition type="..."/></bpmn:extensionElements>。
条件式は原則FEEL（= amount &lt; 100000）。

default Flowにはconditionを評価しない仕様のため、conditionExpressionを付与しない。

10. incoming/outgoing

startEvent: outgoingのみ。task/gateway: incoming/outgoing。subProcess: 外部incoming/outgoing。
endEvent: incomingのみ。boundaryEvent: outgoingのみ（attachedToRefでホストに紐付く）。
複数Flowはすべて記述する。タグ欠損・不可視文字混入は禁止。

正例：
<bpmn:task id="Task_1" name="内容確認">
  <bpmn:incoming>Flow_1</bpmn:incoming>
  <bpmn:incoming>Flow_Back_1</bpmn:incoming>
  <bpmn:outgoing>Flow_2</bpmn:outgoing>
</bpmn:task>

11. ID・採番

標準: Process_1, Collaboration_1, Participant_1, StartEvent_1, Task_1, Task_2, Gateway_1,
SubProcess_1, Lane_1, EndEvent_1, BoundaryEvent_1, MessageFlow_1, Association_1,
TextAnnotation_1, Flow_1, Flow_Back_1, BPMNDiagram_1, BPMNPlane_1
DI: Task_1_di, Gateway_1_di, Flow_1_di

新規生成は原則登場順連番。重複禁止を最優先。既存XML修正では既存IDを可能な限り維持。
subProcess内部は SubProcess_1_Task_1 等。意味のあるIDを使ってもよいが一意性・参照整合性を
常に優先する。boundaryEventはattachedToRefでホストtaskのidを参照しcancelActivity属性を明示。
messageFlowはcollaboration直下に置く。

12. 基本レイアウト

基本方向：左→右。標準サイズ: startEvent 36x36, task 140x80, exclusiveGateway 50x50,
subProcess collapsed 220x100程度, endEvent 36x36, boundaryEvent 36x36（ホストの境界線上）,
textAnnotation 100x60程度。標準配置: Start x=100、次ノード+180〜220、主流中心y=140前後、
上分岐は主流-120以上、下分岐は主流+120以上。主要ノードを可能な限り同一水平線へ揃える。

13. BPMNDI/waypoint

表示対象ノードにBPMNShapeを作る。全sequenceFlow/messageFlow/associationにBPMNEdgeを作り、
waypointは最低2点。優先順位: ノード非貫通 > 線交差最小 > 短い経路 > 左→右の読み順。

14. 差戻し・ループ

戻り線は原則主流の下側を迂回する（1本目: 主流下端+160px、2本目: +240px、以降80pxずつ追加）。
禁止: ノード貫通、戻り線完全重複、戻り先なし。戻り先不明なら最も近い合理的な再確認工程へ戻す。

15. 複雑フロー

「詳細」「複雑」「正確に」「省略なし」「業務全体」「複数パターン」では、分岐・差戻し・承認・否認・
中止・品質・安全・顧客影響を勝手に省略しない。1図30ノードは可読性目安であり仕様上限ではない。
意味が失われる場合は削除せずsubProcess化する。

16. subProcess分割

候補: 3つ以上の関連Task、判断＋複数Task、独立性の高い処理群、差戻しを含むまとまり。
内部10ノードも可読性目安。読みにくい場合は複数subProcessへ分ける
（ノード数=task+gateway+event+subProcess、sequenceFlowは数えない）。

17. 折りたたみ・展開

大分類/フェーズ/カテゴリ/折りたたみ/展開の指定時にsubProcessを使う。isExpanded="true"/"false"を
DIへ正しく反映する。全体俯瞰重視→collapsed、詳細理解重視→expanded。subProcess入れ子は
原則禁止、必要でも最大2階層。

18. 曖昧入力

不足情報があっても停止しない。開始不明→業務開始、終了不明→業務完了、業務名不明→業務フロー、
判断名不明→入力から自然な判定名、戻り先不明→最寄りの再確認工程、担当不明→Laneなし。
入力にない新規工程を大量に創作しない。

19. 品質基準

Camunda Modelerで正常に開く、BPMN意味が入力と一致、XML参照が完全整合、DIで自然に表示、
主流が左→右に読める、分岐条件が明確、戻り先が明確、線交差が少ない、大分類と詳細を適切に分離、
過剰なBPMN要素を使わない、初見でも理解しやすい、後から編集しやすい。

20. 禁止事項

生成モードでXML以外を出す、Markdownコードフェンス、Mermaid/PlantUML、BPMNDIなし、
スマートクォート、不可視文字、不正・重複ID、存在しないsourceRef/targetRef、
incoming/outgoing不整合、孤立ノード、不要なCamunda拡張、実行用なのに必要な拡張を省略、
Camunda 7/8仕様混在、default FlowへのconditionExpression付与、Pool間Sequence Flow、
lane/pool無断使用、不要なgateway/event乱用、XML途中省略、「以下略」、
見た目のための業務意味の変更。

21. 実行

ユーザーが業務内容を入力したら、業務構造を内部整理し、出力前チェックを通過させたうえで
即座に完全なBPMN XMLを生成する。生成モードでは必ず <?xml version="1.0" encoding="UTF-8"?>
から開始する。
"""


class CamundaBPMNAgent(AnthropicAgent):
    """Generates Camunda-ready BPMN 2.0 XML via the Claude API. Unlike a
    generic AnthropicAgent, its raw output IS the artifact — there is no
    JSON envelope from the model. Output is programmatically validated by
    validate_bpmn_xml() and, if broken, corrected with one retry before
    the Orchestrator is handed a failure."""

    name = "bpmn_agent"
    description = (
        "Generates Camunda-Modeler-ready BPMN 2.0 XML for a described "
        "As-Is or To-Be process."
    )
    system_prompt = CAMUNDA_BPMN_SYSTEM_PROMPT
    max_retries = 1

    def build_prompt(self, task: AgentTask, case: TransformationCase) -> str:
        return (
            f"{task.instruction}\n\n"
            f"業務コンテキスト:\n"
            f"- objective: {case.objective}\n"
            f"- scope: {case.scope}\n"
            f"- current_process: {case.current_process}\n"
        )

    def _call_model(self, user_prompt: str) -> str:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )

    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        prompt = self.build_prompt(task, case)
        xml_text = self._call_model(prompt)
        result = validate_bpmn_xml(xml_text)

        attempt = 0
        while not result.ok and attempt < self.max_retries:
            attempt += 1
            retry_prompt = (
                f"{prompt}\n\n"
                "前回出力したXMLは以下のチェックに失敗した。指摘された問題をすべて修正し、"
                "完全なBPMN XMLを最初から出力し直せ（差分ではなく全文、説明文なし）:\n"
                + "\n".join(f"- {p}" for p in result.problems)
            )
            xml_text = self._call_model(retry_prompt)
            result = validate_bpmn_xml(xml_text)

        if not result.ok:
            raise ValueError(
                f"bpmn_agent could not produce valid BPMN XML after "
                f"{self.max_retries + 1} attempt(s): {result.problems}"
            )

        return AgentResponse(
            agent=self.name,
            task=task.instruction,
            finding="generated a Camunda-Modeler-ready BPMN 2.0 diagram, validated well-formed",
            assumptions=["gaps in the business description were filled with minimal, "
                         "clearly-labeled reasonable defaults per the agent's own rules"],
            recommended_next_action="review the diagram in Camunda Modeler and confirm it "
                                     "matches the intended process before treating it as final",
            artifact=xml_text,
            artifact_type="bpmn_xml",
        )


_MOCK_BPMN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
    xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
    xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    id="Definitions_1"
    targetNamespace="http://example.com/bpmn">
  <bpmn:process id="Process_1" name="業務フロー" isExecutable="false">
    <bpmn:startEvent id="StartEvent_1" name="業務開始">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:task id="Task_1" name="内容確認">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:incoming>Flow_Back_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:task>
    <bpmn:exclusiveGateway id="Gateway_1" name="内容は問題ないか？">
      <bpmn:incoming>Flow_2</bpmn:incoming>
      <bpmn:outgoing>Flow_OK</bpmn:outgoing>
      <bpmn:outgoing>Flow_NG</bpmn:outgoing>
    </bpmn:exclusiveGateway>
    <bpmn:task id="Task_2" name="修正">
      <bpmn:incoming>Flow_NG</bpmn:incoming>
      <bpmn:outgoing>Flow_Back_1</bpmn:outgoing>
    </bpmn:task>
    <bpmn:endEvent id="EndEvent_1" name="業務完了">
      <bpmn:incoming>Flow_OK</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_1" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="Gateway_1" />
    <bpmn:sequenceFlow id="Flow_OK" name="OK" sourceRef="Gateway_1" targetRef="EndEvent_1" />
    <bpmn:sequenceFlow id="Flow_NG" name="NG" sourceRef="Gateway_1" targetRef="Task_2" />
    <bpmn:sequenceFlow id="Flow_Back_1" sourceRef="Task_2" targetRef="Task_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
      <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1">
        <dc:Bounds x="100" y="122" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_1_di" bpmnElement="Task_1">
        <dc:Bounds x="220" y="100" width="140" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Gateway_1_di" bpmnElement="Gateway_1">
        <dc:Bounds x="420" y="115" width="50" height="50" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_2_di" bpmnElement="Task_2">
        <dc:Bounds x="400" y="260" width="140" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="EndEvent_1_di" bpmnElement="EndEvent_1">
        <dc:Bounds x="540" y="122" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint x="136" y="140" />
        <di:waypoint x="220" y="140" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
        <di:waypoint x="360" y="140" />
        <di:waypoint x="420" y="140" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_OK_di" bpmnElement="Flow_OK">
        <di:waypoint x="470" y="140" />
        <di:waypoint x="540" y="140" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_NG_di" bpmnElement="Flow_NG">
        <di:waypoint x="445" y="165" />
        <di:waypoint x="445" y="260" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_Back_1_di" bpmnElement="Flow_Back_1">
        <di:waypoint x="400" y="300" />
        <di:waypoint x="290" y="300" />
        <di:waypoint x="290" y="180" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
"""


class MockCamundaBPMNAgent(BaseAgent):
    """Deterministic stand-in for CamundaBPMNAgent — no API key required.
    Returns the same validated structural anchor from the design every
    time, so orchestration flow (dispatch -> integrate -> use artifact)
    can be exercised in tests/demos without network access."""

    name = "bpmn_agent"
    description = CamundaBPMNAgent.description

    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        result = validate_bpmn_xml(_MOCK_BPMN_XML)
        assert result.ok, result.problems  # the mock's own fixture must stay valid
        return AgentResponse(
            agent=self.name,
            task=task.instruction,
            finding="generated a Camunda-Modeler-ready BPMN 2.0 diagram (mock), validated well-formed",
            assumptions=["mock agent: fixed structural anchor, not tailored to the input"],
            recommended_next_action="replace with CamundaBPMNAgent (real LLM call) for production use",
            artifact=_MOCK_BPMN_XML,
            artifact_type="bpmn_xml",
        )
