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
SIM_EVAL_SERVICE := sim-eval
AW_CMD_SERVICE := aw-cmd
RVIZ2_SERVICE := rviz2

ifeq ($(GPU_ENABLED),1)
AUTOWARE_SERVICE := autoware-gpu
AIC_BUILD_SERVICE := aic-build-gpu
SIMULATOR_SERVICE := simulator-gpu
SIM_EVAL_SERVICE := sim-eval-gpu
AW_CMD_SERVICE := aw-cmd-gpu
RVIZ2_SERVICE := rviz2-gpu
endif

# Used by docker-compose.yml for build/eval artifact ownership.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID

# run_evaluation.bash options (passed via RUN_MODE for docker compose interpolation)
# Usage:
#   make run-sim-eval [ROSBAG=true] [CAPTURE=true] [DOMAIN_ID=1] [OUTPUT_ROOT=/output] [RESULT_WAIT_SECONDS=10]
ROSBAG ?= false
CAPTURE ?= false
DOMAIN_ID ?= 1
OUTPUT_ROOT ?= /output
RESULT_WAIT_SECONDS ?= 10

EVAL_ARGS := $(strip \
	$(if $(filter true,$(ROSBAG)),--rosbag,) \
	$(if $(filter true,$(CAPTURE)),--capture,) \
	--domain-id $(DOMAIN_ID) \
	--output-root $(OUTPUT_ROOT) \
	--result-wait-seconds $(RESULT_WAIT_SECONDS) \
)

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
	@echo "--- Starting Evaluation ---"
	@echo "RUN_MODE=$(EVAL_ARGS)"
	RUN_MODE="$(EVAL_ARGS)" $(DC) up -d $(SIM_EVAL_SERVICE)

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
