# make file inspired by https://roborovsky-racers.github.io/RoborovskyNote/
SHELL := /bin/bash

.PHONY: autoware-build autoware-vehicle autoware-simulator autoware-request-initialpose autoware-request-control  awsim-request-start awsim-request-reset autoware-driver-zenoh autoware-driver-zenoh-rosbag setup-vehicle \
	autoware-down autoware-restart workspace-clean \
	simulator dev dev2 dev3 dev4 driver zenoh download submission-extract driver-zenoh-rosbag rviz2 down down_all ps autoware-attach autoware-bash eval e2e vehicle-tui vehicle-tui-staff workspace

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
# ホスト側の colcon ワークスペース。workspace-clean が消す対象の親。
WORKSPACE_DIR := aichallenge/workspace

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

# autoware コンテナだけを停止して削除する。driver / zenoh / rosbag は動かしたまま
# 残すので、走行枠中にビルドし直した Autoware を入れ替えるときはこれと
# autoware-restart を使う。スタックごと落とすのは make down。
autoware-down:
	docker compose down autoware

# autoware コンテナだけを入れ替える。削除 -> vehicle モードで起動し直す。
autoware-restart: autoware-down autoware-vehicle

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

# 運営が走行枠の最初に上げる土台: driver + all-topic rosbag + zenoh（autoware は参加者が autoware-vehicle で上げる）
driver-zenoh-rosbag:
	LOG_DIR=$(LOG_DIR) docker compose up -d driver rosbag
	sleep 15
	LOG_DIR=$(LOG_DIR) docker compose up -d zenoh

# driver + autoware + all-topic rosbag + zenoh
autoware-driver-zenoh-rosbag:
	@echo "Run vehicle setup preflight check"
	@cd vehicle && ./setup_check.sh --phase preflight
	LOG_DIR=$(LOG_DIR) RUN_MODE=vehicle docker compose up -d driver autoware rosbag
	sleep 15
	LOG_DIR=$(LOG_DIR) docker compose up -d zenoh

# ワークスペースを checkout 直後の状態へ戻す。提出物で上書きされた src/aichallenge_submit/ を
# HEAD に戻し（index も含めて。`git checkout -- <path>` は index から戻すので、stage 済みの
# 提出物が残る）、build/ install/ log/ と untracked ファイルを消す。git 操作はこのディレクトリに
# 限定する（git stash のようにリポジトリ全体へ効かせると .env 以外のローカル変更も巻き込む）。
# 次は make submission-extract からやり直すことになる。
workspace-clean:
	@echo "Clean $(WORKSPACE_DIR): restore src/aichallenge_submit/, remove build/ install/ log/ and untracked files"
	git restore --source=HEAD --staged --worktree -- $(WORKSPACE_DIR)/src/aichallenge_submit
	git clean -fdx $(WORKSPACE_DIR)

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

# 事前に置いた vehicle/submissions/<id>.zip（パスワード付き）で src/aichallenge_submit/ を入れ替える。
# ID とパスワードは対話で聞く（SUBMISSION_ID で ID を先渡し可）。
submission-extract:
	vehicle/extract_submission.py $(if $(SUBMISSION_ID),--id $(SUBMISSION_ID))

# 車両 PC 上の操作コンソール。tmux 常駐なので ssh が切れても作業が残り、
# 再接続して同じターゲットを叩けば -A で同じセッションへアタッチする。
# 参加者用（autoware と提出物のステップだけ）。
vehicle-tui:
	tmux new -A -s aic-vehicle "vehicle/tui.py"

# 運営用。driver / zenoh / rosbag の起動、download、down all も出す。tmux セッションを分けるので
# 参加者の aic-vehicle セッションが残っていても運営側の画面になる。
vehicle-tui-staff:
	tmux new -A -s aic-vehicle-staff "vehicle/tui.py --role staff"

# 遠隔操作用ワークスペース。terminator を 4 分割 (車両 ssh×3 + remote/gui_tools.py) で開く。
workspace:
	remote/workspace.bash
