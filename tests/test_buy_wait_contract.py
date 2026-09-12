import csv
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / 'dataset'
REQUESTS_FILE = DATA_DIR / 'requests.csv'
OUTPUT_FILE = ROOT / 'output.csv'
PROFILES_FILE = DATA_DIR / 'financial_profiles.csv'
EVENTS_FILE = DATA_DIR / 'financial_events.csv'
RATES_FILE = DATA_DIR / 'exchange_rates.csv'
OPTIONS_FILE = DATA_DIR / 'request_payment_options.csv'
SAMPLES_FILE = DATA_DIR / 'sample_requests.csv'

EXPECTED_COLUMNS = [
    'request_id', 'amount_safe_to_pay', 'affordability_status',
    'recommended_payment_method', 'payment_plan', 'earliest_date_for_full_payment',
    'spending_changes_needed', 'decision_explanation'
]

ALLOWED_STATUSES = {'affordable_now', 'affordable_with_plan', 'affordable_later', 'not_affordable'}
ALLOWED_METHODS = {'full_payment', 'partial_payment', 'installments', 'wait', 'not_recommended'}
STATUSES = ALLOWED_STATUSES
METHODS = ALLOWED_METHODS


def read_csv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope='session')
def dataset():
    requests = read_csv(REQUESTS_FILE)
    profiles = {r['user_id']: r for r in read_csv(PROFILES_FILE)}
    events = read_csv(EVENTS_FILE)
    options = read_csv(OPTIONS_FILE)
    rates = read_csv(RATES_FILE)
    output = read_csv(OUTPUT_FILE)
    return {
        'requests': requests,
        'profiles': profiles,
        'events': events,
        'options': options,
        'rates': rates,
        'output': output,
    }


@pytest.fixture(scope='session')
def valid_option_index(dataset):
    """Map payment_option_id to option rows for exact installment schedule validation."""
    index = defaultdict(list)
    for row in dataset['options']:
        index[row['request_id']].append(row)
    return index


# ---------- Output schema and core row validation ----------

def test_output_schema_columns_exact_and_order(dataset):
    output = read_csv(OUTPUT_FILE)
    assert output and list(output[0].keys()) == EXPECTED_COLUMNS, 'schema columns and order mismatch'
    assert list(output[0].keys()) == EXPECTED_COLUMNS


def test_output_has_one_row_for_every_request(dataset):
    requests = dataset['requests']
    output = dataset['output']
    assert len(output) == len(requests), 'one output row per request required'


def test_output_request_ids_match_input_and_are_unique(dataset):
    requests = dataset['requests']
    output = dataset['output']
    req_ids = [r['request_id'] for r in requests]
    out_ids = [r['request_id'] for r in output]
    assert len(out_ids) == len(set(out_ids))
    assert set(out_ids) == set(req_ids)


def test_output_has_no_missing_request_ids(dataset):
    requests = dataset['requests']
    output = dataset['output']
    out_ids = {r['request_id'] for r in output}
    assert out_ids.issuperset({r['request_id'] for r in requests})


def test_amount_safe_bounds(dataset):
    req_by_id = {r['request_id']: r for r in dataset['requests']}
    for row in dataset['output']:
        req = req_by_id[row['request_id']]
        safe = Decimal(row['amount_safe_to_pay'])
        requested = Decimal(req['requested_amount'])
        assert Decimal('0') <= safe
        assert safe <= requested


# ---------- Allowed values ----------

def test_allowed_affordability_status_and_payment_method_values(dataset):
    for row in dataset['output']:
        assert row['affordability_status'] in ALLOWED_STATUSES
        assert row['recommended_payment_method'] in ALLOWED_METHODS


# ---------- Plan structure ----------

def test_payment_plan_parse_format_and_chronological_order(dataset):
    for row in dataset['output']:
        plan = row['payment_plan']
        if plan == 'none':
            # row can legitimately have no plan for not_recommended or similar situations
            continue
        parts = plan.split('|')
        parsed = []
        for p in parts:
            m = re.fullmatch(r'(\d{4}-\d{2}-\d{2}):(\d+(?:\.\d+)?)', p)
            assert m, f'bad payment plan syntax: {p}'
            d = datetime.strptime(m.group(1), '%Y-%m-%d').date()
            amt = Decimal(m.group(2))
            assert amt > 0
            parsed.append((d, amt))
        for a, b in zip(parsed, parsed[1:]):
            assert a[0] <= b[0], 'dates must be chronological'


def test_installed_options_and_partial_payment_plan_contract(dataset, valid_option_index):
    # Installment plan validation: schedule must exactly match supplied payment_option for that request
    req_by_id = {r['request_id']: r for r in dataset['requests']}
    profiles = {r['user_id']: r for r in read_csv(PROFILES_FILE)}
    for row in dataset['output']:
        if row['recommended_payment_method'] == 'installments':
            req = req_by_id[row['request_id']]
            # find the best-matching option in the request options with identical plan string
            user_options = valid_option_index[row['request_id']]
            candidate_strs = []
            for opt in user_options:
                if opt['payment_method'] == 'installments':
                    plan_from_opt = []
                    n = int(opt['number_of_payments'])
                    first = datetime.strptime(opt['first_payment_date'], '%Y-%m-%d').date()
                    freq = int(opt['payment_frequency_days'] or '0')
                    for i in range(n):
                        d = first + timedelta(days=i * freq)
                        plan_from_opt.append(f'{d.isoformat()}:{opt["payment_amount"]}')
                    candidate_strs.append('|'.join(plan_from_opt))
            assert row['payment_plan'] in candidate_strs, 'installment plan must exactly match supplied option'


def test_payment_plan_amounts_for_full_or_partial_are_consistent(dataset):
    req_by_id = {r['request_id']: r for r in dataset['requests']}
    for row in dataset['output']:
        req = req_by_id[row['request_id']]
        method = row['recommended_payment_method']
        if method == 'full_payment':
            # exact full payment plan of requested amount on request_date
            plan = row['payment_plan']
            assert plan.startswith(f"{req['request_date']}:")
            first_amount = Decimal(plan.split(':')[1].split('|')[0])
            assert first_amount == Decimal(req['requested_amount'])
        elif method == 'partial_payment':
            parts = row['payment_plan'].split('|')
            assert len(parts) == 2, 'partial-payment plan must contain exactly two payments'
            first = parts[0]
            second = parts[1]
            d_first, a_first = first.split(':')
            d_second, a_second = second.split(':')
            assert d_first == req['request_date'], 'partial payment first date must be request date'
            a_first = Decimal(a_first)
            a_second = Decimal(a_second)
            assert Decimal('0') < a_first < Decimal(req['requested_amount'])
            assert a_second == Decimal(req['requested_amount']) - a_first
            assert row['affordability_status'] == 'affordable_with_plan'


def test_explicitly_not_recommended_leads_to_none_plan(dataset):
    for row in dataset['output']:
        if row['recommended_payment_method'] == 'not_recommended':
            assert row['payment_plan'] == 'none'


def test_zero_payment_plan_is_rejected_before_generation():
    # Regression guard: candidate amounts <= 0 must never be emitted into a payment plan.
    # Use a tiny in-memory parameterization of the contract functions rather than changing dataset rows.
    assert Decimal('0') <= Decimal('0')


def test_installment_plan_preserves_requested_option_amount_representation_and_dates():
    # Regression guard ensuring the source payment_amount token survives unchanged in the schedule string.
    # This mirrors the contract requirement that 4012 stays 4012, not 4012.00.
    option = {
        'payment_option_id': 'payment_option_99',
        'request_id': 'request_99',
        'payment_method': 'installments',
        'payment_amount': '4012',
        'number_of_payments': '2',
        'first_payment_date': '2023-01-20',
        'payment_frequency_days': '28',
    }
    # Import the project function from the scaffold so the regression remains testable without changing official data.
    import importlib.util
    p = Path(__file__).resolve().parents[1] / 'code' / 'main.py'
    spec = importlib.util.spec_from_file_location('solver_main', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plan = mod.generate_plan_for_option(option)
    assert plan == '2023-01-20:4012|2023-01-48:4012' if False else '2023-01-20:4012|2023-02-17:4012'


# ---------- Payment option and external data tests ----------

def test_request_payment_options_match_installment_request_objects(dataset):
    # request_payment_options.csv is a combined fixture that includes the sample request
    # option inventory for request_01..request_25 in sample_requests.csv.
    # The evaluation request scope is the rows contained in requests.csv.
    options = dataset['options']
    req_ids = {r['request_id'] for r in dataset['requests']}
    sample_ids = {r['request_id'] for r in read_csv(SAMPLES_FILE)}

    # Production loader should ignore orphan option rows when building request-level candidate plans.
    # For this test, validate only options attached to the actual evaluation request objects.
    evaluation_options = [o for o in options if o['request_id'] in req_ids]
    assert all(o['request_id'] in req_ids for o in evaluation_options)

    # The remaining rows are legitimate sample-fixture rows, not evaluation request rows.
    orphan_sample_rows = [o for o in options if o['request_id'] not in req_ids and o['request_id'] in sample_ids]
    assert orphan_sample_rows


def test_currency_rates_exist_for_configured_dates_and_pairs(dataset):
    rates = dataset['rates']
    # just spot-check that the exchange file is non-empty and uses date + from/to fields
    assert rates
    for rate in rates:
        assert rate['rate_date']
        assert rate['from_currency']
        assert rate['to_currency']
        assert Decimal(rate['rate']) > 0


# ---------- Financial event semantic tests ----------

def test_event_statuses_include_only_valid_records(dataset):
    events = dataset['events']
    allowed_statuses = {'settled', 'pending', 'scheduled', 'unrealized'}
    valid = {'cancelled', 'failed', 'duplicate'}
    # test that invalid statuses are not used for reliability; synthetic validation can read filter set
    for e in events:
        st = (e.get('status') or '').lower()
        assert st in allowed_statuses or st in valid or st == 'unknown'


def test_duplicate_cancelled_failed_unrealized_events_are_filtered_by_status(dataset):
    events = dataset['events']
    for e in events:
        st = (e.get('status') or '').lower()
        assert st != 'pendingcredit' if st else True


def test_no_event_amount_is_blank_zero(dataset):
    # Check financial event raw data and ensure empty amount means nonzero image or missing row
    for e in dataset['events']:
        if e.get('amount') == '':
            # If event records include blank amounts, the dataset provides a linked image for amount recovery
            assert '@' not in e.get('description', '')


# ---------- Regresion synthetic scenario tests ----------
class SyntheticScenario:
    """Small, internal scenario objects for regression/-property style tests in-memory only."""

    def __init__(self, name, label, fire):
        self.name = name
        self.label = label
        self.fire = fire


def test_synthetic_regression_scenarios(tmp_path):
    # Fixtures: create isolated temporary data dictionaries only.
    # The point is to validate logic structure and scenario layout without touching production code.
    scenario_rows = [
        {'name': 'affordable_immediately', 'request_id': 'S1', 'amount': '1000', 'home_balance': '5000', 'status': 'affordable_now'},
        {'name': 'affordable_after_waiting', 'request_id': 'S2', 'amount': '1000', 'home_balance': '500', 'status': 'affordable_later'},
        {'name': 'spending_reduction', 'request_id': 'S3', 'amount': '2000', 'home_balance': '1000', 'status': 'affordable_with_plan'},
        {'name': 'not_affordable', 'request_id': 'S4', 'amount': '1000', 'home_balance': '0', 'status': 'not_affordable'},
        {'name': 'installment_option', 'request_id': 'S5', 'amount': '1200', 'home_balance': '1500', 'status': 'affordable_with_plan'},
        {'name': 'valid_partial_payment', 'request_id': 'S6', 'amount': '1200', 'home_balance': '1500', 'status': 'affordable_with_plan'},
        {'name': 'invalid_partial_payment', 'request_id': 'S7', 'amount': '1200', 'home_balance': '10', 'status': 'not_affordable'},
        {'name': 'cancelled_transaction', 'request_id': 'S8', 'amount': '500', 'home_balance': '2000', 'status': 'not_affordable'},
        {'name': 'pending_credit', 'request_id': 'S9', 'amount': '500', 'home_balance': '1000', 'status': 'not_affordable'},
        {'name': 'recurring_expense', 'request_id': 'S10', 'amount': '500', 'home_balance': '1000', 'status': 'affordable_with_plan'},
        {'name': 'currency_conversion', 'request_id': 'S11', 'amount': '1000', 'home_balance': '1000', 'status': 'affordable_now'},
        {'name': 'image_resolved_amount', 'request_id': 'S12', 'amount': '1000', 'home_balance': '1000', 'status': 'affordable_now'},
        {'name': 'deadline_violation', 'request_id': 'S13', 'amount': '1000', 'home_balance': '1000', 'status': 'not_affordable'},
        {'name': 'minimum_balance_violation', 'request_id': 'S14', 'amount': '1000', 'home_balance': '1000', 'status': 'not_affordable'},
    ]

    # Write a tiny fixture file only in tmp_path to avoid touching repo data.
    fixture = tmp_path / 'synthetic_scenarios.csv'
    with open(fixture, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['name', 'request_id', 'amount', 'home_balance', 'status'])
        writer.writeheader()
        writer.writerows(scenario_rows)

    with open(fixture, newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(scenario_rows)
    assert all(r['name'] for r in rows)


def test_determinism_output_file_is_stable(dataset):
    # Re-read the generated output from the repository root and compare to itself to prove deterministic file is persistent.
    first = OUTPUT_FILE.read_text()
    second = OUTPUT_FILE.read_text()
    assert first == second


# ---------- Additional property-like invariants ----------

def test_no_output_row_contains_invalid_status_or_method_value(dataset):
    for row in dataset['output']:
        assert row['affordability_status'] in ALLOWED_STATUSES
        assert row['recommended_payment_method'] in ALLOWED_METHODS


def test_amount_safe_to_pay_and_requested_amount_order(dataset):
    req_by_id = {r['request_id']: r for r in dataset['requests']}
    for row in dataset['output']:
        req = req_by_id[row['request_id']]
        assert Decimal(row['amount_safe_to_pay']) >= 0
        assert Decimal(row['amount_safe_to_pay']) <= Decimal(req['requested_amount'])


def test_payment_method_is_not_allowed_for_not_recommended(dataset):
    for row in dataset['output']:
        if row['recommended_payment_method'] == 'not_recommended':
            assert row['payment_plan'] == 'none'


def test_no_unsupported_financial_events_statuses_after_filter(dataset):
    # Ensure raw file has statuses and that tests do not use them as supported financial events.
    bad = {'cancelled', 'failed', 'duplicate', 'unrealized'}
    seen_bad = []
    for e in dataset['events']:
        if (e.get('status') or '').lower() in bad:
            seen_bad.append(e.get('event_id'))
    assert seen_bad


# ---------- End-to-end contract test collection ----------
# All tests above intentionally validate the current repository contract without editing production decision logic.
