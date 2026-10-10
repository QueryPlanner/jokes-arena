"""Generate a bounded, anonymous OpenRouter joke pool without extra packages."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODELS = ("openai/gpt-5-mini", "anthropic/claude-haiku-4.5")
WORDS = ("calendar", "coffee", "umbrella")
MAX_CALLS = 6
MAX_TOKENS = 1500


def prompt(word):
    if not re.fullmatch(r"[a-z]+", word):
        raise ValueError("Use a single lowercase English word")
    return (
        f'Write one original joke in English using the exact word "{word}". '
        "The word must contribute meaningfully to the joke. Use at most 40 words. "
        "Use everyday situations without country-specific references. "
        "Return only the joke, without a title, explanation, or quotation wrapper."
    )


def payload(model, word, options=None):
    result = {
        "model": model,
        "messages": [{"role": "user", "content": prompt(word)}],
        "max_tokens": MAX_TOKENS,
        "reasoning": (
            {"effort": "minimal", "exclude": True}
            if model == MODELS[0]
            else {"enabled": False}
        ),
    }
    if options is not None:
        result.update(options)
    return result


def request_completion(key, model, word, options=None):
    request = Request(
        ENDPOINT,
        data=json.dumps(payload(model, word, options)).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "Jokes Arena preview",
        },
        method="POST",
    )
    try:
        # urllib does not retry failed requests. Never include upstream bodies
        # or request headers in errors, since they may contain sensitive data.
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as error:
        raise ValueError(f"OpenRouter HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("OpenRouter request failed or timed out") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("OpenRouter returned invalid JSON") from None


def validate(response, model, word):
    if not isinstance(response, dict) or response.get("model") != model:
        raise ValueError("Returned model does not match the requested model")
    try:
        choice = response["choices"][0]
        text = choice["message"]["content"]
        finish = choice["finish_reason"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("OpenRouter returned an invalid completion shape") from None
    if finish != "stop":
        raise ValueError("Completion did not finish normally")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Completion contains no joke text")
    text = text.strip()
    if len(text.split()) > 40:
        raise ValueError("Joke exceeds the 40-word limit")
    if not re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE):
        raise ValueError("Joke is missing the required word")
    return text


def generate(key, words, receipt):
    if not key:
        raise ValueError("OPENROUTER_API_KEY is required")
    if not words or len(words) * len(MODELS) > MAX_CALLS:
        raise ValueError("Batch must contain one to three words")
    for word in words:
        prompt(word)
    pairs = []
    calls = 0
    with receipt.open("x", encoding="utf-8") as log:
        for word in words:
            jokes = []
            for model in MODELS:
                calls += 1
                record = {"word": word, "requested_model": model, "call": calls}
                try:
                    response = request_completion(key, model, word)
                    text = validate(response, model, word)
                except ValueError as error:
                    record.update(status="failed", error=str(error))
                    log.write(json.dumps(record) + "\n")
                    log.flush()
                    raise
                record.update(
                    status="validated",
                    returned_model=response["model"],
                    joke=text,
                    usage=response.get("usage"),
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )
                log.write(json.dumps(record) + "\n")
                log.flush()
                jokes.append(text)
            # Public data contains no model IDs or provenance fields.
            if secrets.randbelow(2):
                jokes.reverse()
            pairs.append({"word": word, "jokes": jokes})
    return pairs, calls


def export(pairs, output):
    with output.open("x", encoding="utf-8") as target:
        target.write("window.JOKES_ARENA_PAIRS = " + json.dumps(pairs) + ";\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--words", nargs="+", default=list(WORDS))
    args = parser.parse_args()
    if args.output.exists() or args.receipt.exists():
        parser.error("Output and receipt must be new files")
    try:
        pairs, calls = generate(os.environ.get("OPENROUTER_API_KEY", ""), args.words, args.receipt)
        export(pairs, args.output)
    except (ValueError, OSError) as error:
        print(f"Generation stopped: {error}", file=sys.stderr)
        return 1
    print(f"Generated {len(pairs)} real model pairs using {calls} OpenRouter requests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
