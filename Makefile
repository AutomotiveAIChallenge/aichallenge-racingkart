# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-build autoware-vehicle autoware-simulator autoware-request-initialpose autoware-request-control autoware-driver-zenoh \
	simulator simulator-reset dev eval autoware-rosbag driver zenoh download rviz2 down ps

# GPU selection:
# - DEVICE=auto (default): enable GPU override if NVIDIA is detected
# - DEVICE=gpu: force GPU override
# - DEVICE=cpu: never use GPU override
DEVICE ?= auto
# Auto-detect NVIDIA GPU availability on the host.
# We only check the device node to avoid depending on NVML (`nvidia-smi`) and Docker daemon access.
# If Docker-side GPU is not configured, use `DEVICE=cpu` (or force GPU by `DEVICE=gpu`).
HAVE_NVIDIA := $(shell [ -e /dev/nvidia0 ] && echo 1 || echo 0)

GPU_ENABLED := 0
ifeq ($(DEVICE),gpu)
GPU_ENABLED := 1
else ifeq ($(DEVICE),auto)
ifeq ($(HAVE_NVIDIA),1)
GPU_ENABLED := 1
endif
endif

# Compose file selection (reduce compose-side variants; use overrides instead)
COMPOSE_FILE ?= docker-compose.yml
COMPOSE_GPU_FILE ?= docker-compose.gpu.yml

ifeq ($(origin DC), undefined)
ifeq ($(GPU_ENABLED),1)
DC := docker compose -f $(COMPOSE_FILE) -f $(COMPOSE_GPU_FILE)
else
DC := docker compose -f $(COMPOSE_FILE)
endif
endif

ifeq ($(GPU_ENABLED),1)
NVIDIA_VISIBLE_DEVICES ?= all
NVIDIA_DRIVER_CAPABILITIES ?= all
export NVIDIA_VISIBLE_DEVICES NVIDIA_DRIVER_CAPABILITIES
endif

AUTOWARE_SERVICE := autoware
SIMULATOR_SERVICE := simulator
AW_CMD_SERVICE := autoware-command
ROSBAG_SERVICE := autoware-rosbag

AIC_BUILD_SERVICE := autoware-build
RVIZ2_SERVICE := rviz2

# Used by docker-compose.yml for build/eval artifact ownership.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID

# Evaluation options (compatible with run_evaluation.bash)
# Usage:
#   make eval [ROSBAG=true] [CAPTURE=true] [DOMAIN_ID=1] [OUTPUT_ROOT=/output] [RESULT_WAIT_SECONDS=10]
ROSBAG ?= false
CAPTURE ?= false
DOMAIN_ID ?= 1
DOMAIN_IDS ?= $(DOMAIN_ID)
OUTPUT_ROOT ?= /output
RESULT_WAIT_SECONDS ?= 10
# Output layout overrides (optional)
# - RUN_ID: run directory name under output/ (default: timestamp)
# - RUN_GROUP: optional subdirectory under RUN_ID (e.g., submit name)
RUN_ID ?=
RUN_GROUP ?=

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

# autowareのbuildのみ
autoware-build:
	$(DC) up -d --force-recreate $(AIC_BUILD_SERVICE)

# run autoware for vehicle
autoware-vehicle:
	@echo "Start Autoware for Vehicle"
	RUN_MODE=vehicle $(DC) up -d $(AUTOWARE_SERVICE)

# run autoware for simulator
autoware-simulator:
	@echo "Start Autoware for AWSIM"
	RUN_MODE=awsim DOMAIN_ID=$(DOMAIN_ID) $(DC) up -d $(AUTOWARE_SERVICE)

# autoware command service
autoware-request-initialpose:
	CMD="env ROS_DOMAIN_ID=$(DOMAIN_ID) /aichallenge/utils/publish.bash request-initialpose" \
	$(DC) up -d $(AW_CMD_SERVICE)

autoware-request-control:
	@echo "Start control"
	CMD="env ROS_DOMAIN_ID=$(DOMAIN_ID) /aichallenge/utils/publish.bash request-control" \
	$(DC) up -d $(AW_CMD_SERVICE)

# run simulator (docker compose up -d simulator)
simulator:
	@echo "Start AWSIM"
	SIM_MODE=$(SIM_MODE) $(DC) up -d $(SIMULATOR_SERVICE)

simulator-reset:
	@echo "Reset simulation"
	CMD="bash /aichallenge/utils/simulator_reset.bash $(DOMAIN_ID)" \
	$(DC) up -d $(AW_CMD_SERVICE)

# racing kart (docker compose up -d driver)
driver:
	$(DC) up -d driver

# zenoh (docker compose up -d zenoh)
zenoh:
	$(DC) up -d zenoh

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

dev:
	@echo "Start dev simulation (AWSIM + Autoware, DOMAIN_ID=$(DOMAIN_ID))"
	@$(MAKE) simulator SIM_MODE=dev
	@$(MAKE) autoware-simulator DOMAIN_ID=$(DOMAIN_ID)

# make eval ROSBAG=true CAPTURE=true
eval:
	@RUN_ID="$(RUN_ID)" RUN_GROUP="$(RUN_GROUP)" \
		OUTPUT_ROOT="$(OUTPUT_ROOT)" DOMAIN_IDS="$(DOMAIN_IDS)" RESULT_WAIT_SECONDS="$(RESULT_WAIT_SECONDS)" \
		ROSBAG="$(ROSBAG)" CAPTURE="$(CAPTURE)" \
		SIMULATOR_SERVICE="$(SIMULATOR_SERVICE)" AUTOWARE_SERVICE="$(AUTOWARE_SERVICE)" \
		AW_CMD_SERVICE="$(AW_CMD_SERVICE)" ROSBAG_SERVICE="$(ROSBAG_SERVICE)" \
		DC="$(DC)" \
		bash aichallenge/utils/run_sim_eval.bash

# remote operation (docker compose up -d rviz2)
rviz2:
	$(DC) stop $(RVIZ2_SERVICE)
	$(DC) up -d $(RVIZ2_SERVICE)

autoware-rosbag:
	$(DC) up -d $(ROSBAG_SERVICE)


# driver + autoware + zenoh
autoware-driver-zenoh:
	RUN_MODE=vehicle $(DC) up -d driver $(AUTOWARE_SERVICE)
	sleep 15
	$(DC) up -d zenoh

down:
	$(DC) down --remove-orphans

ps:
	$(DC) ps
