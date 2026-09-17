"""Generate -> judge -> retry loop as a LangGraph graph, per the explicit
earlier decision to use a real orchestration framework here rather than
hand-rolled control flow.

Three nodes: write, judge, and a conditional edge (route) that ends the
graph once the judge approves or MAX_ATTEMPTS is reached, otherwise loops
back to write carrying the judge's verdict_reason forward as feedback.
"""

from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI

import llm

MAX_ATTEMPTS = 3


class RetryState(TypedDict):
    original_text: str
    rewrite: str
    feedback: Optional[str]
    judge_result: dict
    scores: dict
    attempt_count: int
    approved: bool


def _write_node(client: OpenAI):
    def node(state: RetryState) -> dict:
        rewrite = llm.call_writer(client, state["original_text"], state.get("feedback"))
        return {"rewrite": rewrite, "attempt_count": state["attempt_count"] + 1}
    return node


def _judge_node(client: OpenAI):
    def node(state: RetryState) -> dict:
        # Two separate calls: fidelity (fact-check, self-consistent) and
        # everything else (understanding/readability/explanation/style).
        # Kept apart deliberately — see judge_factcheck_system_prompt.md
        # for why a single combined call proved unreliable.
        judge_result = llm.call_judge(client, state["original_text"], state["rewrite"])
        fact_check_result = llm.call_fact_check_consistent(client, state["original_text"], state["rewrite"])
        scores = llm.get_scores(judge_result, fact_check_result)
        # Merged for storage/feedback — the writer's retry needs to see
        # both kinds of issues (a dropped number just as much as a missing
        # analogy) to fix everything in one pass rather than ping-ponging
        # between the two calls' separate complaints across attempts.
        combined_result = {
            **judge_result,
            "loss": fact_check_result.get("loss", []),
            "gain": fact_check_result.get("gain", []),
            "distortion": fact_check_result.get("distortion", []),
            "fidelity_verdict": fact_check_result.get("verdict_reason"),
        }
        feedback_parts = [
            v for v in (fact_check_result.get("verdict_reason"), judge_result.get("verdict_reason")) if v
        ]
        return {
            "judge_result": combined_result,
            "scores": scores,
            "approved": llm.compute_approved(scores),
            "feedback": " ".join(feedback_parts) or None,
        }
    return node


def _route(state: RetryState) -> str:
    if state["approved"] or state["attempt_count"] >= MAX_ATTEMPTS:
        return END
    return "write"


def build_graph(client: OpenAI):
    graph = StateGraph(RetryState)
    graph.add_node("write", _write_node(client))
    graph.add_node("judge", _judge_node(client))
    graph.add_edge(START, "write")
    graph.add_edge("write", "judge")
    graph.add_conditional_edges("judge", _route, {END: END, "write": "write"})
    return graph.compile()


def run_generate_judge_retry(client: OpenAI, original_text: str) -> RetryState:
    graph = build_graph(client)
    initial: RetryState = {
        "original_text": original_text,
        "rewrite": "",
        "feedback": None,
        "judge_result": {},
        "scores": {},
        "attempt_count": 0,
        "approved": False,
    }
    return graph.invoke(initial)
