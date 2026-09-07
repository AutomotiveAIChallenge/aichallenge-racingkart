# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-build autoware-vehicle autoware-simulator autoware-request-initialpose autoware-request-control  awsim-request-start awsim-request-reset autoware-driver-zenoh autoware-driver-zenoh-rosbag setup-vehicle \
	simulator dev dev2 dev3 dev4 driver zenoh download rviz2 down down_all ps autoware-attach autoware-bash eval e2e vehicle-tui \
	prestage-build prestage-stage prestage-unstage prestage-test prestage-image-export prestage-image-import

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

# make simulator-<mode>: <mode> は simulator_scripts/*.sh のファイル名
SIM_MODES := $(notdir $(basename $(wildcard aichallenge/simulator_scripts/*.sh)))
# dev<N>（車両数、2..4）は run_simulator.bash が展開するエイリアス
DEV_NS := 2 3 4
SIM_MODES += $(addprefix dev,$(DEV_NS))
.PHONY: $(addprefix simulator-,$(SIM_MODES))
$(addprefix simulator-,$(SIM_MODES)): simulator-%:
	@$(MAKE) simulator SIM_MODE=$*

# gate<N>（テスト番号、任意）も run_simulator.bash が展開するエイリアス
simulator-gate%:
	@$(MAKE) simulator SIM_MODE=gate$*

# autowareのbuildのみ
autoware-build:
	docker compose run -T --rm --no-deps autoware-build

# run autoware for vehicle
autoware-vehicle:
	@echo "Start Autoware for Vehicle"
	@echo "Log dir: .$(LOG_DIR)"
	LOG_DIR=$(LOG_DIR) RUN_MODE=vehicle docker compose up -d autoware

# run autoware for simulator
autoware-simulator:
	@echo "Start Autoware for AWSIM"
	@echo "Log dir: .$(LOG_DIR)"
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
	@echo "Log dir: .$(LOG_DIR)"
	LOG_DIR=$(LOG_DIR) SIM_MODE="$(SIM_MODE)" ROS_DOMAIN_ID=0 docker compose up -d simulator

# racing kart (docker compose up -d driver)
driver:
	docker compose up -d driver

# zenoh (docker compose up -d zenoh)
zenoh:
	docker compose up -d zenoh

dev: SIM_MODE := dev
dev: simulator autoware-simulator

# dev<N>: N台並列（autoware を compose -p 1..N / ROS_DOMAIN_ID=1..N で起動）
$(addprefix dev,$(DEV_NS)): dev%:
	@$(MAKE) simulator SIM_MODE=$@ LOG_DIR=$(LOG_DIR)
	@for p in $$(seq 1 $*); do LOG_DIR=$(LOG_DIR) ROS_DOMAIN_ID=$$p docker compose -p $$p up -d autoware; done

# e2e は練習兼提出参考モード（e2e.sh）。e2e-final.sh は make simulator-e2e-final。
e2e: SIM_MODE := e2e
e2e: simulator autoware-simulator
	@echo "Start e2e simulation (AWSIM + Autoware)"
	@echo "To stop: make down  (docker compose down --remove-orphans)"

# gate<N>: 任意のテスト番号を受け付ける（例: make gate7）
gate%:
	@$(MAKE) simulator SIM_MODE=$@ LOG_DIR=$(LOG_DIR)
	@$(MAKE) autoware-simulator LOG_DIR=$(LOG_DIR)

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
	LOG_DIR=$(LOG_DIR) RUN_MODE=vehicle docker compose up -d driver autoware
	sleep 15
	LOG_DIR=$(LOG_DIR) docker compose up -d zenoh

setup-vehicle:
	@echo "Run vehicle setup check"
	@cd vehicle && ./setup_check.sh

# driver + autoware + all-topic rosbag + zenoh
autoware-driver-zenoh-rosbag:
	@echo "Run vehicle setup preflight check"
	@cd vehicle && ./setup_check.sh --phase preflight
	LOG_DIR=$(LOG_DIR) RUN_MODE=vehicle docker compose up -d driver autoware rosbag
	sleep 15
	LOG_DIR=$(LOG_DIR) docker compose up -d zenoh

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

autoware-attach:
	@./docker_exec.sh

autoware-bash:
	CMD="bash --rcfile /etc/skel/.bashrc -i" docker compose run --rm --no-deps autoware-command

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

# 車両 PC 上の操作コンソール。tmux 常駐なので ssh が切れても作業が残り、
# 再接続して同じターゲットを叩けば -A で同じセッションへアタッチする。
vehicle-tui:
	tmux new -A -s aic-vehicle "vehicle/tui.py"

# Prestage submissions into an encrypted vault (organiser machine).
# Usage: make prestage-build VAULT=<cipherdir> TEAMS=<teams.tsv>
prestage-build:
	@[ -n "$(VAULT)" ] || { echo "VAULT=<cipherdir> is required"; exit 2; }
	@[ -n "$(TEAMS)" ] || { echo "TEAMS=<teams.tsv> is required"; exit 2; }
	vehicle/prestage/prestage_all.sh --vault $(VAULT) --teams $(TEAMS)

# Stage one team's prebuilt workspace (vehicle PC, organiser only).
# Usage: make prestage-stage VAULT=<cipherdir> TEAM=<team_id>
prestage-stage:
	@[ -n "$(VAULT)" ] || { echo "VAULT=<cipherdir> is required"; exit 2; }
	@[ -n "$(TEAM)" ] || { echo "TEAM=<team_id> is required"; exit 2; }
	vehicle/prestage/stage_team.sh --vault $(VAULT) $(TEAM)

# Wipe the staged submission after a slot.
# Usage: make prestage-unstage [KEEP_OUTPUT=<dir>]
prestage-unstage:
	@if [ -n "$(KEEP_OUTPUT)" ]; then \
		vehicle/prestage/unstage_team.sh --keep-output $(KEEP_OUTPUT); \
	else \
		vehicle/prestage/unstage_team.sh; \
	fi

prestage-test:
	vehicle/prestage/tests/run_all.sh

# Ship the dev image to the other PCs (stage_team.sh checks the image ID).
# Usage: make prestage-image-export IMAGE_TAR=/path/aichallenge-2025-dev.tar.zst
prestage-image-export:
	@[ -n "$(IMAGE_TAR)" ] || { echo "IMAGE_TAR=<file.tar.zst> is required"; exit 2; }
	vehicle/prestage/image_transfer.sh export $(IMAGE_TAR)

prestage-image-import:
	@[ -n "$(IMAGE_TAR)" ] || { echo "IMAGE_TAR=<file.tar.zst> is required"; exit 2; }
	vehicle/prestage/image_transfer.sh import $(IMAGE_TAR)
