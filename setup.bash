#!/usr/bin/env bash
# shellcheck disable=SC2015  # A && B || C: B is always a simple assignment, so C never runs on A-success.
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-${0-}}"
if [ -n "$SCRIPT_PATH" ] && [ "$SCRIPT_PATH" != "bash" ] && [ "$SCRIPT_PATH" != "-bash" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
else
    # When executed via: curl .../setup.bash | bash
    # there is no script file path. Use the current working directory.
    SCRIPT_DIR="$PWD"
fi

is_repo_root_dir() {
    local d="$1"
    [ -f "${d}/docker-compose.yml" ] && [ -f "${d}/docker_build.sh" ] && [ -f "${d}/setup.bash" ]
}

REPO_ROOT=""
if is_repo_root_dir "$SCRIPT_DIR"; then
    REPO_ROOT="$SCRIPT_DIR"
elif is_repo_root_dir "$PWD"; then
    REPO_ROOT="$PWD"
fi

# Only cd when we are sure we are in the repo.
# This allows running the script via: curl .../setup.bash | bash
if [ -n "$REPO_ROOT" ]; then
    cd "$REPO_ROOT"
fi

OK="✅"
WARN="⚠️"
FAIL="❌"
INFO="ℹ️"

SETUP_ASSUME_YES="${AIC_ASSUME_YES:-0}"
SETUP_TEST_DIR=""
SETUP_TEST_KEEP_DIR=0

log() {
    echo "[setup] $*"
}

warn() {
    echo "[setup][WARN] $*" >&2
}

cleanup_test_dir() {
    local rc=$?
    local d="${SETUP_TEST_DIR-}"
    if [ -z "${d}" ]; then
        return 0
    fi
    if [ "${SETUP_TEST_KEEP_DIR:-0}" = "1" ]; then
        return 0
    fi
    if [ "${rc}" -ne 0 ]; then
        warn "${WARN} Keeping temp dir due to non-zero exit code (${rc}): ${d}"
        warn "${INFO} To delete manually: rm -rf ${d}"
        return 0
    fi
    case "${d}" in
    /tmp/aichallenge-racingkart-test.*)
        log "${INFO} Removing temp dir: ${d}"
        rm -rf "${d}" >/dev/null 2>&1 || true
        ;;
    *)
        warn "${WARN} Refusing to delete unexpected dir: ${d}"
        ;;
    esac
}

normalize_branch_ref() {
    local b="${1-}"
    case "${b}" in
    origin/*)
        echo "${b#origin/}"
        ;;
    *)
        echo "${b}"
        ;;
    esac
}

list_remote_branches() {
    local repo_url="${1-}"
    if [ -z "${repo_url}" ]; then
        return 1
    fi
    require_cmd git || return 1
    git ls-remote --heads "${repo_url}" 2>/dev/null | awk '{print $2}' | sed 's#^refs/heads/##' | sort -u
}

select_branch_from_remote() {
    local repo_url="${1-}"
    local default_branch="${2:-main}"

    if [ "${SETUP_ASSUME_YES}" = "1" ]; then
        echo "${default_branch}"
        return 0
    fi
    if ! [ -r /dev/tty ]; then
        echo "${default_branch}"
        return 0
    fi

    local branches=""
    branches="$(list_remote_branches "${repo_url}" || true)"
    if [ -z "${branches}" ]; then
        echo "${default_branch}"
        return 0
    fi

    printf "[setup] Available branches (remote):\n" >/dev/tty
    local i=0
    while IFS= read -r b; do
        [ -n "${b}" ] || continue
        i=$((i + 1))
        if [ "${b}" = "${default_branch}" ]; then
            printf "[setup]  %2d) %s (default)\n" "${i}" "${b}" >/dev/tty
        else
            printf "[setup]  %2d) %s\n" "${i}" "${b}" >/dev/tty
        fi
        if [ "${i}" -ge 50 ]; then
            printf "[setup]  ... (showing first 50)\n" >/dev/tty
            break
        fi
    done <<EOF
${branches}
EOF

    local ans=""
    while true; do
        printf "[setup] Select branch [default: %s]: " "${default_branch}" >/dev/tty
        if ! IFS= read -r ans </dev/tty; then
            echo "${default_branch}"
            return 0
        fi
        ans="${ans//[[:space:]]/}"
        if [ -z "${ans}" ]; then
            echo "${default_branch}"
            return 0
        fi
        if [[ ${ans} =~ ^[0-9]+$ ]]; then
            local n="${ans}"
            local chosen=""
            chosen="$(printf "%s\n" "${branches}" | awk -v n="${n}" 'NF{c++} c==n{print; exit}')"
            if [ -n "${chosen}" ]; then
                echo "${chosen}"
                return 0
            fi
            printf "[setup] Invalid selection: %s\n" "${ans}" >/dev/tty
            continue
        fi

        if printf "%s\n" "${branches}" | grep -Fxq "${ans}"; then
            echo "${ans}"
            return 0
        fi
        printf "[setup] Unknown branch: %s\n" "${ans}" >/dev/tty
    done
}

on_interrupt() {
    echo ""
    warn "${WARN} Interrupted (Ctrl+C)"
    exit 130
}

trap on_interrupt INT

require_tty_or_yes() {
    if [ "${SETUP_ASSUME_YES}" = "1" ]; then
        return 0
    fi
    if [ -r /dev/tty ]; then
        return 0
    fi
    warn "${FAIL} No TTY available for confirmation prompts. Re-run with --yes."
    return 2
}

confirm_step() {
    local prompt="$1"

    if [ "${SETUP_ASSUME_YES}" = "1" ]; then
        log "${INFO} ${prompt} (auto-yes)"
        return 0
    fi

    local ans=""
    while true; do
        printf "[setup] %s [y/N]: " "${prompt}" >/dev/tty
        if ! IFS= read -r ans </dev/tty; then
            return 1
        fi
        case "${ans}" in
        y | Y)
            return 0
            ;;
        n | N | "")
            return 1
            ;;
        *)
            printf "[setup] Please answer 'y' or 'n'.\n" >/dev/tty
            ;;
        esac
    done
}

run_step() {
    local label="$1"
    shift
    if confirm_step "${label}"; then
        local had_errexit=0
        case $- in *e*) had_errexit=1 ;; esac
        set +e
        "$@"
        local rc=$?
        if [ "${had_errexit}" -eq 1 ]; then
            set -e
        fi
        return "${rc}"
    else
        log "${INFO} Skipped: ${label}"
    fi
}

run_step_if() {
    local enabled="${1:-0}"
    local label="${2-}"
    shift 2

    if [ "${enabled}" != "1" ]; then
        log "${INFO} Skipped: ${label}"
        return 0
    fi

    log "${INFO} Running: ${label}"
    local had_errexit=0
    case $- in *e*) had_errexit=1 ;; esac
    set +e
    "$@"
    local rc=$?
    if [ "${had_errexit}" -eq 1 ]; then
        set -e
    fi
    return "${rc}"
}

usage() {
    cat <<'EOF'
Usage:
  ./setup.bash                # run doctor (environment check + next steps)
  curl -fsSL https://raw.githubusercontent.com/AutomotiveAIChallenge/aichallenge-racingkart/main/setup.bash | bash
                            # bootstrap a fresh Ubuntu host (installs Docker if missing)
  ./setup.bash doctor         # environment check + next steps summary
  ./setup.bash bootstrap      # install Docker if missing + clone repo + run setup (for fresh PCs)
  ./setup.bash test [BRANCH]  # bootstrap into /tmp (kept by default; default: origin/test)
  ./setup.bash pull image     # docker pull Autoware base image (recommended)
  ./setup.bash download awsim # download & extract AWSIM.zip (repo-local)
  ./setup.bash env            # create .env from .env.example (safe, repo-local)
  ./setup.bash bootstrap --yes
                            # non-interactive bootstrap (auto-yes)
  ./setup.bash bootstrap --temp-dir [--keep-dir]
                            # clone into a temp dir (kept by default)

Notes:
  - By design, this script DOES NOT install system packages by default.
  - Some steps require re-login or reboot (Docker group, NVIDIA driver).
EOF
}

os_id() {
    # shellcheck disable=SC1091
    . /etc/os-release 2>/dev/null || return 1
    echo "${ID:-unknown}:${VERSION_ID:-unknown}"
}

cmd_exists() {
    command -v "$1" >/dev/null 2>&1
}

sudo_refresh() {
    require_cmd sudo
    sudo -v
}

docker_as_user_ok() {
    cmd_exists docker && docker info >/dev/null 2>&1
}

docker_as_sudo_ok() {
    cmd_exists sudo && cmd_exists docker && sudo -n docker info >/dev/null 2>&1
}

docker_run() {
    if docker_as_user_ok; then
        docker "$@"
        return 0
    fi
    if cmd_exists sudo && cmd_exists docker; then
        sudo docker "$@"
        return 0
    fi
    warn "${FAIL} docker not available"
    return 1
}

docker_run_no_prompt() {
    if docker_as_user_ok; then
        docker "$@"
        return 0
    fi
    if docker_as_sudo_ok; then
        sudo -n docker "$@"
        return 0
    fi
    return 1
}

docker_compose_run() {
    if docker_as_user_ok; then
        docker compose "$@"
        return 0
    fi
    if cmd_exists sudo && cmd_exists docker; then
        sudo docker compose "$@"
        return 0
    fi
    warn "${FAIL} docker not available"
    return 1
}

docker_compose_run_no_prompt() {
    if docker_as_user_ok; then
        docker compose "$@"
        return 0
    fi
    if docker_as_sudo_ok; then
        sudo -n docker compose "$@"
        return 0
    fi
    return 1
}

require_cmd() {
    local c="$1"
    if ! cmd_exists "$c"; then
        warn "${FAIL} Required command not found: ${c}"
        return 1
    fi
}

in_group() {
    local group="$1"
    id -nG "${USER-}" 2>/dev/null | tr ' ' '\n' | grep -qx "$group"
}

### NOTE:
# The following used to include a `show` command that printed manual setup steps.
# Keep the CLI surface minimal; prefer `bootstrap` / `doctor` / `download` / `pull`.

install_base_packages() {
    sudo_refresh
    sudo apt-get update
    sudo apt-get install -y \
        ca-certificates \
        curl \
        git \
        gnupg \
        make \
        python3 \
        python3-pip
}

install_rocker() {
    if cmd_exists rocker; then
        log "${OK} rocker already installed"
        return 0
    fi
    pip3 install --user rocker
    # Ensure ~/.local/bin is on PATH
    if ! echo "$PATH" | tr ':' '\n' | grep -qx "$HOME/.local/bin"; then
        # shellcheck disable=SC2016
        echo 'export PATH="$HOME/.local/bin:$PATH"' >>~/.bashrc
        export PATH="$HOME/.local/bin:$PATH"
        log "${INFO} Added ~/.local/bin to PATH in ~/.bashrc"
    fi
    log "${OK} Installed rocker"
}

install_docker_if_missing() {
    if cmd_exists docker && docker --version >/dev/null 2>&1; then
        log "${OK} Docker already installed"
        return 0
    fi

    log "${INFO} Installing Docker + docker compose plugin"
    sudo_refresh

    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg

    sudo install -m 0755 -d /etc/apt/keyrings
    if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        sudo chmod a+r /etc/apt/keyrings/docker.gpg
    fi

    local codename
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable" |
        sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo systemctl enable --now docker || true

    log "${OK} Installed Docker"
}

ensure_docker_group() {
    if in_group docker; then
        log "${OK} user is in docker group"
        return 0
    fi

    log "${INFO} Adding ${USER-} to docker group"
    sudo_refresh
    sudo usermod -aG docker "${USER-}"
    warn "${WARN} Docker group takes effect after re-login (or reboot)."
}

clone_or_update_repo() {
    local repo_url="$1"
    local branch_ref="$2"
    local dest_dir="$3"
    local branch
    branch="$(normalize_branch_ref "${branch_ref}")"

    if [ -d "${dest_dir}/.git" ]; then
        log "${INFO} Updating repo: ${dest_dir}"
        git -C "${dest_dir}" fetch --prune origin || git -C "${dest_dir}" fetch --prune
        # Make "origin/test" input convenient by accepting it as "test".
        if git -C "${dest_dir}" show-ref --verify --quiet "refs/heads/${branch}"; then
            git -C "${dest_dir}" checkout "${branch}" >/dev/null 2>&1
        elif git -C "${dest_dir}" show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
            git -C "${dest_dir}" checkout -B "${branch}" "origin/${branch}" >/dev/null 2>&1
            git -C "${dest_dir}" branch --set-upstream-to="origin/${branch}" "${branch}" >/dev/null 2>&1 || true
        else
            warn "${FAIL} Branch not found: ${branch_ref} (normalized: ${branch})"
            return 1
        fi
        git -C "${dest_dir}" pull --ff-only origin "${branch}"
        return 0
    fi

    if [ -e "${dest_dir}" ]; then
        if [ -d "${dest_dir}" ] && [ -z "$(ls -A "${dest_dir}" 2>/dev/null || true)" ]; then
            log "${INFO} Destination exists but is empty; cloning into it: ${dest_dir}"
            git clone --branch "${branch}" "${repo_url}" "${dest_dir}"
            return 0
        fi
        warn "${FAIL} Destination exists but is not a git repo: ${dest_dir}"
        return 1
    fi

    log "${INFO} Cloning repo: ${repo_url} -> ${dest_dir}"
    git clone --branch "${branch}" "${repo_url}" "${dest_dir}"
}

bootstrap_repo_targets() {
    local repo_dir="$1"
    local domain_id="${2:-1}"
    local do_make_autoware_build="${3:-0}"
    local do_make_dev="${4:-0}"

    require_cmd make || return 1

    local use_sudo=0
    if ! docker_as_user_ok && cmd_exists sudo && cmd_exists docker; then
        use_sudo=1
        sudo_refresh
    fi

    if [ "$use_sudo" -eq 1 ]; then
        warn "${WARN} docker daemon not reachable as user yet; using sudo docker for post-setup steps"
    fi

    if [ "${do_make_autoware_build}" = "1" ]; then
        if [ "$use_sudo" -eq 1 ]; then
            (cd "${repo_dir}" && make autoware-build) || {
                warn "${FAIL} make autoware-build failed"
                return 0
            }
        else
            (cd "${repo_dir}" && make autoware-build) || {
                warn "${FAIL} make autoware-build failed"
                return 0
            }
        fi

        {
            local build_cid=""
            build_cid="$(
                cd "${repo_dir}" 2>/dev/null || exit 0
                docker_compose_run ps -q --all autoware-build 2>/dev/null || true
            )"
            if [ -n "${build_cid}" ]; then
                docker_run logs -f "${build_cid}" || true
                local build_rc=""
                build_rc="$(docker_run wait "${build_cid}" 2>/dev/null || true)"
                if [ -n "${build_rc}" ] && [ "${build_rc}" -ne 0 ]; then
                    warn "${FAIL} autoware-build failed (exit=${build_rc})"
                else
                    log "${OK} autoware-build finished"
                fi
            else
                warn "${WARN} Could not resolve autoware-build container id (skip wait/log follow)"
            fi
        }
    fi

    if [ "${do_make_dev}" = "1" ]; then
        if [ "$use_sudo" -eq 1 ]; then
            (cd "${repo_dir}" && make dev DOMAIN_ID="${domain_id}") || warn "${WARN} make dev failed"
        else
            (cd "${repo_dir}" && make dev DOMAIN_ID="${domain_id}") || warn "${WARN} make dev failed"
        fi
    fi
}

bootstrap() {
    local repo_url_default="https://github.com/AutomotiveAIChallenge/aichallenge-racingkart.git"
    local repo_url="${AIC_REPO_URL:-$repo_url_default}"
    local repo_url_explicit=0
    if [ -n "${AIC_REPO_URL-}" ]; then
        repo_url_explicit=1
    fi
    local branch="${AIC_BRANCH:-main}"
    local branch_explicit=0
    if [ -n "${AIC_BRANCH-}" ]; then
        branch_explicit=1
    fi
    local dest_dir="${AIC_DIR:-$HOME/aichallenge-racingkart}"
    local skip_pull_image=0
    local skip_awsim=0
    local skip_build=0
    local skip_make=0
    local use_temp_dir=0
    local keep_dir=0
    SETUP_ASSUME_YES="${AIC_ASSUME_YES:-0}"
    local owner_user owner_group
    owner_user="$(id -un)"
    owner_group="$(id -gn)"

    while [ $# -gt 0 ]; do
        case "${1}" in
        --repo)
            repo_url="${2-}"
            repo_url_explicit=1
            shift 2
            ;;
        --branch)
            branch="${2-}"
            branch_explicit=1
            shift 2
            ;;
        --dir)
            dest_dir="${2-}"
            shift 2
            ;;
        --temp-dir)
            use_temp_dir=1
            shift
            ;;
        --keep-dir)
            keep_dir=1
            shift
            ;;
        --skip-pull-image)
            skip_pull_image=1
            shift
            ;;
        --skip-awsim)
            skip_awsim=1
            shift
            ;;
        --skip-build)
            skip_build=1
            shift
            ;;
        --skip-make)
            skip_make=1
            shift
            ;;
        --yes | -y)
            SETUP_ASSUME_YES=1
            shift
            ;;
        -h | --help)
            cat <<'EOF'
Usage:
  ./setup.bash bootstrap [options]

Options:
  --repo URL            Repo URL (default: https://github.com/AutomotiveAIChallenge/aichallenge-racingkart.git)
  --branch NAME         Git branch (default: main)
  --dir PATH            Clone destination (default: ~/aichallenge-racingkart)
  --temp-dir            Use a temporary directory for --dir (kept by default)
  --keep-dir            Keep --temp-dir directory (default)
  --skip-pull-image     Skip pulling Autoware base image
  --skip-awsim          Skip downloading AWSIM.zip
  --skip-build          Skip ./docker_build.sh dev
  --skip-make           Skip make autoware-build/dev
  --yes, -y             Auto-yes for all steps (non-interactive)

Environment:
  AIC_REPO_URL, AIC_BRANCH, AIC_DIR  Same as options.
  AWSIM_ZIP_URL, AUTOWARE_BASE_IMAGE Passed to repo setup actions.
EOF
            return 0
            ;;
        *)
            warn "Unknown option: ${1}"
            return 2
            ;;
        esac
    done

    if [ "${repo_url_explicit}" -ne 1 ] && cmd_exists git && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        local origin_url=""
        origin_url="$(git remote get-url origin 2>/dev/null || true)"
        if [ -n "${origin_url}" ]; then
            repo_url="${origin_url}"
        fi
    fi

    if [ "${use_temp_dir}" -eq 1 ]; then
        # Keep temp dirs by default so users can inspect logs/workspace after bootstrap.
        keep_dir=1
        dest_dir="$(mktemp -d /tmp/aichallenge-racingkart-test.XXXXXX)"
        SETUP_TEST_DIR="${dest_dir}"
        SETUP_TEST_KEEP_DIR="${keep_dir}"
        trap cleanup_test_dir EXIT
        log "${INFO} Using temp dir: ${dest_dir}"
    fi

    log "${INFO} Bootstrap mode (fresh host)"
    local os
    os="$(os_id || true)"
    if [ -n "$os" ] && [ "$os" != "ubuntu:22.04" ]; then
        warn "${WARN} Recommended OS is Ubuntu 22.04 (current: ${os})"
    fi

    require_tty_or_yes

    # If branch is not explicitly set, allow picking from remote branches (default: main).
    if [ "${branch_explicit}" -ne 1 ]; then
        branch="$(select_branch_from_remote "${repo_url}" "main")"
    fi

    local do_install_base=0
    local do_install_docker=0
    local do_install_rocker=0
    local do_docker_group=0
    local do_clone_repo=0
    local do_repo_doctor=0
    local do_pull_image=0
    local do_download_awsim=0
    local do_build_dev_image=0
    local do_make_autoware_build=0
    local do_make_dev=0

    local _n=0
    log_step() {
        _n=$((_n + 1))
        log "$(printf '%3d) %s' "$_n" "$1")"
    }
    _skip_note() { [ "${1:-0}" -ne 1 ] && echo "(requires repo)" || echo "(SKIP: $2)"; }

    log "${INFO} Planned steps (answer y/N for each, then execution starts):"
    log_step "Install base packages (apt)"
    log_step "Install Docker (if missing)"
    log_step "Install rocker (pip)"
    log_step "Add user to docker group (recommended)"
    log_step "Clone/update repository (branch=${branch}) -> ${dest_dir}"
    log_step "Repo doctor: ./setup.bash doctor (requires repo)"
    log_step "Create .env (GPU/CPU auto-detect)"
    log_step "Pull Autoware base image $(_skip_note "$skip_pull_image" "--skip-pull-image")"
    log_step "Download AWSIM.zip and extract $(_skip_note "$skip_awsim" "--skip-awsim")"
    log_step "Build dev image: ./docker_build.sh dev $(_skip_note "$skip_build" "--skip-build")"
    log_step "make autoware-build $(_skip_note "$skip_make" "--skip-make")"
    log_step "make dev DOMAIN_ID=${DOMAIN_ID:-1} $(_skip_note "$skip_make" "--skip-make")"

    confirm_step "Install base packages (apt)" && do_install_base=1 || true
    confirm_step "Install Docker (if missing)" && do_install_docker=1 || true
    confirm_step "Install rocker (pip)" && do_install_rocker=1 || true
    confirm_step "Add user to docker group (recommended)" && do_docker_group=1 || true

    local repo_exists_now=0
    is_repo_root_dir "${dest_dir}" && repo_exists_now=1 || true
    confirm_step "Clone/update repository (branch=${branch}) -> ${dest_dir}" && do_clone_repo=1 || true

    if [ "${repo_exists_now}" -eq 1 ] || [ "${do_clone_repo}" -eq 1 ]; then
        confirm_step "Run repo doctor: ./setup.bash doctor" && do_repo_doctor=1 || true
        [ "$skip_pull_image" -ne 1 ] && confirm_step "Pull Autoware base image" && do_pull_image=1 || true
        [ "$skip_awsim" -ne 1 ] && confirm_step "Download AWSIM.zip and extract" && do_download_awsim=1 || true
        [ "$skip_build" -ne 1 ] && confirm_step "Build dev image: ./docker_build.sh dev" && do_build_dev_image=1 || true
        if [ "$skip_make" -ne 1 ]; then
            confirm_step "Run make autoware-build (this can take a while)" && do_make_autoware_build=1 || true
            confirm_step "Run make dev DOMAIN_ID=${DOMAIN_ID:-1}" && do_make_dev=1 || true
        fi
    else
        log "${INFO} Repo steps skipped (repo not selected / not present)"
        skip_pull_image=1
        skip_awsim=1
        skip_build=1
        skip_make=1
    fi

    log "${INFO} Starting execution..."

    run_step_if "${do_install_base}" "Install base packages (apt)" install_base_packages
    run_step_if "${do_install_docker}" "Install Docker (if missing)" install_docker_if_missing
    run_step_if "${do_install_rocker}" "Install rocker (pip)" install_rocker
    run_step_if "${do_docker_group}" "Add user to docker group (recommended)" ensure_docker_group

    # Best-effort verification (avoid hard-fail on network issues)
    if cmd_exists docker; then
        if docker_as_user_ok; then
            log "${OK} docker daemon reachable (user)"
        else
            warn "${WARN} docker daemon not reachable as user yet (re-login may be required). Using sudo docker for now."
            sudo docker info >/dev/null 2>&1 || warn "${WARN} sudo docker info failed (check docker service)"
        fi
    fi

    if ! run_step_if "${do_clone_repo}" "Clone/update repository: ${dest_dir}" clone_or_update_repo "$repo_url" "$branch" "$dest_dir"; then
        return 1
    fi

    if ! is_repo_root_dir "${dest_dir}"; then
        warn "${WARN} Repo not found at: ${dest_dir} (skipping repo steps)"
        skip_pull_image=1
        skip_awsim=1
        skip_build=1
        skip_make=1
    fi

    if is_repo_root_dir "${dest_dir}"; then
        run_step_if "${do_repo_doctor}" "Run repo doctor: ./setup.bash doctor" bash "${dest_dir}/setup.bash" doctor || true
        # Create .env with GPU/CPU selection
        (cd "${dest_dir}" && AIC_ASSUME_YES="${SETUP_ASSUME_YES}" bash ./setup.bash env) || true
    fi

    if [ "$skip_pull_image" -ne 1 ]; then
        run_step_if "${do_pull_image}" "Pull Autoware base image" bash "${dest_dir}/setup.bash" pull image || true
    fi
    if [ "$skip_awsim" -ne 1 ]; then
        run_step_if "${do_download_awsim}" "Download AWSIM.zip and extract" bash "${dest_dir}/setup.bash" download awsim || true
    fi
    if [ "$skip_build" -ne 1 ]; then
        if [ -x "${dest_dir}/docker_build.sh" ]; then
            if [ "${do_build_dev_image}" -eq 1 ]; then
                if docker_as_user_ok; then
                    (cd "${dest_dir}" && bash ./docker_build.sh dev) || true
                else
                    warn "${WARN} docker not usable as user yet; building with sudo docker (will chown logs back to you)"
                    (cd "${dest_dir}" && sudo bash ./docker_build.sh dev) || true
                    # Fix ownership if the script had to run under sudo.
                    (cd "${dest_dir}" && sudo chown -R "${owner_user}:${owner_group}" output/docker 2>/dev/null) || true
                fi
            fi
        else
            warn "${WARN} docker_build.sh not found/executable in ${dest_dir}"
        fi
    fi

    if [ "$skip_make" -ne 1 ]; then
        bootstrap_repo_targets "${dest_dir}" "${DOMAIN_ID:-1}" "${do_make_autoware_build}" "${do_make_dev}" || true
    fi

    cat <<EOF

${OK} Bootstrap finished.

Repo dir:
  cd "${dest_dir}"

Common commands:
  make autoware-build
  make dev DOMAIN_ID=1
  make down_all   # stop/remove all docker containers (sudo)
EOF
}

pull_autoware_image() {
    local image="${AUTOWARE_BASE_IMAGE:-ghcr.io/automotiveaichallenge/autoware-universe:humble-latest}"
    local attempts=5

    while [ $# -gt 0 ]; do
        case "${1}" in
        --image)
            image="${2-}"
            shift 2
            ;;
        -h | --help)
            cat <<'EOF'
Usage:
  ./setup.bash pull image [--image IMAGE]

Environment:
  AUTOWARE_BASE_IMAGE  Override default base image.
EOF
            return 0
            ;;
        *)
            warn "Unknown option: ${1}"
            return 2
            ;;
        esac
    done

    require_cmd docker || {
        warn "${INFO} Docker not found. Run: ./setup.bash bootstrap"
        return 1
    }

    log "${INFO} Pulling base image: ${image}"
    local i
    for ((i = 1; i <= attempts; i++)); do
        if docker_run pull "$image"; then
            log "${OK} Pulled: ${image}"
            return 0
        fi
        warn "${WARN} docker pull failed (attempt ${i}/${attempts})"
        sleep $((i * 2))
    done

    warn "${FAIL} Failed to pull image after ${attempts} attempts: ${image}"
    warn "${INFO} If ghcr requires auth, run: docker login ghcr.io"
    return 1
}

download_awsim() {
    local default_url='https://tier4inc-my.sharepoint.com/:u:/g/personal/taiki_tanaka_tier4_jp/IQAtXfOtrp1LQZxghshalXVhAQ3JYJLyyZaJsOH-npFDoUY?e=lztz8K'
    local url="${AWSIM_ZIP_URL:-$default_url}"

    local force=0
    local keep_zip=0

    while [ $# -gt 0 ]; do
        case "${1}" in
        --url)
            url="${2-}"
            shift 2
            ;;
        --force)
            force=1
            shift
            ;;
        --keep-zip)
            keep_zip=1
            shift
            ;;
        -h | --help)
            cat <<'EOF'
Usage:
  ./setup.bash download awsim [--url URL] [--force] [--keep-zip]

Environment:
  AWSIM_ZIP_URL  Override the default AWSIM.zip share link.
EOF
            return 0
            ;;
        *)
            warn "Unknown option: ${1}"
            return 2
            ;;
        esac
    done

    require_cmd curl || return 1
    require_cmd python3 || {
        warn "${INFO} Install python3: sudo apt update && sudo apt install -y python3"
        return 1
    }

    local dest_dir="./aichallenge/simulator"
    local awsim_bin="${dest_dir}/AWSIM/AWSIM.x86_64"
    local zip_path="${dest_dir}/AWSIM.zip"

    mkdir -p "$dest_dir"

    if [ -x "$awsim_bin" ] && [ "$force" -ne 1 ]; then
        log "${OK} AWSIM already present: ${awsim_bin}"
        log "${INFO} Re-download: ./setup.bash download awsim --force"
        return 0
    fi

    log "${INFO} Downloading AWSIM.zip..."
    log "${INFO} URL: ${url}"
    log "${INFO} Destination: ${zip_path}"

    local download_url="$url"
    if [[ $download_url != *"download=1"* ]]; then
        if [[ $download_url == *"?"* ]]; then
            download_url="${download_url}&download=1"
        else
            download_url="${download_url}?download=1"
        fi
    fi

    local cookie
    cookie="$(mktemp /tmp/awsim-cookie.XXXXXX)"

    # SharePoint/OneDrive may require cookies across redirects; use a cookie jar.
    # Support resume (-C -) since the file is large.
    if [ -f "$zip_path" ]; then
        log "${INFO} Resuming download (if possible)..."
        curl --fail --location --retry 5 --retry-delay 2 --connect-timeout 20 \
            --progress-bar -C - \
            -c "$cookie" -b "$cookie" \
            "$download_url" -o "$zip_path"
    else
        curl --fail --location --retry 5 --retry-delay 2 --connect-timeout 20 \
            --progress-bar \
            -c "$cookie" -b "$cookie" \
            "$download_url" -o "$zip_path"
    fi
    rm -f "$cookie" || true

    ZIP_PATH="$zip_path" python3 - <<'PY'
import os
import sys
import zipfile

p = os.environ["ZIP_PATH"]
if not zipfile.is_zipfile(p):
    print(f"[setup][WARN] Downloaded file is not a zip: {p}", file=sys.stderr)
    sys.exit(2)
PY

    log "${INFO} Extracting AWSIM.zip to ${dest_dir}..."
    ZIP_PATH="$zip_path" DEST_DIR="$dest_dir" python3 - <<'PY'
import os
import zipfile

zip_path = os.environ["ZIP_PATH"]
dest_dir = os.environ["DEST_DIR"]
with zipfile.ZipFile(zip_path) as zf:
    zf.extractall(dest_dir)
PY

    if [ -f "$awsim_bin" ]; then
        chmod +x "$awsim_bin" || true
        log "${OK} AWSIM extracted: ${awsim_bin}"
    else
        warn "${WARN} AWSIM extracted but binary not found at expected path: ${awsim_bin}"
        warn "${INFO} Inspect: ls -la ${dest_dir}"
    fi

    if [ "$keep_zip" -ne 1 ]; then
        rm -f "$zip_path" || true
        log "${INFO} Removed zip: ${zip_path}"
    fi
}

ensure_env() {
    if [ -f .env ]; then
        if ! confirm_step ".env already exists. Replace with fresh .env.example?"; then
            log "${OK} Keeping existing .env"
            return 0
        fi
        rm -f .env
    fi
    if [ ! -f .env.example ]; then
        warn "${FAIL} .env.example not found"
        return 1
    fi
    cp .env.example .env

    if [ -e /dev/nvidia0 ]; then
        sed -i 's/^#\s*COMPOSE_FILE=/COMPOSE_FILE=/' .env
        log "${OK} .env created (GPU)"
    else
        log "${OK} .env created (CPU)"
    fi
}

doctor() {
    local failed=0
    # _chk LEVEL "message" ["hint"] — print result, set failed=1 on FAIL
    _chk() {
        local icon msg="$2" hint="${3-}"
        case "$1" in OK) icon="$OK" ;; WARN) icon="$WARN" ;; FAIL)
            icon="$FAIL"
            failed=1
            ;;
        *) icon="$INFO" ;; esac
        echo "${icon} ${msg}"
        [ -n "$hint" ] && echo "    ${hint}"
        return 0
    }

    echo "=== Host / OS ==="
    local os
    os="$(os_id || true)"
    if [ -z "$os" ]; then
        echo "${WARN} Cannot read /etc/os-release"
    else
        echo "${INFO} OS: ${os}"
        [ "$os" = "ubuntu:22.04" ] && _chk OK "Ubuntu 22.04 detected" || _chk WARN "Recommended: ubuntu:22.04 (current: ${os})"
    fi

    echo ""
    echo "=== Tools ==="
    for c in bash curl git make python3 sudo; do
        if cmd_exists "$c"; then
            _chk OK "${c} found"
        else
            local hint=""
            case "$c" in
            git) hint="Fix: sudo apt update && sudo apt install -y git" ;;
            curl) hint="Fix: sudo apt update && sudo apt install -y curl ca-certificates" ;;
            python3) hint="Fix: sudo apt update && sudo apt install -y python3" ;;
            make) hint="Fix: sudo apt update && sudo apt install -y make" ;;
            sudo) hint="Fix: install sudo (or run as root)" ;;
            esac
            _chk FAIL "${c} not found" "$hint"
        fi
    done
    if cmd_exists python3 && ! python3 -m pip --version >/dev/null 2>&1; then
        _chk WARN "python3-pip not found (optional, needed for rocker)" "Fix: sudo apt update && sudo apt install -y python3-pip"
    fi

    echo ""
    echo "=== Docker ==="
    if cmd_exists docker; then
        echo "${OK} docker found: $(command -v docker)"
        if docker_as_user_ok; then
            _chk OK "docker daemon reachable (user)"
        elif docker_as_sudo_ok; then
            _chk WARN "docker daemon reachable only via sudo (recommended: add user to docker group)"
        else
            _chk FAIL "docker daemon not reachable (is docker running? permissions?)"
        fi
        docker_compose_run_no_prompt version >/dev/null 2>&1 &&
            _chk OK "docker compose plugin available" ||
            _chk FAIL "docker compose plugin not available (install docker-compose-plugin)"
        in_group docker &&
            _chk OK "user is in docker group" ||
            _chk WARN "user is NOT in docker group (recommended)" "Fix: sudo usermod -aG docker \"$USER\" && newgrp docker"
    else
        _chk FAIL "docker not found" "Next: ./setup.bash bootstrap"
    fi

    echo ""
    echo "=== Repo ==="
    [ -f docker-compose.yml ] && _chk OK "docker-compose.yml exists" || _chk FAIL "docker-compose.yml missing (run from repo root)"
    [ -f .env ] && _chk OK ".env exists" || _chk INFO ".env not found (optional)" "Tip: ./setup.bash env   (or: cp .env.example .env)"
    [ -x ./docker_build.sh ] && _chk OK "./docker_build.sh is executable" || _chk WARN "./docker_build.sh not executable" "Fix: chmod +x ./docker_build.sh"
    cmd_exists git && git rev-parse --is-inside-work-tree >/dev/null 2>&1 &&
        _chk OK "git repository detected" ||
        _chk INFO "git repository not detected (optional)" "Tip: clone repository first, then run ./setup.bash doctor"
    if cmd_exists docker; then
        docker_run_no_prompt image inspect aichallenge-2025-dev >/dev/null 2>&1 &&
            _chk OK "image exists: aichallenge-2025-dev" ||
            _chk INFO "image missing: aichallenge-2025-dev" "Next: ./docker_build.sh dev"
        docker_run_no_prompt image inspect ghcr.io/automotiveaichallenge/autoware-universe:humble-latest >/dev/null 2>&1 &&
            _chk OK "base image exists: ghcr.io/automotiveaichallenge/autoware-universe:humble-latest" ||
            _chk INFO "base image not found (will be pulled during build)" "Tip: ./setup.bash pull image"
    fi

    echo ""
    echo "=== AWSIM asset ==="
    local awsim_bin="./aichallenge/simulator/AWSIM/AWSIM.x86_64"
    if [ -f "$awsim_bin" ]; then
        [ -x "$awsim_bin" ] && _chk OK "AWSIM binary: ${awsim_bin}" ||
            _chk WARN "AWSIM binary NOT executable: ${awsim_bin}" "Fix: chmod +x ${awsim_bin}"
    else
        _chk WARN "AWSIM binary not found: ${awsim_bin}" "Next: ./setup.bash download awsim"
    fi

    echo ""
    echo "=== GPU (optional) ==="
    if cmd_exists nvidia-smi; then
        echo "${INFO} nvidia-smi found (GPU may be available)"
        echo "    To enable GPU containers later, install NVIDIA Container Toolkit:"
        echo "    https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
    else
        echo "${INFO} nvidia-smi not found (CPU-only is OK)"
    fi

    echo ""
    echo "=== X11 Display ==="
    if [ -z "${DISPLAY-}" ]; then
        _chk WARN "DISPLAY not set"
    else
        _chk OK "DISPLAY=${DISPLAY}"
    fi
    if [ -n "${XAUTHORITY-}" ] && [ -f "${XAUTHORITY}" ]; then
        _chk OK "XAUTHORITY=${XAUTHORITY}"
    else
        _chk WARN "XAUTHORITY not set or file not found (${XAUTHORITY:-<unset>})"
        echo "    Fix: export XAUTHORITY=~/.Xauthority"
        echo "         If that does not work, find the actual path: ps aux | grep Xorg"
    fi

    echo ""
    echo "=== Next steps ==="
    echo "${INFO} 1) If Docker missing:  ./setup.bash bootstrap"
    echo "${INFO} 2) Pull base image:    ./setup.bash pull image (recommended)"
    echo "${INFO} 3) Download AWSIM:     ./setup.bash download awsim"
    echo "${INFO} 4) Build image:        ./docker_build.sh dev"
    echo "${INFO} 5) Build Autoware:     make autoware-build && docker compose logs -f autoware-build"
    echo "${INFO} 6) Run evaluation:     ./run_evaluation.bash  (optional: ROSBAG=true CAPTURE=true)"
    echo "${INFO} 7) Start dev:          make dev DOMAIN_ID=1"
    echo "${INFO} 8) Dev shell:          docker compose run --rm -it --entrypoint bash autoware"

    return "$failed"
}

main() {
    if [ $# -eq 0 ]; then
        if [ -n "$REPO_ROOT" ]; then
            doctor
        else
            bootstrap
        fi
        exit $?
    fi

    case "${1}" in
    -h | --help | help)
        usage
        ;;
    test)
        shift
        # Usage:
        #   ./setup.bash test [BRANCH] [bootstrap-options...]
        #   curl .../setup.bash | bash -s -- test [BRANCH]
        #
        # Default BRANCH is "main".
        local test_branch="main"
        if [ $# -gt 0 ] && [[ ${1} != -* ]]; then
            test_branch="${1}"
            shift
        else
            # Interactive selection from remote branches.
            local repo_url_default="https://github.com/AutomotiveAIChallenge/aichallenge-racingkart.git"
            local repo_url="${AIC_REPO_URL:-$repo_url_default}"
            test_branch="$(select_branch_from_remote "${repo_url}" "main")"
        fi
        bootstrap --branch "${test_branch}" --temp-dir "$@"
        ;;
    doctor)
        doctor
        ;;
    bootstrap)
        shift
        bootstrap "$@"
        ;;
    env)
        ensure_env
        ;;
    download)
        case "${2-}" in
        awsim)
            shift 2
            download_awsim "$@"
            ;;
        *)
            warn "Unknown download target: ${2-}"
            usage
            exit 2
            ;;
        esac
        ;;
    pull)
        case "${2-}" in
        image)
            shift 2
            pull_autoware_image "$@"
            ;;
        *)
            warn "Unknown pull target: ${2-}"
            usage
            exit 2
            ;;
        esac
        ;;
    *)
        warn "Unknown command: ${1}"
        usage
        exit 2
        ;;
    esac
}

main "$@"
