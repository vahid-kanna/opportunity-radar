"""Local matching preferences, configured as a JSON object in RADAR_PROFILE_JSON."""
import json
import os

DEFAULT_PROFILE = {
    'country': 'India', 'max_experience': 2, 'graduation_year': 2027,
    'branch': 'Civil Engineering', 'include_engineering': False,
    'stale_days': 14, 'evidence_max_age_days': 14,
}


def get_profile():
    """Reject invalid configuration rather than silently loosening qualification."""
    try:
        override = json.loads(os.environ.get('RADAR_PROFILE_JSON', '{}'))
    except (ValueError, TypeError) as exc:
        raise ValueError('RADAR_PROFILE_JSON must be a JSON object') from exc
    if not isinstance(override, dict) or set(override) - set(DEFAULT_PROFILE):
        raise ValueError('RADAR_PROFILE_JSON contains unknown profile fields')
    result = dict(DEFAULT_PROFILE, **override)
    for key in ('country', 'branch'):
        if not isinstance(result[key], str) or not result[key].strip():
            raise ValueError(f'Profile {key} must be nonempty text')
    for key, lower, upper in (('max_experience', 0, 50), ('graduation_year', 2000, 2100),
                              ('stale_days', 1, 365), ('evidence_max_age_days', 1, 90)):
        if type(result[key]) is not int or not lower <= result[key] <= upper:
            raise ValueError(f'Profile {key} must be an integer from {lower} to {upper}')
    if type(result['include_engineering']) is not bool:
        raise ValueError('Profile include_engineering must be boolean')
    return result
