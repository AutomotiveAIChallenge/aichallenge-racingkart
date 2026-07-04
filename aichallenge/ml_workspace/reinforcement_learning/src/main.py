#!/usr/bin/env python3
"""AWSIM RL entry point"""

from pathlib import Path

from config.load_config import load_config
from environment.awsim_env import AWSIMEnv
from select_parts import (
    select_action_adapter,
    select_algorithm,
    select_algorithm_class,
    select_context_manager,
    select_observation_builder,
    select_reward_function,
    select_termination_function,
    select_wrappers,
)

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--config',
        type=str,
        default=None,
        help='Optional YAML override config path (default: use Python defaults)',
    )
    parser.add_argument('--check', action='store_true', help='Run env checker')
    parser.add_argument('--train', action='store_true', help='Run SAC training')  #[change]
    parser.add_argument('--infer', action='store_true', help='Run inference with saved model')
    parser.add_argument('--model-path', type=str, default='awsim_sac_model',
                        help='Path to saved model (default: awsim_sac_model)')
    parser.add_argument('--episodes', type=int, default=5,
                        help='Number of episodes to run in inference (default: 5)')
    args = parser.parse_args()

    cfgs = load_config(args.config)

    # ログやモデルの保存場所を設定（configと同じディレクトリ下）
    config_base_dir = (
        Path(args.config).expanduser().resolve().parent
        if args.config
        else Path.cwd()
    )
    algorithm_cfg = dict(cfgs['algorithm'])
    algorithm_cfg['save_path'] = str(config_base_dir / 'model')
    algorithm_cfg['tensorboard_log'] = str(config_base_dir / 'log')

    # クラスの初期化
    context_cfg = cfgs['context_manager']
    action_cfg = cfgs['action_adapter']
    observation_cfg = cfgs['observation_builder']
    reward_cfg = cfgs['reward']
    termination_cfg = cfgs['termination']

    context_manager = select_context_manager(context_cfg)
    action_adapter = select_action_adapter(action_cfg)
    observation_builder = select_observation_builder(observation_cfg)
    reward_function = select_reward_function(reward_cfg)
    termination_function = select_termination_function(termination_cfg)

    # 環境の初期化
    env = AWSIMEnv(
        context_manager=context_manager,
        action_adapter=action_adapter,
        observation_builder=observation_builder,
        reward_function=reward_function,
        termination_function=termination_function,
    )

    # 環境Wrapper
    env = select_wrappers(algorithm_cfg, env)

    if args.check:
        from stable_baselines3.common.env_checker import check_env
        print("Checking environment...")
        check_env(env, warn=True)
        print("Check passed!")

    elif args.train:
        model = select_algorithm(algorithm_cfg, env)
        model.learn(
            total_timesteps=int(algorithm_cfg.get('total_timesteps', 300_000)),
            log_interval=int(algorithm_cfg.get('log_interval', 1)),
        )
        model.save(algorithm_cfg['save_path'])

    elif args.infer:
        # ============================================================
        # 推論モード
        # 保存済みモデルをロードしてAWSIM上で実行する
        # 使用例:
        #   ROS_DOMAIN_ID=1 python3 main.py --infer
        #   ROS_DOMAIN_ID=1 python3 main.py --infer --model-path ./awsim_sac_model --episodes 10
        # ============================================================
        print(f"Loading model from: {args.model_path}")
        algorithm_class = select_algorithm_class(algorithm_cfg)
        model = algorithm_class.load(args.model_path, env=env)
        print(f"Model loaded. Running {args.episodes} episode(s)...")

        for ep in range(args.episodes):
            obs, info = env.reset()
            ep_reward = 0.0
            ep_steps  = 0
            done = False

            print(f"=== Episode {ep + 1} / {args.episodes} ===")

            while not done:
                # deterministic=True: 確率的サンプリングではなく最良行動を選択
                action, _ = model.predict(obs, deterministic=True)
                obs, reward, terminated, truncated, info = env.step(action)

                ep_reward += reward
                ep_steps  += 1
                done = terminated or truncated

                print(
                    f"  step={ep_steps:4d} | "
                    f"reward={reward:7.3f} | "
                    f"speed={info['speed']:.2f} m/s | "
                    f"section={info['section']} | "
                    f"lap={info['lap_count']}"
                )

            print(
                f"Episode {ep + 1} finished: "
                f"total_reward={ep_reward:.2f}, steps={ep_steps}, "
                f"laps={info['lap_count']}, lap_time={info['lap_time']:.2f}s"
            )

    else:
        # デフォルト: 動作確認用ランダムステップ
        print("Running random action loop for sanity check...")
        obs, info = env.reset()
        print(f"Reset done. image shape={obs['image'].shape}, speed={obs['speed'][0]:.2f}")
        for i in range(200):
            action = env.action_space.sample()
            obs, reward, terminated, truncated, info = env.step(action)
            print(f"step={i+1:3d} | reward={reward:.3f} | speed={info['speed']:.2f} | terminated={terminated}")
            if terminated:
                print("Episode ended. Resetting...")
                obs, info = env.reset()

    env.close()
