# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: download

# 既にコンテナが立ち上がっている場合はログの表示を行います。
# autowareのみ起動・ログ表示
autoware-vehicle:
	RUN_MODE=vehicle docker compose up -d autoware
	docker compose logs -f autoware


# racing kartの起動・ログ表示
driver:
	docker compose up -d driver
	docker compose logs -f driver

# zenohの起動・ログ表示
zenoh:
	docker compose up -d zenoh
	docker compose logs -f zenoh

run-full-kart-system:
	RUN_MODE=vehicle docker compose up -d driver
	docker compose up -d autoware
# 	$(MAKE) rosbag
	sleep 15
	docker compose up -d zenoh
	docker compose logs -f autoware driver zenoh 2>&1 | tee log/full_log.log | stdbuf -oL grep -E "(ERROR|WARN)"

# autowareのbuildのみ
build-autoware:
	HOST_UID=$(shell id -u) HOST_GID=$(shell id -g) docker compose up -d aic-build
	docker compose logs -f --tail="0" aic-build

# Download submission data by asking for credentials interactively
# Usage:
#   make download [SUBMISSION_ID=<id>]
# Usage (Only Admins):
#   make download [USER_ID=<id>] [SUBMISSION_ID=<id>]
download:
	@if [ -n "$(USER_ID)" ]; then \
		if [ -n "$(SUBMISSION_ID)" ]; then \
			../vehicle/download_submission.sh --output ../aichallenge/workspace/src/ --user-id $(USER_ID) --submission-id $(SUBMISSION_ID); \
		else \
			../vehicle/download_submission.sh --output ../aichallenge/workspace/src/ --user-id $(USER_ID); \
		fi; \
	else \
		if [ -n "$(SUBMISSION_ID)" ]; then \
			../vehicle/download_submission.sh --output ../aichallenge/workspace/src/ --submission-id $(SUBMISSION_ID); \
		else \
			../vehicle/download_submission.sh --output ../aichallenge/workspace/src/; \
		fi; \
	fi

autoware-sim:
	@echo "Start Autoware(AWSIM mode)"
	RUN_MODE=awsim docker compose up -d autoware
	docker compose logs -f autoware

ifeq ($(ROSBAG),true)
    ifeq ($(CAPTURE),true)
        # ROSBAG=true かつ CAPTURE=true の場合
        RUN_MODE_VAL := "--rosbag --capture"
        LOG_MSG := "--- Starting Evaluation (With ROSBAG & CAPTURE) ---"
    else
        # ROSBAG=true だが CAPTURE!=true の場合
        RUN_MODE_VAL := "--rosbag"
        LOG_MSG := "--- Starting Evaluation (With ROSBAG) ---"
    endif
else
    # 2. ROSBAG!=true のレシピ
    ifeq ($(CAPTURE),true)
        # ROSBAG!=true かつ CAPTURE=true の場合
        RUN_MODE_VAL := "--capture"
        LOG_MSG := "--- Starting Evaluation (no ROSBAG, With CAPTURE) ---"
    else
        # ROSBAG!=true かつ CAPTURE!=true の場合
        RUN_MODE_VAL := ""
        LOG_MSG := "--- Starting Evaluation (Live & Clean) ---"
    endif
endif

# make run-sim-eval ROSBAG=true CAPTURE=true
run-sim-eval:
	@echo $(LOG_MSG)
	@echo "RUN_MODE will be set to: $(RUN_MODE_VAL)"
	RUN_MODE=$(RUN_MODE_VAL) HOST_UID=$(shell id -u) HOST_GID=$(shell id -g) \
	docker compose up -d sim-eval
	docker compose logs -f sim-eval

# rvizの起動・ログ表示
rviz2:
	docker compose stop rviz2
	docker compose up -d rviz2
	docker compose logs -f rviz2

sim:
	@echo "Start AWSIM"
	docker compose up -d simulator
	docker compose logs -f simulator

init:
	CMD=python3 ./publish_initialpose.py \
	docker compose up -d aw-cmd
	docker compose logs -f aw-cmd
	
start:
	@echo "Start control"
	CMD="ros2 topic pub -t 10 /awsim/control_mode_request_topic std_msgs/msg/Bool '{data: true}'" \
	docker compose up -d aw-cmd
	docker compose logs -f aw-cmd
	
reset:
	@echo "Reset simulation"
	CMD="ros2 topic pub --once /aichallenge/awsim/reset std_msgs/msg/Empty {} | python3 ./publish_initialpose.py" \
	docker compose up -d aw-cmd
	docker compose logs -f aw-cmd

# aicコンテナにてros2 bag record -a
#rosbag:
#	docker compose up -d rosbag 
#	docker compose logs -f rosbag
