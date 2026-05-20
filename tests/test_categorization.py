"""Unit tests for app.services.categorization_service.

Mocks ai_client.call_chat and mongodb_service so no real backends are touched.
Live round-trip is in tests/integration/test_categorization_live.py.
"""

import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from app.services import categorization_service as cs


def _make_docs(n, prefix="doc"):
    """Build n minimally-shaped summarized doc records."""
    return [
        {
            "_id": f"{prefix}{i:03d}",
            "filename": f"{prefix}{i:03d}.pdf",
            "title": f"Title {i}",
            "short_summary": f"Summary for {prefix} number {i}.",
            "keywords": [f"kw{i}", "shared"],
            "document_type": "report",
        }
        for i in range(n)
    ]


def _ai_categories_response(names_and_descs):
    return {"raw_text": json.dumps({"categories": [
        {"name": n, "description": d} for n, d in names_and_descs
    ]})}


def _ai_assignments_response(assignment_map):
    return {"raw_text": json.dumps({"assignments": [
        {"id": doc_id, "category": cat} for doc_id, cat in assignment_map.items()
    ]})}


# --- Pure helpers ----------------------------------------------------------


class TestSeededSample:
    def test_short_list_returns_unchanged(self):
        items = list(range(5))
        assert cs._seeded_sample(items, 10, seed_key="x") == items

    def test_deterministic_across_calls(self):
        items = list(range(500))
        a = cs._seeded_sample(items, 50, seed_key="https://example.com/scan-1")
        b = cs._seeded_sample(items, 50, seed_key="https://example.com/scan-1")
        assert a == b

    def test_different_seed_different_sample(self):
        items = list(range(500))
        a = cs._seeded_sample(items, 50, seed_key="scan-A")
        b = cs._seeded_sample(items, 50, seed_key="scan-B")
        assert a != b


class TestTokenBudget:
    def test_short_prompt_passes(self):
        cs._check_token_budget("label", "x" * 100)  # ~25 tokens — fine

    def test_soft_warn_above_threshold_below_hard_cap_does_not_raise(self):
        # Sits between SOFT_TOKEN_WARN and HARD_TOKEN_CAP — must log a warning
        # internally but not raise. (We don't assert on the log here; caplog is
        # disabled by -p no:logging in many CI invocations of this project.)
        prompt = "x" * (cs.SOFT_TOKEN_WARN * 4 + 100)
        cs._check_token_budget("label", prompt)

    def test_hard_cap_raises(self):
        prompt = "x" * (cs.HARD_TOKEN_CAP * 4 + 100)
        with pytest.raises(cs.CategorizationError, match="hard cap"):
            cs._check_token_budget("label", prompt)


# --- JSON parsing ----------------------------------------------------------


class TestParseCategoriesJSON:
    def test_strips_markdown_fence(self):
        raw = "```json\n" + json.dumps({"categories": [
            {"name": f"Cat {i}", "description": f"d{i}"} for i in range(5)
        ]}) + "\n```"
        cats = cs._parse_categories_json(raw)
        assert len(cats) == 5

    def test_rejects_under_min(self):
        raw = json.dumps({"categories": [{"name": "A", "description": "d"}]})
        with pytest.raises(cs.CategorizationError, match=f"{cs.MIN_CATEGORIES}"):
            cs._parse_categories_json(raw)

    def test_rejects_over_max(self):
        raw = json.dumps({"categories": [
            {"name": f"Cat {i}", "description": "d"} for i in range(cs.MAX_CATEGORIES + 5)
        ]})
        with pytest.raises(cs.CategorizationError, match=f"{cs.MAX_CATEGORIES}"):
            cs._parse_categories_json(raw)

    def test_skips_invalid_entries(self):
        # 4 valid + 2 invalid → only 4 categories survive → fails MIN check
        raw = json.dumps({"categories": [
            {"name": "A", "description": "d"},
            {"name": "B", "description": "d"},
            {"name": "C", "description": "d"},
            {"name": "D", "description": "d"},
            {"name": "", "description": "no name"},
            {"name": "E", "description": ""},
        ]})
        with pytest.raises(cs.CategorizationError):
            cs._parse_categories_json(raw)

    def test_deduplicates_case_insensitive(self):
        raw = json.dumps({"categories": [
            {"name": "Reports", "description": "d1"},
            {"name": "reports", "description": "d2"},  # dup
            {"name": "Memos", "description": "d3"},
            {"name": "Letters", "description": "d4"},
            {"name": "Notes", "description": "d5"},
            {"name": "Plans", "description": "d6"},
        ]})
        cats = cs._parse_categories_json(raw)
        names = [c["name"] for c in cats]
        assert len(names) == 5  # one dup dropped, rest kept
        assert "Reports" in names and "reports" not in names

    def test_unparseable_json_raises(self):
        with pytest.raises(cs.CategorizationError, match="unparseable"):
            cs._parse_categories_json("not json at all")

    def test_missing_top_level_key_raises(self):
        with pytest.raises(cs.CategorizationError, match="missing 'categories'"):
            cs._parse_categories_json(json.dumps({"foo": []}))


class TestParseAssignmentsJSON:
    def test_basic_round_trip(self):
        raw = json.dumps({"assignments": [
            {"id": "d1", "category": "A"},
            {"id": "d2", "category": "B"},
        ]})
        result = cs._parse_assignments_json(raw, expected_ids=["d1", "d2"], valid_names=["A", "B"])
        assert result == {"d1": "A", "d2": "B"}

    def test_hallucinated_category_becomes_other(self):
        raw = json.dumps({"assignments": [
            {"id": "d1", "category": "MadeUp"},
        ]})
        result = cs._parse_assignments_json(raw, expected_ids=["d1"], valid_names=["A", "B"])
        assert result == {"d1": "Other"}

    def test_missing_id_filled_with_other(self):
        raw = json.dumps({"assignments": [{"id": "d1", "category": "A"}]})
        result = cs._parse_assignments_json(raw, expected_ids=["d1", "d2", "d3"], valid_names=["A"])
        assert result == {"d1": "A", "d2": "Other", "d3": "Other"}

    def test_case_insensitive_category_match(self):
        raw = json.dumps({"assignments": [{"id": "d1", "category": "financial reports"}]})
        result = cs._parse_assignments_json(raw, expected_ids=["d1"], valid_names=["Financial Reports"])
        assert result == {"d1": "Financial Reports"}  # canonical casing preserved

    def test_other_explicit_passes_through(self):
        raw = json.dumps({"assignments": [{"id": "d1", "category": "Other"}]})
        result = cs._parse_assignments_json(raw, expected_ids=["d1"], valid_names=["A"])
        assert result == {"d1": "Other"}


# --- Quality evaluation ----------------------------------------------------


class TestEvaluateQuality:
    def _cats(self, names):
        return [{"name": n, "description": "d"} for n in names]

    def test_balanced_distribution_ok(self):
        cats = self._cats(["A", "B", "C"])
        assignments = {f"d{i}": ("A" if i < 4 else "B" if i < 8 else "C") for i in range(12)}
        q = cs.evaluate_quality(assignments, cats, total_docs=12)
        assert q["ok"] is True
        assert q["signals"] == []

    def test_singleton_signal(self):
        cats = self._cats(["A", "B", "C"])
        assignments = {"d1": "A", "d2": "A", "d3": "A", "d4": "B", "d5": "C"}
        # Add 95 more docs in A so C is < 1%
        for i in range(95):
            assignments[f"x{i}"] = "A"
        q = cs.evaluate_quality(assignments, cats, total_docs=100)
        assert q["ok"] is False
        assert any("singleton" in s for s in q["signals"])

    def test_mega_signal(self):
        cats = self._cats(["A", "B", "C"])
        assignments = {f"d{i}": "A" for i in range(70)}
        for i in range(15):
            assignments[f"x{i}"] = "B"
        for i in range(15):
            assignments[f"y{i}"] = "C"
        q = cs.evaluate_quality(assignments, cats, total_docs=100)
        assert any("mega" in s and "'A'" in s for s in q["signals"])

    def test_high_other_signal(self):
        cats = self._cats(["A", "B", "C", "D", "E"])
        assignments = {f"o{i}": "Other" for i in range(30)}
        for i in range(70):
            assignments[f"d{i}"] = ["A", "B", "C", "D", "E"][i % 5]
        q = cs.evaluate_quality(assignments, cats, total_docs=100)
        assert any("uncategorized" in s for s in q["signals"])

    def test_imbalance_signal(self):
        cats = self._cats(["Big", "Small"])
        assignments = {f"big{i}": "Big" for i in range(50)}  # 50% but not >50%
        assignments["small1"] = "Small"
        assignments["small2"] = "Small"
        # 50 / 2 = 25 ratio → trips imbalance, but 50/52 < MEGA_FRACTION
        q = cs.evaluate_quality(assignments, cats, total_docs=52)
        assert any("imbalance" in s for s in q["signals"])

    def test_no_docs_returns_not_ok(self):
        q = cs.evaluate_quality({}, self._cats(["A", "B", "C", "D", "E"]), total_docs=0)
        assert q["ok"] is False


# --- Async orchestration ---------------------------------------------------


class TestPropose:
    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    async def test_calls_ai_with_propose_system_prompt(self, mock_call):
        mock_call.return_value = _ai_categories_response([
            (f"Cat{i}", f"desc{i}") for i in range(5)
        ])
        docs = _make_docs(20)
        cats = await cs.propose_categories(docs, seed_key="scan-X")
        assert len(cats) == 5
        sys_arg = mock_call.call_args.args[0] if mock_call.call_args.args else mock_call.call_args.kwargs["system_prompt"]
        # First positional arg is the system prompt
        assert "information architect" in sys_arg

    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    async def test_propagates_ai_error(self, mock_call):
        mock_call.return_value = {"error": "HTTP 500"}
        with pytest.raises(cs.CategorizationError, match="PROPOSE call failed"):
            await cs.propose_categories(_make_docs(20), seed_key="x")


class TestAssignDocuments:
    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    async def test_batches_and_aggregates(self, mock_call):
        # 120 docs at PHASE2_BATCH_SIZE=50 → 3 batches (50, 50, 20)
        docs = _make_docs(120)
        categories = [{"name": "A", "description": "d"}, {"name": "B", "description": "d"}]

        def _respond(*args, **kwargs):
            # Each call gets the user_prompt as args[1]; parse the doc ids out of it
            user_prompt = args[1]
            ids_in_prompt = [line.split(" | ", 1)[0] for line in user_prompt.split("\n") if line.startswith("doc")]
            return _ai_assignments_response({i: "A" for i in ids_in_prompt})
        mock_call.side_effect = _respond

        result = await cs.assign_documents(docs, categories)
        assert len(result) == 120
        assert all(v == "A" for v in result.values())
        assert mock_call.call_count == 3

    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    async def test_failed_batch_buckets_to_other(self, mock_call):
        docs = _make_docs(50)
        categories = [{"name": f"Cat{i}", "description": "d"} for i in range(5)]
        mock_call.return_value = {"error": "HTTP 500"}
        result = await cs.assign_documents(docs, categories)
        assert len(result) == 50
        assert all(v == "Other" for v in result.values())

    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    async def test_emits_progress_events(self, mock_call):
        docs = _make_docs(75)  # 2 batches at PHASE2_BATCH_SIZE=50
        categories = [{"name": "A", "description": "d"}, {"name": "B", "description": "d"}]
        mock_call.side_effect = lambda *a, **k: _ai_assignments_response({"x": "A"})

        events = []
        async def collect(event_type, payload):
            events.append((event_type, payload))

        await cs.assign_documents(docs, categories, emit=collect)
        progress = [e for e in events if e[0] == "phase_progress"]
        assert len(progress) == 2  # one per batch completion
        assert progress[-1][1]["completed"] == 2
        assert progress[-1][1]["total"] == 2


class TestRefineCategories:
    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    async def test_includes_signals_in_prompt(self, mock_call):
        mock_call.return_value = _ai_categories_response([
            (f"Refined{i}", f"d{i}") for i in range(5)
        ])
        current = [{"name": "Mega", "description": "d"}]
        quality = {
            "ok": False,
            "signals": ["mega: 'Mega' holds 80/100"],
            "distribution": {"Mega": 80, "Other": 20},
            "other_count": 20,
        }
        await cs.refine_categories(current, quality, _make_docs(20), seed_key="x")
        user_prompt = mock_call.call_args.args[1]
        assert "mega: 'Mega'" in user_prompt
        assert "Mega" in user_prompt  # current proposal echoed


class TestRunPipeline:
    @patch.object(cs.mongodb_service, "get_summarized_documents_for_scan")
    async def test_aborts_when_ai_not_configured(self, mock_docs):
        mock_docs.return_value = _make_docs(20)
        with patch.object(cs, "runtime_settings") as mock_rs:
            mock_rs.ai_api_url = ""
            mock_rs.ai_api_key = ""
            mock_rs.ai_model = ""
            with pytest.raises(cs.CategorizationError, match="not configured"):
                await cs.run_categorization_pipeline("https://x")

    @patch.object(cs.mongodb_service, "get_summarized_documents_for_scan")
    async def test_aborts_when_too_few_docs(self, mock_docs):
        mock_docs.return_value = _make_docs(cs.MIN_SUMMARIZED_DOCS - 1)
        with patch.object(cs, "runtime_settings") as mock_rs:
            mock_rs.ai_api_url = "https://x"
            mock_rs.ai_api_key = "k"
            mock_rs.ai_model = "m"
            with pytest.raises(cs.CategorizationError, match="at least"):
                await cs.run_categorization_pipeline("https://x")

    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    @patch.object(cs.mongodb_service, "get_summarized_documents_for_scan")
    async def test_happy_path_converges_in_one_iteration(self, mock_docs, mock_call):
        # 15 docs, AI proposes 5 perfectly balanced categories that all get used
        docs = _make_docs(15)
        mock_docs.return_value = docs

        # Categories that will produce a balanced distribution
        category_response = _ai_categories_response([("A", "d"), ("B", "d"), ("C", "d"), ("D", "d"), ("E", "d")])
        # 3 docs per category → no signal trips
        balanced_assignment = {}
        for i, doc in enumerate(docs):
            balanced_assignment[doc["_id"]] = ["A", "B", "C", "D", "E"][i % 5]
        assignment_response = _ai_assignments_response(balanced_assignment)

        # First call = propose, second call = assign
        mock_call.side_effect = [category_response, assignment_response]

        with patch.object(cs, "runtime_settings") as mock_rs:
            mock_rs.ai_api_url = "https://x"
            mock_rs.ai_api_key = "k"
            mock_rs.ai_model = "test-model"
            result = await cs.run_categorization_pipeline("https://scan")

        assert result["iterations_used"] == 1
        assert result["quality"]["ok"] is True
        assert result["doc_count"] == 15
        assert result["model"] == "test-model"
        assert set(result["assignments"].values()) == {"A", "B", "C", "D", "E"}

    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    @patch.object(cs.mongodb_service, "get_summarized_documents_for_scan")
    async def test_iterates_when_first_attempt_has_signals(self, mock_docs, mock_call):
        docs = _make_docs(20)
        mock_docs.return_value = docs

        # First propose returns categories
        cats1 = _ai_categories_response([("A", "d"), ("B", "d"), ("C", "d"), ("D", "d"), ("E", "d")])
        # First assignment puts all 20 into A → mega signal trips
        assign1 = _ai_assignments_response({d["_id"]: "A" for d in docs})
        # Refine returns a new category list
        cats2 = _ai_categories_response([("X1", "d"), ("X2", "d"), ("X3", "d"), ("X4", "d"), ("X5", "d")])
        # Second assignment balances
        assign2 = _ai_assignments_response({
            d["_id"]: f"X{(i % 5) + 1}" for i, d in enumerate(docs)
        })

        mock_call.side_effect = [cats1, assign1, cats2, assign2]

        with patch.object(cs, "runtime_settings") as mock_rs:
            mock_rs.ai_api_url = "https://x"
            mock_rs.ai_api_key = "k"
            mock_rs.ai_model = "m"
            result = await cs.run_categorization_pipeline("https://scan")

        assert result["iterations_used"] == 2
        assert result["quality"]["ok"] is True
        assert mock_call.call_count == 4  # propose + assign + refine + assign

    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    @patch.object(cs.mongodb_service, "get_summarized_documents_for_scan")
    async def test_returns_best_iteration_when_cap_hit(self, mock_docs, mock_call):
        docs = _make_docs(20)
        mock_docs.return_value = docs

        # Every iteration puts everything in A (mega signal always trips)
        cats = _ai_categories_response([("A", "d"), ("B", "d"), ("C", "d"), ("D", "d"), ("E", "d")])
        assign = _ai_assignments_response({d["_id"]: "A" for d in docs})

        # 1 propose + 1 assign + (refine + assign) * (CAP - 1) calls
        side_effects = [cats, assign]
        for _ in range(cs.ITERATION_CAP - 1):
            side_effects.extend([cats, assign])
        mock_call.side_effect = side_effects

        with patch.object(cs, "runtime_settings") as mock_rs:
            mock_rs.ai_api_url = "https://x"
            mock_rs.ai_api_key = "k"
            mock_rs.ai_model = "m"
            result = await cs.run_categorization_pipeline("https://scan")

        assert result["iterations_used"] == cs.ITERATION_CAP
        assert result["quality"]["ok"] is False
        assert any("mega" in s for s in result["quality"]["signals"])

    @patch.object(cs.ai_client, "call_chat", new_callable=AsyncMock)
    @patch.object(cs.mongodb_service, "get_summarized_documents_for_scan")
    async def test_emits_lifecycle_events(self, mock_docs, mock_call):
        docs = _make_docs(15)
        mock_docs.return_value = docs
        mock_call.side_effect = [
            _ai_categories_response([("A", "d"), ("B", "d"), ("C", "d"), ("D", "d"), ("E", "d")]),
            _ai_assignments_response({d["_id"]: ["A", "B", "C", "D", "E"][i % 5] for i, d in enumerate(docs)}),
        ]

        events = []
        async def collect(event_type, payload):
            events.append(event_type)

        with patch.object(cs, "runtime_settings") as mock_rs:
            mock_rs.ai_api_url = "https://x"
            mock_rs.ai_api_key = "k"
            mock_rs.ai_model = "m"
            await cs.run_categorization_pipeline("https://scan", emit=collect)

        assert "iteration_start" in events
        assert "proposal" in events
        assert "phase_progress" in events
        assert "quality_report" in events
        assert "final" in events
