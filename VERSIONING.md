# OPM versioning policy

OPM uses `MAJOR.MINOR.PATCH` with a project-specific release cadence.

| Change | Number changed | Example | Publication |
| --- | --- | --- | --- |
| **GitHub release** | Increment the **second** (MINOR) number; reset PATCH to zero | `0.5.24` → **`0.6.0`**, then `0.7.0` | Publish as a GitHub release |
| **Internal testing** | Increment only the **third** (PATCH) number | `0.6.0` → `0.6.1` → `0.6.2` | Private development/testing build, not a new GitHub release |
| **Major / GM** | Increment the **first** (MAJOR) number; reset MINOR and PATCH | `0.x.y` → **`1.0.0`** | Major GitHub/Gold Master milestone |

## Rules

- The first GitHub milestone with this policy is **v0.6.0**. Previous `v0.5.x` archives used the patch number for interim feature builds and remain historical tags.
- After `0.6.0`, internal candidates progress as `0.6.1`, `0.6.2`, etc. On the next GitHub publication, jump to `0.7.0` and reset the third number; do not publish the internal candidates as GitHub releases by default.
- After v1.0.0, any subsequent major milestone increments MAJOR; this numbering policy is about release cadence and does not by itself guarantee API compatibility.
- Keep `pyproject.toml`, `src/opm/__init__.py`, installer UI version, README, release notes, tests and ZIP filename consistent for each packaged build.
- Tag public GitHub releases as `vMAJOR.MINOR.PATCH`, such as `v0.6.0`. Publish the source ZIP and put the consolidated change summary directly in the GitHub release description; do not add a separate release-description Markdown file.
- Pre-GM GitHub releases are development milestones, not production or security certifications; GM requires completing the remaining live acceptance checklist.
