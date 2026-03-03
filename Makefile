# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-build autoware-vehicle autoware-simulator autoware-request-initialpose autoware-request-control autoware-driver-zenoh \
	simulator simulator-reset dev driver zenoh download rviz2 down ps

# Used by docker-compose.yml for build/eval artifact ownership.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID

DOMAIN_ID ?= 1
TIMESTAMP := $(shell date +%Y%m%d-%H%M%S)
LOG_DIR ?= /output/$(TIMESTAMP)/d$(DOMAIN_ID)

# autowareのbuildのみ
autoware-build:
	docker compose run -T --rm --no-deps autoware-build

# run autoware for vehicle
autoware-vehicle:
	@echo "Start Autoware for Vehicle"
	RUN_MODE=vehicle docker compose up -d autoware

# run autoware for simulator
autoware-simulator:
	@echo "Start Autoware for AWSIM"
	LOG_DIR=$(LOG_DIR) RUN_MODE=awsim DOMAIN_ID=$(DOMAIN_ID) docker compose up -d autoware

# autoware command service
autoware-request-initialpose:
	CMD="env ROS_DOMAIN_ID=$(DOMAIN_ID) ros2 service call /set_initial_pose std_srvs/srv/Trigger '{}'" \
	docker compose run --rm --no-deps autoware-command

autoware-request-control:
	@echo "Start control"
	CMD="env ROS_DOMAIN_ID=$(DOMAIN_ID) ros2 topic pub -1 /awsim/control_mode_request_topic std_msgs/msg/Bool '{data: true}'" \
	docker compose run --rm --no-deps autoware-command

# run simulator (docker compose up -d simulator)
simulator:
	@echo "Start AWSIM"
	LOG_DIR=$(LOG_DIR) SIM_MODE=dev docker compose up -d simulator

simulator-reset:
	@echo "Reset simulation"
	CMD="bash /aichallenge/utils/simulator_reset.bash 0" \
	docker compose run --rm --no-deps autoware-command

# racing kart (docker compose up -d driver)
driver:
	docker compose up -d driver

# zenoh (docker compose up -d zenoh)
zenoh:
	docker compose up -d zenoh

dev: simulator autoware-simulator
	@echo "Start dev simulation (AWSIM + Autoware, DOMAIN_ID=$(DOMAIN_ID))"
	@echo "To stop: make down  (docker compose down --remove-orphans)"

eval:
	@echo "Start evaluation simulation (AWSIM + Autoware, DOMAIN_ID=$(DOMAIN_ID))"
	docker compose up -d autoware-simulator-evaluation
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
	docker compose down --remove-orphans

down_all:
	sudo docker ps -aq | xargs -r sudo docker rm -f

ps:
	docker compose ps

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
