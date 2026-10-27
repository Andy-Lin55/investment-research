# Candidate evidence cards

This independent module is not imported by the daily/weekly production jobs. It accepts supplied public/synthetic metadata, suggests a category, validates its structure and returns a candidate for human review. It does not fetch documents, call AI, write Notion/OneDrive, persist research tasks or execute trades.

Main categories: `trading_flows`, `fundamentals` (including prices and valuation), and `policy`. Unknown news remains unclassified pending review. Document type must be assigned from authoritative source metadata; title classification is not independent source verification. A supplied category/subtype is validated against the taxonomy and remains a suggestion.

`facts`, `calculations` and `interpretations` are separate fields. `human_review` always starts `pending`; `automatic_validation=structure_only` is not financial fact-checking. Earnings types and explicitly material disclosures flag research as required even if importance is Low or content is missing. These flags do not constitute a durable queue: that integration is a later phase.

Plan/draft stages may be conservative hints from supplied announcement titles. Executed status requires explicit source metadata; completion words in titles are insufficient because they may be negated or conditional. All stages need source review. No inflow, net buying, policy exposure or investment recommendation is inferred from an event label.

Event identity is issuer plus source locator/document ID. Ingestion time is excluded. A revision is based on source text, so altered/replaced source material retains event identity but gets another revision. Source locators must identify the document, not a generic site homepage. An amended document issued under a new URL should supply the same canonical `document_id` if that identity has been established. Do not guess it.

Offline preview:

```sh
python -m research.preview < supplied-evidence.json
```

Input fields: `issuer_id`, `ticker`, `title`, `document_type`, `source_url`, timezone-aware `published_at` and `ingested_at`, `content`, and optional `facts`, `calculations`, `interpretations`, `source_locator`, `tags`, `document_id`, `is_material`, `event_stage`, `category` plus `subtype`. Each calculation requires a statement, formula description and finite numerical inputs. Preview never evaluates arbitrary formula text.

Historical/forecast statement schemas, units/period reconciliation, human review persistence, automatic task scheduling, Notion properties and OneDrive publication are subsequent deliverables. Do not treat an information card as a financial model.
