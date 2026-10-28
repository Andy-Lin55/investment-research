"""Guard the boundary between the existing service and the opt-in pilot."""
import hashlib,re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_research_workflow_cannot_inherit_legacy_production_credentials():
    workflow=(ROOT/'.github/workflows/holdings_research.yml').read_text()
    names=set(re.findall(r'secrets\.([A-Z0-9_]+)',workflow))
    legacy={'NOTION_TOKEN','DMXAPI_KEY','GEMINI_API_KEY','TUSHARE_TOKEN','FINNHUB_KEY',
            'SMTP_USER','SMTP_PASS','SMTP_HOST','BRIEFING_TO_EMAIL','BRIEFING_FROM_EMAIL'}
    assert not names.intersection(legacy)
    assert {'RESEARCH_NOTION_TOKEN','RESEARCH_DMXAPI_KEY','RESEARCH_GEMINI_API_KEY'}<=names


def test_daily_and_weekly_workflows_are_unchanged_from_approved_baseline():
    expected={'.github/workflows/daily_hk_us.yml':'89d1603a57bfafd9669035290e9fdfd31ed04fe054df8738b901830cf2c5ecc7',
              '.github/workflows/weekly_financials.yml':'b8489f05295939382ae40e01ca8a8be413f8ff70e96440058331f83e36354713'}
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest for path,digest in expected.items())


def test_research_and_regression_keep_explicit_execution_boundaries():
    pilot=(ROOT/'.github/workflows/holdings_research.yml').read_text()
    regression=(ROOT/'.github/workflows/regression.yml').read_text()
    assert "github.ref == 'refs/heads/main'" in pilot
    assert "vars.HOLDINGS_RESEARCH_ENABLED == 'true'" in pilot
    assert 'environment: research-pilot' in pilot
    assert 'group: holdings-research-single-publisher' in pilot
    assert 'secrets.' not in regression
    assert 'contents: read' in regression and 'pull_request_target' not in regression
