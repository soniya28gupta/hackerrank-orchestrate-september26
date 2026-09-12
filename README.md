# Buy or Wait? — AI Financial Affordability Agent

This repository contains a deterministic Python implementation for the HackerRank Orchestrate September 2026 challenge, Buy or Wait? — AI Financial Affordability Agent. The project reads the evaluation inputs in the repository’s dataset/ folder and writes the required prediction file output.csv in the repository root.

## Problem overview

The task is to determine, for every request in dataset/requests.csv, whether a requested expense can be safely afforded while protecting the user’s financial buffer. The decision must consider the user profile, current available balance, minimum balance to keep, recurring expenses, essential commitments, confirmed income, future events, installment or payment options, request dates, desired completion date, and any available evidence in messages.csv and images.csv. The result is a row in the required output schema used by the evaluation harness.

The implementation in this repository does not perform any external model inference. It is a deterministic local workflow implemented in the Python files under code/ that loads the CSV context files and builds an affordability recommendation from the scenario data.

## Solution overview

The actual implementation is a deterministic local solver in code/main.py. It loads challenge data from dataset/, normalizes values, converts amounts into the home_currency defined in the user profile, constructs a projected cash-flow timeline for a 90-day horizon, applies status and duplicate filtering to events, compares possible payment options and request deadlines, and emits the required solver row to the root-level output.csv.

The repository also includes a small deterministic helper in evaluation/validate_usage_report.py to validate the structure of output.csv and the usage-report artifact. It does not change the financial decision logic.

## End-to-end pipeline

The implemented pipeline is:

1. dataset/requests.csv is loaded as the evaluation request objects.
2. dataset/financial_profiles.csv, dataset/financial_events.csv, dataset/request_payment_options.csv, dataset/exchange_rates.csv, dataset/messages.csv, and dataset/images.csv are loaded as context and supporting records.
3. The system normalizes fields, converts event amounts using the configured exchange_rates.csv row and profile currency context, and records a home_currency view of monetary amounts.
4. It builds a filtered event timeline for each user by using the event_valid() rules and event date selection from settlement_date or event_date within a 90-day recomputation horizon.
5. It separately loads the available payment options and groups them by request_id in options_by_request.
6. It reads messages.csv and images.csv as structured context files, but the actual code present in this repository does not apply any VLM/LLM image interpretation or message inference. It only supplies the data files and keeps the implementation deterministic.
7. It forecasts the user’s future cash state, then computes the largest safe amount payable on the request date using the deterministic binary-search-style amount_safe() routine.
8. It computes the earliest date for a safe full payment with earliest_full_date().
9. It chooses the best method among full_payment, partial_payment, installments, wait, or not_recommended using choose_plan() and validates the candidate with plan_candidate_valid().
10. It emits an ordered row via generate_row() and writes the required columns to the repository-root output.csv.
11. It writes a deterministic usage report artifact at evaluation/usage_report.md.

Only the stages above are implemented in the repository. There is no external model, no VLM, and no API calls in the current implementation.

## Financial safety logic

The repository uses the deterministic rules in code/main.py to protect the account state across the 90-day forecast window:

- minimum_balance_to_keep is enforced in the simulation path. The solver uses simulate_safe() to ensure projected balances never fall below the user’s configured minimum balance after accounting for projected obligations and payments.
- Recurring and one-off events are loaded from financial_events.csv and converted into the user’s home_currency. Credit events can be treated as funds when they are settled, and debit events are treated as expenses when their event status is valid. Pending credit events are filtered out, following the challenge contract’s guidance.
- Confirmed future transactions are kept only when the event has a valid status and is within the 90-day forecast window. That includes resolved scheduled and settled events while excluding cancelled, failed, duplicate, and unrealized rows. Pending credit records are not counted as cash.
- event_valid() filters events by the accepted historical and future statuses and returns only those valid in the requested date range.
- Duplicate source rows and cancelled or failed repeated event representations are dropped during data filtering.
- Currency normalization is performed through the rate_for(day, from_currency, to_currency) lookup in exchange_rates.csv and converted into the user’s home_currency using amount_to_home().
- The forecast horizon is fixed at request_date to request_date + 90 days.
- Deadline handling follows the constraints in the payment-plan validator and from the supplied request’s desired_completion_date.

## Payment methods

The implemented solver method selection is intentionally deterministic:

- full_payment: selected when the safe amount reaches the requested amount and the user’s payment preferences permit a full payment. It emits a one-step plan matching the requested-date schedule.
- partial_payment: considered only when allows_partial_payment is true, the user profile considers the payment method, and the deterministic bounds check returns 0 < safe_val < requested_amount. It emits exactly two payments: the amount safe on the request date and then the remaining balance on the earliest safe full-pay date, constrained by the deadline.
- installments: recognized when the profile’s payment_methods_user_will_consider includes installments and there are valid installment rows in the grouped options_by_request structure. The plan generator exactly reuses the represented source schedule string from the selected option row.
- wait: selected when a safe full payment is not available immediately but occurs on an earliest safe future date, and the use of a single-date wait plan remains consistent with the forecast.
- not_recommended: emitted when no valid plan satisfies the 90-day simulation or the selected method is rejected by the planner’s validation gates.

The current code returns a not_recommended method and a none payment plan for cases where no eligible safe plan exists.

## Payment-plan validation

The repository includes centralized deterministic validation functions in code/main.py:

- plan_candidate_valid() checks requested method and plan consistency before returning a recommendation.
- Installments must exactly match the source payment_amount representation and schedule from a valid option row in request_payment_options.csv.
- Payment dates must be chronological in the emitted payment_plan string.
- Every committed plan component must be positive and parseable as a valid date and numeric amount.
- Partial payments must be emitted in the exact layout request_date:first_amount|earliest_safe_date:remaining_amount, with the second date not after the request’s desired_completion_date and the two amounts summing to the requested value.
- full_payment requires a single component whose date is the request date and amount equals the requested amount.
- generate_plan_for_option() preserves the original option payment_amount string shape rather than forcing a new decimal string representation.

This is part of the repository’s deterministic integrity contract and is what the tests enforce.

## Flexible spending changes

The current solver output path itself writes spending_changes_needed as fixed none. It does not yet implement the actual stop:<event_id> or reduce_to:<event_id>:<new_amount> emission workflow in code/main.py. In the README, the deterministic validation tests and contract rules recognize that flexible recurring spending changes are part of the expected output semantics, but the present repository implementation writes none because the code currently generates a finite set of deterministic rows without implementing the spending-change engine. Only recurring flexible spending events are the valid target concept in the challenge contract; the existing implementation does not claim to implement those reductions yet.

## Messages and images

The repository includes dataset/messages.csv and dataset/images.csv as context files. code/main.py reads the repository dataset but does not implement message extraction, image OCR, image analysis, or VLM interpretation. It does not transform messages or images into financial facts. It intentionally treats the message/image files as static, non-invasive context and does not invent evidence from them. This is an explicit limitation of the current implementation.

## AI/model usage

This repository does not call any external LLM, VLM, or model service. The solution is a deterministic local Python solver based on the CSV files in dataset/. There are therefore no model provider names, model names, model calls, prompt tokens, completion tokens, total tokens, or model cost entries to fabricate. The root-level evaluation/usage_report.md records the correct zero-model facts:

Model provider(s): none
Model name(s): none
Model calls: 0
Input tokens: 0
Output tokens: 0
Total tokens: 0
Average tokens per request: 0
Estimated total cost: $0.00
Estimated cost per request: $0.00

The included evaluation/validate_usage_report.py utility confirms that the repository report remains reproducible and deterministic.

## Running the project

The current repository is executed from the root folder using PowerShell:

```powershell
python code/main.py
```

This command reads the deterministic dataset context and creates the required repository-root file:

```text
output.csv
```

The generated output.csv contains one row per request and one row per payment recommendation or rejection decision following the specified schema.

## Testing

The verification suite is available in the repository:

```powershell
pytest -v
```

The current verified result is 23 passed in 4.13s from the deterministic contract suite in tests/test_buy_wait_contract.py.

## Output schema

The generated output.csv must contain the following eight columns in order:

```text
request_id,amount_safe_to_pay,affordability_status,recommended_payment_method,payment_plan,earliest_date_for_full_payment,spending_changes_needed,decision_explanation
```

The solver is deterministic and writes exactly that header order from code/main.py.

## Dataset

The repository’s dataset/ directory contains the official submission context and input files. It must not be modified. The data files in dataset/ are part of the challenge contract and supply the official evaluation data. dataset/requests.csv is the evaluation source for the row shape that must be reproduced in output.csv.

## Reproducibility

To reproduce the repository output and regenerate the same root-level output.csv:

```powershell
python code/main.py
```

The output row generation is deterministic because the solver reads the same files in the same order and writes the listed fields using csv.DictWriter. The optional evaluation/validate_usage_report.py script can be run for deterministic validation of the report and the schema relation, if the repository is being evaluated locally.

## Evaluation

The repository contains a project-specific evaluation folder:

```text
evaluation/
    usage_report.md
    validate_usage_report.py
```

The folder exists at the repository root and contains the required usage-report artifact and a simple deterministic validation utility. The report documents the zero-model, zero-token, zero-cost status because the implementation is strictly local and deterministic and performs no model invocation.

## Project structure

```text
.
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── code/
│   ├── main.py
│   └── evaluation/
│       ├── main.py
│       └── usage_report.md
├── dataset/
│   ├── requests.csv
│   ├── sample_requests.csv
│   ├── financial_profiles.csv
│   ├── financial_events.csv
│   ├── request_payment_options.csv
│   ├── exchange_rates.csv
│   ├── messages.csv
│   ├── images.csv
│   └── media/images/
├── evaluation/
│   ├── usage_report.md
│   └── validate_usage_report.py
├── output.csv
├── tests/
│   └── test_buy_wait_contract.py
└── log.txt
```

## Submission notes

HackerRank expects the submission package to include these artifacts:

- code.zip
- output.csv
- log.txt

The dataset/ directory must remain sources of truth for challenge data and must not be included in the submitted code.zip. The current repository also includes the evaluation/usage_report.md artifact as the required usage-report file in the evaluation folder and this update notes that the implementation performs no external model calls.

## Security

Secrets or API keys must be supplied through environment variables and never committed to the repository. This repository’s implementation is purely deterministic and local; it contains no API keys, no VLM keys, and no external model configuration in the checked-in code.

## Limitations

The current implementation has genuine limitations that are visible in the code:

- It does not use messages.csv or images.csv for evidence extraction or scenario enrichment; those files are present as context only.
- It does not implement the requested spending_changes_needed action generator. It currently writes none in the generated output file.
- It does not implement a real VLM/LLM or external model workflow. It is a deterministic local Python financial planner.
- It does not emit any custom model call or token usage accounting, because no external model is invoked.
