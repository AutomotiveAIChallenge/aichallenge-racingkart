# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-build autoware-vehicle autoware-simulator autoware-init autoware-start autoware-driver-zenoh \
	simulator simulator-reset driver zenoh download simulator-eval simulator-eval-1-4 rviz2 down ps

# GPU selection:
# - DEVICE=auto (default): enable GPU override if NVIDIA is detected
# - DEVICE=gpu: force GPU override
# - DEVICE=cpu: never use GPU override
DEVICE ?= auto
HAVE_NVIDIA := $(shell command -v nvidia-smi >/dev/null 2>&1 && [ -e /dev/nvidia0 ] && echo 1 || echo 0)

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
ROSBAG_SERVICE := rosbag

AIC_BUILD_SERVICE := autoware-build
RVIZ2_SERVICE := rviz2

# Used by docker-compose.yml for build/eval artifact ownership.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID

# Evaluation options (compatible with run_evaluation.bash)
# Usage:
#   make simulator-eval [ROSBAG=true] [CAPTURE=true] [DOMAIN_ID=1] [OUTPUT_ROOT=/output] [RESULT_WAIT_SECONDS=10]
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
	RUN_MODE=awsim $(DC) up -d $(AUTOWARE_SERVICE)

# autoware command service
autoware-init:
	CMD="env ROS_DOMAIN_ID=$(DOMAIN_ID) /aichallenge/utils/publish.bash request-initialpose" \
	$(DC) up -d $(AW_CMD_SERVICE)

autoware-start:
	@echo "Start control"
	CMD="env ROS_DOMAIN_ID=$(DOMAIN_ID) /aichallenge/utils/publish.bash request-control" \
	$(DC) up -d $(AW_CMD_SERVICE)

# run simulator
simulator:
	@echo "Start AWSIM"
	$(DC) up -d $(SIMULATOR_SERVICE)

simulator-reset:
	@echo "Reset simulation"
	CMD="bash /aichallenge/utils/simulator_reset.bash $(DOMAIN_ID)" \
	$(DC) up -d $(AW_CMD_SERVICE)

# racing kart
driver:
	$(DC) up -d driver

# zenoh
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

# make simulator-eval ROSBAG=true CAPTURE=true
simulator-eval:
	@RUN_ID="$(RUN_ID)" RUN_GROUP="$(RUN_GROUP)" \
		OUTPUT_ROOT="$(OUTPUT_ROOT)" DOMAIN_IDS="$(DOMAIN_IDS)" RESULT_WAIT_SECONDS="$(RESULT_WAIT_SECONDS)" \
		ROSBAG="$(ROSBAG)" CAPTURE="$(CAPTURE)" \
		SIMULATOR_SERVICE="$(SIMULATOR_SERVICE)" AUTOWARE_SERVICE="$(AUTOWARE_SERVICE)" \
		AW_CMD_SERVICE="$(AW_CMD_SERVICE)" ROSBAG_SERVICE="$(ROSBAG_SERVICE)" \
		DC="$(DC)" \
		bash aichallenge/utils/run_sim_eval.bash

simulator-eval-1-4:
	@$(MAKE) simulator-eval DOMAIN_IDS=1,2,3,4

# remote operation
rviz2:
	$(DC) stop $(RVIZ2_SERVICE)
	$(DC) up -d $(RVIZ2_SERVICE)


# driver + autoware + zenoh
autoware-driver-zenoh:
	RUN_MODE=vehicle $(DC) up -d driver $(AUTOWARE_SERVICE)
	sleep 15
	$(DC) up -d zenoh

down:
	$(DC) down --remove-orphans

ps:
	$(DC) ps
