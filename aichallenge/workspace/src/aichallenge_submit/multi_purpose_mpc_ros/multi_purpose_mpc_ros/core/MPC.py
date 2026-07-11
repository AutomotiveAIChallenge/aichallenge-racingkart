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

        # 追加: サイクル不変な QP 構造のキャッシュ (key = N)
        # - _cost_cache: (P, q_Q_tile, q_R_tile). update_Q/update_R/update_QN で無効化。
        # - _rate_matrix_cache: ステアリングレート制約込みの不等式行列 (eye + rate) の csc。
        # - _eq_pattern_cache: Aeq の COO (rows, cols) パターン (値は毎サイクル差し替え)。
        # - _bounds_template_cache: kron で作る境界テンプレート。update_v_max で無効化
        #   (umax配列がin-placeで書き換えられるため)。
        self._cost_cache = {}
        self._rate_matrix_cache = {}
        self._eq_pattern_cache = {}
        self._bounds_template_cache = {}

        # 追加: OSQP ワークスペース永続化キャッシュ。setup() 済みの A_full スパース
        # パターン (N, indptr, indices) を保持し、次サイクルでパターンが一致すれば
        # optimizer.update() で使い回す (Task 5)。update_Q/update_R/update_QN は P の
        # 値 (構造ではなく数値) を変えるが optimizer.update() には Px を渡していない
        # ため、これらの呼び出し時は強制的に None にして re-setup させる。
        # update_v_max/update_ay_max は l/u/q の値のみに影響し P の値にも A_full の
        # スパースパターンにも影響しないため、ここでは無効化しない
        # (mpc_controller.py の _control() から毎サイクル update_v_max が呼ばれており、
        # 無効化すると update() 経路に一切乗らなくなり Task 5 の効果が消えるため)。
        self._osqp_cache = None

        if not self.use_obstacle_avoidance:
            self.model.reference_path.update_simple_path_constraints(
                N,
                self.model.safety_margin)

    def update_v_max(self, v_max: float):
        self.input_constraints['umax'][0] = v_max
        # umax はテンプレート内に値が焼き込まれているため無効化する
        self._bounds_template_cache.clear()
        # 注意: v_max は l/u/q の値のみに影響し、P の値にも A_full のスパース
        # パターンにも影響しないため _osqp_cache は無効化しない (update() 経路を
        # 維持する)。この関数は mpc_controller.py の _control() から毎サイクル
        # 呼ばれるため、無効化すると osqp の再 setup が毎サイクル発生してしまう。

    def update_ay_max(self, ay_max: float):
        self.ay_max = ay_max
        # v_max と同様、ay_max も umax_dyn (u ベクトルの値) にのみ影響し P の値にも
        # A_full のスパースパターンにも影響しないため _osqp_cache は無効化しない。

    def update_wp_id_offset(self, wp_id_offset: int):
        self.wp_id_offset = wp_id_offset

    def update_Q(self, Q: np.ndarray):
        self.Q = Q
        self._cost_cache.clear()
        # P の数値が変わるが optimizer.update() には Px を渡していないため
        # 強制的に re-setup させる。
        self._osqp_cache = None

    def update_R(self, R: np.ndarray):
        self.R = R
        self._cost_cache.clear()
        self._osqp_cache = None

    def update_QN(self, QN: np.ndarray):
        self.QN = QN
        self._cost_cache.clear()
        self._osqp_cache = None

    def _get_eq_pattern(self, N):
        """COO (rows, cols) pattern of Aeq for horizon length N (cached).

        Layout matches the original dense construction exactly:
        Ax = kron(eye(N+1), -eye(nx)) + block_diag_offdiag(A_lin blocks),
        Bu = block_diag_offdiag(B_lin blocks), Aeq = hstack([Ax, Bu]).
        """
        if N not in self._eq_pattern_cache:
            nx, nu = self.nx, self.nu
            nx_N = nx * (N + 1)
            n_idx = np.arange(N)

            # -I diagonal (nx_N entries)
            rows_diag = np.arange(nx_N)
            cols_diag = np.arange(nx_N)

            # A_lin blocks: row (n+1)*nx + r, col n*nx + c, all nx*nx entries
            rr, cc = np.meshgrid(np.arange(nx), np.arange(nx), indexing='ij')
            rows_A = ((n_idx + 1) * nx)[:, None, None] + rr[None, :, :]
            cols_A = (n_idx * nx)[:, None, None] + cc[None, :, :]

            # B_lin blocks: row (n+1)*nx + r, col nx_N + n*nu + c, all nx*nu entries
            rr2, cc2 = np.meshgrid(np.arange(nx), np.arange(nu), indexing='ij')
            rows_B = ((n_idx + 1) * nx)[:, None, None] + rr2[None, :, :]
            cols_B = nx_N + (n_idx * nu)[:, None, None] + cc2[None, :, :]

            rows = np.concatenate([rows_diag, rows_A.ravel(), rows_B.ravel()])
            cols = np.concatenate([cols_diag, cols_A.ravel(), cols_B.ravel()])
            self._eq_pattern_cache[N] = (rows, cols)
        return self._eq_pattern_cache[N]

    def _get_rate_ineq(self, N):
        """Combined [eye(nx_N+nu_N); steering_rate_matrix] inequality matrix
        (constant for a given N, cached)."""
        if N not in self._rate_matrix_cache:
            nx, nu = self.nx, self.nu
            nx_N = nx * (N + 1)
            nu_N = nu * N
            n_rate = N - 1

            i_idx = np.arange(n_rate)
            rows_rate = np.concatenate([i_idx, i_idx])
            cols_rate = np.concatenate([
                nx_N + nu * i_idx + 1,
                nx_N + nu * (i_idx + 1) + 1,
            ])
            vals_rate = np.concatenate([-np.ones(n_rate), np.ones(n_rate)])
            rate_csc = sparse.csc_matrix(
                (vals_rate, (rows_rate, cols_rate)), shape=(n_rate, nx_N + nu_N))

            A_inequality = sparse.vstack([
                sparse.eye(nx_N + nu_N, format='csc'),
                rate_csc,
            ], format='csc')
            self._rate_matrix_cache[N] = A_inequality
        return self._rate_matrix_cache[N]

    def _get_bounds_template(self, N):
        """kron-expanded constraint templates for horizon N (cached)."""
        if N not in self._bounds_template_cache:
            xmin = self.state_constraints['xmin']
            xmax = self.state_constraints['xmax']
            umin = self.input_constraints['umin']
            umax = self.input_constraints['umax']
            xmin_dyn = np.kron(np.ones(N + 1), xmin)
            xmax_dyn = np.kron(np.ones(N + 1), xmax)
            umin_dyn = np.kron(np.ones(N), umin)
            umax_dyn = np.kron(np.ones(N), umax)
            self._bounds_template_cache[N] = (xmin_dyn, xmax_dyn, umin_dyn, umax_dyn)
        return self._bounds_template_cache[N]

    def _get_cost_cache(self, N):
        """(P, q_Q_tile, q_R_tile) for horizon N (cached, depends on Q/R/QN)."""
        if N not in self._cost_cache:
            P = sparse.block_diag([
                sparse.kron(sparse.eye(N), self.Q),
                self.QN,
                sparse.kron(sparse.eye(N), self.R)
            ], format='csc')
            q_Q_tile = -np.tile(np.diag(self.Q.toarray()), N)
            q_R_tile = -np.tile(np.diag(self.R.toarray()), N)
            self._cost_cache[N] = (P, q_Q_tile, q_R_tile)
        return self._cost_cache[N]

    def _init_problem(self, N, safety_margin):
        """
        Initialize optimization problem for current time step with steering rate constraints.
        """
        # 既存の制約設定
        umin = self.input_constraints['umin']
        umax = self.input_constraints['umax']

        # Precompute common terms
        nx = self.nx
        nu = self.nu
        nx_N = nx * (N + 1)
        nu_N = nu * N

        # Dynamic constraint templates (cached per N)
        xmin_dyn_tpl, xmax_dyn_tpl, umin_dyn, umax_dyn_tpl = self._get_bounds_template(N)
        xmin_dyn = xmin_dyn_tpl.copy()
        xmax_dyn = xmax_dyn_tpl.copy()
        umax_dyn = umax_dyn_tpl.copy()

        # Get curvature predictions
        kappa_pred = np.tan(np.append(np.array(self.current_control[3::self.nu]), self.current_control[-1])) / self.model.length

        # Consider control delay
        self.model.wp_id += self.wp_id_offset

        # Vectorized horizon reference lookup (replicates get_waypoint's
        # circular-mod / non-circular-clamp semantics exactly)
        ref = self.model.reference_path
        n_wp = ref.n_waypoints
        idx = self.model.wp_id + np.arange(N + 1)
        if ref.circular:
            idx = idx % n_wp
        else:
            idx = np.minimum(idx, n_wp - 1)

        xy = ref.waypoints_xy
        kappa = ref.kappas[idx[:-1]]
        v_ref = np.clip(ref.v_refs[idx[:-1]], umin[0], umax[0])
        dx = xy[idx[1:], 0] - xy[idx[:-1], 0]
        dy = xy[idx[1:], 1] - xy[idx[:-1], 1]
        # NOTE: (dx**2 + dy**2)**0.5, not np.hypot, for bit-identity with
        # Waypoint.__sub__ (reference_path.py:145).
        delta_s = (dx ** 2 + dy ** 2) ** 0.5

        # Compute LTV matrices for the whole horizon at once
        f, A_lin, B_lin = self.model.linearize_batch(v_ref, kappa, delta_s)

        # Set reference
        ur = np.column_stack([v_ref, kappa]).ravel()
        uq = (B_lin[:, :, 0] * v_ref[:, None] + B_lin[:, :, 1] * kappa[:, None] - f).ravel()

        # Constrain maximum speed based on curvature
        if self.use_max_kappa_pred:
            suffix_max = np.maximum.accumulate(np.abs(kappa_pred)[::-1])[::-1]
            vmax_dyn = np.sqrt(self.ay_max / (suffix_max[:N] + 1e-12))
        else:
            vmax_dyn = np.sqrt(self.ay_max / (np.abs(kappa_pred[:N]) + 1e-12))
        umax_dyn[0::self.nu] = np.minimum(vmax_dyn, umax_dyn[0::self.nu])

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
        xmin_dyn[nx::nx] = lb
        xmax_dyn[nx::nx] = ub
        xr = np.zeros(nx_N)
        xr[nx::nx] = (lb + ub) / 2

        # Get equality matrix directly in coordinate form (fixed sparsity
        # pattern per N, structural zeros preserved — no eliminate_zeros /
        # csc(dense) pruning, required for Task 5's osqp.update() reuse).
        rows, cols = self._get_eq_pattern(N)
        vals = np.concatenate([-np.ones(nx_N), A_lin.ravel(), B_lin.ravel()])
        Aeq = sparse.csc_matrix((vals, (rows, cols)), shape=(nx_N, nx_N + nu_N))

        # ステアリングレート制約込みの不等式行列 (N ごとに一度だけ構築してキャッシュ)
        n_rate_constraints = N - 1
        A_inequality = self._get_rate_ineq(N)

        # 完全な制約行列 (Aeq は毎サイクル変わるため vstack はここで実施)
        A_full = sparse.vstack([Aeq, A_inequality], format='csc')

        # 境界制約の構築
        x0 = np.array(self.model.spatial_state[:])
        leq = np.hstack([-x0, uq])
        ueq = leq

        # 入力と状態の制約境界
        lineq_basic = np.hstack([xmin_dyn, umin_dyn])
        uineq_basic = np.hstack([xmax_dyn, umax_dyn])

        # ステアリングレート制約の境界
        max_delta_change = self.max_steering_rate * self.model.Ts
        lineq_rate = -max_delta_change * np.ones(n_rate_constraints)
        uineq_rate = max_delta_change * np.ones(n_rate_constraints)

        # 全ての境界を結合
        l = np.hstack([leq, lineq_basic, lineq_rate])
        u = np.hstack([ueq, uineq_basic, uineq_rate])

        # コスト行列 (Q/R/QN/N にのみ依存するためキャッシュ)
        P, q_Q_tile, q_R_tile = self._get_cost_cache(N)

        q = np.hstack([
            q_Q_tile * xr[:-nx],
            -self.QN.dot(xr[-nx:]),
            q_R_tile * ur
        ])

        # オプティマイザの設定 (Task 5: パターン不変なら setup() を省き update() で
        # ウォームスタート再利用する)
        self._setup_or_update(P, q, A_full, l, u, N)

    def _setup_or_update(self, P, q, A_full, l, u, N):
        """Reuse the OSQP workspace via update() when the sparsity pattern of
        A_full (and N) matches the last setup(); otherwise re-setup from
        scratch (which also resets OSQP's internal warm-start state).

        NOTE: cross-cycle warm-starting is deliberately disabled (see the
        explicit warm_start(0, 0) call below) — see task-5-report.md for the
        equivalence-failure investigation that led to this.
        """
        c = self._osqp_cache
        if (c is not None and c["N"] == N
                and np.array_equal(c["A_indptr"], A_full.indptr)
                and np.array_equal(c["A_indices"], A_full.indices)):
            self.optimizer.update(q=q, l=l, u=u, Ax=A_full.data)
            # Reset both primal and dual warm-start state to zero. Empirically,
            # letting OSQP auto-warm-start from the *previous* cycle's solution
            # across update() (its default behavior) causes the LTV problem's
            # dual iterate to diverge after ~200 cycles into spurious "primal
            # infeasible" detections (see task-5-report.md for the
            # investigation) — passing x=None/y=None to warm_start() is a
            # documented no-op in this OSQP version (osqp==1.0.4), so an
            # explicit zero array is required to actually cold-start.
            self.optimizer.warm_start(x=np.zeros_like(q), y=np.zeros_like(l))
            return
        self.optimizer = osqp.OSQP()
        self.optimizer.setup(P=P, q=q, A=A_full, l=l, u=u, verbose=False)
        self._osqp_cache = {"N": N, "A_indptr": A_full.indptr.copy(),
                             "A_indices": A_full.indices.copy()}

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
                    self._init_problem(N, relaxed_safety_margin)
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
