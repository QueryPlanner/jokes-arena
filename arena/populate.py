"""Plan or generate one balanced, budget-bounded OpenRouter joke batch."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from arena.config import load_config, active_profiles, PROMPT_VERSION
from scripts.generate_preview import prompt, request_completion, validate

CATALOG_URL = 'https://openrouter.ai/api/v1/models'
MAX_TOKENS = 2048
# Reserve extra headroom beyond the requested output cap for reasoning and billing.
OUTPUT_RESERVE = 4096
INPUT_RESERVE = 2048
MAX_BUDGET = Decimal('2')


def verify_key_budget(key, plan):
    """Require a provider-enforced lifetime limit before making paid calls."""
    try:
        request = Request('https://openrouter.ai/api/v1/key', headers={'Authorization': f'Bearer {key}'})
        with urlopen(request, timeout=30) as response:
            details = json.load(response)['data']
        limit = Decimal(str(details['limit']))
        remaining = Decimal(str(details['limit_remaining']))
        if not limit.is_finite() or not remaining.is_finite() or details.get('limit_reset') is not None:
            raise ValueError('Key needs a fixed lifetime credit limit')
        if not 0 < remaining <= limit <= Decimal(plan['budget_usd']):
            raise ValueError('Key lifetime limit must be at most the batch budget')
        if remaining < Decimal(plan['reserved_usd']):
            raise ValueError('Key remaining credits are below the planned reserve')
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError, InvalidOperation):
        raise ValueError('Use a separate OpenRouter key with a fixed $2 lifetime credit limit and enough remaining credits. No paid requests made.') from None


def read_catalog():
    try:
        with urlopen(CATALOG_URL, timeout=30) as response:
            catalog = json.load(response)
            if not isinstance(catalog, dict) or not isinstance(catalog.get('data'), list):
                raise ValueError('Invalid catalog shape')
            return catalog
    except (HTTPError, URLError, OSError, ValueError):
        raise ValueError('Could not read the OpenRouter model catalog') from None


def build_plan(config, catalog, budget):
    budget = Decimal(str(budget))
    if not budget.is_finite() or not 0 < budget <= MAX_BUDGET:
        raise ValueError('Budget must be positive and at most $2 for this starter batch')
    profiles = list(active_profiles(config).values())
    if len(profiles) < 3 or len(config['words']) < len(profiles):
        raise ValueError('A cycle needs three profiles and one unique word per pair')
    models = {model['id']: model for model in catalog['data']}
    specifications = {}
    for profile in profiles:
        model = models.get(profile['api_model'])
        if model is None:
            raise ValueError(f"Model is no longer available: {profile['api_model']}")
        reasoning = model.get('reasoning', {})
        effort = profile['reasoning'].get('effort')
        if (profile['reasoning'].get('enabled') is False and reasoning.get('mandatory')) or (
            effort is not None and reasoning.get('supported_efforts') is not None
            and effort not in reasoning['supported_efforts']
        ):
            raise ValueError(f"Unsupported reasoning setting: {profile['profile_id']}")
        if not isinstance(model.get('pricing'), dict):
            raise ValueError('Catalog pricing is invalid')
        try:
            prices = {field: Decimal(str(model['pricing'].get(field, '0')))
                      for field in ('prompt', 'completion', 'request')}
        except (InvalidOperation, KeyError, TypeError):
            raise ValueError('Catalog pricing is invalid') from None
        if any(not price.is_finite() or price < 0 for price in prices.values()):
            raise ValueError('Catalog pricing must be finite and nonnegative')
        # An endpoint exceeding these prices cannot be selected by the gateway.
        options = {'reasoning': profile['reasoning'], 'max_tokens': MAX_TOKENS,
                   'provider': {'require_parameters': True, 'allow_fallbacks': False,
                                'max_price': {'prompt': float(prices['prompt'] * 1000000),
                                              'completion': float(prices['completion'] * 1000000),
                                              'request': float(prices['request'])}}}
        reserve = prices['prompt'] * INPUT_RESERVE + prices['completion'] * OUTPUT_RESERVE + prices['request']
        specifications[profile['profile_id']] = {'options': options, 'reserved_usd': str(reserve)}
    total = sum(Decimal(spec['reserved_usd']) * 2 for spec in specifications.values())
    if total > budget:
        raise ValueError(f'Full batch reserve ${total:.6f} exceeds budget ${budget:.2f}; no calls made')
    random = secrets.SystemRandom()
    random.shuffle(profiles)
    words = random.sample(config['words'], len(profiles))
    pairs = [{'word': word, 'profiles': [profiles[index]['profile_id'], profiles[(index + 1) % len(profiles)]['profile_id']]}
             for index, word in enumerate(words)]
    for word in words:
        if len(prompt(word).encode()) + 1024 > INPUT_RESERVE:
            raise ValueError('Prompt exceeds the reserved input allowance')
    return {'prompt_version': PROMPT_VERSION, 'budget_usd': str(budget), 'reserved_usd': str(total),
            'max_calls': len(pairs) * 2, 'profiles': {p['profile_id']: p for p in profiles},
            'requests': specifications, 'pairs': pairs,
            'unavailable': [p['name'] for p in config['models'] if not p['available']]}


def generate_batch(key, plan, directory, require_key_limit=False):
    if not key:
        raise ValueError('OPENROUTER_API_KEY is required')
    if require_key_limit:
        verify_key_budget(key, plan)
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    (directory / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    reserved = Decimal('0')
    reported = Decimal('0')
    calls = 0
    missing_costs = 0
    published = 0
    appearances = Counter()
    completed = False
    stop_reason = None
    try:
        with (directory / 'attempts.jsonl').open('x') as attempts, (directory / 'receipt.jsonl').open('x') as receipt:
            for pair in plan['pairs']:
                records = []
                for identity in pair['profiles']:
                    profile = plan['profiles'][identity]
                    spec = plan['requests'][identity]
                    allowance = Decimal(spec['reserved_usd'])
                    if max(reserved, reported) + allowance > Decimal(plan['budget_usd']):
                        raise ValueError('Remaining budget is too small; stopped before the next request')
                    reserved += allowance  # Reserve failed and timed-out requests too; never retry automatically.
                    calls += 1
                    record = {'word': pair['word'], 'profile_id': identity, 'requested_model': profile['api_model'],
                              'reasoning': profile['reasoning'], 'prompt_version': PROMPT_VERSION,
                              'call': calls, 'generated_at': datetime.now(timezone.utc).isoformat()}
                    cost_known = False
                    try:
                        response = request_completion(key, profile['api_model'], pair['word'], spec['options'])
                        record['usage'] = response.get('usage') if isinstance(response, dict) else None
                        if record['usage'] is not None and not isinstance(record['usage'], dict):
                            raise ValueError('Provider usage is invalid')
                        cost = (record['usage'] or {}).get('cost')
                        if cost is not None:
                            amount = Decimal(str(cost))
                            if not amount.is_finite() or amount < 0:
                                raise ValueError('Provider usage cost is invalid')
                            reported += amount
                            cost_known = True
                            reserved += max(Decimal('0'), amount - allowance)
                        text = validate(response, profile['api_model'], pair['word'])
                        record.update(status='validated', returned_model=response['model'], joke=text)
                        records.append(record)
                    except (ValueError, InvalidOperation) as error:
                        record.update(status='failed', error=str(error))
                    if not cost_known:
                        missing_costs += 1
                    attempts.write(json.dumps(record) + '\n')
                    attempts.flush()
                    print(f"Request {calls}/{plan['max_calls']}: {profile['name']} ({record['status']})", flush=True)
                    if reported > Decimal(plan['budget_usd']):
                        raise ValueError('Provider reported charges above the budget; no further requests will be sent')
                if len(records) == 2:
                    for record in records:
                        receipt.write(json.dumps(record) + '\n')
                        appearances[record['profile_id']] += 1
                    receipt.flush()
                    published += 1
        completed = True
    except (ValueError, OSError) as error:
        stop_reason = str(error)
        raise
    finally:
        status = 'over-budget' if reported > Decimal(plan['budget_usd']) else 'complete' if completed else 'stopped'
        summary = {'status': status, 'stop_reason': stop_reason, 'calls': calls, 'pairs': published, 'reserved_usd': str(reserved),
                   'reported_cost_usd': str(reported), 'missing_cost_calls': missing_costs,
                   'published_appearances': dict(appearances)}
        (directory / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='Fresh private directory')
    parser.add_argument('--budget', default='2')
    parser.add_argument('--execute', action='store_true', help='Make the planned paid requests')
    parser.add_argument('--require-key-limit', action='store_true', help='Require a provider-enforced lifetime credit limit')
    args = parser.parse_args()
    try:
        plan = build_plan(load_config(), read_catalog(), args.budget)
        print(f"Plan: {len(plan['pairs'])} pairs, {plan['max_calls']} requests, ${Decimal(plan['reserved_usd']):.6f} reserved.", flush=True)
        if args.execute:
            os.umask(0o077)
            summary = generate_batch(os.environ.get('OPENROUTER_API_KEY', ''), plan, args.directory, args.require_key_limit)
            print(f"Published {summary['pairs']} complete pairs; reported cost ${summary['reported_cost_usd']}.")
        else:
            args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
            (args.directory / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            print('Plan saved. No paid requests were made.')
    except (ValueError, OSError, InvalidOperation) as error:
        print(f'Batch stopped: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
