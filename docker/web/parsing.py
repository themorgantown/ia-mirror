"""URL and identifier parsing utilities."""

import re
from typing import Tuple, List


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


def parse_batch_input(text: str) -> Tuple[List[str], List[str]]:
    """
    Parse batch input (newline-separated identifiers/URLs).
    
    Returns:
        Tuple[valid_identifiers, invalid_lines]
    """
    valid = []
    invalid = []
    
    # Split by newlines first to handle comments
    tokens = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Split line by comma or space
        line_tokens = [t for t in re.split(r'[,\s]+', line) if t.strip()]
        tokens.extend(line_tokens)
    
    for token in tokens:
        identifier, is_valid = normalize_identifier(token)
        if identifier:
            if is_valid:
                valid.append(identifier)
            else:
                invalid.append(token)
    
    return valid, invalid


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
    # Normalize base
    base = os.path.normpath(base)
    # Join and normalize
    full = os.path.normpath(os.path.join(base, subpath))
    # Ensure result starts with base
    if not full.startswith(base):
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

    # Must start with allowed base directory
    allowed_bases = ['/data', '/downloads']
    if not any(path.startswith(base) for base in allowed_bases):
        return False

    # Normalize and check for escape attempts
    normalized = os.path.normpath(path)
    # Check for parent directory traversal
    if '..' in normalized:
        return False
    # Ensure normalized path still starts with an allowed base
    if not any(normalized.startswith(base) for base in allowed_bases):
        return False

    # Additional safety: no absolute symlink escapes
    # (More thorough checks can be done at runtime)
    return True
