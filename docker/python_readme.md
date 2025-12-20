To run just fetcher.py, you can use the following command from the root: 

```bash
source .venv/bin/activate && cd docker && python fetcher.py goodytwoshoes00newy --dry-run --destdir ./mirror/ --glob "*.zip" --exclude "*.nfo" --concurrency 8 --checksum
```

Your venv should use 3.13.5 or 3.14.x. 