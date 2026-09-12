# usage_report.md

Model provider(s): none
Model name(s): none
Model calls: 0
Input tokens: 0
Output tokens: 0
Total tokens: 0
Average tokens per request: 0
Estimated total cost: $0.00
Estimated cost per request: $0.00

No external model calls were made. The current solution in code/main.py is a deterministic, local Python CSV-driven financial decision generator that reads dataset/requests.csv, dataset/*.csv, and writes output.csv without any LLM, VLM, external API, or model inference call. Because no model was invoked, all per-model, per-request, and overall model usage/token/cost values are zero by definition.

Model/source-of-truth evidence:
- Source implementation inspected: code/main.py
- Source files for deterministic logic: code/main.py and code/evaluation/main.py
- Final generation command used: python code/main.py
- Verification command run: python code/main.py followed by pytest -v
