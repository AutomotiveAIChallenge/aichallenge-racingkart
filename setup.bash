#!/usr/bin/env bash
set -euo pipefail

OK="[OK]"
WARN="[WARN]"
FAIL="[FAIL]"
INFO="[INFO]"
DEFAULT_REPO_URL="https://github.com/AutomotiveAIChallenge/aichallenge-racingkart.git"
DEFAULT_BRANCH="main"
DEFAULT_DIR="${HOME}/aichallenge-racingkart"
SETUP_ASSUME_YES=0

log() { echo "[setup] $*"; }
warn() { echo "[setup][WARN] $*" >&2; }
die() {
    echo "[setup][ERROR] $*" >&2
    exit 1
}
on_interrupt() {
    echo ""
    warn "Interrupted (Ctrl+C)"
    exit 130
}
trap on_interrupt INT

cmd_exists() { command -v "$1" >/dev/null 2>&1; }
require_cmd() { cmd_exists "$1" || {
    warn "Missing required command: $1"
    return 1
}; }

os_id() {
    [ -r /etc/os-release ] || return 1
    # shellcheck disable=SC1091
    . /etc/os-release
    echo "${ID:-unknown}:${VERSION_ID:-unknown}"
}

is_repo_root_dir() { [ -f "$1/docker-compose.yml" ] && [ -f "$1/Dockerfile" ] && [ -f "$1/Makefile" ]; }
normalize_branch_ref() { case "${1-}" in origin/*) echo "${1#origin/}" ;; *) echo "${1-}" ;; esac }

sudo_refresh() {
    [ "$(id -u)" -eq 0 ] && return 0
    require_cmd sudo || return 1
    sudo -v
}

apt_run() {
    if [ "$(id -u)" -eq 0 ]; then apt-get "$@"; else
        sudo_refresh
        sudo apt-get "$@"
    fi
}

in_group() { id -nG "${USER-}" 2>/dev/null | tr ' ' '\n' | grep -qx "$1"; }
docker_as_user_ok() { cmd_exists docker && docker info >/dev/null 2>&1; }
docker_as_sudo_ok() { cmd_exists docker && cmd_exists sudo && sudo -n docker info >/dev/null 2>&1; }

docker_compose_available() {
    if cmd_exists docker && docker compose version >/dev/null 2>&1; then return 0; fi
    if cmd_exists docker && cmd_exists sudo && sudo -n docker compose version >/dev/null 2>&1; then return 0; fi
    return 1
}

confirm_step() {
    local prompt="$1" ans=""
    if [ "${SETUP_ASSUME_YES}" = "1" ]; then
        log "${INFO} ${prompt} (auto-yes)"
        return 0
    fi
    if ! [ -r /dev/tty ]; then
        warn "No TTY available. Re-run with --yes."
        return 1
    fi
    printf "[setup] %s [y/N]: " "${prompt}" >/dev/tty
    IFS= read -r ans </dev/tty || return 1
    case "${ans}" in y | Y) return 0 ;; *) return 1 ;; esac
}

install_base_packages() {
    log "${INFO} Installing base packages"
    apt_run update
    apt_run install -y ca-certificates curl git gnupg make python3 python3-pip sudo
}

install_docker_if_missing() {
    if cmd_exists docker && docker --version >/dev/null 2>&1 && docker_compose_available; then
        log "${OK} Docker and docker compose plugin already installed"
        return 0
    fi
    log "${INFO} Installing Docker Engine and docker compose plugin"
    apt_run update
    apt_run install -y ca-certificates curl gnupg

    if [ "$(id -u)" -eq 0 ]; then
        install -m 0755 -d /etc/apt/keyrings
        if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            chmod a+r /etc/apt/keyrings/docker.gpg
        fi
    else
        sudo install -m 0755 -d /etc/apt/keyrings
        if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
        fi
    fi

    local codename=""
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"

    if [ "$(id -u)" -eq 0 ]; then
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable" >/etc/apt/sources.list.d/docker.list
    else
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    fi

    apt_run update
    apt_run install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    if [ "$(id -u)" -eq 0 ]; then systemctl enable --now docker || true; else sudo systemctl enable --now docker || true; fi
    log "${OK} Docker installed"
}

ensure_docker_group() {
    [ "$(id -u)" -eq 0 ] && return 0
    if in_group docker; then
        log "${OK} user is already in docker group"
        return 0
    fi
    sudo_refresh
    sudo usermod -aG docker "${USER-}"
    warn "${WARN} Added ${USER-} to docker group. Re-login is required."
}

clone_or_update_repo() {
    local repo_url="$1" branch_ref="$2" dest_dir="$3" branch=""
    branch="$(normalize_branch_ref "${branch_ref}")"

    if [ -d "${dest_dir}/.git" ]; then
        log "${INFO} Updating existing repository: ${dest_dir}"
        git -C "${dest_dir}" fetch --prune origin || git -C "${dest_dir}" fetch --prune
        if git -C "${dest_dir}" show-ref --verify --quiet "refs/heads/${branch}"; then
            git -C "${dest_dir}" checkout "${branch}"
        elif git -C "${dest_dir}" show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
            git -C "${dest_dir}" checkout -B "${branch}" "origin/${branch}"
            git -C "${dest_dir}" branch --set-upstream-to="origin/${branch}" "${branch}" >/dev/null 2>&1 || true
        else
            die "Branch not found: ${branch_ref}"
        fi
        git -C "${dest_dir}" pull --ff-only origin "${branch}"
        return 0
    fi

    if [ -e "${dest_dir}" ] && [ -n "$(ls -A "${dest_dir}" 2>/dev/null || true)" ]; then
        die "Destination exists and is not an empty directory or git repo: ${dest_dir}"
    fi

    log "${INFO} Cloning repository: ${repo_url} (branch=${branch}) -> ${dest_dir}"
    git clone --branch "${branch}" "${repo_url}" "${dest_dir}"
}

preflight() {
    local failed=0 os=""

    echo "=== Host / OS ==="
    os="$(os_id || true)"
    if [ -z "${os}" ]; then
        echo "${WARN} Could not detect OS from /etc/os-release"
    else
        echo "${INFO} OS: ${os}"
        [ "${os}" = "ubuntu:22.04" ] && echo "${OK} Ubuntu 22.04 detected" || echo "${WARN} Recommended OS: ubuntu:22.04"
    fi

    echo ""
    echo "=== Tools ==="
    for c in bash curl git make python3 sudo; do
        if cmd_exists "${c}"; then
            echo "${OK} ${c} found"
        else
            echo "${FAIL} ${c} not found"
            failed=1
        fi
    done

    echo ""
    echo "=== Docker ==="
    if cmd_exists docker; then
        echo "${OK} docker found: $(command -v docker)"
        if docker_as_user_ok; then
            echo "${OK} docker daemon reachable as current user"
        elif docker_as_sudo_ok; then
            echo "${WARN} docker daemon reachable only via sudo (docker group recommended)"
        else
            echo "${FAIL} docker daemon is not reachable"
            failed=1
        fi

        if docker_compose_available; then
            echo "${OK} docker compose plugin available"
        else
            echo "${FAIL} docker compose plugin not available"
            failed=1
        fi
    else
        echo "${FAIL} docker not found"
        failed=1
    fi

    echo ""
    echo "=== Repository (current directory) ==="
    if is_repo_root_dir "$PWD"; then
        echo "${OK} repository root detected"
        if [ -f .env ]; then
            echo "${OK} .env exists"
        elif [ -f .env.example ]; then
            echo "${INFO} .env not found (.env.example exists)"
        else
            echo "${WARN} neither .env nor .env.example found"
        fi
    else
        echo "${INFO} repository root not detected in current directory"
        echo "${INFO} if needed, run: ./setup.bash bootstrap"
    fi

    return "${failed}"
}

bootstrap() {
    local repo_url="${AIC_REPO_URL:-${DEFAULT_REPO_URL}}"
    local branch="${AIC_BRANCH:-${DEFAULT_BRANCH}}"
    local dest_dir="${AIC_DIR:-${DEFAULT_DIR}}"
    local os="" need_base=0
    SETUP_ASSUME_YES="${AIC_ASSUME_YES:-0}"

    while [ $# -gt 0 ]; do
        case "$1" in
        --repo)
            repo_url="${2-}"
            shift 2
            ;;
        --branch)
            branch="${2-}"
            shift 2
            ;;
        --dir)
            dest_dir="${2-}"
            shift 2
            ;;
        --yes | -y)
            SETUP_ASSUME_YES=1
            shift
            ;;
        -h | --help)
            cat <<BOOT_HELP
Usage:
  ./setup.bash bootstrap [--repo URL] [--branch NAME] [--dir PATH] [--yes|-y]
Defaults:
  repo=${DEFAULT_REPO_URL}
  branch=${DEFAULT_BRANCH}
  dir=${DEFAULT_DIR}
BOOT_HELP
            return 0
            ;;
        *)
            warn "Unknown option for bootstrap: $1"
            return 2
            ;;
        esac
    done

    [ -n "${repo_url}" ] || die "--repo must not be empty"
    [ -n "${branch}" ] || die "--branch must not be empty"
    [ -n "${dest_dir}" ] || die "--dir must not be empty"

    os="$(os_id || true)"
    log "${INFO} bootstrap configuration"
    log "  repo  : ${repo_url}"
    log "  branch: ${branch}"
    log "  dir   : ${dest_dir}"
    if [ -n "${os}" ] && [ "${os}" != "ubuntu:22.04" ]; then
        warn "${WARN} Recommended OS is ubuntu:22.04 (current: ${os})"
    fi

    for c in curl git make python3 sudo; do
        if ! cmd_exists "${c}"; then need_base=1; fi
    done
    if [ "${need_base}" -eq 1 ]; then
        if confirm_step "Install base packages (curl/git/make/python3/sudo)?"; then
            install_base_packages
        else
            die "Base tools are missing. Re-run with --yes or install dependencies manually."
        fi
    fi

    require_cmd git || return 1
    require_cmd curl || return 1

    if ! cmd_exists docker || ! docker_compose_available; then
        if confirm_step "Install Docker Engine and docker compose plugin?"; then
            install_docker_if_missing
        else
            die "Docker is required for this repository."
        fi
    fi

    if ! in_group docker; then
        if confirm_step "Add ${USER-} to docker group?"; then
            ensure_docker_group
        fi
    fi

    clone_or_update_repo "${repo_url}" "${branch}" "${dest_dir}"

    if [ -x "${dest_dir}/setup.bash" ]; then
        log "${INFO} Running preflight in ${dest_dir}"
        (cd "${dest_dir}" && bash ./setup.bash preflight) || warn "${WARN} preflight reported issues"
    fi

    cat <<BOOT_DONE

${OK} Bootstrap finished.

Next steps:
  cd "${dest_dir}"
  ./setup.bash preflight
  make autoware-build
  make dev DOMAIN_ID=1
BOOT_DONE
}

unsupported_command() {
    warn "Command '$1' is not supported in the simplified setup.bash."
    echo "Supported commands: preflight, bootstrap"
    echo "See docs: design_docs/how_to_setup.md"
    return 2
}

usage() {
    cat <<'USAGE_HELP'
Usage:
  ./setup.bash                 # run preflight
  ./setup.bash preflight
  ./setup.bash bootstrap [--repo URL] [--branch NAME] [--dir PATH] [--yes|-y]

Unsupported legacy commands:
  doctor, test, show, pull, download, env
USAGE_HELP
}

main() {
    if [ $# -eq 0 ]; then
        preflight
        return $?
    fi

    case "$1" in
    -h | --help | help) usage ;;
    preflight)
        shift
        [ $# -eq 0 ] || {
            warn "preflight takes no options"
            return 2
        }
        preflight
        ;;
    bootstrap)
        shift
        bootstrap "$@"
        ;;
    doctor | test | show | pull | download | env) unsupported_command "$1" ;;
    *)
        warn "Unknown command: $1"
        usage
        return 2
        ;;
    esac
}

main "$@"
