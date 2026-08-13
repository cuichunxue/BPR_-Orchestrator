from bpr_orchestrator.agents.bpmn_agent import (
    MockCamundaBPMNAgent,
    _MOCK_BPMN_XML,
    validate_bpmn_xml,
)
from bpr_orchestrator.contracts import AgentTask
from bpr_orchestrator.models import TransformationCase
from bpr_orchestrator.orchestrator import BPROrchestrator


def test_golden_anchor_is_valid():
    result = validate_bpmn_xml(_MOCK_BPMN_XML)
    assert result.ok, result.problems


def test_smart_quotes_are_rejected():
    broken = _MOCK_BPMN_XML.replace('id="Task_1"', "id=“Task_1”")
    result = validate_bpmn_xml(broken)
    assert not result.ok
    assert any("smart quote" in p for p in result.problems)


def test_invisible_characters_are_rejected():
    broken = _MOCK_BPMN_XML.replace(
        "<bpmn:outgoing>Flow_1</bpmn:outgoing>",
        "<bpmn:outgoing>⁠Flow_1</bpmn:outgoing>",
    )
    result = validate_bpmn_xml(broken)
    assert not result.ok
    assert any("invisible" in p for p in result.problems)


def test_duplicate_ids_are_rejected():
    broken = _MOCK_BPMN_XML.replace('id="Task_2"', 'id="Task_1"')
    result = validate_bpmn_xml(broken)
    assert not result.ok
    assert any("duplicate IDs" in p for p in result.problems)


def test_dangling_source_ref_is_rejected():
    broken = _MOCK_BPMN_XML.replace(
        '<bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_1" />',
        '<bpmn:sequenceFlow id="Flow_1" sourceRef="NoSuchNode" targetRef="Task_1" />',
    )
    result = validate_bpmn_xml(broken)
    assert not result.ok
    assert any("does not exist" in p for p in result.problems)


def test_missing_bpmndi_is_rejected():
    start = _MOCK_BPMN_XML.index("<bpmndi:BPMNDiagram")
    end = _MOCK_BPMN_XML.index("</bpmndi:BPMNDiagram>") + len("</bpmndi:BPMNDiagram>")
    broken = _MOCK_BPMN_XML[:start] + "</bpmn:definitions>"
    result = validate_bpmn_xml(broken)
    assert not result.ok
    assert any("BPMNDI" in p for p in result.problems)


def test_missing_outgoing_tag_is_rejected():
    broken = _MOCK_BPMN_XML.replace(
        "<bpmn:incoming>Flow_1</bpmn:incoming>\n      <bpmn:incoming>Flow_Back_1</bpmn:incoming>\n      <bpmn:outgoing>Flow_2</bpmn:outgoing>",
        "<bpmn:incoming>Flow_1</bpmn:incoming>\n      <bpmn:incoming>Flow_Back_1</bpmn:incoming>",
    )
    result = validate_bpmn_xml(broken)
    assert not result.ok
    assert any("Flow_2" in p for p in result.problems)


def test_default_flow_with_condition_is_rejected():
    broken = _MOCK_BPMN_XML.replace(
        '<bpmn:exclusiveGateway id="Gateway_1" name="内容は問題ないか？">',
        '<bpmn:exclusiveGateway id="Gateway_1" name="内容は問題ないか？" default="Flow_OK">',
    ).replace(
        '<bpmn:sequenceFlow id="Flow_OK" name="OK" sourceRef="Gateway_1" targetRef="EndEvent_1" />',
        '<bpmn:sequenceFlow id="Flow_OK" name="OK" sourceRef="Gateway_1" targetRef="EndEvent_1">'
        '<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">true</bpmn:conditionExpression>'
        "</bpmn:sequenceFlow>",
    )
    result = validate_bpmn_xml(broken)
    assert not result.ok
    assert any("default sequenceFlow" in p for p in result.problems)


def test_mock_agent_integrates_via_orchestrator_dispatch():
    case = TransformationCase(objective="x", scope="y")
    orch = BPROrchestrator(case, {"bpmn_agent": MockCamundaBPMNAgent()})
    response = orch.dispatch("bpmn_agent", "diagram the as-is process")
    assert response.artifact_type == "bpmn_xml"
    assert validate_bpmn_xml(response.artifact).ok
