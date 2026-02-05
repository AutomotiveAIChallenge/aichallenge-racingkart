#!/usr/bin/env bash
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
  ./setup.bash                # run preflight checks (no install)
  curl -fsSL https://raw.githubusercontent.com/AutomotiveAIChallenge/aichallenge-racingkart/main/setup.bash | bash
                            # bootstrap a fresh Ubuntu host (installs Docker if missing)
  ./setup.bash preflight      # same as default
  ./setup.bash bootstrap      # install Docker if missing + clone repo + run setup (for fresh PCs)
  ./setup.bash test [BRANCH]  # bootstrap into /tmp (kept by default; default: origin/test)
  ./setup.bash show workspace # print workspace/bootstrap steps (manual)
  ./setup.bash show docker    # print Docker install steps (manual)
  ./setup.bash show rocker    # print rocker install steps (manual, optional)
  ./setup.bash pull image     # docker pull Autoware base image (recommended)
  ./setup.bash show image     # print Autoware base image pull steps (manual)
  ./setup.bash show gpu       # print NVIDIA/GPU optional steps (manual)
  ./setup.bash show awsim     # print AWSIM asset placement steps (manual)
  ./setup.bash download awsim # download & extract AWSIM.zip (repo-local)
  ./setup.bash env            # create .env from .env.example (safe, repo-local)
  ./setup.bash doctor         # run preflight + next steps summary
  ./setup.bash bootstrap --enter-shell
                            # after bootstrap, open an interactive shell in the repo dir
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

show_workspace_steps() {
    local default_repo_url='https://github.com/AutomotiveAIChallenge/aichallenge-racingkart.git'
    local repo_url="${AIC_REPO_URL:-$default_repo_url}"

    cat <<EOF
# Workspace bootstrap (Ubuntu)

sudo apt update
sudo apt install -y git

cd ~
git clone ${repo_url}

# Then run:
cd aichallenge-racingkart
./setup.bash
EOF
}

show_docker_install_steps() {
    cat <<'EOF'
# Docker install (Ubuntu) - based on Docker official docs
# https://docs.docker.com/engine/install/ubuntu/

sudo apt update
sudo apt install -y python3-pip ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Allow non-root docker usage (requires re-login or 'newgrp docker')
sudo usermod -aG docker "$USER"
newgrp docker

# Sanity check
sudo docker run hello-world

# Optional: Install rocker (only if you want to use docker_run.sh)
pip install rocker
echo export PATH='$HOME/.local/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
EOF
}

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

    local dc_override=""
    if [ "$use_sudo" -eq 1 ]; then
        local compose_file="${COMPOSE_FILE:-docker-compose.yml}"
        local compose_gpu_file="${COMPOSE_GPU_FILE:-docker-compose.gpu.yml}"
        local device="${DEVICE:-auto}"
        local gpu_enabled=0
        if [ "${device}" = "gpu" ]; then
            gpu_enabled=1
        elif [ "${device}" = "auto" ] && [ -e /dev/nvidia0 ]; then
            gpu_enabled=1
        fi

        dc_override="sudo docker compose -f ${compose_file}"
        if [ "$gpu_enabled" -eq 1 ]; then
            dc_override="${dc_override} -f ${compose_gpu_file}"
        fi
        warn "${WARN} docker daemon not reachable as user yet; using sudo docker for post-setup steps"
    fi

    if [ "${do_make_autoware_build}" = "1" ]; then
        if [ "$use_sudo" -eq 1 ]; then
            (cd "${repo_dir}" && DC="${dc_override}" make autoware-build) || {
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
            (cd "${repo_dir}" && DC="${dc_override}" make dev DOMAIN_ID="${domain_id}") || warn "${WARN} make dev failed"
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
    local enter_shell=0
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
        --enter-shell)
            enter_shell=1
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
  --enter-shell         Open an interactive shell in the repo dir when finished

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
    local do_docker_group=0
    local do_clone_repo=0
    local do_repo_doctor=0
    local do_pull_image=0
    local do_download_awsim=0
    local do_build_dev_image=0
    local do_make_autoware_build=0
    local do_make_dev=0

    log "${INFO} Planned steps (answer y/N for each, then execution starts):"
    log "  1) Install base packages (apt)"
    log "  2) Install Docker (if missing)"
    log "  3) Add user to docker group (recommended)"
    log "  4) Clone/update repository (branch=${branch}) -> ${dest_dir}"
    log "  5) Repo preflight: ./setup.bash doctor (requires repo)"
    if [ "$skip_pull_image" -ne 1 ]; then
        log "  6) Pull Autoware base image (requires repo)"
    else
        log "  6) Pull Autoware base image (SKIP: --skip-pull-image)"
    fi
    if [ "$skip_awsim" -ne 1 ]; then
        log "  7) Download AWSIM.zip and extract (requires repo)"
    else
        log "  7) Download AWSIM.zip and extract (SKIP: --skip-awsim)"
    fi
    if [ "$skip_build" -ne 1 ]; then
        log "  8) Build dev image: ./docker_build.sh dev (requires repo)"
    else
        log "  8) Build dev image: ./docker_build.sh dev (SKIP: --skip-build)"
    fi
    if [ "$skip_make" -ne 1 ]; then
        log "  9) make autoware-build (requires repo)"
        log " 10) make dev DOMAIN_ID=${DOMAIN_ID:-1} (requires repo)"
    else
        log "  9) make autoware-build (SKIP: --skip-make)"
        log " 10) make dev (SKIP: --skip-make)"
    fi

    if confirm_step "Install base packages (apt)"; then
        do_install_base=1
    fi
    if confirm_step "Install Docker (if missing)"; then
        do_install_docker=1
    fi
    if confirm_step "Add user to docker group (recommended)"; then
        do_docker_group=1
    fi

    local repo_exists_now=0
    if is_repo_root_dir "${dest_dir}"; then
        repo_exists_now=1
    fi

    if confirm_step "Clone/update repository (branch=${branch}) -> ${dest_dir}"; then
        do_clone_repo=1
    fi

    local repo_planned=0
    if [ "${repo_exists_now}" -eq 1 ] || [ "${do_clone_repo}" -eq 1 ]; then
        repo_planned=1
    fi

    if [ "${repo_planned}" -eq 1 ]; then
        if confirm_step "Run repo preflight: ./setup.bash doctor"; then
            do_repo_doctor=1
        fi
        if [ "$skip_pull_image" -ne 1 ] && confirm_step "Pull Autoware base image"; then
            do_pull_image=1
        fi
        if [ "$skip_awsim" -ne 1 ] && confirm_step "Download AWSIM.zip and extract"; then
            do_download_awsim=1
        fi
        if [ "$skip_build" -ne 1 ] && confirm_step "Build dev image: ./docker_build.sh dev"; then
            do_build_dev_image=1
        fi
        if [ "$skip_make" -ne 1 ]; then
            if confirm_step "Run make autoware-build (this can take a while)"; then
                do_make_autoware_build=1
            fi
            if confirm_step "Run make dev DOMAIN_ID=${DOMAIN_ID:-1}"; then
                do_make_dev=1
            fi
        fi
    else
        log "${INFO} Repo steps skipped (repo not selected / not present)"
        skip_pull_image=1
        skip_awsim=1
        skip_build=1
        skip_make=1
        enter_shell=0
    fi

    log "${INFO} Starting execution..."

    run_step_if "${do_install_base}" "Install base packages (apt)" install_base_packages
    run_step_if "${do_install_docker}" "Install Docker (if missing)" install_docker_if_missing
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
        enter_shell=0
    fi

    if is_repo_root_dir "${dest_dir}"; then
        run_step_if "${do_repo_doctor}" "Run repo preflight: ./setup.bash doctor" bash "${dest_dir}/setup.bash" doctor || true
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
                    (cd "${dest_dir}" && sudo chown -R "${owner_user}:${owner_group}" output/_host 2>/dev/null) || true
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

    if [ "${enter_shell}" -eq 1 ]; then
        if [ -r /dev/tty ]; then
            log "${INFO} Entering repo shell: ${dest_dir}"
            cd "${dest_dir}"
            exec "${SHELL:-bash}" -i </dev/tty
        else
            warn "${WARN} --enter-shell requested but no TTY detected; skipping"
        fi
    fi
}

show_rocker_steps() {
    cat <<'EOF'
# Rocker (optional)
# https://github.com/osrf/rocker

python3 -m pip install --user rocker
echo export PATH='$HOME/.local/bin:$PATH' >> ~/.bashrc
source ~/.bashrc

rocker --help
EOF
}

show_autoware_image_steps() {
    cat <<'EOF'
# Autoware base image (recommended pre-pull, ~10GB)
docker pull ghcr.io/automotiveaichallenge/autoware-universe:humble-latest
docker images | head
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
        warn "${INFO} Docker not found. Run: ./setup.bash show docker"
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

show_gpu_optional_steps() {
    cat <<'EOF'
# GPU (optional): NVIDIA driver + NVIDIA Container Toolkit + Vulkan

# 1) NVIDIA driver (example)
sudo add-apt-repository ppa:graphics-drivers/ppa
sudo apt update
sudo ubuntu-drivers install
reboot
nvidia-smi

# 2) NVIDIA Container Toolkit (for docker --gpus)
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html
distribution=$(. /etc/os-release;echo $ID$VERSION_ID) \
  && curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
  && curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

sudo docker run --rm --gpus all nvidia/cuda:11.6.2-base-ubuntu20.04 nvidia-smi

# 3) Vulkan (GUI AWSIM)
sudo apt update
sudo apt install -y libvulkan1
EOF
}

show_awsim_steps() {
    cat <<'EOF'
# AWSIM asset placement (manual)
#
# Place AWSIM under:
#   ./aichallenge/simulator/AWSIM/AWSIM.x86_64
#
# After extracting, ensure the binary is executable:
chmod +x ./aichallenge/simulator/AWSIM/AWSIM.x86_64

# Tip: This repo also provides an automated downloader:
./setup.bash download awsim
EOF
}

download_awsim() {
    local default_url='https://tier4inc-my.sharepoint.com/:u:/g/personal/taiki_tanaka_tier4_jp/IQBPAz39bTOuSbAhNIhYi8SnAQ91zs1rEUq71hMITWWd3ew?e=Ra7Lw2'
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
        log "${OK} .env already exists"
        return 0
    fi
    if [ ! -f .env.example ]; then
        warn "${FAIL} .env.example not found"
        return 1
    fi
    cp .env.example .env
    log "${OK} Created .env from .env.example"
    log "${INFO} Edit .env if needed (NTRIP_*, VEHICLE_ID, ...)"
}

preflight() {
    local failed=0

    echo "=== Host / OS ==="
    local os
    os="$(os_id || true)"
    if [ -z "$os" ]; then
        echo "${WARN} Cannot read /etc/os-release"
    else
        echo "${INFO} OS: ${os}"
        if [ "$os" != "ubuntu:22.04" ]; then
            echo "${WARN} Recommended: ubuntu:22.04 (current: ${os})"
        else
            echo "${OK} Ubuntu 22.04 detected"
        fi
    fi

    echo ""
    echo "=== Tools ==="
    for c in bash curl git make python3 sudo; do
        if cmd_exists "$c"; then
            echo "${OK} ${c} found"
        else
            echo "${FAIL} ${c} not found"
            case "$c" in
            git)
                echo "    Fix: sudo apt update && sudo apt install -y git"
                ;;
            curl)
                echo "    Fix: sudo apt update && sudo apt install -y curl ca-certificates"
                ;;
            python3)
                echo "    Fix: sudo apt update && sudo apt install -y python3"
                ;;
            make)
                echo "    Fix: sudo apt update && sudo apt install -y make"
                ;;
            sudo)
                echo "    Fix: install sudo (or run as root)"
                ;;
            *) ;;
            esac
            failed=1
        fi
    done
    if cmd_exists python3 && ! python3 -m pip --version >/dev/null 2>&1; then
        echo "${WARN} python3-pip not found (optional, needed for rocker)"
        echo "    Fix: sudo apt update && sudo apt install -y python3-pip"
    fi

    echo ""
    echo "=== Docker ==="
    if cmd_exists docker; then
        echo "${OK} docker found: $(command -v docker)"
        if docker_as_user_ok; then
            echo "${OK} docker daemon reachable (user)"
        elif docker_as_sudo_ok; then
            echo "${WARN} docker daemon reachable only via sudo (recommended: add user to docker group)"
        else
            echo "${FAIL} docker daemon not reachable (is docker running? permissions?)"
            failed=1
        fi

        if docker_compose_run_no_prompt version >/dev/null 2>&1; then
            echo "${OK} docker compose plugin available"
        else
            echo "${FAIL} docker compose plugin not available (install docker-compose-plugin)"
            failed=1
        fi

        if in_group docker; then
            echo "${OK} user is in docker group"
        else
            echo "${WARN} user is NOT in docker group (recommended)"
            echo "    Fix: sudo usermod -aG docker \"$USER\" && newgrp docker"
        fi
    else
        echo "${FAIL} docker not found"
        echo "    Next: ./setup.bash show docker"
        failed=1
    fi

    echo ""
    echo "=== Repo ==="
    if [ -f docker-compose.yml ]; then
        echo "${OK} docker-compose.yml exists"
    else
        echo "${FAIL} docker-compose.yml missing (run from repo root)"
        failed=1
    fi
    if [ -f .env ]; then
        echo "${OK} .env exists"
    else
        echo "${INFO} .env not found (optional)"
        echo "    Tip: ./setup.bash env   (or: cp .env.example .env)"
    fi
    if [ -x ./docker_build.sh ]; then
        echo "${OK} ./docker_build.sh is executable"
    else
        echo "${WARN} ./docker_build.sh not executable"
        echo "    Fix: chmod +x ./docker_build.sh"
    fi
    if cmd_exists git && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "${OK} git repository detected"
    else
        echo "${INFO} git repository not detected (optional)"
        echo "    Tip: ./setup.bash show workspace"
    fi
    if cmd_exists docker; then
        if docker_run_no_prompt image inspect aichallenge-2025-dev >/dev/null 2>&1; then
            echo "${OK} image exists: aichallenge-2025-dev"
        else
            echo "${INFO} image missing: aichallenge-2025-dev"
            echo "    Next: ./docker_build.sh dev"
        fi
        if docker_run_no_prompt image inspect ghcr.io/automotiveaichallenge/autoware-universe:humble-latest >/dev/null 2>&1; then
            echo "${OK} base image exists: ghcr.io/automotiveaichallenge/autoware-universe:humble-latest"
        else
            echo "${INFO} base image not found (will be pulled during build)"
            echo "    Tip: ./setup.bash pull image"
        fi
    fi

    echo ""
    echo "=== AWSIM asset ==="
    local awsim_bin="./aichallenge/simulator/AWSIM/AWSIM.x86_64"
    if [ -f "$awsim_bin" ]; then
        if [ -x "$awsim_bin" ]; then
            echo "${OK} AWSIM binary exists and is executable: ${awsim_bin}"
        else
            echo "${WARN} AWSIM binary exists but is NOT executable: ${awsim_bin}"
            echo "    Fix: chmod +x ${awsim_bin}"
        fi
    else
        echo "${WARN} AWSIM binary not found: ${awsim_bin}"
        echo "    Next: ./setup.bash download awsim   (or: ./setup.bash show awsim)"
    fi

    echo ""
    echo "=== GPU (optional) ==="
    if cmd_exists nvidia-smi; then
        echo "${INFO} nvidia-smi found (GPU may be available)"
        echo "    To enable GPU containers later, install NVIDIA Container Toolkit:"
        echo "    ./setup.bash show gpu"
    else
        echo "${INFO} nvidia-smi not found (CPU-only is OK)"
    fi

    return "$failed"
}

doctor() {
    local rc=0
    if ! preflight; then
        rc=1
    fi

    echo ""
    echo "=== Next steps ==="
    echo "${INFO} 1) If repo not cloned: ./setup.bash show workspace"
    echo "${INFO} 2) If Docker missing:  ./setup.bash show docker"
    echo "${INFO} 3) Pull base image:    ./setup.bash pull image (recommended)"
    echo "${INFO} 4) Download AWSIM:     ./setup.bash download awsim"
    echo "${INFO} 5) Build image:        ./docker_build.sh dev"
    echo "${INFO} 6) Build Autoware:     make autoware-build && docker compose logs -f autoware-build"
    echo "${INFO} 7) Run evaluation:     ./run_evaluation.bash  (optional: ROSBAG=true CAPTURE=true)"
    echo "${INFO} 8) Start dev:          make dev DOMAIN_ID=1"
    echo "${INFO} 9) Dev shell:          docker compose run --rm -it --entrypoint bash autoware"

    return "$rc"
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
    preflight)
        preflight
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
    show)
        case "${2-}" in
        workspace)
            show_workspace_steps
            ;;
        docker)
            show_docker_install_steps
            ;;
        rocker)
            show_rocker_steps
            ;;
        image)
            show_autoware_image_steps
            ;;
        gpu)
            show_gpu_optional_steps
            ;;
        awsim)
            show_awsim_steps
            ;;
        *)
            warn "Unknown show target: ${2-}"
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
