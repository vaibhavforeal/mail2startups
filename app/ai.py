import anthropic
from pydantic import BaseModel

from app.config import get_settings

MAX_INPUT_CHARS = 200_000


class ExtractedStartup(BaseModel):
    name: str
    website: str | None = None
    description: str | None = None


class StartupList(BaseModel):
    startups: list[ExtractedStartup]


_EXTRACT_PROMPT = (
    "The following is the text of a web article listing startups. Extract every "
    "startup company mentioned. For each, give its name, its website URL if the "
    "article states or clearly implies one (otherwise null), and a one-sentence "
    "description from the article. Do not invent companies or URLs.\n\n"
    "ARTICLE TEXT:\n{text}"
)


def extract_startups_from_text(text: str, client=None) -> list[ExtractedStartup]:
    client = client or anthropic.Anthropic()
    response = client.messages.parse(
        model=get_settings().anthropic_model,
        max_tokens=8192,
        messages=[{"role": "user",
                   "content": _EXTRACT_PROMPT.format(text=text[:MAX_INPUT_CHARS])}],
        output_format=StartupList,
    )
    if response.parsed_output is None:
        return []
    return response.parsed_output.startups
