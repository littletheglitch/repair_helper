# test_power_rails

Reserved for per-rail test fixtures. The actual rail-engine tests
currently live in `Tests/run_all_tests.py`
(`TestEngineWorkflow` and `TestCommonFailureScenarios`).

Add per-rail fixtures here as the rule set grows — one file per rail
family, asserting the rule chain the engine should walk for each
fault mode.
