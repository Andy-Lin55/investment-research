# Development and release safety

`main` is the existing service; implement each upgrade on a `codex/` feature branch and review it through a pull request. A branch isolates code, not credentials or external data.

## Local and pull-request checks

Run `python -m pytest -q`. Test collection must not load the repository `.env`. Regression CI has no service secrets and cannot use real external sockets or SMTP. Real integrations belong in a separately configured manual acceptance run, never in the PR job. `test_env.yml` is an existing credentialed API diagnostic, not the regression suite.

The current production jobs and schedules are preserved by the foundation PR. Do not run daily/weekly production commands as a test: they can send email or modify Notion.

## Testing new integrations

Use dedicated Notion test databases and a OneDrive test folder with test credentials scoped as narrowly as the provider allows. A directory name or dry-run label alone is not credential isolation. Use synthetic fixtures until those destinations and their read-back are configured. Do not give the regression job production API, OneDrive or SMTP credentials.

New scheduled workflows must be manually tested first, start with production writes disabled, and require explicit configured destinations. Keep final publication in one cloud writer and compare the candidate's baseline version before updating the official model/report.

## GitHub protection

Require PRs and the `regression` check on `main`; include administrators and disallow force pushes/deletion. The `production` environment should allow only `main`. Merely creating the environment does not protect existing repository secrets: jobs must reference it and credentials must be migrated in a separately validated change. Do not add a reviewer requirement to every scheduled daily run.

## Rollback

Tag the known-good commit before upgrades. Revert the relevant PR to restore code behavior. Restore Notion/OneDrive data from pre-change snapshots separately: reverting Git does not undo external writes. Disable the new feature before attempting a data restore, and verify read-back afterwards.
