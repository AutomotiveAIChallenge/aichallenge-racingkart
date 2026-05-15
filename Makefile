# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-build autoware-vehicle autoware-simulator autoware-request-initialpose autoware-request-control  autoware-request-start autoware-driver-zenoh \
	simulator simulator-reset dev dev2 dev3 dev4 driver zenoh download rviz2 down down2 down3 down4 ps autoware-bash

# Used by docker-compose.yml for build/eval artifact ownership.
HOST_UID ?= $(shell id -u)
HOST_GID ?= $(shell id -g)
export HOST_UID HOST_GID

ROS_DOMAIN_ID := 1
TIMESTAMP := $(shell date +%Y%m%d-%H%M%S)
LOG_DIR := /output/$(TIMESTAMP)/d$(ROS_DOMAIN_ID)

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
	LOG_DIR=$(LOG_DIR) RUN_MODE=awsim ROS_DOMAIN_ID=$(ROS_DOMAIN_ID) docker compose up -d autoware

# autoware command service
autoware-request-initialpose:
	CMD="env ROS_DOMAIN_ID=$(ROS_DOMAIN_ID) ros2 service call /set_initial_pose std_srvs/srv/Trigger '{}'" docker compose run --rm --no-deps autoware-command

autoware-request-control:
	CMD="env ROS_DOMAIN_ID=$(ROS_DOMAIN_ID) ros2 topic pub -1 /awsim/control_mode_request_topic std_msgs/msg/Bool '{data: true}'" docker compose run --rm --no-deps autoware-command

autoware-request-start:
	CMD="env ROS_DOMAIN_ID=0 ros2 topic pub -1 /admin/awsim/start std_msgs/msg/Bool '{data: true}'" docker compose run --rm --no-deps autoware-command

# run simulator (docker compose up -d simulator)
simulator:
	@echo "Start AWSIM (SIM_MODE=$(SIM_MODE))"
	LOG_DIR=$(LOG_DIR) SIM_MODE=$(SIM_MODE) ROS_DOMAIN_ID=0 docker compose up -d simulator

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

dev: SIM_MODE := dev
dev: simulator autoware-simulator
	@echo "Start dev simulation (AWSIM + Autoware, ROS_DOMAIN_ID=$(ROS_DOMAIN_ID))"
	@echo "To stop: make down  (docker compose down --remove-orphans)"

dev2: SIM_MODE := 2p
dev3: SIM_MODE := 3p
dev4: SIM_MODE := 4p
dev2 dev3 dev4: simulator
	@N=$(@:dev%=%); \
	echo "Start $$N-vehicle dev (autoware on ROS_DOMAIN_ID 1..$$N via docker compose -p)"; \
	for p in $$(seq 1 $$N); do LOG_DIR=/output/$(TIMESTAMP)/d$$p ROS_DOMAIN_ID=$$p docker compose -p $$p up -d autoware; done; \
	$(MAKE) autoware-request-start; \
	echo "To Stop: make down"

# Kept for backward compatibility; `make down` already cleans all projects.
down2 down3 down4: down

eval:
	@echo "Start evaluation simulation (AWSIM + Autoware, ROS_DOMAIN_ID=$(ROS_DOMAIN_ID))"
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
