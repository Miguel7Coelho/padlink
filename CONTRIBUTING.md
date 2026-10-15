# Contributing to padlink

Thanks for helping! Hardware reports are especially valuable: if padlink works (or doesn't) with
your controller, distribution or game, please open an issue.

## Branches

padlink uses a simple Git Flow model:

| Branch | Purpose | Rules |
|---|---|---|
| `main` | Latest **stable release**. Every commit on `main` is a tagged release (`vX.Y.Z`) | Only merged from `release/*` or `hotfix/*`; never committed to directly |
| `develop` | Integration branch for the next release | Feature branches merge here through pull requests |
| `feature/<name>` | New features (`feature/gyro-mouse`) | Branch from `develop`, merge back into `develop` |
| `fix/<name>` | Bug fixes for the next release (`fix/bt-trigger-offset`) | Branch from `develop`, merge back into `develop` |
| `docs/<name>` | Documentation only | Branch from `develop`, merge back into `develop` |
| `release/X.Y.Z` | Release preparation: version bump, changelog | Branch from `develop`, merge into `main` (tag) **and** back into `develop` |
| `hotfix/X.Y.Z` | Urgent fixes to a release | Branch from `main`, merge into `main` (tag) and `develop` |

Merges into `develop` and `main` use `--no-ff`, so each feature stays visible in the history:

```bash
git switch develop && git pull
git switch -c feature/my-change
# … commits …
git push -u origin feature/my-change      # open a pull request into develop
```

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/):

```text
feat(triggers): add "galloping" effect
fix(xinput): swap Square/Triangle to match the xpad layout
docs(troubleshooting): explain Steam Input light bar override
test(games): cover Flatpak Steam library paths
chore(ci): run tests on Python 3.13
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`.

## Development setup

```bash
git clone https://github.com/Miguel7Coelho/padlink.git && cd padlink
git switch develop
python3 -m pip install --user pytest
python3 -m pytest -q                    # tests need no controller and no GUI
python3 -m padlink list                 # run from the source tree
python3 -m padlink gui
```

To try a change with the background service, run `./install.sh` from your checkout; it installs
that copy.

## Code guidelines

- Python 3.11+, standard library only for the core (`padlink/*.py`); GTK only in `padlink/gui/`;
  `evdev` only imported inside Xbox mode code.
- Pure logic (report building, parsing, matching) stays separate from I/O, so it can be tested
  without hardware. Add a test for every report offset or protocol detail you touch, citing its
  source (for example, the DS4Windows file and line).
- Never flag output-report fields you don't intend to change; padlink shares the controller with
  the kernel driver and with games.
- `packaging/padlink-hide` runs as root: keep it tiny, validate every argument, and explain any
  change in the pull request. See [SECURITY.md](SECURITY.md).
- UI text: Sony's control names (Create, Options, Touch pad, Light bar…), sentence case, no jargon.

## Pull requests

- Target `develop` (or `main` only for `hotfix/*`).
- CI must pass (tests on Python 3.11–3.13, `shellcheck` on `install.sh`).
- Say what you tested it on: distribution, kernel, controller, USB or Bluetooth.
- Update [CHANGELOG.md](CHANGELOG.md) under **Unreleased**.

## Releasing (maintainers)

```bash
git switch develop && git switch -c release/0.2.0
# bump padlink/__init__.py + pyproject.toml, move "Unreleased" in CHANGELOG.md to 0.2.0
git commit -am "chore(release): 0.2.0"
git switch main && git merge --no-ff release/0.2.0 && git tag -a v0.2.0 -m "padlink 0.2.0"
git switch develop && git merge --no-ff release/0.2.0
git push origin main develop v0.2.0      # the tag triggers the release workflow
```
