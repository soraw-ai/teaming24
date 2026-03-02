"""Centralized prompt template registry with strict variable validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from string import Template
from typing import Any


class PromptTemplateError(ValueError):
    """Raised when prompt template render/registration fails."""


@dataclass(frozen=True)
class PromptTemplate:
    """Prompt template specification."""

    template_id: str
    version: str
    role: str
    template: str
    description: str = ""
    default_values: dict[str, Any] = field(default_factory=dict)

    def variables(self) -> set[str]:
        """Return all variable names referenced by this template."""
        vars_found: set[str] = set()
        for match in Template.pattern.finditer(self.template):
            name = match.group("named") or match.group("braced")
            if name:
                vars_found.add(name)
        return vars_found


class PromptTemplateRegistry:
    """Template registry with strict render-time variable validation."""

    def __init__(self):
        self._templates: dict[str, PromptTemplate] = {}

    def register(self, template: PromptTemplate) -> None:
        existing = self._templates.get(template.template_id)
        if existing and existing.version != template.version:
            raise PromptTemplateError(
                f"Template id '{template.template_id}' already registered with version "
                f"{existing.version}, cannot replace with {template.version}"
            )
        self._templates[template.template_id] = template

    def get(self, template_id: str) -> PromptTemplate:
        tmpl = self._templates.get(template_id)
        if tmpl is None:
            raise PromptTemplateError(f"Template '{template_id}' not found")
        return tmpl

    def render(self, template_id: str, **values: Any) -> str:
        tmpl = self.get(template_id)
        merged: dict[str, Any] = {**tmpl.default_values, **values}
        variables = tmpl.variables()
        missing = sorted(var for var in variables if var not in merged)
        if missing:
            raise PromptTemplateError(
                f"Template '{template_id}' missing required variables: {', '.join(missing)}"
            )
        unknown = sorted(var for var in merged if var not in variables)
        if unknown:
            raise PromptTemplateError(
                f"Template '{template_id}' received unknown variables: {', '.join(unknown)}"
            )
        normalized = {k: str(v) for k, v in merged.items()}
        return Template(tmpl.template).substitute(normalized)

    def list_ids(self) -> list[str]:
        return sorted(self._templates.keys())


def _default_templates() -> list[PromptTemplate]:
    return [
        PromptTemplate(
            template_id="an_router.system.organizer_selection",
            version="1.0.0",
            role="router",
            description="Organizer-side AN routing selection prompt.",
            template=(
                "You are the routing advisor for the Organizer. Return ONLY the selection result-no reasoning or explanation.\n"
                "The Organizer expects you to:\n"
                "1. ANALYZE the user task deeply — understand intent, implicit requirements, constraints.\n"
                "2. REFORMULATE into clear, detailed, actionable goals (what must be delivered).\n"
                "3. SPLIT into subtasks based on the pool members below — each subtask must be concrete,\n"
                "   self-contained, and matched to the assigned member's capabilities.\n\n"
                "Given the user's task and the available Agentic Node Workforce Pool members below,\n"
                "which members should participate, and what specific subtask should each handle?\n\n"
                "AGENTIC NODE WORKFORCE POOL MEMBERS:\n"
                "$pool_lines\n\n"
                "RULES:\n"
                "- You MUST select at least $min_members different pool member(s).\n"
                "- CRITICAL: Each member has a unique `an_id`. Use `an_id` in the\n"
                "  `assigned_to` field to identify which member gets each subtask.\n"
                "  Names may be duplicated across members - `an_id` is the only\n"
                "  guaranteed unique identifier.\n"
                "- Each member can be assigned AT MOST ONCE. Never assign\n"
                "  the same `an_id` to multiple subtasks. If you need more subtasks\n"
                "  than there are members, combine related work into a single subtask\n"
                "  for one member.\n"
                "- [LOCAL] is the local team coordinator - it has a team of local Workers\n"
                "  who will internally break the assigned subtask into sub-subtasks.\n"
                "  Give the local team coordinator ONE comprehensive subtask; it handles\n"
                "  the internal decomposition autonomously.\n"
                "- [REMOTE] members are remote Agentic Nodes, each with their own\n"
                "  worker teams. They also receive ONE subtask each.\n"
                "- Use endpoint/source/region/wallet metadata to choose stable,\n"
                "  reachable members and avoid duplicate-equivalent members.\n"
                "- Match subtasks to the member whose capabilities best fit. Each subtask\n"
                "  description must be DETAILED and EXECUTABLE — not vague. Include:\n"
                "  specific deliverables, data sources if needed, and success criteria.\n"
                "$prefer_remote_rule\n"
                "EXECUTION MODE - you MUST decide:\n"
                "- \"parallel\"  - all subtasks run concurrently (no dependency between them).\n"
                "  Use parallel when subtasks are independent and can be done at the same time.\n"
                "  Example: member A does frontend, member B does backend - no dependency.\n"
                "- \"sequential\" - subtasks execute one after another in ascending `order`.\n"
                "  Each step receives the FULL OUTPUT of the previous step as context.\n"
                "  Use sequential when there is a real data dependency between steps.\n"
                "  Example: step 1 researches data -> step 2 analyzes step 1's output ->\n"
                "  step 3 generates a report from step 2's analysis.\n"
                "  You MUST assign an \"order\" field (integer starting from 1) to each subtask.\n\n"
                "Return a JSON object with this exact structure (selection only, no reasoning):\n"
                "{\n"
                "  \"execution_mode\": \"parallel\" or \"sequential\",\n"
                "  \"subtasks\": [\n"
                "    {\n"
                "      \"description\": \"Detailed, executable subtask: specific deliverables, data/tools if needed, success criteria. Not vague.\",\n"
                "      \"assigned_to\": \"an_id of the member (from the pool above)\",\n"
                "      \"order\": 1\n"
                "    }\n"
                "  ]\n"
                "}\n\n"
                "IMPORTANT:\n"
                "- Return ONLY valid JSON, no markdown fences, no extra text.\n"
                "- Do NOT include reasoning, reason, or any explanation - only the selection result.\n"
                "- NEVER assign the same `an_id` more than once.\n"
                "- Use the `an_id` value (not the name) in `assigned_to`.\n"
                "- You MUST assign at least $min_members DIFFERENT members."
            ),
            default_values={"prefer_remote_rule": ""},
        ),
        PromptTemplate(
            template_id="local_agent_router.user.routing",
            version="1.0.0",
            role="router",
            description="Coordinator local worker routing prompt.",
            template=(
                "You are the Coordinator. Given a subtask and the Local Agent Workforce Pool (Workers), "
                "select which Workers to use and assign each a sub-subtask.\n\n"
                "SUBTASK:\n"
                "$subtask_prompt\n\n"
                "AVAILABLE WORKERS:\n"
                "$workers_desc\n\n"
                "Return a JSON object with this exact structure (selection only, no reasoning):\n"
                "{\n"
                "  \"assignments\": [\n"
                "    {\"worker_role\": \"<exact role name>\", \"description\": \"<sub-subtask for this worker>\"},\n"
                "    ...\n"
                "  ]\n"
                "}\n\n"
                "Rules:\n"
                "- Select only the Workers needed for this subtask.\n"
                "- Assign each selected Worker at most one sub-subtask.\n"
                "- The description should be self-contained.\n"
                "- Return ONLY valid JSON, no markdown fences.\n"
                "- Do NOT include reasoning or explanation - only the selection result.\n"
            ),
        ),
        PromptTemplate(
            template_id="local_agent_router.system.routing",
            version="1.0.0",
            role="router",
            description="System instruction for local worker routing.",
            template="You are a routing advisor. Return valid JSON only. Output only the selection result-no reasoning or explanation.",
        ),
        PromptTemplate(
            template_id="native.hierarchical.planning",
            version="1.0.0",
            role="manager",
            description="Native hierarchical runner planning prompt.",
            template=(
                "You are $manager_role. You NEVER execute tasks directly - only plan, delegate, and synthesize.\n\n"
                "Your job:\n"
                "- Break down the task into subtasks\n"
                "- Assign each subtask to the most suitable worker\n"
                "- Workers execute; you only coordinate and validate\n\n"
                "TASK:\n"
                "$prompt\n\n"
                "AVAILABLE WORKERS:\n"
                "$workers_desc\n\n"
                "Return a JSON object with this exact structure:\n"
                "{\n"
                "  \"reasoning\": \"brief explanation of why you chose this plan\",\n"
                "  \"assignments\": [\n"
                "    {\"worker_role\": \"<exact role name>\", \"description\": \"<subtask description>\"},\n"
                "    ...\n"
                "  ]\n"
                "}\n\n"
                "Rules:\n"
                "- NEVER assign work to yourself. Always delegate to workers.\n"
                "- Assign each worker at most one subtask.\n"
                "- If the task is simple, assign it to the single best worker.\n"
                "- The description should be self-contained - the worker sees only its subtask.\n"
                "- Return ONLY valid JSON, no markdown fences.\n"
            ),
        ),
        PromptTemplate(
            template_id="native.hierarchical.aggregate",
            version="1.0.0",
            role="manager",
            description="Native hierarchical runner final aggregation prompt.",
            template=(
                "You are $manager_role. Your workers completed the task. "
                "Synthesize their results into a single, coherent response.\n\n"
                "ORIGINAL REQUEST:\n$original_prompt\n\n"
                "WORKER RESULTS:\n---\n$combined_results\n---\n\n"
                "CRITICAL - Structure your response:\n"
                "1. FIRST: The direct answer (prediction number, conclusion, runnable code) — never start with process description\n"
                "2. THEN: Brief context, metrics, how to reproduce\n"
                "3. Include result file contents (JSON values, chart paths) when workers produced them\n"
                "4. For images/charts: use markdown ![alt](path) to display them\n"
                "Be direct and specific. Remove redundancy."
            ),
        ),
        PromptTemplate(
            template_id="core.local_refinement",
            version="1.0.0",
            role="coordinator",
            description="Local coordinator refinement retry prompt.",
            template=(
                "[Refinement round $round_num] Previous attempt produced insufficient output.\n\n"
                "Original task: $original_task\n\n"
                "Previous output (incomplete): $previous_output\n\n"
                "Please produce a complete, substantive response. If the task is genuinely "
                "impossible after this attempt, state clearly: \"I cannot complete this because [reason]\"."
            ),
        ),
        PromptTemplate(
            template_id="core.organizer_quality_eval",
            version="1.0.0",
            role="organizer",
            description="Organizer quality evaluator prompt.",
            template=(
                "You are the Organizer quality evaluator.\n"
                "Evaluate if the result fully solves the request.\n"
                "Task class: $task_class\n\n"
                "ORIGINAL REQUEST:\n$original_request\n\n"
                "RESULT (round $round_num):\n$result_snippet\n\n"
                "Return valid JSON only:\n"
                "{\"satisfied\": true, \"feedback\": \"\", \"confidence\": 0.0}\n"
                "Rules:\n"
                "- confidence must be between 0 and 1\n"
                "- satisfied=true when answer is complete, has concrete evidence, and addresses the request\n"
                "- satisfied=true when agent explicitly gave up (e.g. \"I cannot complete because...\") — accept and stop\n"
                "- satisfied=false when answer is incomplete, plan-only, incorrect, or not directly actionable\n"
                "- feedback must be specific when satisfied=false"
            ),
        ),
        PromptTemplate(
            template_id="core.independent_verifier_eval",
            version="1.0.0",
            role="verifier",
            description="Independent verifier prompt.",
            template=(
                "You are an independent verifier model.\n"
                "Do not trust prior evaluators. Judge strictly from request+result+evidence.\n\n"
                "TASK CLASS: $task_class\n"
                "ORIGINAL REQUEST:\n$original_request\n\n"
                "RESULT (round $round_num):\n$result_snippet\n\n"
                "EVIDENCE_SCHEMA:\n$evidence_schema_json\n\n"
                "Return JSON only:\n"
                "{\"satisfied\": true, \"feedback\": \"\", \"confidence\": 0.0, "
                "\"checks\": {\"direct_answer\": true, \"evidence_complete\": true, \"not_plan_only\": true}}\n"
                "Rules: satisfied=true when agent explicitly gave up (e.g. \"I cannot complete because...\")."
            ),
        ),
        PromptTemplate(
            template_id="core.organizer_summary_synthesis",
            version="1.0.0",
            role="organizer",
            description="Organizer synthesis prompt for multi-agent outputs.",
            default_values={"output_files": ""},
            template=(
                "You are the Organizer. $task_desc "
                "Write as if you actually did the work and are reporting back.\n\n"
                "$question_section\n"
                "CRITICAL:\n"
                "- Lead with the direct answer (prediction number, conclusion, runnable code). "
                "NEVER start with process (\"I built...\", \"The pipeline...\").\n"
                "- PRESERVE exact numbers verbatim. Copy prediction values, RMSE, MAE, and any metrics "
                "character-for-character from the agent output. Do not round, approximate, or reformat.\n"
                "- Organize in a natural flow: answer first, then brief context (data source, model, "
                "how to reproduce), then output files and charts. Use sections/headers but avoid "
                "rigid numbered lists (1, 2, 3).\n"
                "- Output files (JSON, CSV, PNG) are attached to the chat. Include key values inline "
                "(prediction, RMSE, MAE). For charts, use markdown images. URL format: "
                "/api/agent/outputs/$task_id/file?name=FILENAME. Use exact filenames from: $output_files. "
                "Example: ![Chart](/api/agent/outputs/$task_id/file?name=tsla_chart.png)\n"
                "- Evidence: sources, metrics, artifacts, commands.\n\n"
                "FORBIDDEN: 'Collected approaches', 'Recommended', 'Key Findings', 'Deliverables', "
                "'Status: Succeeded/Failed', 'provided in Local Team Result', "
                "'can't access', 'cannot access', 'I don't have access to live data', "
                "'unable to access live market data'. Agents HAVE tools - use actual fetched data.\n\n"
                "STYLE: Direct, specific. Use markdown (headers, code blocks, tables). "
                "Preserve important code (max ~50 lines per block). Total under $out_max chars.\n\n"
                "--- AGENT OUTPUT ---\n\n"
                "$agent_output\n\n"
                "--- END ---"
            ),
        ),
    ]


_REGISTRY: PromptTemplateRegistry | None = None


def get_prompt_registry() -> PromptTemplateRegistry:
    """Get the process-global prompt registry."""
    global _REGISTRY
    if _REGISTRY is None:
        reg = PromptTemplateRegistry()
        for tmpl in _default_templates():
            reg.register(tmpl)
        _REGISTRY = reg
    return _REGISTRY


def render_prompt(template_id: str, **values: Any) -> str:
    """Render a prompt template by id with strict variable checks."""
    return get_prompt_registry().render(template_id, **values)
