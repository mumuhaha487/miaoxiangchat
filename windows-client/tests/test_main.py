from vmss_agent.main import arguments


def test_self_test_argument_accepts_report_path():
    options = arguments(["--self-test", "report.json"])

    assert options.self_test == "report.json"

