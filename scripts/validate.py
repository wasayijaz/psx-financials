#!/usr/bin/env python3
"""Validate the public compilation, optionally checking rows against read-only inputs."""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True
import argparse
import ast
from collections import Counter
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import random
import re
from urllib.parse import urlsplit

from export import (SYMBOLS, FIELDS, KEY, FORBIDDEN, DEFAULT_ADMITTED,
                    DEFAULT_BATCHES, key, number, decimal_text, wide_rows,
                    csv_bytes, resolve, arithmetic, period_type, main_table_fact, FilingDates)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_csv(path):
    with path.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def allowed_paths():
    paths = {'README.md', 'coverage.md', 'LICENSE', 'DATA-LICENSE', '.gitignore', '.gitattributes', 'CHANGELOG.md',
             'scripts/export.py', 'scripts/validate.py', 'data/facts.csv', 'data/conflicts.csv',
             'data/exclusions.csv', 'data/lineage.csv', 'data/export_manifest.json', 'data/spot_checks.csv', 'data/date_resolution_review.csv'}
    for symbol in SYMBOLS:
        paths.update(symbol + '/' + name for name in ('README.md', 'annual.csv', 'quarterly.csv'))
    return paths


def inspect_files(root):
    found = set()
    total = 0
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if relative.parts[0] == '.git':
            continue
        require(not path.is_symlink(), 'Symlink forbidden: ' + relative.as_posix())
        if not path.is_file():
            continue
        name = relative.as_posix()
        require(name in allowed_paths(), 'Unexpected public repository file: ' + name)
        payload = path.read_bytes()
        require(b'\x00' not in payload, 'Binary file: ' + name)
        text = payload.decode('utf-8')
        require(not text.startswith(('%PDF', '\x89PNG', 'GIF8', 'PK\x03\x04')), 'Document/image content: ' + name)
        require(not re.search(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)', text), 'Possible secret: ' + name)
        if name.endswith('.py'):
            ast.parse(text, filename=name, feature_version=(3, 12))
        found.add(name)
        total += len(payload)
    require(allowed_paths() - {'data/spot_checks.csv'} <= found, 'Required public files missing')
    return len(found), total


def validate(root, check_audit=True):
    files, size = inspect_files(root)
    fields, facts = read_csv(root / 'data/facts.csv')
    require(fields == FIELDS, 'facts.csv schema mismatch')
    require(not any(FORBIDDEN.search(f) for f in fields), 'Forbidden field name')
    require(facts, 'No exported facts')
    manifest = json.loads((root / 'data/export_manifest.json').read_text(encoding='utf-8'))
    cutoff = manifest['data_cutoff']
    seen = set()
    for row in facts:
        require(all(row.values()), 'Missing fact field: ' + str(key(row)))
        require(row['symbol'] in SYMBOLS, 'Outside roster')
        require(row['statement'] in ('income_statement', 'balance_sheet', 'cash_flow', 'other'), 'Invalid statement')
        require(row['period_type'] in ('annual', 'quarter', 'half_year', 'nine_months'), 'Invalid period type')
        require(row['basis'] in ('consolidated', 'standalone'), 'Invalid basis')
        require(row['status'] in ('admitted_checked', 'collected_checked'), 'Invalid status')
        require(not FORBIDDEN.search(row['line_item'] + ' ' + row['reported_label']), 'Forbidden financial line')
        require(decimal_text(number(row['value'])) == row['value'], 'Noncanonical numeric value')
        for field in ('period_end', 'published_on', 'retrieved_on'):
            require(date.fromisoformat(row[field]).isoformat() == row[field] and row[field] <= cutoff, 'Invalid or future ' + field)
        require(row['published_on'] >= row['period_end'], 'Publication before period end')
        require(int(row['page']) >= 1 and str(int(row['page'])) == row['page'], 'Invalid physical page')
        parsed = urlsplit(row['source_url'])
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password, 'Invalid official URL')
        require(key(row) not in seen, 'Duplicate canonical fact: ' + str(key(row)))
        seen.add(key(row))
    require(manifest['stocks'] == len(SYMBOLS) and manifest['facts'] == len(facts), 'Aggregate counts mismatch')
    require(dict(Counter(r['status'] for r in facts)) == manifest['status_counts'], 'Status count mismatch')
    for symbol in SYMBOLS:
        fs = [r for r in facts if r['symbol'] == symbol]
        company = manifest['companies'][symbol]
        require(company['facts'] == len(fs), symbol + ' fact count mismatch')
        require(company['admitted'] == sum(r['status'] == 'admitted_checked' for r in fs), symbol + ' admitted count mismatch')
        require(company['collected'] == sum(r['status'] == 'collected_checked' for r in fs), symbol + ' collected count mismatch')
        for ptype, field in [('annual', 'annual_years'), ('quarter', 'quarters'), ('half_year', 'half_years'), ('nine_months', 'nine_months')]:
            require(company[field] == sorted({r['fiscal_year'] if ptype == 'annual' else r['period_end'] for r in fs if r['period_type'] == ptype}), symbol + ' coverage mismatch: ' + ptype)
        require(company['statements'] == sorted({r['statement'] for r in fs}), symbol + ' statements mismatch')
        text = (root / symbol / 'README.md').read_text(encoding='utf-8')
        table_facts = [f for f in fs if main_table_fact(f)]
        require(text.count('<a id="s') == len(table_facts), symbol + ' Markdown figure count mismatch')
        for annual, name in ((True, 'annual.csv'), (False, 'quarterly.csv')):
            expected_fields, expected_rows = wide_rows(fs, annual)
            actual_fields, actual_rows = read_csv(root / symbol / name)
            require(expected_fields == actual_fields and expected_rows == actual_rows, symbol + '/' + name + ' differs from facts.csv')
            require(sum(bool(r[p]) for r in actual_rows for p in actual_fields if '|' in p) == sum((r['period_type'] == 'annual') == annual for r in table_facts), symbol + ' wide cell count mismatch')
        # Every Markdown figure reference must point back to a fact with full provenance.
        for row in table_facts:
            reference = f"[{row['document_id']}]({row['source_url']}), page {row['page']}; published {row['published_on']}; retrieved {row['retrieved_on']}; period {row['period_end']} ({row['period_type']}); {row['basis']}; {row['unit']}, {row['currency']}; {row['status']}"
            require(reference in text, symbol + ' missing Markdown source reference')
    _, lineage = read_csv(root / 'data/lineage.csv')
    require(len(lineage) == len(facts) and {key(r) for r in lineage} == seen, 'Lineage coverage mismatch')
    hashes = {r['input']: r['sha256'] for r in manifest['input_files']}
    for row in lineage:
        require(row['input_sha256'] == hashes.get(row['input']), 'Lineage input hash mismatch')
        require(re.fullmatch('[a-f0-9]{64}', row['source_sha256']), 'Missing source content hash')
        require(row['date_input'] in hashes and row['date_field'] in ('published_at', 'available_on'), 'Missing filing-date lineage')
        fact = next(r for r in facts if key(r) == key(row))
        require(row['date_value'][:10] == fact['published_on'], 'Filing-date lineage mismatch')
    conflict_fields, conflicts = read_csv(root / 'data/conflicts.csv')
    require(conflict_fields == FIELDS + ['reason'], 'Conflict schema mismatch')
    require(len(conflicts) == manifest['conflict_observations'], 'Conflict count mismatch')
    require(all(key(r) not in seen for r in conflicts), 'Conflict leaked into main table')
    for row in conflicts:
        require(all(row[f] for f in FIELDS), 'Incomplete conflict provenance')
    if check_audit and (root / 'data/spot_checks.csv').exists():
        _, audit = read_csv(root / 'data/spot_checks.csv')
        require(len(audit) >= min(15, len(facts)), 'Too few spot checks')
        require(all(r['result'] == 'PASS' and key(r) in seen for r in audit), 'Failed or obsolete spot check')
    print(f'PASS: {len(SYMBOLS)} stocks; {len(facts)} facts; {len(conflicts)} conflict observations; {files} UTF-8 public files ({size:,} bytes); no documents, images, binaries, unexpected files or detected secrets.')
    return facts, lineage


def spot_check(root, facts, lineage, admitted, batches, count):
    """Stratified reproducible sample; compare source values without copying raw inputs."""
    manifest = json.loads((root / 'data/export_manifest.json').read_text(encoding='utf-8'))
    lineage = {key(r): r for r in lineage}
    cache = {}
    def load(path):
        if path not in cache:
            cache[path] = json.loads(path.read_text(encoding='utf-8-sig'))
        return cache[path]
    documents = load(admitted / 'state/company_documents.json')['documents']
    rng = random.Random(20261019)
    chosen = []
    for symbol in SYMBOLS:
        pool = sorted((r for r in facts if r['symbol'] == symbol), key=key)
        if pool:
            chosen.append(rng.choice(pool))
    # Required focus for this date-resolution pass: at least three per target
    # wherever qualifying exported data exists; never invent an eligible figure.
    for symbol in ('DGKC', 'HBL', 'LUCK'):
        pool = [r for r in sorted(facts, key=key) if r['symbol'] == symbol and key(r) not in {key(c) for c in chosen}]
        chosen += rng.sample(pool, min(2, len(pool)))
    remaining = [r for r in sorted(facts, key=key) if key(r) not in {key(c) for c in chosen}]
    # Check both statuses where they exist, in addition to geographic/issuer spread.
    for status in ('admitted_checked', 'collected_checked'):
        if not any(r['status'] == status for r in chosen):
            pool = [r for r in remaining if r['status'] == status]
            if pool:
                extra = rng.choice(pool)
                chosen.append(extra)
                remaining.remove(extra)
    chosen += rng.sample(remaining, min(max(count - len(chosen), 0), len(remaining)))
    report = []
    for row in sorted(chosen, key=key):
        trace = lineage[key(row)]
        origin = trace['input']
        if origin.startswith('admitted/'):
            path = admitted / origin.removeprefix('admitted/')
        else:
            path = batches / origin.removeprefix('collected/')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == trace['input_sha256'], 'Input changed since export: ' + origin)
        payload = load(path)
        if origin.endswith('annual_data_floor.json'):
            source_record = next(l['selected'] for y in payload['companies'][row['symbol']]['years'] for l in y['lines'].values() if (l.get('selected') or {}).get('reconciliation_id') == trace['record_id'])
            source = source_record['source']
        elif origin.endswith('financial_evidence_reconciliation.json'):
            source_record = next(r for r in payload['companies'][row['symbol']]['facts'] if r.get('reconciliation_id') == trace['record_id'])
            source = source_record['source']
        else:
            source_record = next(r for r in (payload if isinstance(payload, list) else payload['records']) if r['record_id'] == trace['record_id'])
            batch_manifest = load(path.parent / 'manifest.json')
            docs = batch_manifest['documents']
            docs = list(docs.values()) if isinstance(docs, dict) else docs
            source = next(d for d in docs if d['document_id'] == row['document_id'])
        require(number(source_record['normalized_value']) == number(row['value']), 'Spot check value mismatch')
        require(source_record['unit'] == row['unit'] and source_record['currency'] == row['currency'], 'Spot check unit/currency mismatch')
        require(source_record['period_end'] == row['period_end'], 'Spot check period mismatch')
        require(period_type(source_record, manifest['companies'][row['symbol']]['fiscal_year_end']) == row['period_type'], 'Spot check period type mismatch')
        require(source['document_id'] == row['document_id'], 'Spot check document mismatch')
        require(str(source.get('page', source_record.get('page'))) == row['page'], 'Spot check page mismatch')
        require(source_record['consolidation'] == ('unconsolidated' if row['basis'] == 'standalone' else 'consolidated'), 'Spot check basis mismatch')
        source_options = [source, documents.get(row['document_id'], {})] + [d for d in documents.values() if d.get('content_sha256') == trace['source_sha256']]
        require(any(s.get('source_url') == row['source_url'] for s in source_options), 'Spot check URL mismatch')
        date_origin = trace['date_input']
        if date_origin.startswith('admitted/'):
            date_path = admitted / date_origin.removeprefix('admitted/')
        elif date_origin.startswith('collected/'):
            date_path = batches / date_origin.removeprefix('collected/')
        else:
            date_path = Path('D:/Henneth-Backups/collection-originals') / date_origin.removeprefix('url-map/')
        expected_hash = next(i['sha256'] for i in manifest['input_files'] if i['input'] == date_origin)
        require(hashlib.sha256(date_path.read_bytes()).hexdigest() == expected_hash, 'Date input changed since export')
        matched = []
        identities = FilingDates.identities(dict(source, document_id=row['document_id'], source_url=row['source_url']))
        def visit(value):
            if isinstance(value, dict):
                if identities & FilingDates.identities(value) and str(value.get(trace['date_field'], '')) == trace['date_value']:
                    matched.append(value)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(load(date_path))
        require(matched and trace['date_value'][:10] == row['published_on'], 'Spot check publication mismatch')
        require(any(FilingDates.own_date(s) for s in matched), 'Retrieval/inspection date used as publication')
        report.append(dict({k: row[k] for k in FIELDS}, input=origin, record_id=trace['record_id'], result='PASS'))
    data = csv_bytes(FIELDS + ['input', 'record_id', 'result'], report)
    target = root / 'data/spot_checks.csv'
    if not target.exists() or target.read_bytes() != data:
        target.write_bytes(data)
    print(f'PASS: {len(report)} deterministic random spot checks across {len({r["symbol"] for r in report})} stocks against original input records: value, unit, currency, period/type, basis, document, URL, page and publication date.')
    # Independently inspect the retained source metadata for the requested stocks
    # that still have no admissible filing dates. This publishes no withheld values.
    date_index = FilingDates()
    input_hashes = {i['input']: i['sha256'] for i in manifest['input_files']}
    def add_sources(value, origin):
        if isinstance(value, dict):
            if value.get('document_id') or value.get('doc_id') or value.get('source_url'):
                date_index.add(value, origin)
            for child in value.values():
                add_sources(child, origin)
        elif isinstance(value, list):
            for child in value:
                add_sources(child, origin)
    for origin in input_hashes:
        if origin in ('admitted/state/company_documents.json', 'admitted/state/company_financial_series.json'):
            path = admitted / origin.removeprefix('admitted/')
        elif origin.startswith('collected/') and origin.endswith('/manifest.json'):
            path = batches / origin.removeprefix('collected/')
        elif origin.startswith('url-map/'):
            path = Path('D:/Henneth-Backups/collection-originals') / origin.removeprefix('url-map/')
        else:
            continue
        require(hashlib.sha256(path.read_bytes()).hexdigest() == input_hashes[origin], 'Date metadata changed since export')
        add_sources(load(path), origin)
    _, reviews = read_csv(root / 'data/date_resolution_review.csv')
    for symbol in ('DGKC', 'HBL', 'LUCK'):
        expected = min(3, manifest['companies'][symbol]['missing_publication_observations'])
        require(sum(r['symbol'] == symbol for r in reviews) >= expected, 'Too few excluded-date reviews: ' + symbol)
    for review in reviews:
        origin = review['input']
        path = admitted / origin.removeprefix('admitted/') if origin.startswith('admitted/') else batches / origin.removeprefix('collected/')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == input_hashes[origin], 'Excluded source input changed')
        payload = load(path)
        if origin.endswith('annual_data_floor.json'):
            record = next(l['selected'] for y in payload['companies'][review['symbol']]['years'] for l in y['lines'].values() if (l.get('selected') or {}).get('reconciliation_id') == review['record_id'])
            source = record['source']
        elif origin.endswith('financial_evidence_reconciliation.json'):
            record = next(r for r in payload['companies'][review['symbol']]['facts'] if r.get('reconciliation_id') == review['record_id'])
            source = record['source']
        else:
            record = next(r for r in (payload if isinstance(payload, list) else payload['records']) if r['record_id'] == review['record_id'])
            docs = load(path.parent / 'manifest.json')['documents']
            docs = list(docs.values()) if isinstance(docs, dict) else docs
            source = next(d for d in docs if d['document_id'] == review['document_id'])
        require(record['period_end'] == review['period_end'] and str(source.get('page', record.get('page'))) == review['page'], 'Excluded source identity mismatch')
        require(date_index.resolve(dict(source, document_id=review['document_id'], source_url=review['source_url']), origin) is None, 'Resolvable date incorrectly excluded')
    print('PASS: excluded-date source checks: ' + ', '.join(s + '=' + str(sum(r['symbol'] == s for r in reviews)) for s in ('DGKC', 'HBL', 'LUCK')) + '; no retrieval or inspection dates substituted.')


def self_test():
    require(FilingDates.own_date({'available_on': '2026-10-16', 'availability_basis': 'actual_batch_original_hash_and_physical_page_verification_at_created_at; not an original publication date'}) is None, 'Inspection date accepted as publication')
    require(FilingDates.own_date({'available_on': '2026-10-16', 'retrieved_at': '2026-10-16'}) is None, 'Unproven available_on accepted')
    date_index = FilingDates()
    date_index.add({'document_id': 'psx:123', 'published_at': '2025-09-01'}, 'catalog')
    require(date_index.resolve({'source_url': 'https://dps.psx.com.pk/download/document/123.pdf'}, 'candidate') == ('catalog', 'published_at', '2025-09-01'), 'PSX URL date resolution regression')
    require(not main_table_fact({'statement': 'other', 'line_item': 'supplemental_reported_inventory_detail'}), 'Other notes leaked into main tables')
    require(main_table_fact({'statement': 'other', 'line_item': 'dividend_per_share'}), 'Dividend excluded from main tables')
    def c(value, status='collected_checked', unit='PKR'):
        row = dict.fromkeys(FIELDS, 'x')
        row.update(symbol='ATRL', line_item='revenue', period_end='2025-06-30', period_type='annual', basis='consolidated', value=str(value), unit=unit, currency='PKR', statement='income_statement', status=status, published_on='2025-09-01', document_id='psx:1', page='1')
        return dict(row=row, origin='test', record_id=str(value))
    selected, conflicts, _ = resolve([c(1), c(2)])
    require(not selected and len(conflicts) == 2, 'Conflict regression')
    selected, conflicts, _ = resolve([c(1, 'admitted_checked'), c(2)])
    require(len(selected) == 1 and selected[0]['row']['value'] == '1' and not conflicts, 'Precedence regression')
    selected, conflicts, _ = resolve([c(1), c(1, unit='PKR/share')])
    require(not selected and len(conflicts) == 2, 'Unit conflict regression')
    group = [dict(record_id='a', metric='total_assets', normalized_value=100, unit_multiplier=1), dict(record_id='l', metric='total_liabilities', normalized_value=60, unit_multiplier=1), dict(record_id='e', metric='total_equity', normalized_value=40, unit_multiplier=1)]
    require(arithmetic(group, {})['a'] == ['balance_identity'], 'Balance identity regression')
    group[-1]['normalized_value'] = 50
    try:
        arithmetic(group, {})
    except ValueError:
        pass
    else:
        raise ValueError('Failed arithmetic accepted')
    require(period_type({'period_type': 'interim', 'duration_months': 6}, '06-30') == 'half_year', 'Cumulative classification regression')
    require(period_type({'period_type': 'interim', 'snapshot': True, 'period_end': '2023-12-31'}, '06-30') == 'half_year', 'Snapshot classification regression')
    print('PASS: date resolution, retrieval-date rejection, table scope, conflict, admitted precedence, unit mismatch, arithmetic and cumulative/snapshot regression checks.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--admitted', type=Path, default=DEFAULT_ADMITTED)
    parser.add_argument('--batches', type=Path, default=DEFAULT_BATCHES)
    parser.add_argument('--spot-check', type=int, default=0, metavar='N')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    facts, lineage = validate(args.root, check_audit=not args.spot_check)
    if args.spot_check:
        spot_check(args.root, facts, lineage, args.admitted, args.batches, max(args.spot_check, 15))
        validate(args.root)


if __name__ == '__main__':
    main()
