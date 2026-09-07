# Prestage for SIM Finals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing `feat/prestage-submissions` branch usable for the 2026-09-19 SIM finals: 32 Sim-to-Real teams prebuilt into one encrypted vault, staged one team at a time on the 4 organiser Autoware PCs (seats A–D) and the rehearsal-table PC.

**Architecture:** The branch already implements download → containerised build (`SYMLINK_INSTALL=0`) → `install.tar.zst` in a gocryptfs vault → `stage_team.sh` / `unstage_team.sh`. This plan (1) rebases it onto `origin/main`, (2) adds the operational glue the SIM finals need (human-readable team labels in `teams.tsv`, image export/import so every PC has the identical `aichallenge-2025-dev` image ID, a repeatable real-image E2E script), and (3) rewrites the spec/README from "車両 PC" wording to the SIM-finals PC roster with measured numbers.

**Tech Stack:** bash (shfmt -i=4, shellcheck), python3 stdlib, gocryptfs 1.8, zstd, docker compose, colcon (inside `aichallenge-2025-dev`).

**Spec:** `docs/spec/prestaged-submissions.md` (on the branch). Background facts not in the repo: `~/.claude/projects/-home-taikitanaka-aic-aichallenge-racingkart/memory/project_sim_finals_prestage_context.md`.

## Global Constraints

- Conventional Commits; **no** `Co-Authored-By` / "Generated with" trailers.
- Shell: `set -euo pipefail`, shfmt `-i=4`, shellcheck clean (`pre-commit run --files ...`).
- Vault-mounting scripts keep the separated traps: `trap cleanup EXIT` + `trap 'exit 130' INT` + `trap 'exit 143' TERM`.
- Team IDs: `[A-Za-z0-9_-]+`. Convention for the SIM finals: `<class>-<qualifying rank, 2 digits>` (`general-03`, `student-12`). Seat (A–D / ROS_DOMAIN_ID) is never part of a team ID.
- `stage_team.sh` extracts only into `aichallenge/workspace/install`; `docker-entrypoint.sh` and `run_autoware.bash` stay unchanged.
- All work happens in the worktree `.claude/worktrees/prestage` on branch `feat/prestage-submissions`. Never touch the main checkout's working tree.
- Do not read or print `.env` contents. The worktree needs a copy of the main checkout's `.env` for Task 4; the user copies it (`cp .env .claude/worktrees/prestage/`).

---

### Task 1: Rebase onto origin/main and re-run the suite

**Files:** none new. Rebase touches `Makefile`, `aichallenge/build_autoware.bash`, `vehicle/download_submission.py`, `docs/README.md` if they conflict.

- [ ] **Step 1:** `git worktree add .claude/worktrees/prestage feat/prestage-submissions`; `cd` there.
- [ ] **Step 2:** `git rebase origin/main`; resolve conflicts keeping both upstream and prestage changes.
- [ ] **Step 3:** `git merge --no-commit --no-ff experiment && git merge --abort` — must be conflict-free (the finals PCs may run `experiment`).
- [ ] **Step 4:** `make prestage-test` → expect `ALL SUITES PASS`.
- [ ] **Step 5:** `pre-commit run --files $(git diff --name-only origin/main...HEAD)` → clean.
- [ ] **Step 6:** `DRY_RUN=1 SYMLINK_INSTALL=0 bash aichallenge/build_autoware.bash` prints `colcon build --allow-overriding gyro_odometer --cmake-args -DCMAKE_BUILD_TYPE=Release` (no `--symlink-install`).

---

### Task 2: Human-readable label column in `teams.tsv`

**Files:**
- Modify: `vehicle/prestage/prestage_all.sh` (the `while IFS=$'\t' read -r team_id user_id submission_id` loop)
- Modify: `vehicle/prestage/teams.tsv.example`
- Test: `vehicle/prestage/tests/prestage_test.sh`

**Interfaces:**
- Produces: TSV format `team_id<TAB>user_id[<TAB>submission_id[<TAB>label]]`. `label` is free text for humans (group_name / affiliation), ignored by the scripts, may contain spaces. `submission_id` may be empty while `label` is set (`t01<TAB>u1<TAB><TAB>Shibaura`).

- [ ] **Step 1: Write the failing test.** Append to `vehicle/prestage/tests/prestage_test.sh` before the final `[ "${fails}" -eq 0 ]` line:

```bash
# --- 4 列目 (label) があっても、submission_id が空でも受け付けること ---
mkdir -p "${WORK}/vault3" "${WORK}/mnt3"
printf 'general-01\tuser-a\t\tShibaura Univ\nstudent-02\tuser-b\tsub-b\tChiba Tech (student)\n' >"${WORK}/teams3.tsv"
gocryptfs -q -init -passfile "${WORK}/pw" "${WORK}/vault3" >/dev/null 2>&1
label_out=$("${PRESTAGE}" --vault "${WORK}/vault3" --teams "${WORK}/teams3.tsv" 2>&1)
label_rc=$?
echo "    ${label_out//$'\n'/$'\n    '}"
expect_eq "labelled tsv exits 0" "0" "${label_rc}"
gocryptfs -q -passfile "${WORK}/pw" "${WORK}/vault3" "${WORK}/mnt3"
expect_eq "general-01 ok (empty submission_id + label)" "ok" \
    "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt3/manifest.json" --team-id general-01 --field build_status)"
expect_eq "student-02 keeps its submission_id" "sub-b" \
    "$(python3 "${MANIFEST_PY}" get "${WORK}/mnt3/manifest.json" --team-id student-02 --field submission_id)"
fusermount -u "${WORK}/mnt3"
```

  Also extend the trap at the top of the file to unmount `${WORK}/mnt3`.

- [ ] **Step 2:** Run `bash vehicle/prestage/tests/prestage_test.sh`. Expected: the new `student-02 keeps its submission_id` check FAILs (with 3 fields, `read` stuffs `sub-b<TAB>Chiba Tech (student)` into `submission_id`).
- [ ] **Step 3:** In `prestage_all.sh` change the loop header to `while IFS=$'\t' read -r team_id user_id submission_id label || [ -n "${team_id}" ]; do` and add `: "${label:=}"` (unused, keeps shellcheck quiet with `# shellcheck disable=SC2034` if needed). Log the label in the `=== team ===` line: `log "=== ${team_id} (user ${user_id})${label:+ — ${label}} ==="`.
- [ ] **Step 4:** Run the test again → `ALL PASS`. Run `make prestage-test` → all suites pass.
- [ ] **Step 5:** Update `teams.tsv.example`:

```
# team_id<TAB>user_id[<TAB>submission_id[<TAB>label]]
# team_id: ボールト内のディレクトリ名。英数と - _ のみ。SIM決勝の規約は <class>-<予選順位2桁> (general-03 / student-12)。席 (A-D) は入れない
# user_id: aic-next の user id（管理画面 / DB から取得）
# submission_id: 省略（空欄）すると最新の提出を自動選択する
# label: 人が読むための自由記述（チーム名・所属）。スクリプトは無視する
general-01	00000000-0000-0000-0000-000000000001		Shibaura Univ
student-02	00000000-0000-0000-0000-000000000002	11111111-1111-1111-1111-111111111111	Chiba Tech
```

- [ ] **Step 6:** `pre-commit run --files vehicle/prestage/prestage_all.sh vehicle/prestage/tests/prestage_test.sh`; commit `feat(prestage): accept a label column in teams.tsv`.

---

### Task 3: Image export/import so every PC has the same image ID

**Files:**
- Create: `vehicle/prestage/image_transfer.sh`
- Test: `vehicle/prestage/tests/image_transfer_test.sh`
- Modify: `Makefile` (two targets + `.PHONY`), `vehicle/prestage/README.md`

**Interfaces:**
- `image_transfer.sh export <file.tar.zst>` → `docker save $PRESTAGE_IMAGE | zstd -T0 -o <file>`, then prints `image <tag> -> <id>` and the file size.
- `image_transfer.sh import <file.tar.zst>` → `zstd -dc <file> | docker load`, then prints the resulting local ID via `image_id` from `lib.sh`.
- Honours `PRESTAGE_DOCKER_CMD` (already used by `unstage_team.sh`) and `PRESTAGE_IMAGE` (from `lib.sh`).

- [ ] **Step 1: Write the failing test** `vehicle/prestage/tests/image_transfer_test.sh`:

```bash
#!/usr/bin/env bash
# image_transfer.sh: export は docker save の出力を zstd で固め、import は zstd 展開を docker load に流すこと。
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
XFER="${SCRIPT_DIR}/../image_transfer.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT INT TERM
fails=0
expect_eq() { if [ "$2" = "$3" ]; then echo "PASS: $1"; else echo "FAIL: $1 (expected '$2' got '$3')"; fails=$((fails + 1)); fi; }

# docker スタブ: save は固定バイト列を吐き、load は stdin を記録する
cat >"${WORK}/fake_docker.sh" <<'STUB'
#!/usr/bin/env bash
case "$1" in
save) printf 'IMAGE-BYTES-%s' "$2" ;;
load) cat >"${FAKE_LOADED}" ; echo "Loaded image: fake" ;;
image) echo "sha256:fakeid" ;;
*) exit 1 ;;
esac
STUB
chmod +x "${WORK}/fake_docker.sh"
export PRESTAGE_DOCKER_CMD="${WORK}/fake_docker.sh"
export PRESTAGE_IMAGE="aichallenge-2025-dev"
export FAKE_LOADED="${WORK}/loaded.bin"

out=$("${XFER}" export "${WORK}/img.tar.zst" 2>&1); rc=$?
expect_eq "export exits 0" "0" "${rc}"
expect_eq "export wrote zstd" "IMAGE-BYTES-aichallenge-2025-dev" "$(zstd -dc "${WORK}/img.tar.zst")"
expect_eq "export prints image id" "yes" "$(printf '%s' "${out}" | grep -q 'sha256:fakeid' && echo yes || echo no)"

out=$("${XFER}" import "${WORK}/img.tar.zst" 2>&1); rc=$?
expect_eq "import exits 0" "0" "${rc}"
expect_eq "import fed docker load" "IMAGE-BYTES-aichallenge-2025-dev" "$(cat "${FAKE_LOADED}")"

"${XFER}" import "${WORK}/missing.tar.zst" >/dev/null 2>&1; expect_eq "import rejects missing file" "1" "$?"
"${XFER}" bogus x >/dev/null 2>&1; expect_eq "unknown verb exits 2" "2" "$?"

[ "${fails}" -eq 0 ] && echo "ALL PASS" || echo "${fails} FAILURE(S)"
exit "${fails}"
```

- [ ] **Step 2:** Run it → FAIL (script missing).
- [ ] **Step 3:** Create `vehicle/prestage/image_transfer.sh`:

```bash
#!/usr/bin/env bash
# Move the aichallenge-2025-dev image between PCs without rebuilding it.
# stage_team.sh refuses a vault whose image ID differs from the local image, and
# `docker_build.sh dev` on each PC yields a different ID, so build once and ship it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"
DOCKER_CMD="${PRESTAGE_DOCKER_CMD:-docker}"

usage() {
    cat >&2 <<'EOF'
Usage: image_transfer.sh export <image.tar.zst>   # on the PC that built the image
       image_transfer.sh import <image.tar.zst>   # on every other PC
Environment: PRESTAGE_IMAGE (default aichallenge-2025-dev)
EOF
}

verb="${1-}"
file="${2-}"
[ -n "${verb}" ] && [ -n "${file}" ] || { usage; exit 2; }
require_tools zstd

case "${verb}" in
export)
    log "saving ${PRESTAGE_IMAGE} ($("${DOCKER_CMD}" image inspect --format '{{.Id}}' "${PRESTAGE_IMAGE}")) -> ${file}"
    "${DOCKER_CMD}" save "${PRESTAGE_IMAGE}" | zstd -T0 -q -f -o "${file}"
    log "wrote ${file} ($(du -h "${file}" | cut -f1))"
    ;;
import)
    [ -f "${file}" ] || die "file not found: ${file}"
    zstd -dc "${file}" | "${DOCKER_CMD}" load
    log "local ${PRESTAGE_IMAGE} is now $("${DOCKER_CMD}" image inspect --format '{{.Id}}' "${PRESTAGE_IMAGE}")"
    ;;
*)
    usage
    exit 2
    ;;
esac
```

  Note `image_id()` in `lib.sh` calls the real `docker`; this script calls `${DOCKER_CMD} image inspect` directly so the stub works. Make the file executable.

- [ ] **Step 4:** Run the test → `ALL PASS`. `make prestage-test` picks it up automatically (`*_test.sh` glob).
- [ ] **Step 5:** Makefile targets (add to `.PHONY` too):

```make
# Ship the dev image to the other PCs (stage_team.sh checks the image ID).
# Usage: make prestage-image-export IMAGE_TAR=/path/aichallenge-2025-dev.tar.zst
prestage-image-export:
	@[ -n "$(IMAGE_TAR)" ] || { echo "IMAGE_TAR=<file.tar.zst> is required"; exit 2; }
	vehicle/prestage/image_transfer.sh export $(IMAGE_TAR)

prestage-image-import:
	@[ -n "$(IMAGE_TAR)" ] || { echo "IMAGE_TAR=<file.tar.zst> is required"; exit 2; }
	vehicle/prestage/image_transfer.sh import $(IMAGE_TAR)
```

- [ ] **Step 6:** README「会場前」step 3 becomes: build once on the operator PC, `make prestage-image-export IMAGE_TAR=...`, carry the file with the vault, `make prestage-image-import IMAGE_TAR=...` on each Autoware PC and the rehearsal PC, then confirm `docker image inspect --format '{{.Id}}' aichallenge-2025-dev` matches everywhere.
- [ ] **Step 7:** pre-commit on the new/changed files; commit `feat(prestage): add image export/import for identical image IDs across PCs`.

---

### Task 4: Real-image E2E script and measurements

**Files:**
- Create: `vehicle/prestage/e2e_real_image.sh`
- Modify: `vehicle/prestage/README.md` (「検証」section), `Makefile` (`prestage-e2e` target)

**Interfaces:**
- `e2e_real_image.sh --submit <aichallenge_submit.tar.gz> [--keep-vault <dir>]`. Runs on a PC with docker, the `aichallenge-2025-dev` image and a valid `.env`. Uses a temporary vault + passfile (this is the organiser machine, so `PRESTAGE_PASSFILE` is acceptable). Exits non-zero on any failed check. Prints: build wall time, `install.tar.zst` size, node list.

- [ ] **Step 1:** Produce a submission tar to test with: `./create_submit_file.bash` in the worktree → `submit/aichallenge_submit.tar.gz` (the reference `aichallenge_submit` plays the role of a team).
- [ ] **Step 2:** Create `vehicle/prestage/e2e_real_image.sh`:

```bash
#!/usr/bin/env bash
# End-to-end check with the REAL image: prestage one local tar -> stage -> launch autoware -> unstage.
# Organiser machine only (docker, aichallenge-2025-dev, .env). Prints build time and archive size.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SUBMIT=""
KEEP_VAULT=""
TEAM="e2e-01"
while [ $# -gt 0 ]; do
    case "$1" in
    --submit) SUBMIT="${2-}"; shift 2 ;;
    --keep-vault) KEEP_VAULT="${2-}"; shift 2 ;;
    *) die "unknown argument: $1" ;;
    esac
done
[ -f "${SUBMIT}" ] || die "--submit <aichallenge_submit.tar.gz> is required"
require_tools gocryptfs fusermount zstd python3 docker

WORK="$(mktemp -d)"
cleanup() {
    (cd "${REPO_ROOT}" && docker compose down --remove-orphans >/dev/null 2>&1) || true
    [ -n "${KEEP_VAULT}" ] || rm -rf "${WORK}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

VAULT="${KEEP_VAULT:-${WORK}/vault}"
mkdir -p "${VAULT}"
printf 'e2e-passphrase\n' >"${WORK}/pw"
export PRESTAGE_PASSFILE="${WORK}/pw"
[ -f "${VAULT}/gocryptfs.conf" ] || gocryptfs -q -init -passfile "${WORK}/pw" "${VAULT}"

# Download stub: copy the local tar to --dest-file.
cat >"${WORK}/fake_download.sh" <<STUB
#!/usr/bin/env bash
set -eo pipefail
while [ \$# -gt 0 ]; do case "\$1" in --dest-file) dest="\$2"; shift 2 ;; *) shift ;; esac; done
mkdir -p "\$(dirname "\${dest}")" && cp "${SUBMIT}" "\${dest}"
STUB
chmod +x "${WORK}/fake_download.sh"
export PRESTAGE_DOWNLOAD_CMD="${WORK}/fake_download.sh"
printf '%s\tlocal\t\tE2E local tar\n' "${TEAM}" >"${WORK}/teams.tsv"

cd "${REPO_ROOT}"
[ -e aichallenge/workspace/install ] && die "aichallenge/workspace/install exists; run unstage_team.sh first"

log "=== prestage (real build) ==="
t0=$(date +%s)
"${SCRIPT_DIR}/prestage_all.sh" --vault "${VAULT}" --teams "${WORK}/teams.tsv" --force
t1=$(date +%s)
log "build+archive wall time: $((t1 - t0)) s"

mkdir -p "${WORK}/mnt"
gocryptfs -q -ro -passfile "${WORK}/pw" "${VAULT}" "${WORK}/mnt"
log "install.tar.zst size: $(du -h "${WORK}/mnt/team_${TEAM}/install.tar.zst" | cut -f1)"
fusermount -u "${WORK}/mnt"

log "=== stage ==="
"${SCRIPT_DIR}/stage_team.sh" --vault "${VAULT}" "${TEAM}"

log "=== launch autoware (RUN_MODE=awsim) ==="
make autoware-simulator
nodes=""
for _ in $(seq 1 24); do
    sleep 5
    nodes="$(CMD='ros2 node list' docker compose run -T --rm --no-deps autoware-command 2>/dev/null || true)"
    if printf '%s' "${nodes}" | grep -q '/simple_trajectory_generator' &&
        printf '%s' "${nodes}" | grep -q '/imu_gnss_poser'; then
        break
    fi
done
printf '%s\n' "${nodes}" | sed 's/^/    /'
printf '%s' "${nodes}" | grep -q '/simple_trajectory_generator' || die "participant nodes did not come up within 120 s"
log "participant nodes are up"

make down >/dev/null 2>&1 || true

log "=== unstage ==="
"${SCRIPT_DIR}/unstage_team.sh" --yes
log "E2E OK"
```

  Node names come from `aichallenge_submit_launch/launch/reference.launch.xml` (`imu_gnss_poser`, `simple_trajectory_generator`). Adjust only if the reference launch renames them.

- [ ] **Step 3:** Makefile: `prestage-e2e: ; vehicle/prestage/e2e_real_image.sh --submit $(SUBMIT)` with the same `[ -n "$(SUBMIT)" ]` guard pattern; add to `.PHONY`.
- [ ] **Step 4:** Run it for real: `make prestage-e2e SUBMIT=submit/aichallenge_submit.tar.gz`. Requirements: `.env` present in the worktree (user copies it), no other `autoware` container on the host (`docker ps`). Record: wall time, archive size, node list, and whether `make autoware-simulator` used the staged `install/` (check `output/latest/.../autoware.log` for `aichallenge_system.launch.xml` start and no "package not found").
- [ ] **Step 5:** If AWSIM is available (`aichallenge/simulator/AWSIM` in the main checkout), optionally run `make simulator` from the main checkout first and confirm in the autoware log that localization receives `/sensing/gnss/...`. This is a bonus check, not a gate.
- [ ] **Step 6:** pre-commit; commit `test(prestage): add real-image end-to-end script`. Do not commit `submit/*.tar.gz` (check `.gitignore`; add `submit/` if missing).

---

### Task 5: Spec and README for the SIM finals

**Files:**
- Modify: `docs/spec/prestaged-submissions.md`, `vehicle/prestage/README.md`

- [ ] **Step 1:** Spec: rename the actor/host wording. "車両 PC" → "運営 PC（SIM 決勝: 席 A〜D の Autoware PC + リハーサル卓 PC / 実機決勝: 車両 PC）". Add a section「SIM 決勝での適用」with: PC roster (AWSIM PC 1, Autoware PC 4, rehearsal PC), 32 teams = general 16 + student 16 over 8 matches, per-slot flow (T-20 rehearsal PC stage → T-5 rehearsal unstage; T-0 stage PC stage → T+18 unstage with `KEEP_OUTPUT`), seat = `ROS_DOMAIN_ID` fixed per PC in `.env` (A=1 … D=4), team ID convention, image distribution requirement (Task 3), and the freeze caveat: 事前ビルドは「提出締切後」にしか意味を持たない。締切をリハーサル確認時刻のままにする場合、直前更新チームは運営 PC で `make prestage-build TEAMS=... --team <id> --force` 相当の個別再 prestage（要ネット + ビルド時間）で対応する。
- [ ] **Step 2:** Replace the「ディスク見積り」見込み値 with the Task 4 measurements (mark them 実測 with date and machine), and keep the caveat that MPC `.venv` dominates.
- [ ] **Step 3:** 「検証方法」: item 3 is now covered by `make prestage-e2e SUBMIT=...`; describe what it checks.
- [ ] **Step 4:** README: add「SIM 決勝 当日の流れ（1 試合）」as a numbered checklist per PC role, the image export/import step, and「初期化」(`make prestage-unstage` once on each PC before the day so `install/` is absent). Mention that a team may edit param YAML under `aichallenge/workspace/install/` during its slot (plaintext copies) but cannot rebuild C++.
- [ ] **Step 5:** `pre-commit run --files docs/spec/prestaged-submissions.md vehicle/prestage/README.md`; commit `docs(prestage): describe the SIM-finals PC roster, slot flow and measured sizes`.

---

### Follow-ups outside this repo (not tasks here)

- Participant-facing doc `aichallenge-documentation-racingkart/docs/competition/sim-finals.ja.md` still says teams download+build in the 10-minute setup; update once the flow is agreed.
- Decide the code freeze time for the SIM finals (proposal: 9/18 18:00) — owner: the user.
- Generate `teams.tsv` from the finals standings (`/api/rankings?course=general|student&limit=16` + user IDs from the admin DB).
