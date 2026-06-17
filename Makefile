# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-build autoware-vehicle autoware-simulator autoware-request-initialpose autoware-request-control  awsim-request-start awsim-request-reset autoware-driver-zenoh \
	simulator dev dev2 dev3 dev4 recovery-dev recovery-repro recovery-reverse-smoke recovery-supervisor-repro recovery-check driver zenoh download rviz2 down down2 down3 down4 ps autoware-bash

# Used by docker-compose.yml for build/eval artifact ownership.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID
# Stop host shell's ROS_DOMAIN_ID from overriding .env via compose interpolation,
# but still honor an explicit `make foo ROS_DOMAIN_ID=N` command-line override.
unexport ROS_DOMAIN_ID
ifeq ($(origin ROS_DOMAIN_ID),command line)
export ROS_DOMAIN_ID
endif

TIMESTAMP := $(shell date +%Y%m%d-%H%M%S)
LOG_DIR := /output/$(TIMESTAMP)
RECOVERY_PROFILE_INITIAL_DELAY_SEC ?= 15.0
RECOVERY_PROFILE_MAX_DURATION_SEC ?= 35.0
RECOVERY_SUPERVISOR_PARAM_FILE ?=
RECOVERY_SUPERVISOR_STUCK_SPEED_THRESHOLD ?=
RECOVERY_SUPERVISOR_STUCK_DURATION ?=
RECOVERY_SUPERVISOR_COMMAND_SPEED_THRESHOLD ?=
RECOVERY_SUPERVISOR_COMMAND_ACCEL_THRESHOLD ?=
RECOVERY_SUPERVISOR_MOVING_SPEED_THRESHOLD ?=
RECOVERY_SUPERVISOR_REVERSE_SPEED ?=
RECOVERY_SUPERVISOR_REVERSE_ACCEL ?=
RECOVERY_SUPERVISOR_REVERSE_DURATION ?=
RECOVERY_SUPERVISOR_DRIVE_SETTLE_DURATION ?=
RECOVERY_SUPERVISOR_COOLDOWN_DURATION ?=
RECOVERY_SUPERVISOR_MAX_RECOVERY_ATTEMPTS ?=
RECOVERY_SUPERVISOR_NOMINAL_TIMEOUT_SEC ?=
RECOVERY_SUPERVISOR_VELOCITY_TIMEOUT_SEC ?=
RECOVERY_SUPERVISOR_TIMER_HZ ?=
RECOVERY_SUPERVISOR_CMD_ENV = RECOVERY_SUPERVISOR=true RECOVERY_PROFILE_INITIAL_DELAY_SEC=$(RECOVERY_PROFILE_INITIAL_DELAY_SEC) RECOVERY_PROFILE_MAX_DURATION_SEC=$(RECOVERY_PROFILE_MAX_DURATION_SEC) RECOVERY_SUPERVISOR_PARAM_FILE="$(RECOVERY_SUPERVISOR_PARAM_FILE)" RECOVERY_SUPERVISOR_STUCK_SPEED_THRESHOLD="$(RECOVERY_SUPERVISOR_STUCK_SPEED_THRESHOLD)" RECOVERY_SUPERVISOR_STUCK_DURATION="$(RECOVERY_SUPERVISOR_STUCK_DURATION)" RECOVERY_SUPERVISOR_COMMAND_SPEED_THRESHOLD="$(RECOVERY_SUPERVISOR_COMMAND_SPEED_THRESHOLD)" RECOVERY_SUPERVISOR_COMMAND_ACCEL_THRESHOLD="$(RECOVERY_SUPERVISOR_COMMAND_ACCEL_THRESHOLD)" RECOVERY_SUPERVISOR_MOVING_SPEED_THRESHOLD="$(RECOVERY_SUPERVISOR_MOVING_SPEED_THRESHOLD)" RECOVERY_SUPERVISOR_REVERSE_SPEED="$(RECOVERY_SUPERVISOR_REVERSE_SPEED)" RECOVERY_SUPERVISOR_REVERSE_ACCEL="$(RECOVERY_SUPERVISOR_REVERSE_ACCEL)" RECOVERY_SUPERVISOR_REVERSE_DURATION="$(RECOVERY_SUPERVISOR_REVERSE_DURATION)" RECOVERY_SUPERVISOR_DRIVE_SETTLE_DURATION="$(RECOVERY_SUPERVISOR_DRIVE_SETTLE_DURATION)" RECOVERY_SUPERVISOR_COOLDOWN_DURATION="$(RECOVERY_SUPERVISOR_COOLDOWN_DURATION)" RECOVERY_SUPERVISOR_MAX_RECOVERY_ATTEMPTS="$(RECOVERY_SUPERVISOR_MAX_RECOVERY_ATTEMPTS)" RECOVERY_SUPERVISOR_NOMINAL_TIMEOUT_SEC="$(RECOVERY_SUPERVISOR_NOMINAL_TIMEOUT_SEC)" RECOVERY_SUPERVISOR_VELOCITY_TIMEOUT_SEC="$(RECOVERY_SUPERVISOR_VELOCITY_TIMEOUT_SEC)" RECOVERY_SUPERVISOR_TIMER_HZ="$(RECOVERY_SUPERVISOR_TIMER_HZ)"

# make simulator-<mode>: <mode> は simulator_scripts/*.sh のファイル名
SIM_MODES := $(notdir $(basename $(wildcard aichallenge/simulator_scripts/*.sh)))
# dev<N>（車両数）/ gate<N>（テスト番号）は run_simulator.bash が展開するエイリアス
SIM_MODES += dev2 dev3 dev4 gate1 gate2 gate3
.PHONY: $(addprefix simulator-,$(SIM_MODES))
$(addprefix simulator-,$(SIM_MODES)): simulator-%:
	@$(MAKE) simulator SIM_MODE=$*

# autowareのbuildのみ
autoware-build:
	docker compose run -T --rm --no-deps autoware-build

# run autoware for vehicle
autoware-vehicle:
	@echo "Start Autoware for Vehicle"
	LOG_DIR=$(LOG_DIR) RUN_MODE=vehicle docker compose up -d autoware

# run autoware for simulator
autoware-simulator:
	@echo "Start Autoware for AWSIM"
	LOG_DIR=$(LOG_DIR) RUN_MODE=awsim docker compose up -d autoware

# autoware command service use ROS_DOMAIN_ID from .env
autoware-request-initialpose:
	CMD="ros2 service call /set_initial_pose std_srvs/srv/Trigger '{}'" docker compose run --rm --no-deps autoware-command

autoware-request-control:
	CMD="ros2 topic pub -1 /awsim/control_mode_request_topic std_msgs/msg/Bool '{data: true}'" docker compose run --rm --no-deps autoware-command

# awsim admin service use ROS_DOMAIN_ID 0
awsim-request-start:
	CMD="env ROS_DOMAIN_ID=0 ros2 topic pub -1 /admin/awsim/start std_msgs/msg/Bool '{data: true}'" docker compose run --rm --no-deps autoware-command

awsim-request-reset:
	CMD="env ROS_DOMAIN_ID=0 ros2 topic pub -1 /admin/awsim/reset std_msgs/msg/Empty '{}'" docker compose run --rm --no-deps autoware-command

# run simulator (docker compose up -d simulator)
simulator:
	@echo "Start AWSIM (SIM_MODE=$(SIM_MODE))"
	LOG_DIR=$(LOG_DIR) SIM_MODE="$(SIM_MODE)" ROS_DOMAIN_ID=0 docker compose up -d simulator

# racing kart (docker compose up -d driver)
driver:
	docker compose up -d driver

# zenoh (docker compose up -d zenoh)
zenoh:
	docker compose up -d zenoh

dev: SIM_MODE := dev
dev: simulator autoware-simulator
	@echo "Start dev simulation (AWSIM + Autoware)"
	@echo "To stop: make down  (docker compose down --remove-orphans)"

recovery-dev: SIM_MODE := recovery-dev
recovery-dev: simulator autoware-simulator
	@echo "Start recovery dev simulation (AWSIM wall-recovery off + Autoware)"
	@echo "To stop: make down  (docker compose down --remove-orphans)"

recovery-repro: SIM_MODE := recovery-dev
recovery-repro:
	@echo "Start recovery repro (stuck under throttle + rosbag + stuck_under_throttle check)"
	LOG_DIR=$(LOG_DIR) SIM_MODE="$(SIM_MODE)" ROS_DOMAIN_ID=0 docker compose up -d simulator
	LOG_DIR=$(LOG_DIR) RUN_MODE=awsim-no-control docker compose up -d autoware
	CMD='RECOVERY_PROFILE_INITIAL_DELAY_SEC=$(RECOVERY_PROFILE_INITIAL_DELAY_SEC) RECOVERY_OUTPUT_DIR=$(LOG_DIR)/recovery bash /aichallenge/utils/run_recovery_profile.bash stuck_repro stuck_under_throttle' docker compose run --rm --no-deps autoware-command

recovery-reverse-smoke: SIM_MODE := recovery-dev
recovery-reverse-smoke:
	@echo "Start recovery reverse smoke (reverse gear + rosbag + reverse_capable check)"
	LOG_DIR=$(LOG_DIR) SIM_MODE="$(SIM_MODE)" ROS_DOMAIN_ID=0 docker compose up -d simulator
	LOG_DIR=$(LOG_DIR) RUN_MODE=awsim-no-control docker compose up -d autoware
	CMD='RECOVERY_PROFILE_INITIAL_DELAY_SEC=$(RECOVERY_PROFILE_INITIAL_DELAY_SEC) RECOVERY_OUTPUT_DIR=$(LOG_DIR)/recovery bash /aichallenge/utils/run_recovery_profile.bash reverse_smoke reverse_capable' docker compose run --rm --no-deps autoware-command

recovery-supervisor-repro: SIM_MODE := recovery-dev
recovery-supervisor-repro:
	@echo "Start recovery supervisor repro (stuck + supervisor reverse + recovered check)"
	LOG_DIR=$(LOG_DIR) SIM_MODE="$(SIM_MODE)" ROS_DOMAIN_ID=0 docker compose up -d simulator
	LOG_DIR=$(LOG_DIR) RUN_MODE=awsim-no-control docker compose up -d autoware
	CMD='$(RECOVERY_SUPERVISOR_CMD_ENV) RECOVERY_OUTPUT_DIR=$(LOG_DIR)/recovery bash /aichallenge/utils/run_recovery_profile.bash stuck_repro recovered' docker compose run --rm --no-deps autoware-command

EXPECT ?= stuck_under_throttle
recovery-check:
	@if [ -z "$(BAG)" ]; then echo "Usage: make recovery-check BAG=/output/<run>/recovery/rosbag2_recovery [EXPECT=stuck_under_throttle]"; exit 2; fi
	@bag="$(BAG)"; \
	case "$$bag" in output/*) bag="/$$bag" ;; ./output/*) bag="/$${bag#./}" ;; esac; \
	CMD="ros2 run recovery_test_tools analyze_recovery_bag.py $$bag --expect $(EXPECT)" docker compose run --rm --no-deps autoware-command

dev2: SIM_MODE := dev2
dev3: SIM_MODE := dev3
dev4: SIM_MODE := dev4
dev2 dev3 dev4: simulator
	@N=$(@:dev%=%); \
	echo "Start $$N-vehicle dev (autoware on ROS_DOMAIN_ID 1..$$N via docker compose -p)"; \
	for p in $$(seq 1 $$N); do LOG_DIR=$(LOG_DIR) ROS_DOMAIN_ID=$$p docker compose -p $$p up -d autoware; done; \
	echo "To Stop: make down"

gate1: SIM_MODE := gate1
gate2: SIM_MODE := gate2
gate3: SIM_MODE := gate3
gate1 gate2 gate3: simulator autoware-simulator
	@echo "Start safety gate simulation (AWSIM + Autoware)"
	@echo "To stop: make down  (docker compose down --remove-orphans)"

# Kept for backward compatibility; `make down` already cleans all projects.
down2 down3 down4: down

eval:
eval:
	@echo "Start evaluation simulation (AWSIM + Autoware)"
	docker compose up -d autoware-simulator-evaluation
	$(MAKE) awsim-request-start
	@echo "To stop: make down  (docker compose down --remove-orphans)"
# remote operation (docker compose up -d rviz2)
rviz2:
	docker compose stop rviz2
	docker compose up -d rviz2

# driver + autoware + zenoh
autoware-driver-zenoh:
	RUN_MODE=vehicle docker compose up -d driver autoware
	sleep 15
	docker compose up -d zenoh

down:
	@for p in 1 2 3 4; do docker compose -p $$p down --remove-orphans; done
	@docker compose down --remove-orphans

down_all:
	sudo docker ps -aq | xargs -r sudo docker rm -f

ps:
	@docker compose ps
	@for p in 1 2 3 4; do \
		out=$$(docker compose -p $$p ps --format '{{.Name}}\t{{.Service}}\t{{.Status}}' 2>/dev/null); \
		if [ -n "$$out" ]; then \
			echo "--- project=$$p ---"; \
			echo "$$out"; \
		fi; \
	done

autoware-bash:
	@if [ -z "$(VEHICLE_NUM)" ]; then \
		docker compose exec autoware bash; \
	else \
		docker compose -p $(VEHICLE_NUM) exec autoware bash; \
	fi

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
