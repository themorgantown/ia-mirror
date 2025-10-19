Test the ia-mirror container with Internet Archive item: $ARGUMENTS

This command performs a dry-run test of the container to verify it can connect and process an Internet Archive item without actually downloading files.

Follow these steps:

1. Ensure the local image is built (`ia-mirror:local`)
2. Create a test directory for output
3. Run the container with dry-run enabled
4. Verify the container can authenticate and access the item
5. Check the logs for any errors or warnings

Commands to run:
```bash
# Create test directory
mkdir -p ./test-mirror

# Run dry-run test  
docker run --rm \
  -v "./test-mirror:/data" \
  -e IA_IDENTIFIER=$ARGUMENTS \
  -e IA_DESTDIR=/data \
  -e IA_DRY_RUN=true \
  -e IA_ACCESS_KEY=${IA_ACCESS_KEY} \
  -e IA_SECRET_KEY=${IA_SECRET_KEY} \
  ia-mirror:local
```

What to look for:
- Container starts without errors
- Connects to Internet Archive successfully  
- Shows what would be downloaded (file list)
- No authentication errors
- Clean container shutdown

If authentication fails:
- Verify IA_ACCESS_KEY and IA_SECRET_KEY are set
- Or mount your ia config: `-v ~/.config/ia:/home/app/.config/ia:ro`

Common test items:
- `jillem-full-archive` (small test item)
- `opensource` (very small collection sample)