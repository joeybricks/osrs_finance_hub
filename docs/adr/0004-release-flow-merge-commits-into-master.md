# 0004. Release flow: squash into develop, merge commits into master

- **Status:** Accepted
- **Date:** 2026-11-02
- **Tickets:** OSRS-16, OSRS-17, OSRS-18 (epic OSRS-7)

## Context

Work goes feature → `develop` → `master`. `master` matters for more than
tidiness: WIF only lets runs on `master` borrow `deployer-sa` and
`killswitch-sa` (ADR 0002, `infra/iam/wif.tf`), so a change isn't live until
it is released there.

Both branches first had linear history (squash or rebase only). Squashing a
release PR into `master` creates a new commit whose content matches `develop`
but which isn't in `develop`'s history:

```
squash release                         merge-commit release
develop: A─B─C                         develop: A─B─C
master:  A─S   (S = B+C squashed)      master:  A─B─C─M   (M has parents A and C)
next PR: git sees B, C as "new"        next PR: only commits after C are new
         again → conflicts / sync PR            → clean, no back-merge needed
```

With squash, each release would need `master` merged back into `develop`, or
`develop` reset, before the next one.

## Decision

| Branch | Ruleset | Merge methods | Linear history |
| --- | --- | --- | --- |
| `develop` | `protect-develop` | squash, rebase | required |
| `master` | `protect-master` | merge commit only | not required |

- Feature PRs squash into `develop`: one commit per ticket.
- Releases are a PR `develop` → `master`, merged with a merge commit. The merge
  commit is the release marker.
- Both rulesets keep: PR only, no force push or deletion, required checks.
- **Merge first, apply second.** Locally applied roots (`iam`, `billing`,
  `sandbox/*`) are applied by the Owner from an up-to-date `develop` or
  `master`, never from a feature branch, so state never records code that
  isn't merged.

## Consequences

- `develop` is always an ancestor of `master`, so releases never conflict
  and never need a back-merge.
- `master`'s history is not linear: one merge commit per release. Read it with
  `git log --first-parent master` to see only releases.
- Shipping a change takes two PRs (feature → develop, develop → master).
  Changes the button needs, like a new project in the killswitch allow list,
  aren't usable until the release PR is merged.
- Hotfixes go through `develop` too; there is no direct path to `master`.
- Cost: none.
