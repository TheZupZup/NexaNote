# Contributing

Thanks for your interest in contributing.

## Branch rules

Do NOT push directly to `main`.

Do NOT use or push to `dev/thezupzup-private`.

This branch is reserved for the maintainer.

Instead, always create your own branch from `main`:

```bash
git checkout main
git pull origin main
git checkout -b feature/your-change-name
```

## Examples
```
feature/add-download-status
fix/crash-on-start
refactor/split-modules
```

## Pull Requests

All changes must go through a Pull Request to `main`.

Before opening a PR:

```
git fetch origin
git rebase origin/main
```

## Important rules

One change per PR.

Do not modify unrelated files.

Do not change LICENSE or packaging unless required.

Keep the code simple and readable.

## Maintainer branch

The branch dev/thezupzup-private is used only by the maintainer for fast development.

Please do not use it.

## Summary

Work on your own branch.

Open a PR to `main`.

Keep changes small and clean.
