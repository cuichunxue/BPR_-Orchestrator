import pytest

from bpr_orchestrator.agents.bpmn_agent import (
    CamundaBPMNAgent,
    MockCamundaBPMNAgent,
    _MOCK_BPMN_XML,
    validate_bpmn_xml,
)
from bpr_orchestrator.contracts import AgentTask
from bpr_orchestrator.models import TransformationCase
from bpr_orchestrator.orchestrator import BPROrchestrator


class _FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    """Stands in for anthropic.Anthropic().messages so CamundaBPMNAgent's
    retry loop can be exercised without any network access."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("fake client received more calls than responses queued")
        return _FakeMessage(self._responses.pop(0))


class _FakeClient:
    def __init__(self, responses: list[str]):
        self.messages = _FakeMessages(responses)


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


def test_dispatch_persists_artifact_into_case_tagged_with_initiative_and_phase():
    from bpr_orchestrator.models import Phase

    case = TransformationCase(objective="x", scope="y")
    case.phase = Phase.REDESIGN
    orch = BPROrchestrator(case, {"bpmn_agent": MockCamundaBPMNAgent()})
    orch.dispatch(
        "bpmn_agent",
        "diagram the to-be process",
        problem_id="prob_123",
        initiative_id="init_456",
    )
    assert len(case.design_artifacts) == 1
    artifact = next(iter(case.design_artifacts.values()))
    assert artifact.artifact_type == "bpmn_xml"
    assert artifact.phase == Phase.REDESIGN
    assert artifact.problem_id == "prob_123"
    assert artifact.initiative_id == "init_456"
    assert artifact.source_agent == "bpmn_agent"
    assert validate_bpmn_xml(artifact.content).ok

    # a second dispatch must not overwrite the first — both are kept
    orch.dispatch("bpmn_agent", "diagram again", initiative_id="init_456")
    assert len(case.design_artifacts) == 2


def test_camunda_agent_retries_once_on_invalid_output_then_succeeds(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key-for-test")
    broken_xml = _MOCK_BPMN_XML.replace('id="Task_1"', "id=“Task_1”")
    assert not validate_bpmn_xml(broken_xml).ok  # sanity: fixture really is invalid

    agent = CamundaBPMNAgent()
    fake_client = _FakeClient([broken_xml, _MOCK_BPMN_XML])
    agent._client = fake_client

    case = TransformationCase(objective="x", scope="y")
    task = AgentTask(task_id="t1", agent_name="bpmn_agent", instruction="draw the to-be process")
    response = agent.run(task, case)

    assert len(fake_client.messages.calls) == 2  # initial call + exactly one retry
    assert response.artifact == _MOCK_BPMN_XML
    assert response.artifact_type == "bpmn_xml"
    assert validate_bpmn_xml(response.artifact).ok
    # the retry prompt must carry the validator's own complaints, not a generic nudge
    retry_prompt = fake_client.messages.calls[1]["messages"][0]["content"]
    assert "smart quote" in retry_prompt


def test_camunda_agent_gives_up_after_exhausting_retries(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key-for-test")
    always_broken = "this is not xml at all"

    agent = CamundaBPMNAgent()
    fake_client = _FakeClient([always_broken, always_broken])
    agent._client = fake_client

    case = TransformationCase(objective="x", scope="y")
    task = AgentTask(task_id="t1", agent_name="bpmn_agent", instruction="draw the to-be process")

    with pytest.raises(ValueError, match="could not produce valid BPMN XML"):
        agent.run(task, case)
    assert len(fake_client.messages.calls) == 2  # gives up after initial + max_retries, no more
