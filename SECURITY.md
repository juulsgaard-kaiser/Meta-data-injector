# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately using
[Report a vulnerability](https://github.com/juulsgaard-kaiser/Meta-data-injector/security/advisories/new).
Do not include credentials, personal media or exploitable details in a public issue.
Include the affected version, reproduction steps and a minimal non-sensitive sample
when possible. There is no guaranteed response time or paid support commitment.

## Supported versions

Security fixes target the current `main` branch and the latest alpha version.
Earlier alpha releases are not maintained separately. The software remains alpha;
keep backups and validate media in your intended workflow.

## Contribution controls

- Outside contributors use forks and pull requests; repository collaborators use
  feature branches. Public visibility does not grant write access.
- Changes to `main` require passing Linux/Windows tests, CodeQL analysis, dependency
  review and resolved review conversations.
- `@xcifer` owns review of every path, including workflows and `CODEOWNERS`.
  New commits invalidate earlier approvals.
- `@xcifer` has the explicitly approved review exception for their own maintenance
  changes. This does not waive required checks, force-push or deletion protection.
- Other administrators retain administration access; repository administrators and
  organization owners can change these policies. Branch rules cannot prevent that.
- Actions use pinned commits, minimal token permissions and hosted runners.
  External contributors require approval before their workflows run. Workflow
  approval allows code execution; review the entire change before approving it.
- Dependabot, dependency review, secret scanning and push protection supplement
  review. Automated checks do not guarantee the absence of vulnerabilities.
