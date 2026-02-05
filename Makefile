# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-build autoware-vehicle autoware-simulator autoware-request-initialpose autoware-request-control autoware-driver-zenoh \
	simulator simulator-reset dev driver zenoh download rviz2 down ps print-dc print-gpu-env

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

# Used by docker-compose.yml for build/eval artifact ownership.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID

ROSBAG ?= false
CAPTURE ?= false
DOMAIN_ID ?= 1
DOMAIN_IDS ?= $(DOMAIN_ID)
OUTPUT_ROOT ?= /output
# Output layout overrides (optional)
# - RUN_ID: run directory name under /output/ (default: timestamp)
# - RUN_GROUP: optional subdirectory under RUN_ID (e.g., submit name)
RUN_ID ?=
RUN_GROUP ?=

# autowareのbuildのみ
autoware-build:
	$(DC) run -T --rm --no-deps autoware-build

# run autoware for vehicle
autoware-vehicle:
	@echo "Start Autoware for Vehicle"
	RUN_MODE=vehicle $(DC) up -d autoware

# run autoware for simulator
autoware-simulator:
	@echo "Start Autoware for AWSIM"
	RUN_MODE=awsim DOMAIN_ID=$(DOMAIN_ID) $(DC) up -d autoware

# autoware command service
autoware-request-initialpose:
	CMD="env ROS_DOMAIN_ID=$(DOMAIN_ID) ros2 service call /set_initial_pose std_srvs/srv/Trigger '{}'" \
	$(DC) run --rm --no-deps autoware-command

autoware-request-control:
	@echo "Start control"
	CMD="env ROS_DOMAIN_ID=$(DOMAIN_ID) ros2 topic pub -1 /awsim/control_mode_request_topic std_msgs/msg/Bool '{data: true}'" \
	$(DC) run --rm --no-deps autoware-command

# run simulator (docker compose up -d simulator)
simulator:
	@echo "Start AWSIM"
	SIM_MODE=$(SIM_MODE) $(DC) up -d simulator

simulator-reset:
	@echo "Reset simulation"
	CMD="bash /aichallenge/utils/simulator_reset.bash $(DOMAIN_ID)" \
	$(DC) run --rm --no-deps autoware-command

# racing kart (docker compose up -d driver)
driver:
	$(DC) up -d driver

# zenoh (docker compose up -d zenoh)
zenoh:
	$(DC) up -d zenoh

dev:
	@echo "Start dev simulation (AWSIM + Autoware, DOMAIN_ID=$(DOMAIN_ID))"
	@$(MAKE) simulator SIM_MODE=dev
	@$(MAKE) autoware-simulator DOMAIN_ID=$(DOMAIN_ID)
	@echo "To stop: make down  (docker compose down --remove-orphans)"

# remote operation (docker compose up -d rviz2)
rviz2:
	$(DC) stop rviz2
	$(DC) up -d rviz2

# driver + autoware + zenoh
autoware-driver-zenoh:
	RUN_MODE=vehicle $(DC) up -d driver autoware
	sleep 15
	$(DC) up -d zenoh

down:
	$(DC) down --remove-orphans

down_all:
	sudo docker ps -aq | xargs -r sudo docker rm -f

ps:
	$(DC) ps

# Helpers for scripts: keep compose/GPU selection centralized in this Makefile.
print-dc:
	@echo "$(DC)"

print-gpu-env:
ifeq ($(GPU_ENABLED),1)
	@echo "export NVIDIA_VISIBLE_DEVICES=$(NVIDIA_VISIBLE_DEVICES)"
	@echo "export NVIDIA_DRIVER_CAPABILITIES=$(NVIDIA_DRIVER_CAPABILITIES)"
else
	@:
endif

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
