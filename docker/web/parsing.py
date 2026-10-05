"""URL and identifier parsing utilities."""

import re
from typing import Dict, Tuple, List
from urllib.parse import parse_qs, urlsplit


def search_query_from_url(url: str) -> str:
    """
    Translate the search filters on an archive.org collection page URL into an
    `ia search` query fragment (without the `collection:` clause).

    Mirrors archive.org's own semantics: several `and[]` values for one field are
    ORed, different fields are ANDed, `not[]` values are excluded, and `query`
    (search within the collection) is ANDed as written.

        ?query=bridge&and[]=subject:"A"&and[]=subject:"B"&not[]=year:"2020"
        -> (bridge) AND (subject:"A" OR subject:"B") AND NOT year:"2020"

    Returns '' when the URL carries no filters.
    """
    if '?' not in url:
        return ''
    params = parse_qs(urlsplit(url).query)
    by_field: Dict[str, List[str]] = {}
    for value in params.get('and[]', []):
        by_field.setdefault(value.split(':', 1)[0], []).append(value)
    clauses = [f"({q.strip()})" for q in params.get('query', []) if q.strip()]
    clauses += [f"({' OR '.join(values)})" for values in by_field.values()]
    clauses += [f"NOT {value}" for value in params.get('not[]', [])]
    return ' AND '.join(clauses)


_SEARCH_PAGE_URL = re.compile(r'archive\.org/search(?:\.php)?(?:[?#]|$)')


def search_folder_name(query: str) -> str:
    """Destination folder for a search-page download: search-<slug of the query>."""
    slug = re.sub(r'[^a-z0-9]+', '-', query.lower()).strip('-')[:80].rstrip('-')
    return f"search-{slug or 'results'}"


def collection_search_query(collection_id: str, query: str = '') -> str:
    """The `ia search` query for a collection's items, narrowed by `query` if given."""
    base = f'collection:"{collection_id}"'
    return f'{base} AND ({query})' if query else base


def normalize_identifier(line: str) -> Tuple[str, bool]:
    """
    Normalize a line to an IA identifier.
    
    Accepts:
    - https://archive.org/details/<id>
    - http://archive.org/details/<id>
    - Raw identifier: item-name
    
    Returns:
        Tuple[identifier, is_valid]
    """
    line = line.strip()
    
    # Reject empty/whitespace/comments
    if not line or line.startswith('#'):
        return '', False
    
    # Try to extract from archive.org URL
    match = re.search(r'archive\.org/details/([a-zA-Z0-9_\-\.]+)', line)
    if match:
        return match.group(1), True
    
    # Check if it's a valid identifier (alphanumeric, hyphen, underscore, dot)
    if re.match(r'^[a-zA-Z0-9_\-\.]+$', line):
        return line, True
    
    return line, False


def parse_batch_entries(text: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """
    Parse batch input (newline-separated identifiers/URLs) into entries.

    Each entry is {'identifier', 'query', 'source', 'kind'}. For an
    archive.org/details/ URL `query` is its search filter ('' if none) and kind is
    'item'. An archive.org/search URL has kind 'search': `query` is the whole
    search and `identifier` the folder its results go in. `source` is the raw token.

    Returns:
        Tuple[entries, invalid_lines]
    """
    entries = []
    invalid = []

    # Split by newlines first to handle comments
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Split by whitespace, then by comma - except inside URLs, whose search
        # filters can legitimately contain commas (subject:"Hudson, NY").
        for part in line.split():
            is_url = '://' in part or 'archive.org/' in part
            tokens.extend([part] if is_url else [t for t in part.split(',') if t])

    for token in tokens:
        if _SEARCH_PAGE_URL.search(token):
            query = search_query_from_url(token)
            # Full-text, TV-caption and radio searches (`sin=`) use a different index
            # than `ia search`, so they cannot be reproduced; reject rather than guess.
            full_text = parse_qs(urlsplit(token).query).get('sin', [''])[0]
            if query and not full_text:
                entries.append({'identifier': search_folder_name(query), 'query': query,
                                'source': token, 'kind': 'search'})
            else:
                invalid.append(token)
            continue

        identifier, is_valid = normalize_identifier(token)
        if identifier:
            if is_valid:
                query = search_query_from_url(token) if 'archive.org/details/' in token else ''
                entries.append({'identifier': identifier, 'query': query, 'source': token, 'kind': 'item'})
            else:
                invalid.append(token)

    return entries, invalid


def parse_batch_input(text: str) -> Tuple[List[str], List[str]]:
    """
    Parse batch input (newline-separated identifiers/URLs).

    Returns:
        Tuple[valid_identifiers, invalid_lines]
    """
    entries, invalid = parse_batch_entries(text)
    return [e['identifier'] for e in entries], invalid


def safe_join(base: str, subpath: str) -> str:
    """
    Safely join base directory with subpath, ensuring the result stays within base.

    Args:
        base: Absolute base directory path.
        subpath: Subpath relative to base.

    Returns:
        Joined absolute path if safe, otherwise raises ValueError.
    """
    import os

    base_real = os.path.realpath(base)
    full = os.path.realpath(os.path.join(base_real, subpath))

    try:
        common = os.path.commonpath([base_real, full])
    except ValueError:
        raise ValueError(f"Path traversal attempt: {subpath} escapes {base}")
    if common != base_real:
        raise ValueError(f"Path traversal attempt: {subpath} escapes {base}")
    return full


def validate_destination(path: str) -> bool:
    """
    Validate a destination path.

    Rules:
    - Must be within /data or /downloads (allowed base directories)
    - Cannot contain .. or other escapes
    - Must not start with /etc, /root, etc.

    Args:
        path: Path to validate

    Returns:
        True if valid, False otherwise
    """
    import os

    if not path or not os.path.isabs(path):
        return False

    allowed_bases = ['/data', '/downloads']
    normalized = os.path.realpath(path)
    for base in allowed_bases:
        base_real = os.path.realpath(base)
        try:
            if os.path.commonpath([base_real, normalized]) == base_real:
                return True
        except ValueError:
            continue
    return False
