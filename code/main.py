#!/usr/bin/env python3
import csv
import os
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict

DATA_DIR = os.path.join(os.getcwd(), 'dataset')
ROOT_DIR = os.getcwd()


def load_csv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except Exception:
        return None


def fmt_date(d):
    return d.strftime('%Y-%m-%d') if d else ''


def dec(x, default='0'):
    if x is None or x == '':
        return Decimal(default)
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal(default)


def q2(x):
    return Decimal(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def money(x):
    return str(q2(x))


def as_bool(x):
    return str(x).strip().lower() in {'true', '1', 'yes', 'y'}


def clean_amount(x):
    # Preserve source robustness while avoiding fake 0.00 artifacts from a rounded subtraction.
    q = Decimal(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if q == q.to_integral():
        return str(q.quantize(Decimal('1')))
    s = format(q.normalize(), 'f')
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s

# Central candidate validator. Run this before returning any payment plan.
def plan_candidate_valid(request, method, plan, requested=None, safe_val=None, desired=None):
    # not_recommended must emit the canonical none marker.
    if method == 'not_recommended':
        return plan == 'none'

    if not plan or plan == 'none':
        return method == 'not_recommended'

    # Reject any component whose amount is not positive and any date that cannot be parsed.
    parts = []
    if plan:
        parts = plan.split('|')
    for comp in parts:
        if ':' not in comp:
            return False
        d_s, a_s = comp.split(':', 1)
        if not d_s or not parse_date(d_s):
            return False
        try:
            a = Decimal(a_s)
        except Exception:
            return False
        if a <= 0:
            return False

    # Full payment: first date must be request_date and single amount requested_amount.
    if method == 'full_payment':
        if len(parts) != 1:
            return False
        d_s, a_s = parts[0].split(':', 1)
        return d_s == request['request_date'] and Decimal(a_s) == requested

    # Partial payment: exactly two payments, first date request_date, first amount >0 and < requested,
    # second amount == requested - first safe, second date <= desired completion.
    if method == 'partial_payment':
        if len(parts) != 2:
            return False
        d1, a1 = parts[0].split(':', 1)
        d2, a2 = parts[1].split(':', 1)
        if d1 != request['request_date']:
            return False
        if not (Decimal('0') < Decimal(a1) < requested):
            return False
        if Decimal(a2) != requested - Decimal(a1):
            return False
        if parse_date(d2) and parse_date(d2) > desired:
            return False
        return True

    # Installments must exactly match an option's source payment_amount representation and schedule.
    if method == 'installments':
        req_id = request['request_id']
        for opt in options_by_request.get(req_id, []):
            if opt.get('payment_method') == 'installments':
                if generate_plan_for_option(opt) == plan:
                    return True
        return False

    # Wait can be represented as a one-shot plan on the earliest safe date.
    if method == 'wait':
        if len(parts) != 1:
            return False
        d_s, a_s = parts[0].split(':', 1)
        return parse_date(d_s) and Decimal(a_s) == requested and parse_date(d_s) >= parse_date(request['request_date'])

    return True

# Load files
requests = load_csv(os.path.join(DATA_DIR, 'requests.csv'))
profiles = {r['user_id']: r for r in load_csv(os.path.join(DATA_DIR, 'financial_profiles.csv'))}
events = load_csv(os.path.join(DATA_DIR, 'financial_events.csv'))
raw_options = load_csv(os.path.join(DATA_DIR, 'request_payment_options.csv'))
rates = load_csv(os.path.join(DATA_DIR, 'exchange_rates.csv'))

request_ids = {r['request_id'] for r in requests}
options = [o for o in raw_options if o.get('request_id') in request_ids]

options_by_request = defaultdict(list)
for o in options:
    options_by_request[o['request_id']].append(o)

events_by_user = defaultdict(list)
for e in events:
    if e.get('user_id'):
        ecopy = {k: (v or '') for k, v in e.items()}
        events_by_user[e['user_id']].append(ecopy)

# Pre-normalize profile method acceptance
def payment_methods(profile):
    return set((profile.get('payment_methods_user_will_consider') or '').split('|'))

# Rate map
def rate_for(day, frm, to):
    # Prefer same date exact
    day_s = fmt_date(day)
    for r in rates:
        if r['rate_date'] == day_s and r['from_currency'] == frm and r['to_currency'] == to:
            return Decimal(r['rate'])
    # then fallback same pair date <= event date
    for r in rates:
        if r['from_currency'] == frm and r['to_currency'] == to:
            rr = parse_date(r['rate_date'])
            if rr and rr <= day:
                return Decimal(r['rate'])
    # reverse fallback
    for r in rates:
        if r['from_currency'] == to and r['to_currency'] == frm:
            rr = parse_date(r['rate_date'])
            if rr and rr <= day:
                return Decimal('1') / Decimal(r['rate'])
    return Decimal('1')

# Convert event amount into home currency with sign: event direction says credit/debit.
def amount_to_home(e, profile, default_day):
    day = parse_date(e.get('settlement_date')) or parse_date(e.get('event_date')) or default_day
    frm = e.get('currency') or profile['home_currency']
    to = profile['home_currency']
    if frm == to:
        amt = dec(e.get('amount') or '0')
    else:
        amt = dec(e.get('amount') or '0') * rate_for(day, frm, to)
    # credit = income, debit = expense
    if e.get('direction') == 'credit':
        return +amt
    elif e.get('direction') == 'debit':
        return -amt
    # if direction uses maybe None, keep signed in row data with positive account direction neutral
    # Most rows have direction credit/debit. Fallback to positive amount when direction missing.
    return amt

# Filter event timeline in valid horizon and statuses
# Ignore pending credits and all events that are not settled or some valid status

def event_valid(e, req_date):
    st = (e.get('status') or '').lower()
    if st in {'cancelled', 'failed', 'duplicate', 'unrealized'}:
        return False
    if st == 'pending' and (e.get('direction') or '').lower() == 'credit':
        return False
    day = parse_date(e.get('settlement_date')) or parse_date(e.get('event_date'))
    if day is None:
        return False
    horizon = req_date + timedelta(days=90)
    # only events in 90 day window
    return req_date <= day <= horizon

# Simulate safety on a given date for a given safe payment amount.
def simulate_safe(request, profile, req_date, amount_to_pay):
    bal = dec(profile['current_available_balance']) - dec(amount_to_pay)
    min_bal = dec(profile['minimum_balance_to_keep'])
    horizon = req_date + timedelta(days=90)
    # collect all relevant events for this user in horizon ; do not depend on request
    events_for_user = []
    for e in events_by_user.get(request['user_id'], []):
        if not event_valid(e, req_date):
            continue
        day = parse_date(e.get('settlement_date')) or parse_date(e.get('event_date'))
        if day < req_date or day > horizon:
            continue
        # apply ignored statuses already
        val = amount_to_home(e, profile, req_date)
        events_for_user.append((day, val))
    events_for_user.sort(key=lambda x: (x[0], x[1]))
    # chronological apply
    for day, val in events_for_user:
        bal += val
        if bal < min_bal:
            return False
    return True

# Full date earliest search
def earliest_full_date(request, profile, req_date, requested):
    horizon = req_date + timedelta(days=90)
    d = req_date
    while d <= horizon:
        if simulate_safe(request, profile, d, requested):
            return d
        d = d + timedelta(days=1)
    return None

# Max safe amount by binary search

def amount_safe(request, profile, req_date):
    lo = Decimal('0')
    hi = dec(request['requested_amount'])
    best = Decimal('0')
    # small number of iterations
    for _ in range(26):
        mid = (lo + hi) / Decimal('2')
        if simulate_safe(request, profile, req_date, mid):
            best = mid
            lo = mid
        else:
            hi = mid
    return best

# Installments plan generator exactly from option schedule, preserving the supplied source amount string.
def positive_amount_string(value):
    if value is None:
        return False
    s = str(value).strip()
    if not s:
        return False
    try:
        amt = Decimal(s)
    except Exception:
        return False
    return amt > 0


def generate_plan_for_option(option):
    # Reject zero/negative/blank/nonnumeric installment amount candidates before emitting any schedule.
    amount_src = option.get('payment_amount')
    if not positive_amount_string(amount_src):
        return None

    try:
        n = int(option.get('number_of_payments') or '1')
    except Exception:
        return None

    if n <= 0:
        return None

    first = parse_date(option.get('first_payment_date'))
    if first is None:
        return None

    try:
        freq = int(option.get('payment_frequency_days') or '0')
    except Exception:
        return None

    if freq < 0:
        return None

    parts = []
    for i in range(n):
        d = first + timedelta(days=i * freq)
        parts.append(f'{fmt_date(d)}:{amount_src}')
    return '|'.join(parts)

# Planner logic

def choose_plan(request, profile, req_date, safe_val):
    requested = dec(request['requested_amount'])
    considered = payment_methods(profile)
    desired = parse_date(request['desired_completion_date'])
    earliest = earliest_full_date(request, profile, req_date, requested)

    # Full payment branch: if the safe amount has the same magnitude as the requested amount, treat it as full.
    tol = Decimal('0.01')
    if safe_val >= requested - tol and 'full_payment' in considered:
        full_plan = f'{fmt_date(req_date)}:{clean_amount(requested)}'
        if plan_candidate_valid(request, 'full_payment', full_plan, requested=requested):
            return 'affordable_now', 'full_payment', full_plan, fmt_date(req_date)

    # Partial payment is valid only for the strict inequality 0 < safe_val < requested_amount.
    # If safe_val >= requested, or the rounded remaining is zero/negative, do not emit a partial candidate.
    if as_bool(request['allows_partial_payment']) and 'partial_payment' in considered and safe_val > Decimal('0') and safe_val < requested - tol:
        if earliest and earliest <= desired:
            remaining = requested - safe_val
            if remaining <= Decimal('0'):
                pass
            else:
                # Construct the exact two-payment partial schedule using the source Decimal amount and a trimmed amount string.
                first_amt = clean_amount(safe_val)
                second_amt = clean_amount(remaining)
                if not first_amt or not second_amt or Decimal(first_amt) <= 0 or Decimal(second_amt) <= 0:
                    pass
                else:
                    partial_plan = f'{fmt_date(req_date)}:{first_amt}|{fmt_date(earliest)}:{second_amt}'
                    if plan_candidate_valid(request, 'partial_payment', partial_plan, requested=requested, safe_val=safe_val, desired=desired):
                        return 'affordable_with_plan', 'partial_payment', partial_plan, fmt_date(earliest)

    # Installment if user accepts and options exist; only valid source options can produce a valid candidate.
    if 'installments' in considered:
        opts = [o for o in options_by_request.get(request['request_id'], []) if o.get('payment_method') == 'installments']
        if opts:
            opts.sort(key=lambda o: (parse_date(o['first_payment_date']), dec(o.get('financing_fee')), o['payment_option_id']))
            for chosen in opts:
                plan = generate_plan_for_option(chosen)
                if not plan:
                    continue
                # Normalize guarantee that every component is positive, every date parses, and the exact option string matches.
                if plan_candidate_valid(request, 'installments', plan, requested=requested):
                    return 'affordable_with_plan', 'installments', plan, fmt_date(parse_date(chosen['first_payment_date']))

    # Wait if full payment accepted and not safe yet but future safe date exists.
    if earliest and earliest > req_date and 'full_payment' in considered:
        wait_plan = f'{fmt_date(earliest)}:{clean_amount(requested)}'
        if plan_candidate_valid(request, 'wait', wait_plan, requested=requested):
            return 'affordable_later', 'wait', wait_plan, fmt_date(earliest)

    return 'not_affordable', 'not_recommended', 'none', ''

# Main generator
def generate_row(request):
    profile = profiles.get(request['user_id'])
    if not profile:
        return {
            'request_id': request['request_id'],
            'amount_safe_to_pay': '0',
            'affordability_status': 'not_affordable',
            'recommended_payment_method': 'not_recommended',
            'payment_plan': 'none',
            'earliest_date_for_full_payment': '',
            'spending_changes_needed': 'none',
            'decision_explanation': 'No profile available for this user.'
        }

    req_date = parse_date(request['request_date'])
    requested = dec(request['requested_amount'])
    safe_val = amount_safe(request, profile, req_date)
    earliest = earliest_full_date(request, profile, req_date, requested)
    earliest_str = fmt_date(earliest) if earliest else ''
    status, method, plan, _ = choose_plan(request, profile, req_date, safe_val)

    # Make earliest_date consistent for full-payment case.
    if status == 'affordable_now':
        earliest_str = fmt_date(req_date)

    if method == 'full_payment':
        explanation = f'Full payment is safe on {fmt_date(req_date)} and keeps the user above the minimum balance of {profile["minimum_balance_to_keep"]} {profile["home_currency"]}.'
    elif method == 'partial_payment':
        explanation = f'Safe amount today is {money(safe_val)} {profile["home_currency"]}. The remaining {money(requested-safe_val)} is paid on {earliest_str}.'
    elif method == 'installments':
        explanation = 'The safest accepted installment schedule from the supplied payment options keeps the forecast above the minimum balance.'
    elif method == 'wait':
        explanation = f'No full payment is safe today. The earliest safe full-date is {earliest_str}, so waiting is the selected recommendation.'
    elif method == 'not_recommended':
        explanation = 'No eligible plan is safe under the 90-day forecast.'
    else:
        explanation = 'No valid recommendation was possible.'

    return {
        'request_id': request['request_id'],
        'amount_safe_to_pay': money(safe_val),
        'affordability_status': status,
        'recommended_payment_method': method,
        'payment_plan': plan,
        'earliest_date_for_full_payment': earliest_str,
        'spending_changes_needed': 'none',
        'decision_explanation': explanation,
    }

# Main
rows = []
for req in requests:
    rows.append(generate_row(req))

fieldnames = ['request_id','amount_safe_to_pay','affordability_status','recommended_payment_method','payment_plan','earliest_date_for_full_payment','spending_changes_needed','decision_explanation']
with open(os.path.join(ROOT_DIR, 'output.csv'), 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)

# Evaluation usage report stub
os.makedirs(os.path.join(ROOT_DIR, 'code', 'evaluation'), exist_ok=True)
with open(os.path.join(ROOT_DIR, 'code', 'evaluation', 'usage_report.md'), 'w') as f:
    f.write('# usage_report.md\n\nModel provider(s): none\nModel name(s): none\nModel calls: 0\nInput tokens: 0\nOutput tokens: 0\nTotal tokens: 0\nAverage tokens per request: 0\nEstimated total cost: $0.00\nEstimated cost per request: $0.00\n')
