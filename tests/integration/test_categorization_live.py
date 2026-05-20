"""End-to-end categorization against real OpenAI + real MongoDB (M3).

Stores a fixture corpus of 20 deliberately mixed-topic documents with pre-baked
summaries (no AI summarization step needed for the test), runs the full pipeline,
and asserts that the output is sane: produces 5-15 categories, balanced or close,
and assigns every doc.

Skips if AI or MongoDB isn't configured.
"""

import uuid

import pytest

from app.services import categorization_service as cs
from app.services import mongodb_service
from app.services.settings_service import runtime_settings

pytestmark = [pytest.mark.integration, pytest.mark.live_ai]


# A small but topically diverse corpus. 5 themes × 4 docs each = 20 docs.
# Chosen to give the model clear category structure to find.
_FIXTURES = [
    # --- Financial reports ---
    ("Q1 2026 Earnings Report",
     "Quarterly revenue grew 14% YoY to $245M. Operating margin held at 22%. "
     "Three new product lines contributed $18M in their first full quarter.",
     ["earnings", "quarterly", "revenue", "finance"]),
    ("FY25 Annual Report",
     "Full-year revenue of $920M, up 11% from prior year. Net income $138M. "
     "Capital expenditure of $84M tracked to plan with no material variances.",
     ["annual", "report", "fiscal-year", "finance"]),
    ("2026 Budget Forecast",
     "Operating budget projects $1.05B revenue at 23% margin. Hiring plan adds "
     "180 net heads. R&D spend rises to 14% of revenue.",
     ["budget", "forecast", "planning", "finance"]),
    ("Investor Presentation Q4 2025",
     "Slide deck for analyst day. Highlights growth trajectory, new market entries, "
     "and revised guidance for FY26.",
     ["investor", "presentation", "guidance"]),
    # --- Product datasheets ---
    ("WidgetPro 3000 Product Datasheet",
     "Specifications for the WidgetPro 3000 industrial controller. 16-channel I/O, "
     "PoE+, IP67 rating, operating temp -40 to +85C.",
     ["datasheet", "specifications", "product", "hardware"]),
    ("Sensor Module X12 Specification",
     "Technical specification for the X12 sensor module. Sample rate 10kHz, "
     "I2C/SPI interface, 3.3V operation, 12-bit resolution.",
     ["specification", "sensor", "hardware"]),
    ("CloudConnect Gateway Specs",
     "Hardware specifications for the CloudConnect Gateway. Dual gigabit ethernet, "
     "4G LTE, edge compute capability, ARM Cortex-A72 CPU.",
     ["gateway", "specs", "networking", "hardware"]),
    ("PowerCell Battery Datasheet",
     "Specification sheet for the PowerCell 12V battery pack. 100Ah capacity, "
     "2000+ cycle life, BMS integrated, UL certified.",
     ["battery", "datasheet", "specifications"]),
    # --- Marketing / whitepapers ---
    ("The Future of Edge Computing — Whitepaper",
     "Industry whitepaper exploring trends in edge compute deployment. Covers "
     "5G, latency-sensitive workloads, and the convergence of IT and OT.",
     ["whitepaper", "edge-computing", "trends"]),
    ("Customer Success Story: Acme Manufacturing",
     "Case study of how Acme Manufacturing reduced downtime 38% by deploying "
     "our predictive maintenance platform across 12 plants.",
     ["case-study", "customer", "marketing"]),
    ("Brand Positioning Guide 2026",
     "Updated brand guidelines covering visual identity, tone of voice, and key "
     "messaging pillars for the 2026 campaign refresh.",
     ["brand", "marketing", "guidelines"]),
    ("Product Launch Campaign Brief",
     "Marketing brief for the WidgetPro 3000 launch. Target segments, channel mix, "
     "creative direction, and KPI targets for the first 90 days.",
     ["campaign", "launch", "marketing"]),
    # --- Engineering / technical ---
    ("Firmware Update Protocol v2.3 Specification",
     "Engineering specification for over-the-air firmware update protocol. "
     "Covers signing, rollback, delta updates, and bootloader handoff.",
     ["engineering", "protocol", "firmware"]),
    ("Network Architecture Design Document",
     "Internal design document for the v4 network architecture. Segmentation, "
     "redundancy, and migration path from the v3 deployment.",
     ["architecture", "engineering", "networking"]),
    ("Test Coverage Report Q1 2026",
     "Engineering test coverage report. Backend at 78%, frontend at 64%, "
     "integration suites at 51%. Action items for raising coverage.",
     ["testing", "engineering", "quality"]),
    ("API Reference v3.0",
     "Technical API reference for v3.0 of the platform REST API. Authentication, "
     "rate limits, all resource endpoints with example payloads.",
     ["api", "reference", "engineering"]),
    # --- Legal / compliance ---
    ("Data Processing Addendum",
     "Standard data processing addendum for customer contracts. GDPR Article 28 "
     "compliant; covers sub-processors, security measures, and breach notification.",
     ["legal", "contract", "gdpr", "compliance"]),
    ("Privacy Policy Update Notice 2026",
     "Notification to users of an update to the privacy policy. Summarizes "
     "changes around third-party processors and data retention.",
     ["privacy", "policy", "legal"]),
    ("Vendor Security Questionnaire Response",
     "Completed security questionnaire for an enterprise customer. Covers "
     "SOC 2 controls, encryption posture, and incident response.",
     ["security", "compliance", "questionnaire"]),
    ("Master Services Agreement Template",
     "Standard MSA template for enterprise customers. Liability, IP ownership, "
     "termination terms, and service level commitments.",
     ["legal", "contract", "msa"]),
]


@pytest.fixture(scope="module", autouse=True)
def _prereqs():
    info = mongodb_service.test_connection()
    if not info["connected"]:
        pytest.skip(f"MongoDB not reachable: {info['message']}")
    if not runtime_settings.ai_api_url or not runtime_settings.ai_api_key or not runtime_settings.ai_model:
        pytest.skip("AI API not configured")


@pytest.fixture(scope="module")
def corpus():
    """Provision a scan with 20 pre-summarized documents; tear down after."""
    scan_url = f"https://example.test/cat-live-{uuid.uuid4().hex[:8]}"
    ids = []
    try:
        for title, short_summary, keywords in _FIXTURES:
            payload = title.encode()
            source_url = f"{scan_url}/{uuid.uuid4().hex[:8]}.pdf"
            doc_id, _ = mongodb_service.store_document(
                file_data=payload,
                filename=title.replace(" ", "_")[:50] + ".pdf",
                extension=".pdf",
                source_url=source_url,
                source_page=scan_url,
                scan_url=scan_url,
                file_size_bytes=len(payload),
                content_type="application/pdf",
                extracted_text=short_summary,  # so summary_status update has something
            )
            mongodb_service.update_summary(
                doc_id=doc_id,
                summary=short_summary + " (full summary)",
                keywords=keywords,
                document_type="report",
                model="fixture",
                title=title,
                short_summary=short_summary,
            )
            ids.append(doc_id)
        yield scan_url, ids
    finally:
        mongodb_service.delete_category_set(scan_url)
        for doc_id in ids:
            mongodb_service.delete_document(doc_id)


async def test_pipeline_produces_sane_categorization(corpus):
    scan_url, ids = corpus
    result = await cs.run_categorization_pipeline(scan_url)

    # Basic shape
    assert result["scan_url"] == scan_url
    assert result["doc_count"] == 20
    assert 1 <= result["iterations_used"] <= cs.ITERATION_CAP
    assert result["model"]

    # Categories in range
    cats = result["categories"]
    assert cs.MIN_CATEGORIES <= len(cats) <= cs.MAX_CATEGORIES
    for c in cats:
        assert c["name"].strip()
        assert c["description"].strip()

    # Every document was assigned to something
    assignments = result["assignments"]
    assert set(assignments.keys()) == set(ids)
    valid_names = {c["name"] for c in cats} | {"Other"}
    assert all(v in valid_names for v in assignments.values())

    # Distribution: most docs should be in real categories, not "Other"
    other_count = sum(1 for v in assignments.values() if v == "Other")
    assert other_count <= 6, f"Too many docs landed in Other: {other_count}/20"


async def test_accept_persists_result(corpus):
    scan_url, ids = corpus
    result = await cs.run_categorization_pipeline(scan_url)
    set_id = mongodb_service.accept_categorization(
        scan_url=scan_url,
        categories=result["categories"],
        assignments=result["assignments"],
        iterations_used=result["iterations_used"],
        model=result["model"],
    )
    assert isinstance(set_id, str) and set_id
    persisted = mongodb_service.get_category_set(scan_url)
    assert persisted is not None
    assert {c["name"] for c in persisted["categories"]} == {c["name"] for c in result["categories"]}
    live_counts = mongodb_service.get_category_counts(scan_url)
    # Every assignment shows up in the live count
    total_assigned = sum(c for k, c in live_counts.items() if k)
    assert total_assigned == 20
