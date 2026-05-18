from typing import Tuple
import numpy as np
import osqp
from scipy import sparse
import matplotlib.pyplot as plt

# Colors
PREDICTION = '#BA4A00'

##################
# MPC Controller #
##################

class MPC:
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

        # 静的キャッシュ。N が固定なら 1 回作って tick 間で使い回す。
        # _static は (kron_eye, eye_full, steer_rate, xmin_tile, xmax_tile,
        #              umin_tile, lineq_rate, uineq_rate) を保持。
        # _P_csc は Q/R/QN 由来のコスト行列、_q_diag は np.tile(diag(Q), N) など。
        self._static_cache_N: int = -1
        self._static = None
        self._P_csc = None
        self._q_diag_Q = None
        self._q_diag_R = None
        self._P_cache_keys = None  # (id(Q), id(R), id(QN), N)

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
        self._P_cache_keys = None  # 次回 _init_problem で P 再構築

    def update_R(self, R: np.ndarray):
        self.R = R
        self._P_cache_keys = None

    def update_QN(self, QN: np.ndarray):
        self.QN = QN
        self._P_cache_keys = None

    def _ensure_static_cache(self, N):
        """N に依存する固定パターンの sparse 行列・ベクトルを 1 度だけ作る。"""
        if self._static_cache_N == N and self._static is not None:
            return
        nx = self.nx
        nu = self.nu
        nx_N = nx * (N + 1)
        nu_N = nu * N

        umin = self.input_constraints['umin']
        xmin = self.state_constraints['xmin']
        xmax = self.state_constraints['xmax']

        kron_eye = sparse.kron(sparse.eye(N + 1), -sparse.eye(nx), format='csc')
        eye_full = sparse.eye(nx_N + nu_N, format='csc')

        # ステアリングレート制約行列（連続入力の diff）。
        n_rate = N - 1
        if n_rate > 0:
            rows = np.repeat(np.arange(n_rate), 2)
            cols = np.empty(2 * n_rate, dtype=np.int64)
            data = np.empty(2 * n_rate, dtype=np.float64)
            for i in range(n_rate):
                cols[2*i]   = nx_N + nu*i + 1
                cols[2*i+1] = nx_N + nu*(i+1) + 1
                data[2*i]   = -1.0
                data[2*i+1] = 1.0
            steer_rate = sparse.csc_matrix(
                (data, (rows, cols)),
                shape=(n_rate, nx_N + nu_N))
        else:
            steer_rate = sparse.csc_matrix((0, nx_N + nu_N))

        xmin_tile = np.kron(np.ones(N + 1), xmin)
        xmax_tile = np.kron(np.ones(N + 1), xmax)
        umin_tile = np.kron(np.ones(N), umin)

        max_delta_change = self.max_steering_rate * self.model.Ts
        lineq_rate = -max_delta_change * np.ones(n_rate)
        uineq_rate = max_delta_change * np.ones(n_rate)

        self._static = {
            'kron_eye': kron_eye,
            'eye_full': eye_full,
            'steer_rate': steer_rate,
            'xmin_tile': xmin_tile,
            'xmax_tile': xmax_tile,
            'umin_tile': umin_tile,
            'lineq_rate': lineq_rate,
            'uineq_rate': uineq_rate,
            'n_rate': n_rate,
            'nx_N': nx_N,
            'nu_N': nu_N,
        }
        self._static_cache_N = N

    def _ensure_P_cache(self, N):
        """コスト行列 P と q 計算用 diag-tile を Q/R/QN が変わるたびに作り直す。"""
        key = (id(self.Q), id(self.R), id(self.QN), N)
        if self._P_cache_keys == key and self._P_csc is not None:
            return
        self._P_csc = sparse.block_diag([
            sparse.kron(sparse.eye(N), self.Q),
            self.QN,
            sparse.kron(sparse.eye(N), self.R)
        ], format='csc')
        self._q_diag_Q = np.tile(np.diag(self.Q.toarray()), N)
        self._q_diag_R = np.tile(np.diag(self.R.toarray()), N)
        self._P_cache_keys = key

    def _init_problem(self, N, safety_margin):
        """
        Initialize optimization problem for current time step with steering rate constraints.
        """
        # 既存の制約設定
        umin = self.input_constraints['umin']
        umax = self.input_constraints['umax']

        # N に依存する固定パターンを 1 度だけ準備（tick 間で使い回し）。
        self._ensure_static_cache(N)
        self._ensure_P_cache(N)
        static = self._static
        nx_N = static['nx_N']
        nu_N = static['nu_N']
        n_rate = static['n_rate']

        # LTV System Matrices
        A = np.zeros((nx_N, nx_N))
        B = np.zeros((nx_N, nu_N))

        # Reference vector
        ur = np.zeros(nu_N)
        xr = np.zeros(nx_N)
        uq = np.zeros(N * self.nx)

        # Dynamic constraints（固定 tile からコピー → 一部だけ書き換え）。
        xmin_dyn = static['xmin_tile'].copy()
        xmax_dyn = static['xmax_tile'].copy()
        umax_dyn = np.tile(umax, N)  # umax は update_v_max で変わるのでキャッシュしない

        # Get curvature predictions
        kappa_pred = np.tan(np.append(np.array(self.current_control[3::self.nu]), self.current_control[-1])) / self.model.length

        # Consider control delay
        self.model.wp_id += self.wp_id_offset

        # 曲率制限を事前計算（ループ外で O(N)）。use_max_kappa_pred の場合は
        # kappa_pred[n:] の最大値が必要なので逆向き累積最大で求める。
        abs_kappa = np.abs(kappa_pred)
        if self.use_max_kappa_pred:
            # revmax[n] = max(abs_kappa[n:])
            revmax = np.maximum.accumulate(abs_kappa[::-1])[::-1]
            vmax_curve = np.sqrt(self.ay_max / (revmax + 1e-12))
        else:
            vmax_curve = np.sqrt(self.ay_max / (abs_kappa + 1e-12))

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
            A[(n+1) * self.nx: (n+2)*self.nx, n * self.nx:(n+1)*self.nx] = A_lin
            B[(n+1) * self.nx: (n+2)*self.nx, n * self.nu:(n+1)*self.nu] = B_lin

            # Set reference
            ur[n*self.nu:(n+1)*self.nu] = [v_ref, kappa_ref]
            uq[n * self.nx:(n+1)*self.nx] = B_lin.dot([v_ref, kappa_ref]) - f

            # Constrain maximum speed based on curvature
            if vmax_curve[n] < umax_dyn[self.nu*n]:
                umax_dyn[self.nu*n] = vmax_curve[n]

        # Update path constraints
        ub, lb = self._compute_ub_lb(N, safety_margin)

        # Update dynamic state constraints
        xmin_dyn[0] = xmax_dyn[0] = self.model.spatial_state.e_y
        xmin_dyn[self.nx::self.nx] = lb
        xmax_dyn[self.nx::self.nx] = ub
        xr[self.nx::self.nx] = (lb + ub) / 2

        # Get equality matrix（静的な -I クロネッカーは事前に CSC で作成済み）。
        Ax = static['kron_eye'] + sparse.csc_matrix(A)
        Bu = sparse.csc_matrix(B)
        Aeq = sparse.hstack([Ax, Bu])

        # 不等式制約：[I; steer_rate] はキャッシュ済み。
        A_inequality = sparse.vstack([static['eye_full'], static['steer_rate']])

        # 完全な制約行列
        A_full = sparse.vstack([Aeq, A_inequality], format='csc')

        # 境界制約の構築
        x0 = np.array(self.model.spatial_state[:])
        leq = np.hstack([-x0, uq])
        ueq = leq

        # 入力と状態の制約境界（umin_tile はキャッシュ）。
        lineq_basic = np.hstack([xmin_dyn, static['umin_tile']])
        uineq_basic = np.hstack([xmax_dyn, umax_dyn])

        # 全ての境界を結合（ステアリングレートの ±max は固定）。
        l = np.hstack([leq, lineq_basic, static['lineq_rate']])
        u = np.hstack([ueq, uineq_basic, static['uineq_rate']])

        # コスト行列はキャッシュ済み（Q/R/QN が変わらなければ再構築なし）。
        P = self._P_csc
        q_R_part = -self._q_diag_R * ur
        q = np.hstack([
            -self._q_diag_Q * xr[:-self.nx],
            -self.QN.dot(xr[-self.nx:]),
            q_R_part,
        ])

        # オプティマイザの設定
        self.optimizer = osqp.OSQP()
        self.optimizer.setup(P=P, q=q, A=A_full, l=l, u=u, verbose=False)

        # リトライ専用パス用に safety_margin 不変の中間結果を保持する。
        # tick 中に safety_margin だけが変わる場合は _update_safety_margin で
        # OSQP を update() で済ませ、setup() の因数分解コストを回避する。
        self._tick_ctx = {
            'N': N,
            'xmin_template': xmin_dyn.copy(),  # ub/lb スライスは _update で上書き
            'xmax_template': xmax_dyn.copy(),
            'umax_dyn': umax_dyn,
            'leq': leq,
            'q_R_part': q_R_part,
        }

    def _compute_ub_lb(self, N, safety_margin):
        """Path constraints の ub/lb を safety_margin に応じて取得する。
        tick 内で safety_margin だけ変わるリトライでは tick cache が効くため
        コストは ReferencePath 側のスライスとマージン適用に抑えられる。"""
        if self.use_obstacle_avoidance and not self.use_path_constraints_topic:
            ub, lb, _ = self.model.reference_path.update_path_constraints(
                self.model.wp_id + 1,
                [self.model.temporal_state.x, self.model.temporal_state.y, self.model.temporal_state.psi],
                N, self.model.length, self.model.width, safety_margin)
            return ub, lb

        ref_wp_id = (self.model.wp_id + 1) % len(self.model.reference_path.path_constraints[0])
        ub = self.model.reference_path.path_constraints[0][ref_wp_id]
        lb = self.model.reference_path.path_constraints[1][ref_wp_id]
        self.model.reference_path.border_cells.current_wp_id = ref_wp_id

        # 必要な分だけコピーした方が呼び元のキャッシュ汚染を防げる。
        if self.model.safety_margin != safety_margin:
            ub = ub - (safety_margin - self.model.safety_margin)
            lb = lb + (safety_margin - self.model.safety_margin)
            infeasible_index = ub < lb
            ub[infeasible_index] = 0.0
            lb[infeasible_index] = 0.0
        return ub, lb

    def _update_safety_margin(self, N, safety_margin):
        """既存の OSQP setup を再利用し、safety_margin 変更による
        l/u/q の差分だけを optimizer.update() で反映する。"""
        ctx = self._tick_ctx
        static = self._static
        ub, lb = self._compute_ub_lb(N, safety_margin)

        xmin_dyn = ctx['xmin_template'].copy()
        xmax_dyn = ctx['xmax_template'].copy()
        xmin_dyn[self.nx::self.nx] = lb
        xmax_dyn[self.nx::self.nx] = ub
        xr = np.zeros(self.nx * (N + 1))
        xr[self.nx::self.nx] = (lb + ub) / 2

        lineq_basic = np.hstack([xmin_dyn, static['umin_tile']])
        uineq_basic = np.hstack([xmax_dyn, ctx['umax_dyn']])
        l = np.hstack([ctx['leq'], lineq_basic, static['lineq_rate']])
        u = np.hstack([ctx['leq'], uineq_basic, static['uineq_rate']])

        q = np.hstack([
            -self._q_diag_Q * xr[:-self.nx],
            -self.QN.dot(xr[-self.nx:]),
            ctx['q_R_part'],
        ])

        # P/A の値・sparsity は不変なので q/l/u のみ更新。
        self.optimizer.update(q=q, l=l, u=u)

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

        self._init_problem(N, self.model.safety_margin)

        try:
            dec = self.optimizer.solve()
            control_signals = np.array(dec.x[-N*nu:])
            use_control_signals = control_signals[1::2]

            if not np.all(use_control_signals):
                for i in range(1, 6):
                    relaxed_safety_margin = self.model.safety_margin * ((5-i) / 5.0)
                    # OSQP の setup/因数分解を回避し、l/u/q だけ update で更新する。
                    self._update_safety_margin(N, relaxed_safety_margin)
                    dec = self.optimizer.solve()
                    control_signals = np.array(dec.x[-N*nu:])
                    use_control_signals = control_signals[1::2]

                    if self.infeasibility_counter == 0 and np.all(use_control_signals):
                        if self.last_solved_wp_id != self.model.wp_id:
                            print(f"Relaxed safety margin by {relaxed_safety_margin} ({5-i}/5) to solve the problem")
                        break

            # ステア角の計算と保存
            control_signals[1::2] = np.arctan(control_signals[1::2] * self.model.length)
            v = control_signals[0]
            delta = control_signals[1]

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

        except TypeError or ValueError:
            id = nu * (self.infeasibility_counter + 1)
            if id + 2 < len(self.current_control):
                u = np.array(self.current_control[id:id+2])
                max_delta = np.abs(u[1])
            else:
                u = np.array([0.0, 0.0])
                max_delta = 0.0

            self.infeasibility_counter += 1

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
