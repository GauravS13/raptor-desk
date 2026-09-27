from core import actions


def test_every_ui_action_has_an_api_operation() -> None:
    assert actions.parity_gaps() == []


def test_parity_check_detects_a_missing_api_operation(monkeypatch) -> None:
    monkeypatch.setattr(actions, "_UI", {})
    monkeypatch.setattr(actions, "_API", {})

    @actions.ui_action("demo.only_in_ui")
    def only_ui() -> None: ...

    @actions.ui_action("demo.both")
    def ui_both() -> None: ...

    @actions.api_action("demo.both")
    def api_both() -> None: ...

    assert actions.parity_gaps() == ["demo.only_in_ui"]
    assert ui_both.action_name == "demo.both"
