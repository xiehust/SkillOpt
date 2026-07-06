"""SkillEval environment adapter — trains skills against rubric-judged tasks.

Registers the skilleval task format with the ReflACT trainer so a task set
built for evaluation (``scripts/evaluate_skill.py``) can drive skill
optimization unchanged: rollout runs the tasks through the exec target
(Claude Code / Codex), the LLM judge converts each task's rubric into
``hard``/``soft`` scores, and reflection sees the rubric as hidden reference
material alongside the agent's answer and the judge's verdict.
"""
from __future__ import annotations

import json
import os

from skillopt.datasets.base import BatchSpec
from skillopt.envs.base import EnvAdapter
from skillopt.envs.skilleval.dataloader import SkillEvalDataLoader
from skillopt.envs.skilleval.evaluator import artifacts_listing, judge, merge_scores
from skillopt.envs.skilleval.rollout import collect_support_files, run_batch
from skillopt.model import azure_openai as _llm


class SkillEvalAdapter(EnvAdapter):
    """ReflACT adapter for user task sets with per-task rubrics."""

    def __init__(
        self,
        split_dir: str = "",
        data_path: str = "",
        split_mode: str = "split_dir",
        split_ratio: str = "4:3:3",
        split_seed: int = 42,
        split_output_dir: str = "",
        workers: int = 3,
        timeout: int = 900,
        analyst_workers: int = 4,
        failure_only: bool = False,
        minibatch_size: int = 4,
        edit_budget: int = 4,
        seed: int = 42,
        limit: int = 0,
        skill_dir: str = "",
    ) -> None:
        # For a multi-file skill: only SKILL.md (the trainable state) evolves;
        # supporting files (scripts/, references/, ...) are frozen and copied
        # into every rollout workspace. Collected eagerly so a bad path fails
        # before any model call.
        self.skill_files = collect_support_files(skill_dir) if skill_dir else None
        self.workers = workers
        self.timeout = int(timeout)
        self.analyst_workers = analyst_workers
        self.failure_only = failure_only
        self.minibatch_size = minibatch_size
        self.edit_budget = edit_budget
        self.dataloader = SkillEvalDataLoader(
            split_dir=split_dir,
            data_path=data_path,
            split_mode=split_mode,
            split_ratio=split_ratio,
            split_seed=split_seed,
            split_output_dir=split_output_dir,
            seed=seed,
            limit=limit,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def setup(self, cfg: dict) -> None:
        super().setup(cfg)
        self.dataloader.setup(cfg)

    def get_dataloader(self):
        return self.dataloader

    # ── Env construction ──────────────────────────────────────────────────

    def build_env_from_batch(self, batch: BatchSpec, **kwargs):
        return list(batch.payload or [])

    def build_train_env(self, batch_size: int, seed: int, **kwargs):
        batch = self.dataloader.build_train_batch(batch_size=batch_size, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    def build_eval_env(self, env_num: int, split: str, seed: int, **kwargs):
        batch = self.dataloader.build_eval_batch(env_num=env_num, split=split, seed=seed, **kwargs)
        return self.build_env_from_batch(batch, **kwargs)

    # ── Rollout (scoring lives here; judge provides hard/soft) ───────────

    def rollout(self, env_manager, skill_content: str, out_dir: str, **kwargs) -> list[dict]:
        items: list[dict] = env_manager
        rollout_results = run_batch(
            items,
            skill_content,
            out_dir,
            workers=self.workers,
            timeout=self.timeout,
            model=_llm.TARGET_DEPLOYMENT,
            skill_files=self.skill_files,
        )
        results = merge_scores(items, rollout_results, judge)
        self._persist_trajectories(items, results, out_dir)
        return results

    def _persist_trajectories(self, items: list[dict], results: list[dict], out_dir: str) -> None:
        """Write predictions/<id>/conversation.json + enrich results for reflection.

        ``fmt_minibatch_trajectories`` silently skips any result without a
        conversation.json, so this is what makes reflection see skilleval
        trajectories at all.
        """
        for item, result in zip(items, results):
            task_id = str(result["id"])
            listing = artifacts_listing(result.get("work_dir", ""))
            verdict_note = (
                f"Judge verdict: hard={result.get('hard')} soft={result.get('soft')}\n"
                f"Judge reason: {result.get('judge_reason', '')}\n"
                f"Workspace artifacts:\n{listing or '(none)'}"
            )
            if result.get("error"):
                verdict_note += f"\nRollout error: {result['error']}"
            conversation = [
                {"role": "user", "content": item["question"]},
                {"role": "assistant", "content": result.get("response", "")},
                {"role": "system", "content": verdict_note},
            ]
            pred_dir = os.path.join(out_dir, "predictions", task_id)
            os.makedirs(pred_dir, exist_ok=True)
            with open(os.path.join(pred_dir, "conversation.json"), "w", encoding="utf-8") as f:
                json.dump(conversation, f, ensure_ascii=False, indent=2)

            result["task_description"] = item["question"]
            if not result.get("hard"):
                result["fail_reason"] = result.get("judge_reason") or result.get("error", "")
            result["n_turns"] = 1

    # ── Reflection support ────────────────────────────────────────────────

    def build_reference_text(self, item: dict) -> str:
        """Expose the rubric to the optimizer as hidden reference material."""
        rubric = str(item.get("rubric") or "").strip()
        if not rubric:
            return ""
        return f"Acceptance rubric (not shown to the agent):\n{rubric}"

    def get_task_types(self) -> list[str]:
        seen: list[str] = []
        for item in (
            self.dataloader.train_items
            + self.dataloader.val_items
            + self.dataloader.test_items
        ):
            task_type = str(item.get("task_type") or "default")
            if task_type not in seen:
                seen.append(task_type)
        return seen or ["default"]
