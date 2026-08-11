from types import SimpleNamespace

from app.ai import ExtractedStartup, StartupList, extract_startups_from_text


class FakeMessages:
    def __init__(self, result):
        self._result = result
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(parsed_output=self._result, stop_reason="end_turn")


def test_extract_returns_startups():
    expected = StartupList(startups=[
        ExtractedStartup(name="Acme", website="https://acme.com", description="widgets"),
    ])
    fake_client = SimpleNamespace(messages=FakeMessages(expected))
    result = extract_startups_from_text("Top startups: 1. Acme (acme.com) ...",
                                        client=fake_client)
    assert result == expected.startups
    kwargs = fake_client.messages.last_kwargs
    assert kwargs["output_format"] is StartupList
    assert "Top startups" in kwargs["messages"][0]["content"]


def test_extract_truncates_huge_input():
    expected = StartupList(startups=[])
    fake_client = SimpleNamespace(messages=FakeMessages(expected))
    extract_startups_from_text("x" * 500_000, client=fake_client)
    sent = fake_client.messages.last_kwargs["messages"][0]["content"]
    assert len(sent) < 250_000
