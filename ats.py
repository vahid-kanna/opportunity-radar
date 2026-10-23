"""Opt-in official employer feeds; no search-engine scraping or inferred eligibility.
Contracts: https://developers.greenhouse.io/job-board.html
https://github.com/lever/postings-api
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit


def configured_boards():
    """Fail visibly on bad operator configuration; never fetch arbitrary URLs."""
    path = os.environ.get('RADAR_BOARDS_FILE')
    if not path:
        return []
    boards = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(boards, list) or len(boards) > 5:
        raise ValueError('RADAR_BOARDS_FILE must contain at most five boards')
    result, seen = [], set()
    for board in boards:
        if not isinstance(board, dict):
            raise ValueError('Each employer board must be an object')
        provider, token, company = (board.get(k) for k in ('provider', 'token', 'company'))
        if provider not in ('greenhouse', 'lever', 'lever_eu'):
            raise ValueError('Unsupported employer board provider')
        if not isinstance(token, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', token):
            raise ValueError('Invalid employer board token')
        if not isinstance(company, str) or not company.strip() or len(company) > 160:
            raise ValueError('Employer company name required')
        key = (provider, token)
        if key in seen:
            raise ValueError('Duplicate employer board')
        seen.add(key)
        domains = board.get('employer_domains', [])
        if not isinstance(domains, list) or any(not isinstance(d, str) or not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,}', d) for d in domains):
            raise ValueError('employer_domains must be explicit lowercase hostnames')
        entry = dict(provider=provider, token=token, company=company.strip())
        if domains:
            entry['employer_domains'] = domains
        result.append(entry)
    return result


def source_name(board):
    return f"{board['provider']}:{board['token']}"


def fetch_board(board, fetch_json):
    # Runtime import avoids the scanner -> ats -> scanner import cycle.
    from scanner import _base, _match, _text, _evidence, _salary
    provider, token, company = (board[k] for k in ('provider', 'token', 'company'))
    if provider == 'greenhouse':
        url = f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true'
        data = fetch_json(url)
        if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
            raise ValueError('Greenhouse feed missing jobs')
        items = data['jobs']
        # A truncated response must not be mistaken for complete source coverage.
        if isinstance(data.get('meta'), dict) and data['meta'].get('total', len(items)) > len(items):
            raise ValueError('Incomplete Greenhouse job list')
    else:
        host = 'api.eu.lever.co' if provider == 'lever_eu' else 'api.lever.co'
        url = f'https://{host}/v0/postings/{token}?mode=json&limit=500'
        items = fetch_json(url)
        if not isinstance(items, list):
            raise ValueError('Lever feed must be a list')
        if len(items) >= 500:
            raise ValueError('Lever board reached pagination limit; narrow board or add pagination')
    if len(items) > 2000:
        raise ValueError('Employer board exceeds processing bound')
    records = []
    for item in items:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        title = _text(item.get('title' if provider == 'greenhouse' else 'text', ''))
        track, matched = _match(title)
        if not track:
            continue
        link = item.get('absolute_url' if provider == 'greenhouse' else 'hostedUrl', '')
        from quality import safe_url
        link = safe_url(link)
        if not link or urlsplit(link).scheme != 'https':
            continue
        destination = urlsplit(link)
        if provider != 'greenhouse':
            expected_host = 'jobs.eu.lever.co' if provider == 'lever_eu' else 'jobs.lever.co'
            if destination.hostname != expected_host or not destination.path.startswith('/' + token + '/'):
                continue
        elif destination.hostname not in ('boards.greenhouse.io', 'job-boards.greenhouse.io') + tuple(board.get('employer_domains', [])):
            continue
        elif destination.hostname in ('boards.greenhouse.io', 'job-boards.greenhouse.io') and not destination.path.startswith('/' + token + '/'):
            continue
        if provider == 'greenhouse':
            loc = item.get('location')
            location = loc.get('name', '') if isinstance(loc, dict) else ''
            description = item.get('content', '')
        else:
            categories = item.get('categories') or {}
            location = categories.get('location', '') if isinstance(categories, dict) else ''
            sections = item.get('lists')
            sections = sections[:100] if isinstance(sections, list) else []
            description = ' '.join([_text(item.get('descriptionPlain') or item.get('description', ''))] +
                                   [_text(s.get('text', '')) + ' ' + _text(s.get('content', '')) for s in sections if isinstance(s, dict)])
        record = _base(f'{provider}_{token}_{item["id"]}', source_name(board), title, company, link, description, location)
        record.update(kind='job', type='employer', track=track, matched_on=matched,
                      source_authority='employer_ats', source_feed_url=url)
        # Published on an ATS means listed, not candidate eligibility or an application deadline.
        for field in ('description', 'location'):
            _evidence(record, field, record[field])
        if provider != 'greenhouse':
            salary = item.get('salaryRange')
            if isinstance(salary, dict):
                _salary(record, salary.get('min'), salary.get('max'), salary.get('currency'), salary.get('interval', ''))
            apply_url = safe_url(item.get('applyUrl'))
            if apply_url and urlsplit(apply_url).scheme == 'https' and urlsplit(apply_url).hostname == urlsplit(link).hostname and urlsplit(apply_url).path.startswith('/' + token + '/') :
                record['apply_url'] = apply_url
        records.append(record)
    return records
