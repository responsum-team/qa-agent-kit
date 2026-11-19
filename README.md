# Project Template

This template provides the standard structure for `responsum-team` projects with built-in skill traceability.

## Structure

```
├── .github/workflows/
│   └── update-skills-manifest.yml   # Auto-generates skills-manifest.json
├── skills/                           # Add skills as subtrees here
│   └── .gitkeep
└── README.md
```

## Adding a skill

```bash
git subtree add --prefix=skills/<skill-name> https://github.com/responsum-team/<skill-name>.git main --squash
```

## Setup required

After creating a repo from this template, add the `DISPATCH_TOKEN` secret:

```bash
gh secret set DISPATCH_TOKEN --repo responsum-team/<your-repo-name> --body "<your-pat-or-gh-token>"
```

Or use the onboarding workflow:

```bash
gh workflow run onboard-project.yml -R responsum-team/shared-workflows -f repo=<your-repo-name>
```

## How traceability works

When you push changes to `skills/**`, the workflow automatically:
1. Generates `skills-manifest.json` listing all skills in this project
2. Triggers the central aggregator in `shared-workflows`
3. The aggregator updates `skill-usage-map.json` with bidirectional mapping
