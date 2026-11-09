from typing import Tuple
import collections
import time
import numpy as np
import osqp
from scipy import sparse
import matplotlib.pyplot as plt

# Colors
PREDICTION = '#BA4A00'


class SolverStats:
    """Aggregate OSQP diagnostics and print a summary every `window` control calls."""

    def __init__(self, window: int = 400):
        self.window = window
        self.reset()

    def reset(self):
        self.calls = 0
        self.status = collections.Counter()
        self.iters = []
        self.times_ms = []
        self.max_raw_delta_deg = 0.0
        self.fallbacks = 0
        self.rejected = 0
        self.setups = 0

    def record(self, status: str, iters: int, t_ms: float):
        self.status[status] += 1
        self.iters.append(iters)
        self.times_ms.append(t_ms)

    def end_call(self, raw_delta: float):
        self.calls += 1
        self.max_raw_delta_deg = max(self.max_raw_delta_deg, abs(np.rad2deg(raw_delta)))
        if self.calls >= self.window:
            it = np.array(self.iters) if self.iters else np.zeros(1)
            tm = np.array(self.times_ms) if self.times_ms else np.zeros(1)
            print(f"[mpc_stats] calls={self.calls} solves={len(self.iters)} status={dict(self.status)} "
                  f"iter_mean={it.mean():.0f} iter_max={it.max():.0f} "
                  f"t_mean={tm.mean():.2f}ms t_p99={np.percentile(tm, 99):.2f}ms "
                  f"max_raw_delta={self.max_raw_delta_deg:.1f}deg fallbacks={self.fallbacks} "
                  f"rejected={self.rejected} setups={self.setups}", flush=True)
            self.reset()

##################
# MPC Controller #
##################

class _SolveFailed(Exception):
    pass


class MPC:
    # ホライズン内のステップ間時間 delta_s / v_ref を計算する際の速度下限 [m/s]
    MIN_V_FOR_DT = 1.0
    # 受け入れる OSQP ステータス（解ベクトル自体は別途有限性と制約違反を検査する）
    ACCEPTED_STATUS = ('solved', 'solved inaccurate', 'maximum iterations reached')
    # 舵角(曲率)の箱制約違反をどこまで許容するか（相対値）。超えたら解を棄却する。
    INPUT_VIOLATION_TOL = 0.05

    def __init__(self, model, N, Q, R, QN, StateConstraints, InputConstraints,
                 ay_max, max_steering_rate, wp_id_offset, use_obstacle_avoidance, use_path_constraints_topic, use_max_kappa_pred=True):
        """
        Constructor for the Model Predictive Controller.
        :param model: bicycle model object to be controlled
        :param N: time horizon | int
        :param Q: state cost matrix
        :param R: input cost matrix
        :param QN: final state cost matrix
        :param StateConstraints: dictionary of state constraints
        :param InputConstraints: dictionary of input constraints
        :param ay_max: maximum allowed lateral acceleration in curves
        :param wp_id_offset: offset for waypoint id to consider control delay
        :param use_obstacle_avoidance: flag to enable obstacle avoidance
        :param use_path_constraints_topic: flag to use path constraints from topic
        :param max_steering_rate: maximum allowed steering rate in rad/s
        """
        # 既存の初期化パラメータ
        self.N = N
        self.Q = Q
        self.R = R
        self.QN = QN
        self.wp_id_offset = wp_id_offset
        self.use_obstacle_avoidance = use_obstacle_avoidance
        self.use_path_constraints_topic = use_path_constraints_topic
        self.model = model
        self.nx = self.model.n_states
        self.nu = 2
        self.state_constraints = StateConstraints
        self.input_constraints = InputConstraints
        self.ay_max = ay_max

        # 追加: ステアリングレート制限関連のパラメータ
        self.max_steering_rate = max_steering_rate
        self.previous_steering = 0.0  # 前回のステア角

        # 追加: ay_maxによる速度制限の方式切り替え
        self.use_max_kappa_pred = use_max_kappa_pred
        # 既存の初期化
        self.current_prediction = None
        self.infeasibility_counter = 0
        self.last_solved_wp_id = 0
        self.current_control = np.zeros((self.nu*self.N))
        self.optimizer = osqp.OSQP()
        self.stats = SolverStats()
        self._qp_P = None
        self._qp_A = None

        if not self.use_obstacle_avoidance:
            self.model.reference_path.update_simple_path_constraints(
                N,
                self.model.safety_margin)

    def update_v_max(self, v_max: float):
        self.input_constraints['umax'][0] = v_max

    def update_ay_max(self, ay_max: float):
        self.ay_max = ay_max

    def update_wp_id_offset(self, wp_id_offset: int):
        self.wp_id_offset = wp_id_offset

    def update_Q(self, Q: np.ndarray):
        self.Q = Q

    def update_R(self, R: np.ndarray):
        self.R = R

    def update_QN(self, QN: np.ndarray):
        self.QN = QN

    def _init_problem(self, N, safety_margin):
        """
        Initialize optimization problem for current time step with steering rate constraints.
        """
        # 既存の制約設定
        umin = self.input_constraints['umin']
        umax = self.input_constraints['umax']
        xmin = self.state_constraints['xmin']
        xmax = self.state_constraints['xmax']

        # Precompute common terms
        nx_N = self.nx * (N + 1)
        nu_N = self.nu * N

        # LTV System Matrices (per-step dense blocks; assembled with a fixed sparsity
        # pattern in _build_constraint_matrix so that OSQP can be updated in place)
        A_blocks = []
        B_blocks = []
        # Time between consecutive horizon steps (the horizon is spatial: delta_s per step)
        dt_steps = np.zeros(N)

        # Reference vector
        ur = np.zeros(nu_N)
        xr = np.zeros(nx_N)
        uq = np.zeros(N * self.nx)

        # Dynamic constraints
        xmin_dyn = np.kron(np.ones(N + 1), xmin)
        xmax_dyn = np.kron(np.ones(N + 1), xmax)
        umax_dyn = np.kron(np.ones(N), umax)

        # Get curvature predictions
        kappa_pred = np.tan(np.append(np.array(self.current_control[3::self.nu]), self.current_control[-1])) / self.model.length

        # Consider control delay
        self.model.wp_id += self.wp_id_offset

        # Iterate over horizon
        for n in range(N):
            # Get waypoint information
            current_waypoint = self.model.reference_path.get_waypoint(self.model.wp_id + n)
            next_waypoint = self.model.reference_path.get_waypoint(self.model.wp_id + n + 1)
            delta_s = next_waypoint - current_waypoint
            kappa_ref = current_waypoint.kappa

            # Clip reference velocity
            v_ref = np.clip(current_waypoint.v_ref, self.input_constraints['umin'][0], self.input_constraints['umax'][0])

            # Compute LTV matrices
            f, A_lin, B_lin = self.model.linearize(v_ref, kappa_ref, delta_s)
            A_blocks.append(np.asarray(A_lin, dtype=float))
            B_blocks.append(np.asarray(B_lin, dtype=float))
            dt_steps[n] = delta_s / max(v_ref, self.MIN_V_FOR_DT)

            # Set reference
            ur[n*self.nu:(n+1)*self.nu] = [v_ref, kappa_ref]
            uq[n * self.nx:(n+1)*self.nx] = B_lin.dot([v_ref, kappa_ref]) - f

            # Constrain maximum speed based on curvature
            if self.use_max_kappa_pred:
                max_kappa_pred = np.max(np.abs(kappa_pred[n:]))
                vmax_dyn = np.sqrt(self.ay_max / (np.abs(max_kappa_pred) + 1e-12))
            else:
                vmax_dyn = np.sqrt(self.ay_max / (np.abs(kappa_pred[n]) + 1e-12))
            umax_dyn[self.nu*n] = min(vmax_dyn, umax_dyn[self.nu*n])

        # Update path constraints
        if self.use_obstacle_avoidance and not self.use_path_constraints_topic:
            ub, lb, _ = self.model.reference_path.update_path_constraints(
                self.model.wp_id + 1,
                [self.model.temporal_state.x, self.model.temporal_state.y, self.model.temporal_state.psi],
                N, self.model.length, self.model.width, safety_margin)
        else:
            ref_wp_id = (self.model.wp_id + 1) % len(self.model.reference_path.path_constraints[0])
            ub = self.model.reference_path.path_constraints[0][ref_wp_id]
            lb = self.model.reference_path.path_constraints[1][ref_wp_id]
            self.model.reference_path.border_cells.current_wp_id = ref_wp_id

            # Update safety margin if provided as argument and different from current value
            if self.model.safety_margin != safety_margin:
                safety_margin_diff = safety_margin - self.model.safety_margin
                ub -= safety_margin_diff
                lb += safety_margin_diff

                infeasible_index = ub < lb
                ub[infeasible_index] = 0.0
                lb[infeasible_index] = 0.0

        # Update dynamic state constraints
        xmin_dyn[0] = xmax_dyn[0] = self.model.spatial_state.e_y
        xmin_dyn[self.nx::self.nx] = lb
        xmax_dyn[self.nx::self.nx] = ub
        xr[self.nx::self.nx] = (lb + ub) / 2

        # 等式制約（LTVダイナミクス）+ 箱制約 + ステアリングレート制約を固定パターンで構築
        n_rate_constraints = N - 1
        A_full = self._build_constraint_matrix(N, A_blocks, B_blocks)

        # 境界制約の構築
        x0 = np.array(self.model.spatial_state[:])
        leq = np.hstack([-x0, uq])
        ueq = leq

        # 入力と状態の制約境界
        lineq_basic = np.hstack([xmin_dyn, np.kron(np.ones(N), umin)])
        uineq_basic = np.hstack([xmax_dyn, umax_dyn])

        # ステアリングレート制約の境界
        # [修正(a)] 旧実装は制御周期 Ts(=0.025s) を使っていたが、ホライズンは空間離散
        # (1ステップ = delta_s [m]) なので、ステップ間の時間は delta_s / v_ref (~0.1-0.2s)。
        # また決定変数は曲率 kappa = tan(delta)/L なので、舵角変化量を 1/L 倍して曲率変化量に換算する
        # (d kappa / d delta = 1/(L cos^2 delta) >= 1/L なので保守側の近似)。
        max_kappa_change = self.max_steering_rate * dt_steps[:n_rate_constraints] / self.model.length
        lineq_rate = -max_kappa_change
        uineq_rate = max_kappa_change

        # 全ての境界を結合
        l = np.hstack([leq, lineq_basic, lineq_rate])
        u = np.hstack([ueq, uineq_basic, uineq_rate])

        # コスト行列
        P = sparse.block_diag([
            sparse.kron(sparse.eye(N), self.Q),
            self.QN,
            sparse.kron(sparse.eye(N), self.R)
        ], format='csc')

        q = np.hstack([
            -np.tile(np.diag(self.Q.toarray()), N) * xr[:-self.nx],
            -self.QN.dot(xr[-self.nx:]),
            -np.tile(np.diag(self.R.toarray()), N) * ur
        ])

        # [修正(b)] 旧実装は毎サイクル osqp.OSQP() を作り直して setup していた（ウォームスタート不可）。
        # 疎行列パターンが前回と同じなら update() で数値だけ差し替え、前回解からウォームスタートする。
        self._setup_or_update(P, q, A_full, l, u)

    def _build_constraint_matrix(self, N, A_blocks, B_blocks) -> sparse.csc_matrix:
        """
        Assemble [Aeq; I; R] with a sparsity pattern that depends only on N
        (explicit zeros are kept), so that OSQP's update(Ax=...) can be used.
        """
        nx, nu = self.nx, self.nu
        nx_N = nx * (N + 1)
        nu_N = nu * N
        n_var = nx_N + nu_N
        rows, cols, vals = [], [], []

        # -I on the state variables (x_{n+1} = A x_n + B u_n + ... written as -x + A x + B u)
        idx = np.arange(nx_N)
        rows.append(idx); cols.append(idx); vals.append(-np.ones(nx_N))

        ai, aj = np.meshgrid(np.arange(nx), np.arange(nx), indexing='ij')
        bi, bj = np.meshgrid(np.arange(nx), np.arange(nu), indexing='ij')
        for n in range(N):
            rows.append((n + 1) * nx + ai.ravel()); cols.append(n * nx + aj.ravel())
            vals.append(A_blocks[n].ravel())
            rows.append((n + 1) * nx + bi.ravel()); cols.append(nx_N + n * nu + bj.ravel())
            vals.append(B_blocks[n].ravel())
        r0 = nx_N

        # Box constraints on all variables
        idx = np.arange(n_var)
        rows.append(r0 + idx); cols.append(idx); vals.append(np.ones(n_var))
        r0 += n_var

        # Steering (curvature) rate constraints between consecutive inputs
        n_rate = N - 1
        if n_rate > 0:
            k = np.arange(n_rate)
            rows.append(r0 + k); cols.append(nx_N + nu * k + 1); vals.append(-np.ones(n_rate))
            rows.append(r0 + k); cols.append(nx_N + nu * (k + 1) + 1); vals.append(np.ones(n_rate))
            r0 += n_rate

        return sparse.coo_matrix(
            (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
            shape=(r0, n_var)).tocsc()

    @staticmethod
    def _same_pattern(a: sparse.csc_matrix, b: sparse.csc_matrix) -> bool:
        return (a.shape == b.shape and a.nnz == b.nnz
                and np.array_equal(a.indptr, b.indptr) and np.array_equal(a.indices, b.indices))

    def _setup_or_update(self, P, q, A, l, u):
        P = sparse.triu(P, format='csc')
        if (self._qp_P is not None and self._same_pattern(P, self._qp_P)
                and self._same_pattern(A, self._qp_A)):
            kwargs = dict(q=q, l=l, u=u, Ax=A.data)
            if not np.array_equal(P.data, self._qp_P.data):
                kwargs['Px'] = P.data
            self.optimizer.update(**kwargs)
        else:
            self.optimizer = osqp.OSQP()
            self.optimizer.setup(P=P, q=q, A=A, l=l, u=u, verbose=False, warm_start=True)
            self.stats.setups += 1
        self._qp_P = P
        self._qp_A = A

    def _solve_once(self, N: int, t0: float):
        """Solve the current QP and validate the solution. Returns (dec, control_signals, ok)."""
        dec = self.optimizer.solve()
        self.stats.record(dec.info.status, dec.info.iter, (time.perf_counter() - t0) * 1e3)
        if dec.x is None:
            self.stats.rejected += 1
            return dec, np.zeros(N * self.nu), False
        x = np.asarray(dec.x, dtype=float)
        control_signals = np.array(x[-N * self.nu:])
        kappa = control_signals[1::2]
        kappa_max = self.input_constraints['umax'][1]
        ok = (dec.info.status in self.ACCEPTED_STATUS
              and np.all(np.isfinite(x))
              and np.max(np.abs(kappa)) <= kappa_max * (1.0 + self.INPUT_VIOLATION_TOL))
        if not ok:
            self.stats.rejected += 1
        return dec, control_signals, ok

    def get_control(self) -> Tuple[np.ndarray, float]:
        """
        Get control signal given the current position of the car.
        """
        nx = self.nx
        nu = self.nu

        self.model.get_current_waypoint()

        N = min(self.N, self.model.reference_path.n_waypoints - self.model.wp_id) \
            if not self.model.reference_path.circular else self.N

        self.model.spatial_state = self.model.t2s(
            reference_state=self.model.temporal_state,
            reference_waypoint=self.model.current_waypoint)

        t0 = time.perf_counter()
        self._init_problem(N, self.model.safety_margin)
        raw_delta = 0.0

        try:
            dec, control_signals, ok = self._solve_once(N, t0)
            use_control_signals = control_signals[1::2]

            if not ok or not np.all(use_control_signals):
                for i in range(1, 6):
                    relaxed_safety_margin = self.model.safety_margin * ((5-i) / 5.0)
                    t0 = time.perf_counter()
                    self._init_problem(N, relaxed_safety_margin)
                    dec, control_signals, ok = self._solve_once(N, t0)
                    use_control_signals = control_signals[1::2]

                    if ok and self.infeasibility_counter == 0 and np.all(use_control_signals):
                        if self.last_solved_wp_id != self.model.wp_id:
                            print(f"Relaxed safety margin by {relaxed_safety_margin} ({5-i}/5) to solve the problem")
                        break

            # [修正(c)] 収束していない／制約を大きく破った解は使わず、前回の予測入力にフォールバックする
            if not ok:
                raise _SolveFailed()

            # ステア角の計算と保存
            control_signals[1::2] = np.arctan(control_signals[1::2] * self.model.length)
            v = control_signals[0]
            delta = control_signals[1]
            raw_delta = delta
            # 許容誤差内の違反も最後に舵角上限へ投影する
            delta_max = np.arctan(self.input_constraints['umax'][1] * self.model.length)
            delta = np.clip(delta, -delta_max, delta_max)

            # ステアレートの制限を適用
            max_delta_change = self.max_steering_rate * self.model.Ts
            delta = np.clip(
                delta,
                self.previous_steering - max_delta_change,
                self.previous_steering + max_delta_change
            )
            self.previous_steering = delta

            # 予測の更新
            self.current_control = control_signals
            x = np.reshape(dec.x[:(N+1)*nx], (N+1, nx))
            self.current_prediction = self.update_prediction(x, N)

            u = np.array([v, delta])
            max_delta = np.max(np.abs(control_signals[1:len(control_signals)//3*2:2]))

            if self.infeasibility_counter > (N - 1):
                print(f'Problem solved after {self.infeasibility_counter} infeasible iterations')
            self.infeasibility_counter = 0
            self.last_solved_wp_id = self.model.wp_id

        except (TypeError, ValueError, IndexError, _SolveFailed):
            # 旧実装の `except TypeError or ValueError` は TypeError しか捕捉していなかった
            id = nu * (self.infeasibility_counter + 1)
            if id + 2 < len(self.current_control):
                u = np.array(self.current_control[id:id+2])
                max_delta = np.abs(u[1])
            else:
                u = np.array([0.0, 0.0])
                max_delta = 0.0

            # フォールバック入力にも舵角上限とステアレート制限を適用する
            max_delta_change = self.max_steering_rate * self.model.Ts
            u[1] = np.clip(u[1], self.previous_steering - max_delta_change,
                           self.previous_steering + max_delta_change)
            self.previous_steering = u[1]

            self.infeasibility_counter += 1
            self.stats.fallbacks += 1

        self.stats.end_call(raw_delta)

        if self.infeasibility_counter > (N - 1) and self.infeasibility_counter % 100 == 0:
            print('No control signal computed!')

        return u, max_delta

    def update_prediction(self, spatial_state_prediction, N):
        """
        Transform the predicted states to predicted x and y coordinates.
        Mainly for visualization purposes.
        :param spatial_state_prediction: list of predicted state variables
        :return: lists of predicted x and y coordinates
        """

        # Containers for x and y coordinates of predicted states
        x_pred, y_pred = [], []

        # Iterate over prediction horizon
        for n in range(2, N):
            # Get associated waypoint
            associated_waypoint = self.model.reference_path.\
                get_waypoint(self.model.wp_id+n)
            # Transform predicted spatial state to temporal state
            predicted_temporal_state = self.model.s2t(associated_waypoint,
                                            spatial_state_prediction[n, :])

            # Save predicted coordinates in world coordinate frame
            x_pred.append(predicted_temporal_state.x)
            y_pred.append(predicted_temporal_state.y)

        return x_pred, y_pred

    def show_prediction(self, ax):
        """
        Display predicted car trajectory on the provided axis.
        :param ax: Matplotlib axis object to plot on
        """

        if self.current_prediction is not None:
            # ax.scatter(self.current_prediction[0], self.current_prediction[1],
            #            c=PREDICTION, s=5)
            ax.plot(self.current_prediction[0], self.current_prediction[1], c=PREDICTION)
