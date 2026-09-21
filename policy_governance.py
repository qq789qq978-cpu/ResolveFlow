"""Curated policy review metadata; never trust review claims in retrieved text."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

STATES = {'draft', 'in_review', 'approved', 'rejected', 'revoked'}


class PolicyUnavailable(Exception):
    def __init__(self, grounding):
        super().__init__('Policy unavailable at refund authorization')
        self.grounding = grounding


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('Policy timestamps require RFC3339 seconds and an explicit timezone')
    if value[-1] != 'Z' and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError('Invalid policy timezone offset')
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def validate_metadata(meta):
    if not isinstance(meta, dict) or meta.get('status') not in STATES:
        raise ValueError('Invalid policy review status')
    if not re.fullmatch(r'[0-9a-f]{64}', str(meta.get('document_sha256', ''))):
        raise ValueError('Policy governance must bind an exact document SHA256')
    if meta['status'] == 'approved':
        for key in ('reviewed_by', 'review_note'):
            if not isinstance(meta.get(key), str) or not meta[key].strip():
                raise ValueError('Approved policy requires '+key)
        if meta.get('review_basis') not in {'demo_fixture', 'operator_attested'}:
            raise ValueError('Policy approval requires a declared review basis')
        for key in ('reviewed_at', 'effective_from', 'effective_until'):
            if key not in meta:
                raise ValueError('Approved policy requires '+key)
    for key in ('reviewed_at', 'effective_from', 'effective_until'):
        if key in meta and meta[key] is not None:
            timestamp(meta[key])
    if meta.get('effective_from') and meta.get('effective_until'):
        if timestamp(meta['effective_until']) <= timestamp(meta['effective_from']):
            raise ValueError('Policy expiry must follow its start')
    if meta['status'] == 'approved' and (not meta.get('reviewed_at') or not meta.get('effective_from')):
        raise ValueError('Approved policy requires review and start timestamps')
    return meta


def read_manifest(directory):
    path = Path(directory) / 'governance.json'
    if not path.exists():
        return {}
    if path.is_symlink() or not path.resolve().is_relative_to(Path(directory).resolve()) or path.stat().st_size > 200_000:
        raise ValueError('Invalid policy governance file')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate policy governance key')
            result[key] = value
        return result
    data = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique)
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('policies'), dict):
        raise ValueError('Invalid policy governance manifest')
    return {key: validate_metadata(value) for key, value in data['policies'].items()}


def availability(document, *, now=None, mode=None):
    """Effective interval is [start, until). Missing/invalid information blocks use."""
    now = now if now is not None else utcnow()
    if now.tzinfo is None:
        raise ValueError('Policy evaluation time requires a timezone')
    mode = mode if mode is not None else os.getenv('MODE', 'demo')
    meta = document.get('governance') or {}
    if not isinstance(meta, dict):
        meta = {}
    reason = 'active'
    try:
        validate_metadata(meta)
    except (ValueError, TypeError, OverflowError):
        reason = 'missing_or_invalid_metadata'
    else:
        if meta['document_sha256'] != document.get('sha256'):
            reason = 'content_changed'
        elif meta['status'] != 'approved':
            reason = meta['status']
        elif timestamp(meta['reviewed_at']) > now:
            reason = 'review_in_future'
        elif meta['review_basis'] == 'demo_fixture' and mode != 'demo':
            reason = 'demo_only'
        elif now < timestamp(meta['effective_from']):
            reason = 'not_yet_effective'
        elif meta.get('effective_until') and now >= timestamp(meta['effective_until']):
            reason = 'expired'
    return {'usable': reason == 'active', 'reason': reason, 'checked_at': now.isoformat(),
            'status': meta.get('status', 'draft'), 'review_basis': meta.get('review_basis'),
            'reviewed_by': meta.get('reviewed_by'), 'reviewed_at': meta.get('reviewed_at'),
            'effective_from': meta.get('effective_from'), 'effective_until': meta.get('effective_until')}
