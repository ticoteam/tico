"""Decisions (the Tico AI Gateway's Tico Decide 1.0 is the default provider, TypeSafe's Jev an optional own key; the
question format matches OpenRouter's Decisions API),
the one decision primitive the hub and every bot share. The module keeps its old name, `judge`.

One call takes a JSON `state` and a map of typed questions and returns a calibrated answer per
question, all in one round trip of about two hundred milliseconds. It writes no prose. That is
the whole contract, and it never changes:

    choice   pick one of named options      -> {"choice": name, "confidence": 0-1, "probabilities": {...}}
    score    place the state on ordered levels -> {"score": float, "confidence": 0-1, "probabilities": {...}}
    noul     is this statement true          -> {"noul": probability that the answer is yes}

Four ways to reach it, one signature (`engine(state, questions, label=None) -> result`):

    gateway(key, url)      POST the Tico AI Gateway's decisions endpoint with a gateway key, as
                           `tico-decide-1.0`; the hub server and the Slack gateway ask it first.
    direct(api_key)        POST https://api.typesafe.ai/v1/systemone with a customer's own TypeSafe
                           key; the server's second choice, never set by default.
    through_hub(client)    POST /api/v2/decisions on the hub with the turn's own credential; the
                           `hub decision ask` command, the `hub_decision_ask` MCP tool and the mail CLI use
                           this, so no bot ever holds the key and every call is audited.
    from_env()             whichever of the two the environment allows, or None.

The reusable part is not this module but the question sets in `questions/*.json`: a named,
versioned set of questions with the state fields it expects and the thresholds callers act at
(`load_set`). The hub knows nothing about them; a caller loads one and sends it inline. `label`
(a set's `id@version`) is how calls are grouped in the audit, so a month of judgments can be
read back per question set.

Pure stdlib on purpose: the runner venv, the mail venv, the cloud venv and the `hub` CLI all
import it.
"""
import contextlib
import contextvars
import hashlib
import json
import math
import os
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_ENV = "TYPESAFE_API_KEY"
# The Tico AI Gateway (Tico HQ): the same contract, answered as a Tico model. `tico-classify-1.0` is the Gateway's name
# for a choice-mode classify; the app asks as `tico-decide-1.0` and keeps whichever of the two the Gateway answers as.
GATEWAY_PROVIDER = "Tico AI Gateway"
GATEWAY_MODEL = "tico-decide-1.0"
GATEWAY_MODELS = ("tico-decide-1.0", "tico-classify-1.0")
GATEWAY_PATH = "/v1/gateway/decisions"
GATEWAY_TIMEOUT = 8
TYPESAFE_PROVIDER = "TypeSafe"
LLM_PROVIDERS = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google", "xai": "xAI",
                 "openrouter": "OpenRouter"}
TYPES = ("choice", "score", "noul")
QUESTIONS_DIR = Path(__file__).resolve().parents[1] / "questions"
_REGISTRY_DIR_FD = os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd

# What one call may carry. TypeSafe takes up to 255 options and 10 levels, and Jev 1.13 reads
# 32k tokens of state plus the longest question (64k for the whole call); the state limit here
# is that budget at roughly four characters a token, so an oversized call is refused before it
# is sent. The rest are ours.
MAX_QUESTIONS = 40
MAX_OPTIONS = 255
MIN_LEVELS, MAX_LEVELS = 2, 10
MAX_STATE_CHARS = 120_000
MAX_INSTRUCTIONS = 8_000
MAX_LABEL = 80
TIMEOUT = 20
RETRIES = 2
BACKOFF = (0.5, 1.5)
# Keys a question may carry beyond `type`; `dynamic` is ours (see `with_options`) and is stripped.
QUESTION_KEYS = {"type", "instructions", "criteria", "dynamic"}
# Instructions, an option's description, a level and a noul's true/false are "entries": a string,
# or JSON structure (an object of named parts, a list of things to check) when that reads more
# clearly; null for an option that needs no description.
ENTRY_TYPES = (str, dict, list)


class JudgeError(Exception):
    """The call could not be made or did not come back in the shape promised."""

    def __init__(self, code, detail, status=0, retryable=False):
        super().__init__(detail)
        self.code, self.detail, self.status, self.retryable = code, detail, status, retryable


# The codes a JudgeError carries. An audit row, a log line or an answer to anyone but the caller keeps only these,
# never `detail`: an upstream error can echo the request (a ticket, a Slack message, a saved post) back.
ERROR_CODES = frozenset({"auth", "http", "invalid", "not_found", "shape", "spend_cap", "unavailable", "unconfigured"})


def error_code(exc):
    """`exc`'s code when it is one of `ERROR_CODES`, else `unreadable`: the one part of a failed call that may be kept."""
    code = getattr(exc, "code", "")
    return code if isinstance(code, str) and code in ERROR_CODES else "unreadable"


def _paid(exc, model, usage):
    """`exc` for an answer that came back and could not be used: it was still paid for, so it carries `paid`, the
    model and usage that the server's monthly cap counts (backend/judge.py `metered`)."""
    exc.paid = [{"model": model, "usage": usage if isinstance(usage, dict) else {}}]
    return exc


# A chain of providers (backend/judge.py `ordered`) asks each under one deadline, so a slow one leaves the next only
# what is left, and the whole chain answers before its callers stop waiting. `clock` is the seam the tests use.
clock = time.monotonic
_DEADLINE = contextvars.ContextVar("tico_judge_deadline", default=None)
MIN_ATTEMPT = 1.0                           # seconds: with less left, a provider is not asked at all


@contextlib.contextmanager
def within(seconds):
    """Every provider asked in this block answers within `seconds` of its start, or within an outer block's end."""
    outer = _DEADLINE.get()
    token = _DEADLINE.set(min(clock() + seconds, outer) if outer is not None else clock() + seconds)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def time_left(timeout):
    """`timeout`, cut to what is left of the deadline; `unavailable`, asking nothing, when less than MIN_ATTEMPT is."""
    deadline = _DEADLINE.get()
    if deadline is None:
        return timeout
    left = deadline - clock()
    if left < MIN_ATTEMPT:
        raise JudgeError("unavailable", "no time was left to ask this provider", retryable=True)
    return min(timeout, left)


# ----------------------------------------------------------------------------- the contract
def validate(state, questions, label=None):
    """Refuse before the network does: the shape every transport checks the same way."""
    if not isinstance(questions, dict) or not questions:
        raise JudgeError("invalid", "questions must be a non-empty map of id -> question")
    if len(questions) > MAX_QUESTIONS:
        raise JudgeError("invalid", f"at most {MAX_QUESTIONS} questions in one call")
    for qid, q in questions.items():
        if not isinstance(qid, str) or not qid.strip():
            raise JudgeError("invalid", "every question id is a non-empty string")
        if not isinstance(q, dict):
            raise JudgeError("invalid", f"{qid}: a question is a map")
        extra = set(q) - QUESTION_KEYS
        if extra:
            raise JudgeError("invalid", f"{qid}: unknown keys {', '.join(sorted(extra))}")
        kind = q.get("type")
        if kind not in TYPES:
            raise JudgeError("invalid", f"{qid}: type must be one of {', '.join(TYPES)}")
        text = q.get("instructions")
        if not isinstance(text, ENTRY_TYPES) or not text or (isinstance(text, str) and not text.strip()):
            raise JudgeError("invalid", f"{qid}: instructions are required: a string, or an object or list of parts")
        if len(text if isinstance(text, str) else json.dumps(text, default=str)) > MAX_INSTRUCTIONS:
            raise JudgeError("invalid", f"{qid}: instructions over {MAX_INSTRUCTIONS} characters")
        criteria = q.get("criteria")
        if kind == "choice":
            if not isinstance(criteria, dict) or len(criteria) < 2:
                raise JudgeError("invalid", f"{qid}: a choice needs a criteria map of at least two options")
            if len(criteria) > MAX_OPTIONS:
                raise JudgeError("invalid", f"{qid}: at most {MAX_OPTIONS} options")
            if not all(isinstance(k, str) and k.strip() for k in criteria):
                raise JudgeError("invalid", f"{qid}: option names are non-empty strings")
            if not all(v is None or isinstance(v, ENTRY_TYPES) for v in criteria.values()):
                raise JudgeError("invalid", f"{qid}: an option's description is a string, structure, or null")
        elif kind == "score":
            if not isinstance(criteria, list) or not (MIN_LEVELS <= len(criteria) <= MAX_LEVELS):
                raise JudgeError("invalid", f"{qid}: a score needs {MIN_LEVELS} to {MAX_LEVELS} ordered levels")
            if not all(v is None or isinstance(v, ENTRY_TYPES) for v in criteria):
                raise JudgeError("invalid", f"{qid}: a level is a string, structure, or null")
        elif criteria is not None:
            if not isinstance(criteria, dict) or set(criteria) - {"true", "false"}:
                raise JudgeError("invalid", f"{qid}: a noul's criteria, if given, is a map of `true` and `false` descriptions")
            if not all(v is None or isinstance(v, ENTRY_TYPES) for v in criteria.values()):
                raise JudgeError("invalid", f"{qid}: a noul's true/false description is a string, structure, or null")
    try:
        size = len(json.dumps(state, default=str))
    except (TypeError, ValueError):
        raise JudgeError("invalid", "state must be JSON")
    if size > MAX_STATE_CHARS:
        raise JudgeError("invalid", f"state is {size} characters; the limit is {MAX_STATE_CHARS}")
    if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL):
        raise JudgeError("invalid", f"label is a string of at most {MAX_LABEL} characters")


def clean(questions):
    """The questions as TypeSafe sees them: our `dynamic` marker stripped."""
    return {qid: {k: v for k, v in q.items() if k != "dynamic"} for qid, q in questions.items()}


def check_answers(answers, questions):
    """Every question answered, in its type's shape; anything else is `JudgeError("shape")`."""
    if not isinstance(answers, dict):
        raise JudgeError("shape", "answers is not a map")
    for qid, q in questions.items():
        a = answers.get(qid)
        if not isinstance(a, dict):
            raise JudgeError("shape", f"{qid}: no answer")
        kind = q["type"]
        if kind == "choice":
            if not isinstance(a.get("choice"), str) or a["choice"] not in q["criteria"]:
                raise JudgeError("shape", f"{qid}: choice is not one of the options")
            if not _bounded(a.get("confidence"), 0, 1):
                raise JudgeError("shape", f"{qid}: confidence is not in [0,1]")
        if kind == "score":
            if not _bounded(a.get("score"), 0, len(q["criteria"]) - 1):
                raise JudgeError("shape", f"{qid}: score is outside its levels")
            if not _bounded(a.get("confidence"), 0, 1):
                raise JudgeError("shape", f"{qid}: confidence is not in [0,1]")
        if kind == "noul" and not _bounded(a.get("noul"), 0, 1):
            raise JudgeError("shape", f"{qid}: noul is not in [0,1]")
    return answers


def _bounded(value, low, high):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


# ----------------------------------------------------------------------------- transports
def direct(api_key, model=MODEL, timeout=TIMEOUT, opener=None, sleep=time.sleep, retries=RETRIES):
    """The TypeSafe API with a key. `opener(request, timeout)` is the seam the tests use. The server asks once,
    briefly, and falls back to the company's own model (backend/judge.py); a direct caller keeps the retries."""
    if not api_key:
        raise JudgeError("unconfigured", "no TypeSafe key")
    opener = opener or urllib.request.urlopen

    def engine(state, questions, label=None):
        validate(state, questions, label)
        body = json.dumps({"state": state, "model": model, "questions": clean(questions)},
                          default=str).encode()
        started = time.monotonic()
        for attempt in range(retries + 1):
            request = urllib.request.Request(URL, data=body, method="POST", headers={
                "Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                "Accept": "application/json"})
            try:
                with opener(request, timeout=time_left(timeout)) as response:
                    payload = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    detail = json.load(exc)
                except ValueError:
                    detail = {}
                if status == 401:
                    raise JudgeError("auth", "TypeSafe refused the key", 401)
                if status == 422:
                    raise JudgeError("invalid", str(detail.get("detail") or detail or "TypeSafe refused the request"), 422)
                if status in (429, 529) or status >= 500:
                    if attempt < retries:
                        sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                        continue
                    raise JudgeError("unavailable", f"TypeSafe answered {status}", status, retryable=True)
                raise JudgeError("http", f"TypeSafe answered {status}", status)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                if attempt < retries:
                    sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
                    continue
                raise JudgeError("unavailable", f"TypeSafe did not answer: {type(exc).__name__}", retryable=True)
        ms = round((time.monotonic() - started) * 1000)
        if not isinstance(payload, dict):
            raise JudgeError("shape", "TypeSafe answered with something other than an object")
        try:
            answers = check_answers(payload.get("answers"), questions)
        except JudgeError as exc:
            raise _paid(exc, str(payload.get("model") or model), payload.get("usage"))
        return {"model": str(payload.get("model") or model), "answers": answers,
                "usage": payload.get("usage") or {}, "ms": ms, "provider": TYPESAFE_PROVIDER}
    engine.model, engine.provider = model, TYPESAFE_PROVIDER
    return engine


def gateway(api_key, url, model=GATEWAY_MODEL, timeout=GATEWAY_TIMEOUT, opener=None):
    """The Tico AI Gateway with a gateway key: one try of at most `timeout` seconds, no retry (the Gateway makes none
    either; the server falls back instead). A refusal keeps the HTTP status and a code, never the Gateway's text.
    `opener(request, timeout)` is the seam the tests use."""
    if not api_key or not url:
        raise JudgeError("unconfigured", "no Tico AI Gateway key or URL")
    opener = opener or urllib.request.urlopen

    def engine(state, questions, label=None):
        validate(state, questions, label)
        body = {"state": state, "model": model, "questions": clean(questions)}
        if label:
            body["label"] = label
        request = urllib.request.Request(url, data=json.dumps(body, default=str).encode(), method="POST", headers={
            "Authorization": "Bearer " + api_key, "Content-Type": "application/json", "Accept": "application/json"})
        started = time.monotonic()
        try:
            with opener(request, timeout=time_left(timeout)) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status in (401, 403):                    # unknown or revoked key
                raise JudgeError("auth", f"the Tico AI Gateway refused the key ({status})", status)
            if status in (402, 429, 529) or status >= 500:  # the Gateway's cap, a rate limit, or down
                raise JudgeError("unavailable", f"the Tico AI Gateway answered {status}", status, retryable=True)
            # A 422 too: the request passed `validate` here, so the Gateway disagreeing is its problem, not the
            # caller's, and the next provider is asked.
            raise JudgeError("http", f"the Tico AI Gateway answered {status}", status)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise JudgeError("unavailable", f"the Tico AI Gateway did not answer: {type(exc).__name__}",
                             retryable=True)
        ms = round((time.monotonic() - started) * 1000)
        if not isinstance(payload, dict):
            raise JudgeError("shape", "the Tico AI Gateway answered with something other than an object")
        answered = payload.get("model") if payload.get("model") in GATEWAY_MODELS else model
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        try:
            answers = check_answers(payload.get("answers"), questions)
        except JudgeError as exc:
            raise _paid(exc, answered, usage)
        return {"model": answered, "answers": answers, "usage": usage, "ms": ms, "provider": GATEWAY_PROVIDER}
    engine.model, engine.provider = model, GATEWAY_PROVIDER
    return engine


# The provider-neutral decision model: when there is no TypeSafe key, one call to the company's own model
# provider answers the same questions. Keys are the providers' usual environment variables.
LLM_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "google": "GEMINI_API_KEY",
            "xai": "XAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
LLM_URLS = {"anthropic": "https://api.anthropic.com/v1/messages",
            "openai": "https://api.openai.com/v1/chat/completions",
            "xai": "https://api.x.ai/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "google": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"}
LLM_INSTRUCTIONS = (
    "You judge a JSON state against typed questions. Reply with one JSON object and nothing else: "
    "a key per question id. For `choice`, {\"probabilities\": {option: p}} over every option; for "
    "`score`, {\"probabilities\": [p per level, in order]}; for `noul`, {\"noul\": probability the "
    "statement is true}. Probabilities are numbers in [0,1] that sum to 1 within a question.")


# The longest answer a decision call may give, on every transport. The server's spend cap reserves a call's most with
# it (backend/judge.py `reservation`), so a call can never be billed more output than was held for it.
MAX_OUTPUT_TOKENS = 4096
# Each OpenAI-style API's name for that limit: OpenAI's own reasoning models refuse `max_tokens`.
LLM_OUTPUT_LIMIT = {"openai": "max_completion_tokens", "xai": "max_tokens", "openrouter": "max_tokens"}


def llm_request(provider, api_key, model, system, user, max_output=None):
    """(request) for one provider's chat API; each takes a system prompt and one user turn. `max_output` caps the
    answer's tokens on every transport (Anthropic always needs one, so it gets 4096 when none is given)."""
    headers = {"Content-Type": "application/json"}
    if provider == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        body = {"model": model, "max_tokens": max_output or MAX_OUTPUT_TOKENS, "system": system,
                "messages": [{"role": "user", "content": user}]}
    elif provider == "google":
        headers["x-goog-api-key"] = api_key
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json",
                                     **({"maxOutputTokens": max_output} if max_output else {})}}
    else:
        headers["Authorization"] = "Bearer " + api_key
        body = {"model": model, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                **({LLM_OUTPUT_LIMIT[provider]: max_output} if max_output else {})}
    return urllib.request.Request(LLM_URLS[provider].format(model=model), data=json.dumps(body).encode(),
                                  method="POST", headers=headers)


def llm_text(provider, payload):
    if provider == "anthropic":
        return "".join(part.get("text", "") for part in payload.get("content") or [])
    if provider == "google":
        return "".join(part.get("text", "") for part in payload["candidates"][0]["content"]["parts"])
    return payload["choices"][0]["message"]["content"]


def llm_answers(raw, questions):
    """Turn the model's probabilities into the decisions contract's answers."""
    if not isinstance(raw, str):
        raise JudgeError("shape", "the model did not answer with text")
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        raise JudgeError("shape", "the model did not answer with JSON")
    answers = {}
    for qid, q in questions.items():
        got = parsed.get(qid) if isinstance(parsed, dict) else None
        if not isinstance(got, dict):
            raise JudgeError("shape", f"{qid}: no answer")
        if q["type"] == "noul":
            try:
                answers[qid] = {"noul": min(1.0, max(0.0, float(got.get("noul", 0.5))))}
            except (TypeError, ValueError):
                raise JudgeError("shape", f"{qid}: noul is not a number")
            continue
        raw_p = got.get("probabilities")
        names = list(q["criteria"]) if q["type"] == "choice" else list(range(len(q["criteria"])))
        try:
            values = ([float(raw_p[name]) for name in names] if q["type"] == "choice"
                      else [float(v) for v in raw_p])
        except (TypeError, KeyError, ValueError):
            raise JudgeError("shape", f"{qid}: probabilities do not cover every option")
        if len(values) != len(names) or any(v < 0 for v in values) or not sum(values):
            raise JudgeError("shape", f"{qid}: probabilities are not usable")
        total = sum(values)
        probabilities = [v / total for v in values]
        best = max(range(len(names)), key=probabilities.__getitem__)
        if q["type"] == "choice":
            answers[qid] = {"choice": names[best], "confidence": probabilities[best],
                            "probabilities": dict(zip(names, probabilities))}
        else:
            answers[qid] = {"score": sum(i * p for i, p in enumerate(probabilities)),
                            "confidence": probabilities[best],
                            "probabilities": dict(enumerate(probabilities))}
    return check_answers(answers, questions)


def llm(provider, api_key, model, timeout=TIMEOUT, opener=None):
    """A decision model that asks the company's own provider. Less calibrated than a purpose-built
    classifier: the probabilities are the model's stated ones. `opener` is the test seam."""
    if provider not in LLM_URLS or not api_key or not model:
        raise JudgeError("unconfigured", "no provider key or model for the decision model")
    opener = opener or urllib.request.urlopen

    def engine(state, questions, label=None):
        validate(state, questions, label)
        user = json.dumps({"state": state, "questions": clean(questions)}, default=str)
        started = time.monotonic()
        try:
            with opener(llm_request(provider, api_key, model, LLM_INSTRUCTIONS, user, MAX_OUTPUT_TOKENS),
                        timeout=time_left(timeout)) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise JudgeError("unavailable" if exc.code in (429, 529) or exc.code >= 500 else "http",
                             f"{provider} answered {exc.code}", exc.code,
                             retryable=exc.code in (429, 529) or exc.code >= 500)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise JudgeError("unavailable", f"{provider} did not answer: {type(exc).__name__}", retryable=True)
        # An answer came back, so it was paid for, even when it cannot be used.
        usage = (payload.get("usage") or payload.get("usageMetadata") or {}) if isinstance(payload, dict) else {}
        try:
            text = llm_text(provider, payload)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise _paid(JudgeError("unavailable", f"{provider} did not answer: {type(exc).__name__}", retryable=True),
                        model, usage)
        try:
            answers = llm_answers(text, questions)
        except JudgeError as exc:
            raise _paid(exc, model, usage)
        return {"model": model, "answers": answers, "usage": usage,
                "ms": round((time.monotonic() - started) * 1000), "provider": LLM_PROVIDERS[provider]}
    engine.model, engine.provider = model, LLM_PROVIDERS[provider]
    return engine


def llm_writer(provider, api_key, model, timeout=TIMEOUT, opener=None):
    """Writing on the company's own provider: `write(system, user)` sends those two texts and nothing
    else (no tools, no memory, no history) and returns the JSON object the model answered with."""
    if provider not in LLM_URLS or not api_key or not model:
        raise JudgeError("unconfigured", "no provider key or model to write with")
    opener = opener or urllib.request.urlopen

    def write(system, user):
        try:
            with opener(llm_request(provider, api_key, model, system, user), timeout=timeout) as response:
                text = llm_text(provider, json.load(response)).strip()
        except urllib.error.HTTPError as exc:
            raise JudgeError("unavailable", f"{provider} answered {exc.code}", exc.code, retryable=True)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise JudgeError("unavailable", f"{provider} did not answer: {type(exc).__name__}", retryable=True)
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        try:
            parsed = json.loads(text)
        except ValueError:
            raise JudgeError("shape", "the model did not answer with JSON")
        if not isinstance(parsed, dict):
            raise JudgeError("shape", "the model did not answer with an object")
        return parsed
    write.model = model
    return write


def through_hub(client):
    """The hub's `POST /api/v2/decisions` with a turn's credential (`clients.tico.Client`)."""
    def engine(state, questions, label=None):
        validate(state, questions, label)
        body = {"state": state, "questions": questions}
        if label:
            body["label"] = label
        try:
            result = client.post("decisions", body)         # `Client.post` puts /api/v2/ in front
        except Exception as exc:                        # APIError, without importing clients.tico
            code = getattr(exc, "code", "unavailable")
            raise JudgeError(str(code), getattr(exc, "detail", str(exc)), getattr(exc, "status", 0),
                             bool(getattr(exc, "retryable", False)))
        answers = check_answers(result.get("answers") if isinstance(result, dict) else None, questions)
        return {"model": str(result.get("model") or MODEL), "answers": answers,
                "usage": result.get("usage") or {}, "ms": int(result.get("ms") or 0)}
    return engine


def from_env(env=None):
    """Inside a turn the hub decides (the key stays on the server); a process with its own
    `TYPESAFE_API_KEY` decides directly; otherwise None, and the caller does without."""
    env = os.environ if env is None else env
    url, token = env.get("HUB_API_URL"), env.get("HUB_TOKEN")
    if url and token:
        from clients.tico import Client
        return through_hub(Client(url, token, timeout=TIMEOUT + 10))
    key = (env.get(KEY_ENV) or "").strip()
    if key:
        return direct(key)
    return None


# ----------------------------------------------------------------------------- question sets
def question_dirs(env=None, cwd=None):
    """Where else a question set may be: `TICO_QUESTIONS_DIR`, the checkout a turn names in `HUB_DIR`, and the `questions/`
    folder in the bot's own repository (a message bot's template ships `questions/mail-triage.json`), which a computer
    without this checkout's folder layout still has."""
    env = os.environ if env is None else env
    found = [env.get("TICO_QUESTIONS_DIR"), Path(env["HUB_DIR"]) / "questions" if env.get("HUB_DIR") else None,
             Path(cwd or os.getcwd()) / "questions"]
    return [Path(d) for d in found if d]


def _registry_error(revision):
    error = JudgeError("invalid", "Invalid registry question set. Use a regular, valid JSON file in registry/questions.")
    error.config_revision = revision
    return error


def _registry_text(registry, name):
    """Read only regular files through anchored, no-follow registry directory handles.

    None means an absent override; existing symlinks, nonfiles and read failures are errors.
    The opaque revision lets diagnostics notice changed invalid files without exposing content.
    """
    descriptors, revision = [], hashlib.sha256()
    try:
        parent = None
        anchored = _REGISTRY_DIR_FD and hasattr(os, "O_NOFOLLOW")
        location = Path(registry)
        for part, directory in ((Path(registry), True), ("questions", True), (name + ".json", False)):
            try:
                if anchored:
                    info = os.stat(part, dir_fd=parent, follow_symlinks=False)
                else:
                    location = location if part == Path(registry) else location / part
                    info = location.lstat()
            except FileNotFoundError:
                return None
            revision.update(repr((info.st_dev, info.st_ino, info.st_mode, info.st_size,
                                  info.st_mtime_ns, info.st_ctime_ns)).encode())
            expected = stat.S_ISDIR if directory else stat.S_ISREG
            if not expected(info.st_mode):
                raise _registry_error(revision.hexdigest())
            if not anchored:
                if directory:
                    continue
                raise _registry_error(revision.hexdigest())
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory:
                flags |= os.O_DIRECTORY
            descriptor = os.open(part, flags, dir_fd=parent)
            descriptors.append(descriptor)
            opened = os.fstat(descriptor)
            if not expected(opened.st_mode):
                raise _registry_error(revision.hexdigest())
            parent = descriptor
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as stream:
            text = stream.read()
        revision.update(text.encode())
        return text, revision.hexdigest()
    except (OSError, UnicodeError):
        raise _registry_error(revision.hexdigest()) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def load_set(name, root=None, *, registry_dir=None):
    """A validated question set: an explicit root, or the company's registry before the shipped copy.

    An invalid override is refused rather than silently using different questions. The installed
    runner's existing question-directory fallbacks still apply when neither copy is present.
    """
    if not isinstance(name, str) or not name or any(ch in name for ch in "/\\.") :
        raise JudgeError("invalid", f"{name!r} is not a question set name")
    path = Path(root or QUESTIONS_DIR) / (name + ".json")
    if not root:
        registry = registry_dir if registry_dir is not None else os.environ.get("TICO_REGISTRY_DIR")
        custom = _registry_text(registry, name) if registry else None
        if custom is not None:
            try:
                return check_set(json.loads(custom[0]), name)
            except (ValueError, JudgeError):
                raise _registry_error(custom[1]) from None
        if not path.is_file():
            path = next((d / (name + ".json") for d in question_dirs() if (d / (name + ".json")).is_file()), path)
    try:
        data = json.loads(path.read_text())
    except OSError:
        raise JudgeError("not_found", f"no question set {name} ({path})")
    except ValueError as exc:
        raise JudgeError("invalid", f"{path.name}: {exc}")
    return check_set(data, name)


def check_set(data, name):
    if not isinstance(data, dict):
        raise JudgeError("invalid", f"{name}: question set is a map")
    for key in ("id", "version", "summary", "questions"):
        if key not in data:
            raise JudgeError("invalid", f"{name}: question set is missing `{key}`")
    if data["id"] != name:
        raise JudgeError("invalid", f"{name}: `id` must equal the file name")
    if not isinstance(data["version"], int) or data["version"] < 1:
        raise JudgeError("invalid", f"{name}: `version` is a positive integer")
    questions = data["questions"]
    if not isinstance(questions, dict):
        raise JudgeError("invalid", f"{name}: questions is a map of id -> question")
    static = {qid: q for qid, q in questions.items() if not (isinstance(q, dict) and q.get("dynamic"))}
    # A dynamic question's options are completed by the caller; check the fixed ones alone.
    for qid, q in questions.items():
        if isinstance(q, dict) and q.get("dynamic"):
            if q.get("type") != "choice":
                raise JudgeError("invalid", f"{name}: {qid}: only a choice can be dynamic")
            if q.get("criteria") is not None and not isinstance(q["criteria"], dict):
                raise JudgeError("invalid", f"{name}: {qid}: dynamic criteria is a map")
            static[qid] = {**q, "criteria": {**(q.get("criteria") or {}), "_a": "", "_b": ""}}
    validate({}, static)
    thresholds = data.get("thresholds") or {}
    if not isinstance(thresholds, dict) or not all(isinstance(v, (int, float)) for v in thresholds.values()):
        raise JudgeError("invalid", f"{name}: thresholds is a map of name -> number")
    try:
        state = list(data.get("state") or [])
    except TypeError:
        raise JudgeError("invalid", f"{name}: state must list the expected fields") from None
    return {**data, "label": f"{data['id']}@{data['version']}", "thresholds": thresholds,
            "state": state}


def with_options(question, options):
    """A dynamic choice completed: the caller's options first, then the set's fixed ones
    (`new`, `unassigned`, `other`)."""
    fixed = question.get("criteria") or {}
    merged = {**{str(k): (v if v is None or isinstance(v, ENTRY_TYPES) else str(v)) for k, v in options.items()},
              **fixed}
    return {**{k: v for k, v in question.items() if k != "dynamic"}, "criteria": merged}


def list_sets(root=None):
    out = []
    for path in sorted(Path(root or QUESTIONS_DIR).glob("*.json")):
        try:
            data = load_set(path.stem, root)
        except JudgeError as exc:
            out.append({"id": path.stem, "error": exc.detail})
            continue
        out.append({"id": data["id"], "version": data["version"], "label": data["label"],
                    "summary": data["summary"], "questions": list(data["questions"]),
                    "state": data["state"], "thresholds": data["thresholds"]})
    return out


# ----------------------------------------------------------------------------- reading answers
def top(answer):
    """(value, confidence) whatever the type: a noul's confidence is how far it is from a coin toss."""
    if "choice" in answer:
        return answer["choice"], float(answer.get("confidence") or 0)
    if "score" in answer:
        return float(answer["score"]), float(answer.get("confidence") or 0)
    p = float(answer.get("noul") or 0)
    return p, abs(p - 0.5) * 2


def choice(answers, qid):
    a = answers[qid]
    return a["choice"], float(a.get("confidence") or 0)


def noul(answers, qid):
    return float(answers[qid]["noul"])


def score(answers, qid):
    a = answers[qid]
    return float(a["score"]), float(a.get("confidence") or 0)


def summary(answers):
    """What the audit keeps of a call: the value and confidence per question, never the state."""
    out = {}
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict):
            continue
        value, confidence = top(a)
        out[qid] = {"type": a.get("type") or ("choice" if "choice" in a else "score" if "score" in a else "noul"),
                    "value": value, "confidence": round(confidence, 3)}
    return out
