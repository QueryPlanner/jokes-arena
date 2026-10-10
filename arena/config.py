"""Public model profiles and curated single-word prompts."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent / 'config'
PROMPT_VERSION = 'joke-v1'


def load_config(model_path=ROOT / 'models.json', word_path=ROOT / 'words.json'):
    config = json.loads(Path(model_path).read_text())
    words = json.loads(Path(word_path).read_text())
    if not isinstance(words, list) or not words or any(
        not isinstance(word, str) or not re.fullmatch('[a-z]+', word) for word in words
    ) or len(set(words)) != len(words):
        raise ValueError('Word bucket must contain unique lowercase single words')
    if config.get('prompt_version') != PROMPT_VERSION or not config.get('models'):
        raise ValueError('Model configuration needs a supported prompt version and models')
    identities = set()
    for model in config['models']:
        if not isinstance(model.get('name'), str) or not model['name']:
            raise ValueError('Every model needs a name')
        if not model['available']:
            if model.get('api_model') is not None or model.get('profile_id') is not None:
                raise ValueError('Unavailable models cannot have active API identities')
            continue
        reasoning = model.get('reasoning')
        api_model = model.get('api_model')
        if not isinstance(api_model, str) or not re.fullmatch(r'[a-z0-9.-]+/[a-z0-9.-]+', api_model):
            raise ValueError('Model needs an exact OpenRouter API ID')
        label = model.get('reasoning_label')
        expected = {'enabled': False, 'exclude': True} if label == 'off' else {'effort': label, 'exclude': True}
        if label not in ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max') or reasoning != expected:
            raise ValueError('Reasoning configuration is invalid')
        identity = f'{api_model}@{label}:{PROMPT_VERSION}'
        if model.get('profile_id') != identity or identity in identities:
            raise ValueError('Model profiles must have distinct configuration identities')
        identities.add(identity)
    if len(identities) < 3:
        raise ValueError('The balanced cycle needs at least three available profiles')
    return {**config, 'words': words}


def active_profiles(config):
    return {model['profile_id']: model for model in config['models'] if model['available']}
