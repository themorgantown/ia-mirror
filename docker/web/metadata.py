"""Internet Archive Metadata API client."""

import requests
from typing import Dict, Optional, Tuple, List

METADATA_API_URL = "https://archive.org/metadata/{identifier}"


def fetch_metadata(identifier: str) -> Dict[str, Optional[str]]:
    """
    Fetch metadata for an IA identifier.
    
    Returns:
        Dict with keys:
        - title: Item title (or identifier if missing)
        - creator: Item creator (or empty string)
        - thumbnail_url: URL to thumbnail (or None)
    """
    try:
        response = requests.get(METADATA_API_URL.format(identifier=identifier), timeout=10)
        if response.status_code != 200:
            return _default_metadata(identifier)
        
        data = response.json()
        metadata = data.get('metadata', {})
        files = data.get('files', [])
        server = data.get('d1') or data.get('d2') or 'archive.org'
        dir_path = data.get('dir', '')
        
        # Extract title
        title = metadata.get('title', identifier)
        if isinstance(title, list):
            title = title[0]
            
        # Extract creator
        creator = metadata.get('creator', '')
        if isinstance(creator, list):
            creator = ', '.join(creator)
            
        # Find thumbnail
        thumbnail_url = None
        
        # Strategy 1: Look for __ia_thumb.jpg in files
        for f in files:
            if f.get('name') == '__ia_thumb.jpg':
                thumbnail_url = f"https://{server}{dir_path}/__ia_thumb.jpg"
                break
                
        # Strategy 2: Look for 'Item Tile' format
        if not thumbnail_url:
            for f in files:
                if f.get('format') == 'Item Tile':
                    name = f.get('name')
                    thumbnail_url = f"https://{server}{dir_path}/{name}"
                    break
        
        return {
            'title': title,
            'creator': creator,
            'thumbnail_url': thumbnail_url
        }
        
    except Exception as e:
        print(f"Error fetching metadata for {identifier}: {e}")
        return _default_metadata(identifier)


def _default_metadata(identifier: str) -> Dict[str, Optional[str]]:
    """Return default empty metadata."""
    return {
        'title': identifier,
        'creator': '',
        'thumbnail_url': None
    }
