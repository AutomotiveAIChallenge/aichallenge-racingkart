# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-vehicle autoware-sim driver zenoh run-full-kart-system build-autoware \
	download run-sim-eval rviz2 sim init start reset down ps

# GPU selection:
# - DEVICE=auto (default): enable GPU override if NVIDIA is detected
# - DEVICE=gpu: force GPU override
# - DEVICE=cpu: never use GPU override
DEVICE ?= auto
HAVE_NVIDIA := $(shell command -v nvidia-smi >/dev/null 2>&1 && [ -e /dev/nvidia0 ] && echo 1 || echo 0)

DC := docker compose -f docker-compose.yml

GPU_ENABLED := 0
ifeq ($(DEVICE),gpu)
GPU_ENABLED := 1
else ifeq ($(DEVICE),auto)
ifeq ($(HAVE_NVIDIA),1)
GPU_ENABLED := 1
endif
endif

AUTOWARE_SERVICE := autoware
AIC_BUILD_SERVICE := aic-build
SIMULATOR_SERVICE := simulator
AW_CMD_SERVICE := aw-cmd
RVIZ2_SERVICE := rviz2
ROSBAG_SERVICE := rosbag

ifeq ($(GPU_ENABLED),1)
AUTOWARE_SERVICE := autoware-gpu
AIC_BUILD_SERVICE := aic-build-gpu
SIMULATOR_SERVICE := simulator-gpu
RVIZ2_SERVICE := rviz2-gpu
endif

# Used by docker-compose.yml for build/eval artifact ownership.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID

# Evaluation options (compatible with run_evaluation.bash)
# Usage:
#   make run-sim-eval [ROSBAG=true] [CAPTURE=true] [DOMAIN_ID=1] [OUTPUT_ROOT=/output] [RESULT_WAIT_SECONDS=10]
ROSBAG ?= false
CAPTURE ?= false
DOMAIN_ID ?= 1
OUTPUT_ROOT ?= /output
RESULT_WAIT_SECONDS ?= 10

# Window matching overrides for move_window.bash (optional)
# Tips:
#   - Set MOVE_WINDOW_DEBUG=1 to print candidates from wmctrl
#   - Narrow AWSIM_*_REGEX when it grabs the wrong window
AWSIM_TITLE_REGEX ?=
AWSIM_CLASS_REGEX ?=
RVIZ_TITLE_REGEX ?=
RVIZ_CLASS_REGEX ?=
MOVE_WINDOW_DEBUG ?= 0
MOVE_WINDOW_PREFER_LARGEST ?= 1
MOVE_WINDOW_QUIET ?= 1
export AWSIM_TITLE_REGEX AWSIM_CLASS_REGEX RVIZ_TITLE_REGEX RVIZ_CLASS_REGEX MOVE_WINDOW_DEBUG MOVE_WINDOW_PREFER_LARGEST MOVE_WINDOW_QUIET

# autowareのみ起動
autoware-vehicle:
	RUN_MODE=vehicle $(DC) up -d $(AUTOWARE_SERVICE)

# Autoware(AWSIM mode)
autoware-sim:
	@echo "Start Autoware(AWSIM mode)"
	RUN_MODE=awsim $(DC) up -d $(AUTOWARE_SERVICE)

# racing kart
driver:
	$(DC) up -d driver

# zenoh
zenoh:
	$(DC) up -d zenoh

# driver + autoware + zenoh
run-full-kart-system:
	RUN_MODE=vehicle $(DC) up -d driver $(AUTOWARE_SERVICE)
	sleep 15
	$(DC) up -d zenoh

# autowareのbuildのみ
build-autoware:
	$(DC) up -d $(AIC_BUILD_SERVICE)

# Download submission data by asking for credentials interactively
# Usage:
#   make download [SUBMISSION_ID=<id>]
# Usage (Only Admins):
#   make download [USER_ID=<id>] [SUBMISSION_ID=<id>]
download:
	@if [ -n "$(USER_ID)" ]; then \
		if [ -n "$(SUBMISSION_ID)" ]; then \
			vehicle/download_submission.sh --output aichallenge/workspace/src/ --user-id $(USER_ID) --submission-id $(SUBMISSION_ID); \
		else \
			vehicle/download_submission.sh --output aichallenge/workspace/src/ --user-id $(USER_ID); \
		fi; \
	else \
		if [ -n "$(SUBMISSION_ID)" ]; then \
			vehicle/download_submission.sh --output aichallenge/workspace/src/ --submission-id $(SUBMISSION_ID); \
		else \
			vehicle/download_submission.sh --output aichallenge/workspace/src/; \
		fi; \
	fi

# make run-sim-eval ROSBAG=true CAPTURE=true
run-sim-eval:
	@bash -lc 'set -euo pipefail; \
		ts="$$(date +%Y%m%d-%H%M%S)"; \
		mkdir -p output; \
		mkdir -p output/_host; \
		if [ -e output/latest ] && [ ! -L output/latest ]; then \
			legacy="output/_host/legacy-output-latest-$$ts-$$RANDOM"; \
			echo "[make] Moving legacy output/latest to $$legacy"; \
			mv output/latest "$$legacy"; \
		fi; \
			mkdir -p "output/$$ts"; \
			ln -nfs "$$ts" output/latest; \
			output_root="$(OUTPUT_ROOT)"; \
			domain_id="$(DOMAIN_ID)"; \
			mkdir -p "output/$$ts/d$$domain_id"; \
			output_run_dir="$$output_root/$$ts/d$$domain_id"; \
			result_wait_seconds="$(RESULT_WAIT_SECONDS)"; \
			rosbag_enabled="$(ROSBAG)"; \
			capture_enabled="$(CAPTURE)"; \
			sim_svc="$(SIMULATOR_SERVICE)"; \
			autoware_svc="$(AUTOWARE_SERVICE)"; \
			cmd_svc="$(AW_CMD_SERVICE)"; \
			rosbag_svc="$(ROSBAG_SERVICE)"; \
			nvidia_visible_devices=""; \
			nvidia_driver_caps=""; \
			case "$$sim_svc" in *-gpu) nvidia_visible_devices="all"; nvidia_driver_caps="all";; esac; \
			echo "--- Starting Evaluation ---"; \
			echo "OUTPUT: output/$$ts/d$$domain_id (container: $$output_run_dir)"; \
			echo "DOMAIN_ID=$$domain_id ROSBAG=$$rosbag_enabled CAPTURE=$$capture_enabled"; \
			dc() { NVIDIA_VISIBLE_DEVICES="$$nvidia_visible_devices" NVIDIA_DRIVER_CAPABILITIES="$$nvidia_driver_caps" OUTPUT_ROOT="$$output_root" OUTPUT_RUN_DIR="$$output_run_dir" DOMAIN_ID="$$domain_id" EVAL_RUN=1 CMD_WORKDIR="$$output_run_dir" $(DC) "$$@"; }; \
		best_effort() { "$$@" >/dev/null 2>&1 || true; }; \
		capture_started=0; \
		rosbag_started=0; \
		sim_cid=""; \
		autoware_cid=""; \
		rosbag_cid=""; \
		cleanup() { \
			set +e; \
			if [ "$$capture_started" -eq 1 ]; then \
					CMD="env ROS_DOMAIN_ID=$$domain_id /aichallenge/utils/publish.bash request-capture" dc run --rm --no-deps "$$cmd_svc" >/dev/null 2>&1 || true; \
			fi; \
			if [ "$$rosbag_started" -eq 1 ]; then \
				rosbag_cid="$$(dc ps -q "$$rosbag_svc" 2>/dev/null || true)"; \
				if [ -n "$$rosbag_cid" ]; then \
					docker kill --signal INT "$$rosbag_cid" >/dev/null 2>&1 || true; \
					docker wait "$$rosbag_cid" >/dev/null 2>&1 || true; \
				fi; \
				dc stop "$$rosbag_svc" >/dev/null 2>&1 || true; \
			fi; \
			autoware_cid="$$(dc ps -q "$$autoware_svc" 2>/dev/null || true)"; \
			if [ -n "$$autoware_cid" ]; then \
				docker kill --signal INT "$$autoware_cid" >/dev/null 2>&1 || true; \
				docker wait "$$autoware_cid" >/dev/null 2>&1 || true; \
			fi; \
			dc stop "$$autoware_svc" >/dev/null 2>&1 || true; \
			sim_cid="$$(dc ps -q "$$sim_svc" 2>/dev/null || true)"; \
			if [ -n "$$sim_cid" ]; then \
				docker kill --signal INT "$$sim_cid" >/dev/null 2>&1 || true; \
				docker wait "$$sim_cid" >/dev/null 2>&1 || true; \
			fi; \
			dc stop "$$sim_svc" >/dev/null 2>&1 || true; \
				CMD="bash /aichallenge/utils/fix_ownership.bash $(HOST_UID) $(HOST_GID) $$output_root $$ts" dc run --rm --no-deps "$$cmd_svc" >/dev/null 2>&1 || true; \
		}; \
		trap cleanup EXIT; \
		trap "echo \"[make] Interrupted\" >&2; exit 130" INT; \
		trap "echo \"[make] Terminated\" >&2; exit 143" TERM; \
		SIM_MODE=eval dc up -d --force-recreate "$$sim_svc"; \
			CMD="env ROS_DOMAIN_ID=0 /aichallenge/utils/publish.bash check-awsim" dc run --rm --no-deps "$$cmd_svc"; \
		RUN_MODE=awsim dc up -d --force-recreate "$$autoware_svc"; \
		sleep 3; \
			CMD="bash /aichallenge/utils/move_window.bash" dc run --rm --no-deps "$$cmd_svc" || true; \
			CMD="env ROS_DOMAIN_ID=$$domain_id /aichallenge/utils/publish.bash request-initialpose" dc run --rm --no-deps "$$cmd_svc"; \
			CMD="env ROS_DOMAIN_ID=$$domain_id /aichallenge/utils/publish.bash request-control" dc run --rm --no-deps "$$cmd_svc"; \
		if [ "$$capture_enabled" = "true" ]; then \
				CMD="env ROS_DOMAIN_ID=$$domain_id /aichallenge/utils/publish.bash request-capture" dc run --rm --no-deps "$$cmd_svc" >/dev/null 2>&1 || true; \
			capture_started=1; \
		fi; \
		if [ "$$rosbag_enabled" = "true" ]; then \
			dc up -d --force-recreate "$$rosbag_svc" >/dev/null 2>&1 || true; \
			rosbag_started=1; \
		fi; \
		sim_cid="$$(dc ps -q "$$sim_svc")"; \
		if [ -n "$$sim_cid" ]; then \
			docker wait "$$sim_cid" >/dev/null 2>&1 || true; \
		fi; \
			CMD="bash /aichallenge/utils/convert_result.bash $$domain_id $$result_wait_seconds" dc run --rm --no-deps "$$cmd_svc" >/dev/null 2>&1 || true; \
		echo \"[make] Evaluation finished\"'

# rviz
rviz2:
	$(DC) stop $(RVIZ2_SERVICE)
	$(DC) up -d $(RVIZ2_SERVICE)

# simulator
sim:
	@echo "Start AWSIM"
	$(DC) up -d $(SIMULATOR_SERVICE)

init:
	CMD=python3 ./publish_initialpose.py \
	$(DC) up -d $(AW_CMD_SERVICE)

start:
	@echo "Start control"
	CMD="ros2 topic pub -t 10 /awsim/control_mode_request_topic std_msgs/msg/Bool '{data: true}'" \
	$(DC) up -d $(AW_CMD_SERVICE)

reset:
	@echo "Reset simulation"
	CMD="ros2 topic pub --once /aichallenge/awsim/reset std_msgs/msg/Empty {} | python3 ./publish_initialpose.py" \
	$(DC) up -d $(AW_CMD_SERVICE)

down:
	$(DC) down --remove-orphans

ps:
	$(DC) ps
