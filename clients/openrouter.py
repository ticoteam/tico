"""Catalog model id -> OpenRouter model id, for every model Tico reaches through OpenRouter.

One table for the pi runtime (runner/hosts/pi.py) and the server's decision model when it falls back to
OpenRouter (backend/judge.py), so both send OpenRouter the same id. Change the right-hand side to swap a
model; the catalog id (backend/providers.py) stays.
"""

MODELS = {"deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash",
          "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
          "kimi-k3": "moonshotai/kimi-k3",
          "kimi-k2.7-code": "moonshotai/kimi-k2.7-code",
          "llama-4-maverick": "meta-llama/llama-4-maverick",
          "mistral-medium-3.5": "mistralai/mistral-medium-3-5",
          "devstral-2512": "mistralai/devstral-2512"}
DEFAULT = "deepseek-v4.1-flash"


def model_id(model):
    """The OpenRouter id for a catalog id; an id that is already OpenRouter's (or unknown) passes through."""
    m = str(model or "").strip()
    if not m or m == "default":
        return MODELS[DEFAULT]
    return MODELS.get(m, m)
