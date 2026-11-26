from research.model_catalog import parse_model_id, resolve_adaptive_model

ROSTER = [
    'gemini-4-argon-medium',
    'gemini-4-argon-high',
    'gemini-4-argon-low',
    'gemini-4-hydrogen-high',
    'gemini-4-hydrogen-xhigh',
    'gemini-4-hydrogen-medium',
    'gemini-4-hydrogen-low',
    'gemini-pro-orchestrator-g1-pro',
    'gemini-3.8-flash-high',
    'claude-opus-5-5-low',
    'claude-opus-5-5-medium',
    'claude-opus-5-5-high',
    'claude-sonnet-5-5-high',
    'gpt-oss-120b-medium',
]


def test_opus_level_model_is_prioritized_over_newer_non_opus_families():
    assert resolve_adaptive_model(ROSTER) == 'claude-opus-5-5-high'
    assert resolve_adaptive_model(ROSTER, preferred_tier='medium') == 'claude-opus-5-5-medium'


def test_falls_back_to_highest_generation_when_no_opus_available():
    non_opus = [m for m in ROSTER if 'opus' not in m]
    assert resolve_adaptive_model(non_opus) == 'claude-sonnet-5-5-high'
    gemini_only = [m for m in non_opus if m.startswith('gemini')]
    assert resolve_adaptive_model(gemini_only) == 'gemini-4-hydrogen-high'


def test_excludes_orchestrator_and_embedding_pseudo_models():
    assert resolve_adaptive_model(['gemini-pro-orchestrator-g1-pro', 'text-embedding-004']) is None


def test_strips_models_prefix_and_compares_release_segments_numerically():
    roster = ['models/gemini-2.5-pro', 'models/claude-opus-4-9-high', 'models/claude-opus-4-10-high']
    assert resolve_adaptive_model(roster) == 'claude-opus-4-10-high'
    spec = parse_model_id('models/claude-opus-5-5-high')
    assert spec is not None and spec.is_opus and spec.generation == (5, 5)
