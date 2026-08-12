import anthropic

from app.inbox.classify import classify_reply
from app.models import ReplyLabel


class _Content:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Content(text)]


class _Messages:
    def __init__(self, text=None, exc=None):
        self._text, self._exc = text, exc
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return _Resp(self._text)


class _Client:
    def __init__(self, text=None, exc=None):
        self.messages = _Messages(text, exc)


def test_classify_maps_valid_label():
    assert classify_reply(_Client(text="interested"), "yes let's talk",
                          model="m") is ReplyLabel.INTERESTED
    assert classify_reply(_Client(text="rejection"), "no thanks",
                          model="m") is ReplyLabel.REJECTION
    assert classify_reply(_Client(text="auto-reply"), "out of office",
                          model="m") is ReplyLabel.AUTO_REPLY


def test_classify_unknown_label_falls_back_to_other():
    assert classify_reply(_Client(text="banana"), "hmm", model="m") is ReplyLabel.OTHER


def test_classify_error_falls_back_to_other():
    client = _Client(exc=anthropic.AnthropicError("boom"))
    assert classify_reply(client, "anything", model="m") is ReplyLabel.OTHER
    assert client.messages.calls == 1
