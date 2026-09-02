# Modern Workplace Assistant

Foundry agent samples built on top of Microsoft's `enterprise-agent-tutorial` (Tutorial 1),
grounded on a small set of fictional Contoso Corp policy documents.

## Files

- `main.py` - original tutorial baseline, agent grounded via a live SharePoint connection
- `main_file_search.py` - single agent grounded via a Foundry vector store + `FileSearchTool`
  over the local `.docx` files (no SharePoint connection required), plus `MCPTool` for
  Microsoft Learn
- `main_multi_agent.py` - multi-agent version: `hr-policy-agent`, `it-security-policy-agent`,
  and `technical-docs-agent` specialists, routed by a `workplace-orchestrator` agent using
  `FunctionTool`-based dispatch
- `sharepoint-sample-data/` - 7 fictional Contoso Corp policy documents (remote work, security
  guidelines, data governance, collaboration standards, expense/travel, onboarding, acceptable use)
- `evaluate.py` / `questions.jsonl` / `evaluation_results.json` - cloud evaluation sample and
  test questions from the original tutorial

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate   # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
az login
```

Set `endpoint` and `model_name` near the top of `main_file_search.py` / `main_multi_agent.py`
to your own Foundry project endpoint and deployed model name, then run either script directly:

```bash
python main_multi_agent.py
```
