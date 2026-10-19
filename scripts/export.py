#!/usr/bin/env python3
"""Export source-linked reported PSX figures. Python 3.12 standard library only."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

SYMBOLS = tuple('ATRL BOP CNERGY DGKC ENGROH FFC GAL HBL HUBC LUCK MARI MEBL MLCF NBP NRL OGDC PPL PRL PSO UBL'.split())
FIELDS = 'symbol sector statement line_item reported_label period_end period_type fiscal_year basis value unit currency status checks_passed document_id source_url page published_on retrieved_on'.split()
KEY = ['symbol', 'line_item', 'period_end', 'period_type', 'basis']
WIDE = ['statement', 'line_item', 'reported_label', 'basis', 'unit', 'currency']
FORBIDDEN = re.compile(r'forecast|valuation|assumption|scenario|target|recommendation', re.I)
BAD = re.compile(r'held|failed|quarantin|conflict|unresolved|not_proven|not.proven|rejected', re.I)
BASE = Path('D:/PSX Trader X Claude/.codex-henneth-ci-worktrees')
DEFAULT_ADMITTED = BASE / 'admission-integration-20261013'
DEFAULT_BATCHES = BASE / 'financial-backfill-20261016/docs/data-batches'
DEFAULT_BACKUP = Path('D:/Henneth-Backups/collection-originals')


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('invalid_number')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite_number')
    return result


def decimal_text(value):
    text = format(number(value), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def day(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def key(row):
    return tuple(row[k] for k in KEY)


def csv_bytes(fields, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n', extrasaction='raise')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def md(value):
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')


class Inputs:
    """Read JSON once; fail if any inspected input changes before publication output."""
    def __init__(self):
        self.entries = {}

    def read(self, path, group, root):
        data = path.read_bytes()
        token = group + '/' + path.relative_to(root).as_posix()
        digest = hashlib.sha256(data).hexdigest()
        if token in self.entries and self.entries[token][1] != digest:
            raise RuntimeError('Input changed during export: ' + token)
        self.entries[token] = (path, digest)
        return json.loads(data.decode('utf-8-sig'))

    def verify(self):
        for token, (path, digest) in self.entries.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Input changed during export: ' + token)

    def manifest(self):
        return [{'input': k, 'sha256': v[1]} for k, v in sorted(self.entries.items())]


def rows_of(payload):
    if isinstance(payload, list):
        return payload
    return payload.get('records', [])


def docs_of(manifest):
    value = manifest.get('documents', [])
    if isinstance(value, dict):
        return [dict(v, document_id=v.get('document_id', k)) for k, v in value.items() if isinstance(v, dict)]
    return [v for v in value if isinstance(v, dict)]


def reference_ids(value):
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set().union(*(reference_ids(v) for v in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(reference_ids(value[k]) for k in ('batch_id', 'batch_ids', 'supersedes_batch_id', 'supersedes_packet') if k in value))
    return set()


def replacements(batches):
    """Explicit replacement edges, then numeric revisions within the same named scope."""
    replaced = set()
    groups = defaultdict(list)
    for name, batch in batches.items():
        manifest = batch['manifest']
        for field in ('supersedes', 'supersedes_batch_id', 'supersedes_packet', 'prior_batch_id'):
            replaced.update(reference_ids(manifest.get(field)))
            replaced.update(reference_ids((manifest.get('provenance') or {}).get(field)))
        repair = manifest.get('repair_of')
        if isinstance(repair, dict) and repair.get('original_batch_id'):
            replaced.add(repair['original_batch_id'])
        # Version tokens may be followed by a date/scope suffix (envelope-v2-...).
        tokens = list(re.finditer(r'(?<=-)v(\d+)(?=-|$)', name))
        if tokens:
            match = tokens[-1]
            scope = name[:match.start()] + 'v#' + name[match.end():]
            groups[scope].append((int(match.group(1)), name))
    for versions in groups.values():
        highest = max(v for v, _ in versions)
        replaced.update(n for v, n in versions if v < highest)
    return replaced


def arithmetic(records, checks):
    """Recompute explicit record-bound equations; scalar summary assertions are not proof."""
    by_id = {r.get('record_id'): r for r in records}
    if len(by_id) != len(records):
        raise ValueError('duplicate_record_ids')
    passed = defaultdict(list)
    for check in checks.get('arithmetic_checks', []):
        if not isinstance(check, dict):
            raise ValueError('unsupported_arithmetic_schema')
        lhs, rhs = check.get('lhs'), check.get('rhs')
        if isinstance(rhs, dict):
            rhs = [rhs]
        if not isinstance(lhs, list) or not isinstance(rhs, list) or not lhs or not rhs:
            continue
        if not all(isinstance(t, dict) and isinstance(t.get('record_id'), str) for t in lhs + rhs):
            raise ValueError('unsupported_arithmetic_terms')
        if any(t['record_id'] not in by_id for t in lhs + rhs):
            continue
        operands = [by_id[t['record_id']] for t in lhs + rhs]
        if len({tuple(r.get(k) for k in ('document_id', 'period_end', 'period_type', 'duration_months', 'consolidation', 'unit', 'currency')) for r in operands}) != 1:
            raise ValueError('arithmetic_mixed_scope')
        tolerance = number(check.get('tolerance', 0))
        cap = sum(number(r.get('unit_multiplier', 1)) / 2 for r in operands)
        if tolerance < 0 or tolerance > cap:
            raise ValueError('arithmetic_tolerance_too_wide')
        if any(t.get('coefficient') not in (-1, 1) for t in lhs + rhs):
            raise ValueError('unsupported_arithmetic_coefficient')
        def total(terms):
            return sum(number(by_id[t['record_id']]['normalized_value']) * number(t['coefficient']) for t in terms)
        if abs(total(lhs) - total(rhs)) > tolerance:
            raise ValueError('arithmetic_failed')
        for term in lhs + rhs:
            passed[term['record_id']].append(str(check.get('check_id', 'declared_equation')))
    # Known identities do not depend on the collector declaring them.
    groups = defaultdict(dict)
    for row in records:
        scope = tuple(row.get(k) for k in ('document_id', 'period_end', 'period_type', 'duration_months', 'consolidation', 'unit', 'currency'))
        groups[scope][row.get('metric')] = row
    identities = [('balance_identity', 'total_assets', ('total_liabilities', 'total_equity')),
                  ('profit_attribution', 'profit_after_tax', ('profit_after_tax_attributable', 'profit_after_tax_non_controlling')),
                  ('bank_income_subtotal', 'total_operating_income', ('net_interest_income', 'non_interest_income')),
                  ('cash_subtotal', 'net_change_in_cash', ('operating_cash_flow', 'net_cash_from_investing_activities', 'net_cash_from_financing_activities'))]
    for group in groups.values():
        for label, whole, parts in identities:
            if not all(k in group for k in (whole, *parts)):
                continue
            operands = [group[k] for k in (whole, *parts)]
            if abs(number(group[whole]['normalized_value']) - sum(number(group[k]['normalized_value']) for k in parts)) > sum(number(r.get('unit_multiplier', 1)) / 2 for r in operands):
                raise ValueError('automatic_arithmetic_failed')
            for row in operands:
                passed[row['record_id']].append(label)
    return passed


def status_bad(record):
    for field in ('status', 'scope_status', 'truth_status', 'candidate_status', 'batch_status', 'admission_status', 'review_status', 'source_flags', 'quality_flags'):
        if BAD.search(str(record.get(field, ''))):
            return True
    for field in ('held', 'failed', 'quarantined', 'conflicting', 'unresolved'):
        if record.get(field):
            return True
    return False


def period_type(record, fiscal_end):
    original = record.get('period_type')
    months = record.get('duration_months') or record.get('cumulative_period_months')
    if original == 'annual':
        if record.get('statement_type') in ('income_statement', 'cash_flow_statement') and months != 12:
            raise ValueError('annual_duration_missing_or_invalid')
        return 'annual'
    if original in ('quarter', 'half_year', 'nine_months', 'six_months'):
        result = {'six_months': 'half_year'}.get(original, original)
        expected = {'quarter': 3, 'half_year': 6, 'nine_months': 9}[result]
        if months is not None and months != expected:
            raise ValueError('period_duration_mismatch')
        return result
    if original == 'interim' and months in (3, 6, 9):
        return {3: 'quarter', 6: 'half_year', 9: 'nine_months'}[months]
    if original == 'interim' and record.get('snapshot') is True and fiscal_end:
        end = date.fromisoformat(record['period_end'])
        fm, _ = map(int, fiscal_end.split('-'))
        offset = (end.month - fm) % 12
        if offset in (3, 6, 9):
            return {3: 'quarter', 6: 'half_year', 9: 'nine_months'}[offset]
    raise ValueError('ambiguous_period')


def normalize(record, source, symbol, info, status, checks, cutoff):
    if status == 'collected_checked' and status_bad(record):
        raise ValueError('held_or_rejected_record')
    metric = record.get('metric') or ''
    if FORBIDDEN.search(metric + ' ' + str(record.get('reported_label', ''))):
        raise ValueError('outside_historical_statement_scope')
    if any('derived' in str(record.get(k, '')).lower() for k in ('source_method', 'record_role', 'epistemic_type', 'period_type')) or record.get('calculation_version') or record.get('derived'):
        raise ValueError('derived_not_reported')
    value = number(record.get('normalized_value'))
    raw = str(record.get('raw_value', '')).strip().replace(',', '').replace('\u2212', '-')
    if raw.startswith('(') and raw.endswith(')'):
        raw = '-' + raw[1:-1].strip()
    raw = raw.replace(' ', '')
    try:
        scaled = number(raw) * number(record.get('unit_multiplier', 1))
    except (InvalidOperation, ValueError):
        raise ValueError('raw_value_not_numeric') from None
    if scaled != value:
        raise ValueError('raw_scale_mismatch')
    if record.get('consolidation') not in ('consolidated', 'unconsolidated', 'standalone'):
        raise ValueError('unknown_basis')
    end = day(record.get('period_end'))
    if not end or end > cutoff:
        raise ValueError('invalid_or_future_period')
    retrieved = day(source.get('retrieved_at'))
    if not retrieved or retrieved > cutoff:
        raise ValueError('missing_or_future_retrieval_date')
    page = source.get('page', record.get('page'))
    if type(page) is not int or page < 1:
        raise ValueError('missing_page')
    if source.get('page_count') and page > source['page_count']:
        raise ValueError('page_out_of_range')
    if not record.get('unit') or not record.get('currency'):
        raise ValueError('missing_unit_or_currency')
    if not source.get('document_id') or not source.get('source_url'):
        raise ValueError('missing_source')
    parsed = urlsplit(source['source_url'])
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid_source_url')
    if parsed.hostname == 'dps.psx.com.pk':
        match = re.fullmatch(r'/download/document/(\d+)\.pdf', parsed.path)
        if not match or source['document_id'] != 'psx:' + match.group(1):
            raise ValueError('psx_document_url_mismatch')
    elif parsed.hostname.removeprefix('www.') not in info['issuer_hosts']:
        raise ValueError('unverified_issuer_domain')
    ptype = period_type(record, info['fiscal_year_end'])
    if not info['fiscal_year_end']:
        raise ValueError('unknown_fiscal_calendar')
    fiscal = int(end[:4]) + (end[5:] > info['fiscal_year_end'])
    item = re.sub(r'[^a-z0-9]+', '_', metric.lower()).strip('_')
    if not item or not record.get('reported_label'):
        raise ValueError('missing_line_identity')
    statement = {'cash_flow_statement': 'cash_flow'}.get(record.get('statement_type'), record.get('statement_type'))
    if statement not in ('income_statement', 'balance_sheet', 'cash_flow'):
        if not str(record.get('statement_type', '')).startswith(('notes', 'segment', 'equity', 'eps', 'changes_in_equity')):
            raise ValueError('outside_historical_statement_scope')
        statement = 'other'
    # Count publication-only omissions only after all other qualification checks.
    pub = day(source.get('published_at'))
    if not pub:
        raise ValueError('missing_publication_date')
    if pub > cutoff:
        raise ValueError('publication_after_cutoff')
    if end > pub:
        raise ValueError('publication_before_period_end')
    return dict(zip(FIELDS, [symbol, info['sector'], statement, item, record['reported_label'], end, ptype,
        str(fiscal), 'standalone' if record['consolidation'] in ('unconsolidated', 'standalone') else 'consolidated',
        decimal_text(value), record['unit'], record['currency'], status, ';'.join(sorted(set(checks))),
        source['document_id'], source['source_url'], str(page), pub, retrieved]))


def wide_rows(facts, annual):
    facts = [f for f in facts if (f['period_type'] == 'annual') == annual]
    periods = sorted({f['period_end'] + '|' + f['period_type'] for f in facts})
    groups = {}
    for fact in facts:
        ident = tuple(fact[k] for k in WIDE if k != 'reported_label')
        row = groups.setdefault(ident, {k: fact[k] for k in WIDE})
        row[fact['period_end'] + '|' + fact['period_type']] = fact['value']
    return WIDE + periods, [{k: row.get(k, '') for k in WIDE + periods} for _, row in sorted(groups.items())]


def resolve(candidates):
    groups = defaultdict(list)
    for candidate in candidates:
        groups[key(candidate['row'])].append(candidate)
    chosen, conflicts, replaced = [], [], []
    for _, group in sorted(groups.items()):
        admitted = [c for c in group if c['row']['status'] == 'admitted_checked']
        pool = admitted or group
        # Same amount with different statement/unit semantics is still ambiguous.
        variants = {tuple(c['row'][k] for k in ('value', 'unit', 'currency', 'statement')) for c in pool}
        if len(variants) > 1:
            conflicts.extend(dict(c['row'], reason='admitted_conflict' if admitted else 'collected_conflict') for c in pool)
            continue
        picked = max(pool, key=lambda c: (c['row']['published_on'], c['row']['document_id'], int(c['row']['page']), c['origin'], c['record_id']))
        chosen.append(picked)
        replaced.extend(c for c in group if c is not picked)
    return chosen, conflicts, replaced


def build(args):
    inputs = Inputs()
    A, B, C = args.admitted, args.batches, args.backup_map
    def ar(name):
        return inputs.read(A / name, 'admitted', A)
    floor = ar('state/company_intel/annual_data_floor.json')
    series = ar('state/company_financial_series.json')
    reconciliation = ar('state/company_intel/financial_evidence_reconciliation.json')
    documents = ar('state/company_documents.json')['documents']
    universe = ar('state/universe.json')['symbols']
    if floor.get('schema_version') != 'annual_data_floor_v1' or series.get('schema_version') != 1 or reconciliation.get('schema_version') != 1:
        raise RuntimeError('Unsupported admitted input schema')
    cutoff = args.cutoff
    batches = {}
    for folder in sorted(p for p in B.iterdir() if p.is_dir()):
        payload = {}
        for name in ('manifest.json', 'records.json', 'checks.json', 'holds.json', 'scope_holds.json', 'validation-report.json', 'validation.json', 'checker-results.json', 'checker-run.json'):
            path = folder / name
            if path.is_file():
                payload[name.removesuffix('.json')] = inputs.read(path, 'collected', B)
        if payload:
            payload.setdefault('manifest', {})
            supersession_file = payload['manifest'].get('supersession_file')
            if supersession_file:
                if Path(supersession_file).name != supersession_file or not supersession_file.endswith('.json'):
                    raise RuntimeError('Unsafe supersession filename: ' + folder.name)
                payload['supersessions'] = inputs.read(folder / supersession_file, 'collected', B)
            batches[folder.name] = payload
    replaced_batches = replacements(batches)
    replaced_record_ids = set()
    for name, batch in batches.items():
        if name not in replaced_batches:
            for mapping in batch.get('supersessions', {}).get('mapping', []):
                if mapping.get('old_record_id') and mapping.get('new_record_id'):
                    replaced_record_ids.add(mapping['old_record_id'])
    # Official URL recovery only. No source document is opened or copied.
    backup_urls = {}
    maps = sorted(C.glob('backup-map-*.json')) if C.is_dir() else [C]
    for path in maps:
        backup = inputs.read(path, 'url-map', path.parent)
        def visit(value):
            if isinstance(value, dict):
                url = value.get('source_url') or value.get('original_url')
                digest = value.get('sha256') or value.get('content_sha256')
                if url and digest:
                    backup_urls[digest] = url
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(backup)
    issuer_hosts = defaultdict(set)
    doc_by_hash = defaultdict(list)
    for doc in documents.values():
        digest = doc.get('content_sha256')
        if digest:
            doc_by_hash[digest].append(doc)
        if doc.get('source') == 'Issuer website':
            host = urlsplit(doc.get('source_url') or '').hostname
            if host:
                for symbol in doc.get('tickers', []):
                    issuer_hosts[symbol].add(host.removeprefix('www.'))
    infos = {}
    for symbol in SYMBOLS:
        company = floor['companies'][symbol]
        calendar = company.get('fiscal_year_end')
        if not calendar:
            calendars = {p['manifest'].get('fiscal_year_end') for n, p in batches.items() if n not in replaced_batches and p['manifest'].get('symbol') == symbol and p['manifest'].get('fiscal_calendar_evidence')}
            calendars.discard(None)
            if len(calendars) == 1:
                calendar = calendars.pop()
        infos[symbol] = dict(name=universe[symbol]['name'], sector=company['sector_contract_binding']['exchange_sector'], fiscal_year_end=calendar, issuer_hosts=issuer_hosts[symbol])
    def enrich(source):
        source = dict(source)
        digest = source.get('content_sha256')
        catalog = documents.get(source.get('document_id'), {})
        options = [catalog] if catalog and (not digest or catalog.get('content_sha256') == digest) else []
        options += doc_by_hash.get(digest, [])
        for doc in options:
            for field in ('published_at', 'retrieved_at', 'source_url', 'page_count'):
                if not source.get(field) and doc.get(field):
                    source[field] = doc[field]
        if not source.get('source_url'):
            source['source_url'] = backup_urls.get(digest)
        return source
    exclusions = Counter()
    missing_publication = defaultdict(set)
    candidates = []
    def exclude(symbol, stage, reason, batch='', count=1):
        exclusions[(symbol if symbol in SYMBOLS else 'UNKNOWN', stage, reason, batch)] += count
    def candidate(record, source, symbol, status, checks, origin, record_id, stage):
        source = enrich(source)
        if symbol not in infos:
            exclude(symbol, stage, 'outside_roster', origin)
            return
        try:
            row = normalize(record, source, symbol, infos[symbol], status, checks, cutoff)
            candidates.append(dict(row=row, origin=origin, record_id=str(record_id), input_sha256=inputs.entries[origin][1], source_sha256=source.get('content_sha256', '')))
        except (ValueError, InvalidOperation, TypeError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, InvalidOperation) else 'invalid_numeric_or_schema'
            if reason == 'missing_publication_date':
                identity = (symbol, record.get('metric'), record.get('period_end'), record.get('period_type'), record.get('consolidation'), source.get('content_sha256'), source.get('page', record.get('page')), decimal_text(record['normalized_value']))
                missing_publication[symbol].add(identity)
            exclude(symbol, stage, reason, origin)
    floor_ids = set()
    for symbol, company in sorted(floor['companies'].items()):
        if symbol not in infos:
            continue
        for year in company.get('years', []):
            for metric, line in sorted(year.get('lines', {}).items()):
                fact = line.get('selected') or {}
                if not line.get('promoted') or line.get('status') != 'reported_automatically_checked' or line.get('fact_checks') != 'passed' or line.get('failed_checks') or line.get('failed_fact_checks'):
                    exclude(symbol, 'admitted', 'annual_floor_not_checked')
                    continue
                floor_ids.add(fact.get('reconciliation_id'))
                checks = ['source_structure_and_raw_scale'] + ['upstream:' + str(c) for c in line.get('checks', [])]
                if not line.get('checks'):
                    checks.append('cross_check_unavailable')
                candidate(fact, fact.get('source', {}), symbol, 'admitted_checked', checks, 'admitted/state/company_intel/annual_data_floor.json', fact.get('reconciliation_id', metric), 'admitted')
    # Eligible reconciliation observations are retained reported facts; audit-only,
    # quarantined and derived truth records never enter the candidate pool.
    for symbol, company in sorted(reconciliation['companies'].items()):
        for fact in company.get('facts', []):
            if fact.get('reconciliation_id') in floor_ids:
                continue
            if fact.get('truth_status') != 'eligible' or fact.get('status') != 'eligible':
                exclude(symbol, 'admitted', 'not_eligible_reconciliation')
                continue
            candidate(fact, fact.get('source', {}), symbol, 'admitted_checked', ['eligible_reported_provenance', 'source_structure_and_raw_scale', 'cross_check_unavailable'], 'admitted/state/company_intel/financial_evidence_reconciliation.json', fact.get('reconciliation_id'), 'admitted')
    for name, batch in sorted(batches.items()):
        manifest = batch['manifest']
        payload = batch.get('records', {})
        records = rows_of(payload)
        symbol = manifest.get('symbol') or (payload.get('symbol') if isinstance(payload, dict) else None)
        if not symbol and records:
            symbol = records[0].get('symbol')
        reason = None
        if name in replaced_batches:
            reason = 'superseded_batch'
        elif not manifest or 'records' not in batch or 'checks' not in batch:
            reason = 'incomplete_batch_envelope'
        elif status_bad(manifest) or status_bad(payload if isinstance(payload, dict) else {}):
            reason = 'held_or_rejected_batch'
        elif manifest.get('records_sha256') and manifest['records_sha256'] != inputs.entries['collected/' + name + '/records.json'][1]:
            reason = 'records_hash_mismatch'
        elif batch['checks'].get('records_sha256') and batch['checks']['records_sha256'] != inputs.entries['collected/' + name + '/records.json'][1]:
            reason = 'checks_hash_mismatch'
        if reason:
            exclude(symbol, 'collected', reason, name, len(records) or 1)
            continue
        checks = batch['checks']
        # An explicit aggregate failure is not overridden by our subset checks.
        for obj in [checks] + [batch[k] for k in ('validation-report', 'validation', 'checker-results', 'checker-run') if isinstance(batch.get(k), dict)]:
            if any(obj.get(k) for k in ('failures', 'failed', 'records_failed', 'checks_failed', 'unresolved', 'validator_hold')):
                reason = 'failed_or_unresolved_batch_checks'
        if reason:
            exclude(symbol, 'collected', reason, name, len(records) or 1)
            continue
        try:
            checked = arithmetic(records, checks)
        except (ValueError, KeyError, TypeError, InvalidOperation):
            exclude(symbol, 'collected', 'arithmetic_failed_or_unsupported', name, len(records) or 1)
            continue
        docs = {d.get('document_id'): d for d in docs_of(manifest)}
        held_ids = set()
        def holds(value):
            if isinstance(value, dict):
                if value.get('record_id') and not value.get('resolved'):
                    held_ids.add(value['record_id'])
                for child in value.values():
                    holds(child)
            elif isinstance(value, list):
                for child in value:
                    holds(child)
        for k in ('holds', 'scope_holds'):
            holds(batch.get(k))
        for record in records:
            if record.get('record_id') in replaced_record_ids:
                exclude(symbol, 'collected', 'superseded_record', name)
                continue
            if record.get('record_id') in held_ids:
                exclude(symbol, 'collected', 'record_on_hold', name)
                continue
            source = docs.get(record.get('document_id'))
            if not source:
                exclude(symbol, 'collected', 'document_not_in_manifest', name)
                continue
            if record.get('content_sha256') != source.get('content_sha256') or not re.fullmatch(r'[a-f0-9]{64}', str(record.get('content_sha256', ''))):
                exclude(symbol, 'collected', 'document_hash_binding_mismatch', name)
                continue
            # Never turn a source description/business transcription into financial data.
            if 'business_transcription' in str(record.get('source_method')) or record.get('reported_label_is_scope_description'):
                exclude(symbol, 'collected', 'outside_historical_statement_scope', name)
                continue
            names = ['source_structure_and_raw_scale'] + checked.get(record.get('record_id'), [])
            if not checked.get(record.get('record_id')):
                names.append('cross_check_unavailable')
            candidate(record, source, record.get('symbol') or symbol, 'collected_checked', names, 'collected/' + name + '/records.json', record.get('record_id'), 'collected')
    selected, conflicts, duplicates = resolve(candidates)
    for c in duplicates:
        exclude(c['row']['symbol'], 'selection', 'duplicate_or_admitted_precedence')
    for row in conflicts:
        exclude(row['symbol'], 'selection', row['reason'])
    facts = sorted((c['row'] for c in selected), key=lambda f: tuple(f[k] for k in FIELDS))
    # The canonical long table is serialized and reread before any derivative table.
    fact_bytes = csv_bytes(FIELDS, facts)
    facts = list(csv.DictReader(io.StringIO(fact_bytes.decode('utf-8'))))
    info = {'version': 1, 'data_cutoff': cutoff, 'stocks': len(SYMBOLS), 'facts': len(facts), 'status_counts': dict(sorted(Counter(f['status'] for f in facts).items())), 'conflict_observations': len(conflicts), 'superseded_batches': len(replaced_batches), 'input_files': inputs.manifest(), 'companies': {}}
    for s in SYMBOLS:
        fs = [f for f in facts if f['symbol'] == s]
        excluded = Counter()
        for (symbol, _, reason, _), count in exclusions.items():
            if symbol == s:
                excluded[reason] += count
        info['companies'][s] = {k: infos[s][k] for k in ('name', 'sector', 'fiscal_year_end')}
        info['companies'][s].update(facts=len(fs), admitted=sum(f['status'] == 'admitted_checked' for f in fs), collected=sum(f['status'] == 'collected_checked' for f in fs), annual_years=sorted({f['fiscal_year'] for f in fs if f['period_type'] == 'annual'}), quarters=sorted({f['period_end'] for f in fs if f['period_type'] == 'quarter'}), half_years=sorted({f['period_end'] for f in fs if f['period_type'] == 'half_year'}), nine_months=sorted({f['period_end'] for f in fs if f['period_type'] == 'nine_months'}), statements=sorted({f['statement'] for f in fs}), missing_publication_observations=len(missing_publication[s]), exclusions=dict(sorted(excluded.items())))
    output = {}
    def put(path, content):
        output[path] = content if isinstance(content, bytes) else content.encode('utf-8')
    put('data/facts.csv', fact_bytes)
    put('data/conflicts.csv', csv_bytes(FIELDS + ['reason'], sorted(conflicts, key=lambda f: tuple(str(f[k]) for k in FIELDS))))
    put('data/exclusions.csv', csv_bytes(['symbol', 'stage', 'reason', 'batch', 'observations'], [dict(zip(['symbol', 'stage', 'reason', 'batch', 'observations'], (*k, n))) for k, n in sorted(exclusions.items())]))
    put('data/lineage.csv', csv_bytes(KEY + ['input', 'record_id', 'input_sha256', 'source_sha256'], [dict({k: c['row'][k] for k in KEY}, input=c['origin'], record_id=c['record_id'], input_sha256=c['input_sha256'], source_sha256=c['source_sha256']) for c in sorted(selected, key=lambda c: key(c['row']))]))
    put('data/export_manifest.json', json.dumps(info, indent=2, ensure_ascii=False, sort_keys=True) + '\n')
    coverage = ['# Coverage', '', 'Coverage counts individual reported line items, not complete statements. Blank cells mean unavailable. Quarter counts exclude cumulative half-year and nine-month periods.', '', '| Symbol | Annual years | Quarter ends | Half-year ends | Nine-month ends | Statements | Admitted | Collected | Missing publication observations |', '|---|---|---|---|---|---|---:|---:|---:|']
    for s, c in info['companies'].items():
        coverage.append('| ' + ' | '.join([s, ', '.join(c['annual_years']) or 'none', ', '.join(c['quarters']) or 'none', ', '.join(c['half_years']) or 'none', ', '.join(c['nine_months']) or 'none', ', '.join(c['statements']) or 'none', str(c['admitted']), str(c['collected']), str(c['missing_publication_observations'])]) + ' |')
        fs = [f for f in facts if f['symbol'] == s]
        for annual, filename in [(True, 'annual.csv'), (False, 'quarterly.csv')]:
            fields, rows = wide_rows(fs, annual)
            put(s + '/' + filename, csv_bytes(fields, rows))
        put(s + '/README.md', company_readme(s, c, fs))
    coverage += ['', '## Gaps and exclusions', '', 'The intended ten-year collection is incomplete. Presence of a year does not establish that all statements or line items are available. Missing-publication counts are distinct source observations that pass the other export qualification checks but lack a verified publication date. Duplicate deliveries of the same source observation are counted once. Superseded, incomplete and rejected batches are excluded before this count. They are not estimates of all figures in unprocessed filings.', '']
    for s, c in info['companies'].items():
        coverage += [f"### {s}", '', f"Missing publication date: **{c['missing_publication_observations']}** observations. Fiscal year end: {c['fiscal_year_end'] or 'unknown'}. " + ('Absent statements: ' + ', '.join(sorted(set(['income_statement', 'balance_sheet', 'cash_flow']) - set(c['statements']))) if set(['income_statement', 'balance_sheet', 'cash_flow']) - set(c['statements']) else 'At least one line is present from each primary statement.'), '', '; '.join(f'{k}: {v}' for k, v in c['exclusions'].items()) or 'No exclusions.', '', f'Exact period-by-statement coverage: [{s}/README.md]({s}/README.md).', '']
    put('coverage.md', '\n'.join(coverage).rstrip() + '\n')
    put('README.md', main_readme(info))
    put('LICENSE', MIT)
    put('DATA-LICENSE', DATA_LICENSE)
    put('.gitignore', '__pycache__/\n*.py[cod]\n.cache/\n.venv/\n.env\n.env.*\n*.pdf\n*.PDF\n*.png\n*.jpg\n*.jpeg\n*.gif\n*.webp\n*.svg\n*.doc\n*.docx\n*.xls\n*.xlsx\n.DS_Store\n')
    put('.gitattributes', '* text=auto eol=lf\n')
    changelog = args.output / 'CHANGELOG.md'
    version = f"## Version 1 — {cutoff}\n\nInitial compilation: {len(SYMBOLS)} roster companies; {len(facts)} facts ({info['status_counts'].get('admitted_checked', 0)} admitted checked, {info['status_counts'].get('collected_checked', 0)} collected checked); {len(conflicts)} conflict observations withheld. Data cutoff: {cutoff}.\n"
    if changelog.exists():
        history = changelog.read_text(encoding='utf-8')
        # Preserve durable history; a changed compilation gets an appended digest entry.
        digest = hashlib.sha256(fact_bytes).hexdigest()
        if digest not in history:
            history += f'\n## Export — {cutoff}\n\nFacts: {len(facts)}. SHA-256 of facts.csv: `{digest}`.\n'
    else:
        history = '# Changelog\n\n' + version + '\nFacts SHA-256: `' + hashlib.sha256(fact_bytes).hexdigest() + '`.\n'
    put('CHANGELOG.md', history)
    inputs.verify()
    # Only exporter-owned paths are written. No cleanup or traversal of input folders.
    for relative, data in sorted(output.items()):
        path = args.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != data:
            path.write_bytes(data)
    print(json.dumps({k: v for k, v in info.items() if k not in ('input_files', 'companies')}, sort_keys=True))


def company_readme(symbol, company, facts):
    out = [f"# {symbol} — {company['name']}", '', f"Sector: {company['sector']}. Fiscal year end: {company['fiscal_year_end'] or 'unknown'}.", '', f"{company['facts']} reported figures: {company['admitted']} admitted checked; {company['collected']} collected checked. This is partial history, not a completeness assertion.", '', 'CSV files: [annual.csv](annual.csv), [quarterly.csv](quarterly.csv). Each wide cell joins to [facts.csv](../data/facts.csv) by symbol, line item, period end, period type and basis. Half-year and nine-month columns are cumulative.', '']
    refs = []
    for statement in ('income_statement', 'balance_sheet', 'cash_flow', 'other'):
        out += ['## ' + statement.replace('_', ' ').title(), '']
        for annual in (True, False):
            section = [f for f in facts if f['statement'] == statement and (f['period_type'] == 'annual') == annual]
            out += ['### ' + ('Annual' if annual else 'Interim'), '']
            if not section:
                out += ['No qualifying reported figures available.', '']
                continue
            # Chunk by period to keep tables readable even with a decade of interim data.
            periods = sorted({f['period_end'] + ' / ' + f['period_type'] for f in section})
            for start in range(0, len(periods), 6):
                columns = periods[start:start+6]
                out += ['| Line item | Basis | Unit | Currency | ' + ' | '.join(columns) + ' |', '|---|---|---|---|' + '---|' * len(columns)]
                groups = defaultdict(dict)
                for fact in section:
                    groups[(fact['line_item'], fact['basis'], fact['unit'], fact['currency'])][fact['period_end'] + ' / ' + fact['period_type']] = fact
                for ident, entries in sorted(groups.items()):
                    if not any(c in entries for c in columns):
                        continue
                    cells = []
                    for column in columns:
                        if column not in entries:
                            cells.append('')
                        else:
                            fact = entries[column]
                            refs.append(fact)
                            cells.append(f"{fact['value']} [S{len(refs)}](#s{len(refs)})")
                    out.append('| ' + ' | '.join(md(x) for x in (*ident, *cells)) + ' |')
                out.append('')
    out += ['## Coverage and gaps', '', f"Annual fiscal years: {', '.join(company['annual_years']) or 'none'}. Quarter periods: {len(company['quarters'])}; half-year periods: {len(company['half_years'])}; nine-month periods: {len(company['nine_months'])}.", '', f"Missing verified publication date: {company['missing_publication_observations']} distinct inspected observations. No missing figure is estimated. Every period/statement combination below counts available lines only.", '', '| Period end | Type | Basis | Statement | Figures |', '|---|---|---|---|---:|']
    counts = Counter((f['period_end'], f['period_type'], f['basis'], f['statement']) for f in facts)
    out += ['| ' + ' | '.join((*k, str(n))) + ' |' for k, n in sorted(counts.items())]
    out += ['', 'Full exclusion counts and collection limits: [coverage.md](../coverage.md).', '', '## Sources', '', 'Pages are physical PDF page numbers, starting at 1. Documents are linked only; no document files are distributed. Each reference describes exactly one table cell.', '']
    for i, f in enumerate(refs, 1):
        out.append(f"<a id=\"s{i}\"></a> **S{i}**: [{md(f['document_id'])}]({f['source_url']}), page {f['page']}; published {f['published_on']}; retrieved {f['retrieved_on']}; period {f['period_end']} ({f['period_type']}); {f['basis']}; {md(f['unit'])}, {md(f['currency'])}; {f['status']}; checks: {md(f['checks_passed'])}.")
        out.append('')
    return '\n'.join(out).rstrip() + '\n'


DICTIONARY = {
    'symbol': 'PSX ticker from the fixed roster.', 'sector': 'Retained PSX sector label.',
    'statement': 'income_statement, balance_sheet, cash_flow or other (reported financial notes/equity).',
    'line_item': 'Lowercase normalized source metric identifier; punctuation becomes underscores. Supplemental identifiers retain their supplemental_reported prefix. Different issuer captions are not automatically equivalent.',
    'reported_label': 'Source extraction caption, unchanged apart from CSV encoding.',
    'period_end': 'ISO date of the reported period end; balance sheet figures are snapshots.',
    'period_type': 'annual, quarter, half_year or nine_months. No derived quarters.',
    'fiscal_year': 'Year in which the fiscal year ends, based on the sourced fiscal calendar.',
    'basis': 'consolidated or standalone; standalone maps the source term unconsolidated.',
    'value': 'Reported numeric value normalized once to the stated base unit; decimal text.',
    'unit': 'Base unit from the record (for example PKR, PKR/share, shares or percent).',
    'currency': 'Explicit source currency; never filled from a general default.',
    'status': 'admitted_checked or collected_checked; see methodology.',
    'checks_passed': 'Semicolon-separated structural/arithmetic checks. cross_check_unavailable means no supported complete equation covers this row.',
    'document_id': 'Retained identifier of the official filing.', 'source_url': 'HTTPS link to the official PSX DPS or verified issuer source.',
    'page': 'Physical PDF page, starting at 1.', 'published_on': 'Verified filing publication date, ISO format; never substituted by retrieval.',
    'retrieved_on': 'Source retrieval date, ISO format; not the exporter execution date.'}


def main_readme(info):
    out = ['# PSX financial statements', '', 'Source-linked historical financial figures for 20 Pakistan Stock Exchange companies. This compilation is for researchers, students and people building reproducible financial datasets. It contains extracted reported figures from official filings, with partial coverage as of the cutoff below. It is not a complete ten-year database.', '', f"Data cutoff: **{info['data_cutoff']}**. Published facts: **{info['facts']}**. Admitted checked: **{info['status_counts'].get('admitted_checked', 0)}**. Collected checked: **{info['status_counts'].get('collected_checked', 0)}**.", '', 'Only reported historical financial data is included. There are no forecasts, valuations, price-implied expectations, scenarios or investment recommendations. Filing documents and images are not distributed: this repository contains links only.', '', '## Companies and coverage', '', '| Symbol | Company | Sector | Annual years | Quarter periods | Half-year periods | Nine-month periods | Admitted | Collected |', '|---|---|---|---|---:|---:|---:|---:|---:|']
    for s, c in info['companies'].items():
        out.append('| ' + ' | '.join([f'[{s}]({s}/README.md)', md(c['name']), md(c['sector']), ', '.join(c['annual_years']) or 'none', str(len(c['quarters'])), str(len(c['half_years'])), str(len(c['nine_months'])), str(c['admitted']), str(c['collected'])]) + ' |')
    out += ['', 'Exact dates, statements present, per-stock missing-publication counts and exclusion reasons are in [coverage.md](coverage.md). A year in this table means at least one line exists, not that every statement is complete.', '', '## Files and use', '', '`data/facts.csv` is the source of truth: one row per reported figure. Each company folder contains a readable history, an annual wide CSV and an interim wide CSV named `quarterly.csv`. Its interim columns explicitly distinguish quarters, half-years and nine-month periods.', '', 'Wide CSV row identifiers are statement, line item, basis, unit and currency; period columns are `YYYY-MM-DD|period_type`. Join a wide cell back to facts.csv using symbol, line item, period end, period type and basis to retrieve its source. Blank cells mean missing, never zero. Company Markdown tables link each figure to a complete source reference.', '', '### Python / pandas example', '', 'The exporter itself requires only the Python standard library. This optional analysis example uses pandas:', '', '```python', 'import pandas as pd', 'facts = pd.read_csv("data/facts.csv", dtype={"value": "string", "page": "Int64"})', 'rows = facts.query("symbol == \'MLCF\' and period_type == \'annual\' and basis == \'consolidated\'")', 'print(rows[["line_item", "period_end", "value", "unit", "source_url", "page"]])', '```', '', '### Spreadsheet example', '', 'Import `data/facts.csv` through your spreadsheet’s text/CSV import with UTF-8 encoding. Keep identifiers as text. Filter `symbol`, then `basis` and `period_type`; use `period_end` as pivot columns and `line_item` as rows. Filter to one unit/currency and check for duplicates before pivoting. Keep the provenance columns beside any extracted figure. Do not sum EPS or mix bases or cumulative and discrete periods.', '', '## Data dictionary', '', '| Column | Meaning |', '|---|---|']
    out += ['| ' + k + ' | ' + md(DICTIONARY[k]) + ' |' for k in FIELDS]
    out += ['', '## Methodology', '', 'Sources are official PSX DPS filings and issuer investor-relations financial reports. The upstream collection uses statement parsers, text/geometry extraction and model-assisted visual transcription. This exporter does not fetch, open or copy those documents. It reads retained JSON observations and source metadata, and permits only reported financial statement cells and supported financial notes.', '', '`admitted_checked` means an observation passed the admitted annual-floor checks or was classified eligible in the retained reconciliation. The exporter also validates its raw numeric scale, provenance and period structure. `collected_checked` means a collected observation passed this export’s structural and numeric checks and its batch had no detected unresolved failure. It has not been admitted to the private product and has no independent audit assurance. Arithmetic checks test consistency, not transcription accuracy.', '', 'The exporter independently recalculates supported record-bound linear equations and available balance, profit-attribution, bank-income and cash-subtotal identities using Decimal arithmetic. Operands must share document, period, duration, basis, unit and currency. Tolerances cannot exceed half a printed unit per operand. Unsupported scalar summary assertions are not treated as verified equations. A row without a complete supported equation is labelled `cross_check_unavailable`; it may still pass source/structure/raw-scale checks. It is not described as arithmetically reconciled.', '', 'The retained annual floor can select an otherwise checked reported fact even where a narrower reconciliation classifies it outside that reconciliation’s metric scope. That approved annual-floor selection governs its admission status. Other reconciliation observations must be eligible; audit-only, quarantined and derived records are excluded.', '', 'Explicit batch replacement links and numeric versions of the same named scope replace older deliveries before selection. Incomplete envelopes, hash mismatches, failed/unresolved batches and held observations are excluded. The inputs are not modified. Structural unknowns are excluded rather than filled.', '', 'For the same symbol, normalized line item, period end, period type and basis, admitted checked data takes precedence over collected data. Equal duplicate observations are deduplicated deterministically, preferring the latest publication date and then stable source identifiers. Conflicting collected amounts or statement/unit semantics are withheld together in [data/conflicts.csv](data/conflicts.csv), with both sources. Conflicting admitted candidates are also withheld. A selected admitted fact resolves lower-trust collected disagreement by the stated precedence. Only explicit upstream replacement resolves a collected restatement; a later filing alone does not silently overwrite a different collected value. Consolidated and standalone remain separate.', '', '## Conventions', '', 'Values use the unit shown in each row. Monetary scales such as thousands and millions are applied once to raw printed values; do not multiply again. Currency is explicit. Per-share amounts, share counts and percentages retain their distinct units. Parentheses are parsed as negative amounts. Other reported signs are preserved; expenses are not automatically made negative. Printed dashes or ambiguous numeric cells are not filled with zero.', '', 'Fiscal year ends are listed per company. They come from the admitted annual floor or a single consistent calendar supported by collection-manifest evidence; unknown calendars prevent export. A fiscal year is labelled by its ending year. Corporate reorganizations and changes of presentation can limit comparability; ticker continuity does not prove an unchanged group perimeter.', '', 'Annual flows require twelve-month duration. Quarter flows are three months; half-year and nine-month flows remain cumulative. Interim balance-sheet snapshots use the explicit period classification or the evidenced fiscal calendar’s three-, six- or nine-month position. No subtraction creates a quarter, EPS, total or missing line. Balance sheets are point-in-time figures, never flows.', '', '## Limitations and gaps', '', 'Collection is ongoing. Available years, statements, bases and line items differ by company. Publication dates are often unknown even when a retained official report exists; those observations are withheld. Retrieval is never used as publication. See the per-stock counts in coverage.md. Those counts cover otherwise qualifying active observations and are not counts of every cell in every filing; duplicate deliveries of the same observation count once.', '', 'Labels and metric identifiers reflect retained extractions and may differ across collectors or issuers. Source and statement context must be checked before comparing lines. Arithmetic agreement does not establish correct transcription, correct accounting interpretation or a complete statement. The exporter cannot verify document contents without reopening the linked filing. It does not infer issuer-domain authority from a collected URL alone: issuer domains must already be present in retained official-source metadata.', '', '## Reproduce and update', '', 'Python 3.12, standard library only. Inputs are local retained research/collection files and are not distributed. Their filenames and SHA-256 digests are recorded in `data/export_manifest.json`; row-level input references are in `data/lineage.csv`. These files contain generated provenance metadata, not copies of the private inputs. The backup map is consulted only for original URL recovery; its document files are never read.', '', 'From the repository root:', '', '```powershell', f'& "C:/Users/MWasa/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe" scripts/export.py --cutoff {info["data_cutoff"]}', '& "C:/Users/MWasa/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe" scripts/validate.py --self-test --spot-check 25', '```', '', 'Portable equivalent: `python scripts/export.py --cutoff YYYY-MM-DD --admitted PATH --batches PATH --backup-map PATH`, then `python scripts/validate.py --self-test --spot-check 25`. `--output PATH` defaults to this repository. The cutoff is inclusive and fixed for reproducibility. Publication, retrieval and financial period dates after it are excluded. Identical input bytes, arguments and existing changelog history produce identical output bytes. Inputs are hashed again before writing; concurrent changes abort the export. The exporter does not create a network connection, commit or push.', '', 'Refresh after new filings have been collected and checked. Review validation, coverage and exclusions before publishing. Keep releases and meaningful changes in [CHANGELOG.md](CHANGELOG.md). Existing history is preserved, with an export digest appended when the canonical facts change. This is a manually run export, not a scheduled process.', '', '## Cite', '', 'Cite: Wasay Ijaz, “PSX financial statements,” wasayijaz/psx-financials, version 1, data cutoff ' + info['data_cutoff'] + ', plus the Git commit used. Cite the original issuer/PSX filing, document identifier and page for every figure you reuse. Preserve provenance and status when redistributing data and state any modifications.', '', '## Disclaimer', '', 'Research data compiled from public filings. It may contain extraction errors. Verify figures against the linked official filing before relying on them. This compilation is not investment advice and makes no performance claims. It is provided without warranty.', '', '## Licenses and attribution', '', 'Exporter and validator code: [MIT](LICENSE). Original compilation and organization of the data: [CC BY 4.0](DATA-LICENSE), attributed to Wasay Ijaz with PSX and the issuers identified as the original financial-data sources. No ownership or relicensing of the underlying filings is claimed; issuer and PSX rights remain with their respective owners. No PDFs, images or other source documents are included.', '', '## Report an error', '', 'Open a [GitHub issue](https://github.com/wasayijaz/psx-financials/issues) with the ticker, line item, period, basis, document URL, page, observed value and proposed correction. Include the repository commit. Link to the official filing; do not upload a document copy. Corrections require source evidence and are recorded in the changelog.', '']
    return '\n'.join(out)


MIT = '''MIT License

Copyright (c) 2026 Wasay Ijaz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''

DATA_LICENSE = '''Creative Commons Attribution 4.0 International (CC BY 4.0)

The original data compilation and organization in this repository are licensed
by Wasay Ijaz under CC BY 4.0:
https://creativecommons.org/licenses/by/4.0/
Legal terms: https://creativecommons.org/licenses/by/4.0/legalcode

You may share and adapt this compilation, including commercially, subject to
the license: provide appropriate attribution, link to the license and indicate
changes. Do not imply endorsement or impose additional legal/technical restrictions.

Attribution: Wasay Ijaz, PSX financial statements, wasayijaz/psx-financials,
the version/commit and data cutoff used. Original financial-data sources:
Pakistan Stock Exchange (PSX DPS) and the individual issuers identified by
the source URL, document identifier and page attached to each observation.

No ownership or relicensing of the original filings is claimed. Their original
rights remain with PSX and the issuers. Source documents are linked only and
are not distributed in this repository. The license applies only to rights
the compiler can grant, including the original compilation and organization.

The compilation is supplied without warranty; see the full CC BY 4.0 legal terms.
Code is separately licensed under MIT in LICENSE.
'''


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admitted', type=Path, default=DEFAULT_ADMITTED)
    parser.add_argument('--batches', type=Path, default=DEFAULT_BATCHES)
    parser.add_argument('--backup-map', type=Path, default=DEFAULT_BACKUP)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--cutoff', type=lambda s: date.fromisoformat(s).isoformat(), required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    for source in (args.admitted, args.batches, args.backup_map):
        if args.output.is_relative_to(source.resolve()) or source.resolve().is_relative_to(args.output):
            parser.error('Output must be separate from read-only inputs')
    return args


if __name__ == '__main__':
    build(parse_args())
