# PSX financial statements

Source-linked historical financial figures for 20 Pakistan Stock Exchange companies. This compilation is for researchers, students and people building reproducible financial datasets. It contains extracted reported figures from official filings, with partial coverage as of the cutoff below. It is not a complete ten-year database.

Data cutoff: **2026-10-19**. Published facts: **1863**. Admitted checked: **441**. Collected checked: **1422**.

Main tables contain primary income, balance-sheet and cash-flow statements plus EPS, shares and dividends. Other reported note detail is retained under statement `other` in facts.csv only. Only reported historical financial data is included. There are no forecasts, valuations, price-implied expectations, scenarios or investment recommendations. Filing documents and images are not distributed: this repository contains links only.

## Companies and coverage

| Symbol | Company | Sector | Annual years | Quarter periods | Half-year periods | Nine-month periods | Admitted | Collected |
|---|---|---|---|---:|---:|---:|---:|---:|
| [ATRL](ATRL/README.md) | Attock Refinery Limited | Refinery | 2024, 2025, 2026 | 0 | 0 | 0 | 59 | 23 |
| [BOP](BOP/README.md) | The Bank of Punjab | Commercial Banks | 2024, 2025 | 6 | 1 | 2 | 26 | 212 |
| [CNERGY](CNERGY/README.md) | Cnergyico PK  Limited | Refinery | 2025 | 4 | 2 | 2 | 0 | 171 |
| [DGKC](DGKC/README.md) | D.G. Khan Cement Company Limited | Cement | none | 0 | 0 | 0 | 0 | 0 |
| [ENGROH](ENGROH/README.md) | Engro Holdings Limited | Inv. Banks / Inv. Cos. / Securities Cos. | none | 0 | 0 | 0 | 0 | 0 |
| [FFC](FFC/README.md) | Fauji Fertilizer Company Limited | Fertilizer | 2024, 2025 | 0 | 0 | 0 | 8 | 25 |
| [GAL](GAL/README.md) | Ghandhara Automobiles Limited | Automobile Assembler | 2024, 2025 | 0 | 0 | 0 | 0 | 52 |
| [HBL](HBL/README.md) | Habib Bank Limited | Commercial Banks | none | 0 | 0 | 0 | 0 | 0 |
| [HUBC](HUBC/README.md) | The Hub Power Company Limited | Power Generation & Distribution | 2025, 2026 | 0 | 0 | 0 | 72 | 0 |
| [LUCK](LUCK/README.md) | Lucky Cement Limited | Cement | none | 0 | 0 | 0 | 0 | 0 |
| [MARI](MARI/README.md) | Mari Energies Limited | Oil & Gas Exploration Companies | 2025 | 0 | 0 | 0 | 1 | 0 |
| [MEBL](MEBL/README.md) | Meezan Bank Limited | Commercial Banks | none | 0 | 0 | 0 | 0 | 0 |
| [MLCF](MLCF/README.md) | Maple Leaf Cement Factory Limited | Cement | 2022, 2023, 2024, 2025, 2026 | 6 | 2 | 2 | 95 | 406 |
| [NBP](NBP/README.md) | National Bank of Pakistan | Commercial Banks | 2023, 2024, 2025 | 0 | 0 | 0 | 0 | 254 |
| [NRL](NRL/README.md) | National Refinery Limited | Refinery | 2024, 2025, 2026 | 0 | 0 | 0 | 54 | 9 |
| [OGDC](OGDC/README.md) | Oil & Gas Development Company Limited | Oil & Gas Exploration Companies | 2024, 2025, 2026 | 0 | 0 | 0 | 32 | 20 |
| [PPL](PPL/README.md) | Pakistan Petroleum Limited | Oil & Gas Exploration Companies | 2024, 2025 | 3 | 1 | 2 | 0 | 181 |
| [PRL](PRL/README.md) | Pakistan Refinery Limited | Refinery | 2024, 2025, 2026 | 0 | 0 | 0 | 65 | 6 |
| [PSO](PSO/README.md) | Pakistan State Oil Company Limited | Oil & Gas Marketing Companies | 2024, 2025 | 0 | 0 | 0 | 0 | 60 |
| [UBL](UBL/README.md) | United Bank Limited | Commercial Banks | 2024, 2025 | 0 | 0 | 0 | 29 | 3 |

Exact dates, statements present, per-stock missing-publication counts and exclusion reasons are in [coverage.md](coverage.md). A year in this table means at least one line exists, not that every statement is complete.

## Files and use

`data/facts.csv` is the source of truth: one row per reported figure. Each company folder contains a readable history, an annual wide CSV and an interim wide CSV named `quarterly.csv`. Its interim columns explicitly distinguish quarters, half-years and nine-month periods.

Wide CSV row identifiers are statement, line item, basis, unit and currency; period columns are `YYYY-MM-DD|period_type`. Join a wide cell back to facts.csv using symbol, line item, period end, period type and basis to retrieve its source. Blank cells mean missing, never zero. Company Markdown tables link each figure to a complete source reference.

### Python / pandas example

The exporter itself requires only the Python standard library. This optional analysis example uses pandas:

```python
import pandas as pd
facts = pd.read_csv("data/facts.csv", dtype={"value": "string", "page": "Int64"})
rows = facts.query("symbol == 'MLCF' and period_type == 'annual' and basis == 'consolidated'")
print(rows[["line_item", "period_end", "value", "unit", "source_url", "page"]])
```

### Spreadsheet example

Import `data/facts.csv` through your spreadsheet’s text/CSV import with UTF-8 encoding. Keep identifiers as text. Filter `symbol`, then `basis` and `period_type`; use `period_end` as pivot columns and `line_item` as rows. Filter to one unit/currency and check for duplicates before pivoting. Keep the provenance columns beside any extracted figure. Do not sum EPS or mix bases or cumulative and discrete periods.

## Data dictionary

| Column | Meaning |
|---|---|
| symbol | PSX ticker from the fixed roster. |
| sector | Retained PSX sector label. |
| statement | income_statement, balance_sheet, cash_flow or other (reported financial notes/equity). |
| line_item | Lowercase normalized source metric identifier; punctuation becomes underscores. Supplemental identifiers retain their supplemental_reported prefix. Different issuer captions are not automatically equivalent. |
| reported_label | Source extraction caption, unchanged apart from CSV encoding. |
| period_end | ISO date of the reported period end; balance sheet figures are snapshots. |
| period_type | annual, quarter, half_year or nine_months. No derived quarters. |
| fiscal_year | Year in which the fiscal year ends, based on the sourced fiscal calendar. |
| basis | consolidated or standalone; standalone maps the source term unconsolidated. |
| value | Reported numeric value normalized once to the stated base unit; decimal text. |
| unit | Base unit from the record (for example PKR, PKR/share, shares or percent). |
| currency | Explicit source currency; never filled from a general default. |
| status | admitted_checked or collected_checked; see methodology. |
| checks_passed | Semicolon-separated structural/arithmetic checks. cross_check_unavailable means no supported complete equation covers this row. |
| document_id | Retained identifier of the official filing. |
| source_url | HTTPS link to the official PSX DPS or verified issuer source. |
| page | Physical PDF page, starting at 1. |
| published_on | Verified filing publication date or explicitly documented filing-availability date, ISO format; never a retrieval or inspection clock. Date evidence is in lineage.csv. |
| retrieved_on | Source retrieval date, ISO format; not the exporter execution date. |

## Methodology

Sources are official PSX DPS filings and issuer investor-relations financial reports. The upstream collection uses statement parsers, text/geometry extraction and model-assisted visual transcription. This exporter does not fetch, open or copy those documents. It reads retained JSON observations and source metadata, and permits only reported financial statement cells and supported financial notes.

`admitted_checked` means an observation passed the admitted annual-floor checks or was classified eligible in the retained reconciliation. The exporter also validates its raw numeric scale, provenance and period structure. `collected_checked` means a collected observation passed this export’s structural and numeric checks and its batch had no detected unresolved failure. It has not been admitted to the private product and has no independent audit assurance. Arithmetic checks test consistency, not transcription accuracy.

The exporter independently recalculates supported record-bound linear equations and available balance, profit-attribution, bank-income and cash-subtotal identities using Decimal arithmetic. Operands must share document, period, duration, basis, unit and currency. Tolerances cannot exceed half a printed unit per operand. Unsupported scalar summary assertions are not treated as verified equations. A row without a complete supported equation is labelled `cross_check_unavailable`; it may still pass source/structure/raw-scale checks. It is not described as arithmetically reconciled.

The retained annual floor can select an otherwise checked reported fact even where a narrower reconciliation classifies it outside that reconciliation’s metric scope. That approved annual-floor selection governs its admission status. Other reconciliation observations must be eligible; audit-only, quarantined and derived records are excluded.

Explicit batch replacement links and numeric versions of the same named scope replace older deliveries before selection. Incomplete envelopes, hash mismatches, failed/unresolved batches and held observations are excluded. The inputs are not modified. Structural unknowns are excluded rather than filled.

For the same symbol, normalized line item, period end, period type and basis, admitted checked data takes precedence over collected data. Equal duplicate observations are deduplicated deterministically, preferring the latest publication date and then stable source identifiers. Conflicting collected amounts or statement/unit semantics are withheld together in [data/conflicts.csv](data/conflicts.csv), with both sources. Conflicting admitted candidates are also withheld. A selected admitted fact resolves lower-trust collected disagreement by the stated precedence. Only explicit upstream replacement resolves a collected restatement; a later filing alone does not silently overwrite a different collected value. Consolidated and standalone remain separate.

## Conventions

Values use the unit shown in each row. Monetary scales such as thousands and millions are applied once to raw printed values; do not multiply again. Currency is explicit. Per-share amounts, share counts and percentages retain their distinct units. Parentheses are parsed as negative amounts. Other reported signs are preserved; expenses are not automatically made negative. Printed dashes or ambiguous numeric cells are not filled with zero.

Fiscal year ends are listed per company. They come from the admitted annual floor or a single consistent calendar supported by collection-manifest evidence; unknown calendars prevent export. A fiscal year is labelled by its ending year. Corporate reorganizations and changes of presentation can limit comparability; ticker continuity does not prove an unchanged group perimeter.

Annual flows require twelve-month duration. Quarter flows are three months; half-year and nine-month flows remain cumulative. Interim balance-sheet snapshots use the explicit period classification or the evidenced fiscal calendar’s three-, six- or nine-month position. No subtraction creates a quarter, EPS, total or missing line. Balance sheets are point-in-time figures, never flows.

## Limitations and gaps

Collection is ongoing. Available years, statements, bases and line items differ by company. Publication dates are often unknown even when a retained official report exists; those observations are withheld. Dates are resolved by document ID, official PSX document URL, issuer URL and content hash across the document catalog, retained financial series, collection manifests and available backup-map metadata. Only published_at or an explicitly documented filing-owned available_on is accepted. Ambiguous dates stay excluded. Retrieval and inspection clocks are never used as publication. See the per-stock counts in coverage.md. Those counts cover otherwise qualifying active observations and are not counts of every cell in every filing; duplicate deliveries of the same observation count once.

Labels and metric identifiers reflect retained extractions and may differ across collectors or issuers. Source and statement context must be checked before comparing lines. Arithmetic agreement does not establish correct transcription, correct accounting interpretation or a complete statement. The exporter cannot verify document contents without reopening the linked filing. It does not infer issuer-domain authority from a collected URL alone: issuer domains must already be present in retained official-source metadata.

## Reproduce and update

Python 3.12, standard library only. Inputs are local retained research/collection files and are not distributed. Their filenames and SHA-256 digests are recorded in `data/export_manifest.json`; row-level input references are in `data/lineage.csv`. These files contain generated provenance metadata, not copies of the private inputs. The backup map is consulted only for original URL and filing-date metadata; its document files are never read. Date evidence for published rows is recorded in data/lineage.csv. Source-only checks of unresolved DGKC, HBL and LUCK observations are in [data/date_resolution_review.csv](data/date_resolution_review.csv); withheld financial values are not distributed in that review.

From the repository root:

```powershell
& "C:/Users/MWasa/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe" scripts/export.py --cutoff 2026-10-19
& "C:/Users/MWasa/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe" scripts/validate.py --self-test --spot-check 25
```

Portable equivalent: `python scripts/export.py --cutoff YYYY-MM-DD --admitted PATH --batches PATH --backup-map PATH`, then `python scripts/validate.py --self-test --spot-check 25`. `--output PATH` defaults to this repository. The cutoff is inclusive and fixed for reproducibility. Publication, retrieval and financial period dates after it are excluded. Identical input bytes, arguments and existing changelog history produce identical output bytes. Inputs are hashed again before writing; concurrent changes abort the export. The exporter does not create a network connection, commit or push.

Refresh after new filings have been collected and checked. Review validation, coverage and exclusions before publishing. Keep releases and meaningful changes in [CHANGELOG.md](CHANGELOG.md). Existing history is preserved, with an export digest appended when the canonical facts change. This is a manually run export, not a scheduled process.

## Cite

Cite: Wasay Ijaz, “PSX financial statements,” wasayijaz/psx-financials, version 1, data cutoff 2026-10-19, plus the Git commit used. Cite the original issuer/PSX filing, document identifier and page for every figure you reuse. Preserve provenance and status when redistributing data and state any modifications.

## Disclaimer

Research data compiled from public filings. It may contain extraction errors. Verify figures against the linked official filing before relying on them. This compilation is not investment advice and makes no performance claims. It is provided without warranty.

## Licenses and attribution

Exporter and validator code: [MIT](LICENSE). Original compilation and organization of the data: [CC BY 4.0](DATA-LICENSE), attributed to Wasay Ijaz with PSX and the issuers identified as the original financial-data sources. No ownership or relicensing of the underlying filings is claimed; issuer and PSX rights remain with their respective owners. No PDFs, images or other source documents are included.

## Report an error

Open a [GitHub issue](https://github.com/wasayijaz/psx-financials/issues) with the ticker, line item, period, basis, document URL, page, observed value and proposed correction. Include the repository commit. Link to the official filing; do not upload a document copy. Corrections require source evidence and are recorded in the changelog.
