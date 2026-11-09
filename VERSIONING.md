# OPM versioning policy

OPM uses `MAJOR.MINOR.PATCH` with a project-specific release cadence.

| Change | Number changed | Example | Publication |
| --- | --- | --- | --- |
| **GitHub milestone** | Increment the **second** (MINOR) number and reset PATCH to zero | `0.6.x` → **`0.7.0`** | Stage on `nightly` first, then publish an approved release |
| **Internal feature or batch of fixes** | Increment only the **third** (PATCH) number | `0.6.0` → `0.6.1` → `0.6.2` | Private development/testing build |
| **Single small fix** | Keep the current version | `0.6.19` → `0.6.19` | Deploy a signed build revision to the test server |
| **Major / GM** | Increment the **first** (MAJOR) number; reset MINOR and PATCH | `0.x.y` → **`1.0.0`** | Major GitHub/Gold Master milestone |

## Rules

- The first GitHub milestone with this policy is **v0.6.0**. Previous `v0.5.x` archives used the patch number for interim feature builds and remain historical tags.
- Deploy requested fixes to the test server. For each small fix, keep the app version and advance the numeric `OPM_BUILD_REVISION` in the source archive. A release with no revision file is revision zero.
- Use a distinct filename such as `onyx_patch_manager_0.6.19_r1_source.zip` for a same-version build. Bump PATCH when releasing several fixes together or adding a feature, and start the new version at revision zero.
- Use the milestone version for the first nightly build. A nightly branch push does not create a tag or GitHub Release. Keep internal candidates private by default.
- After v1.0.0, any subsequent major milestone increments MAJOR; this numbering policy is about release cadence and does not by itself guarantee API compatibility.
- Keep `pyproject.toml`, `src/opm/__init__.py`, installer UI version, README, release notes, tests and ZIP filename consistent for each packaged build.
- Tag public GitHub releases as `vMAJOR.MINOR.PATCH`, such as `v0.6.0`. Publish the source ZIP and put the consolidated change summary directly in the GitHub release description.
- Pre-GM GitHub releases are development milestones, not production or security certifications; GM requires completing the remaining live acceptance checklist.
