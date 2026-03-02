# Branch Protection: main

Configured via GitHub branch protection API.

## Rules

- Direct pushes to `main` are blocked (PR required)
- Force pushes to `main` are blocked
- Branch deletion is blocked
- CI `test` job must pass before merge
- Branches must be up to date before merging (strict mode)

## Applied via

```bash
gh api repos/bread-wood/breadmin-composer/branches/main/protection \
  --method PUT \
  --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "contexts": ["test"]},
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

## Verification

```bash
gh api repos/bread-wood/breadmin-composer/branches/main/protection \
  --jq '{force_push_blocked: (.allow_force_pushes.enabled | not), deletions_blocked: (.allow_deletions.enabled | not), required_status_checks: .required_status_checks}'
```
