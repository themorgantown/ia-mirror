Run comprehensive quality checks on the codebase.

Follow these steps:

1. **Python Syntax Check**: Verify fetcher.py compiles without errors
2. **Dockerfile Lint**: Use hadolint to check Dockerfile best practices  
3. **Git Status**: Ensure working directory is clean
4. **Container Test**: Build and test the image configuration
5. **Security Review**: Check for common security issues

Commands to run:

```bash
# 1. Python syntax check
echo "=== Python Syntax Check ==="
python -m py_compile docker/fetcher.py
echo "✓ Python syntax OK"

# 2. Dockerfile lint (install hadolint if needed)
echo "=== Dockerfile Lint ==="
hadolint docker/Dockerfile
echo "✓ Dockerfile lint complete"

# 3. Git status
echo "=== Git Status ==="
git status --porcelain
echo "✓ Git status check complete"

# 4. Build test
echo "=== Build Test ==="
docker build -t ia-mirror:test -f docker/Dockerfile docker
echo "✓ Build test complete"

# 5. Config test
echo "=== Configuration Test ==="
docker run --rm ia-mirror:test --print-effective-config
echo "✓ Configuration test complete"

# Cleanup test image
docker rmi ia-mirror:test
```

Expected results:
- No Python syntax errors
- Hadolint passes with minimal warnings
- Clean git working directory (or expected changes)
- Successful Docker build
- Valid configuration output

If hadolint is not installed:
```bash
# On macOS with Homebrew
brew install hadolint

# On Linux
wget -O hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64
chmod +x hadolint
sudo mv hadolint /usr/local/bin/
```