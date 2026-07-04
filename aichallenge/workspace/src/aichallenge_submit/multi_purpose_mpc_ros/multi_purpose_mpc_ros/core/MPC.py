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
        # Keep the runtime path equivalent to the original stable MPC behavior:
        # build and setup a fresh OSQP problem every cycle. The update path is
        # intentionally disabled because the MPC matrix pattern can change with
        # dropped zeros and produces non-transparent behavior versus setup().
        self._osqp_cache_enabled = False
        self._osqp_cache_key = None
        self._osqp_A_indices = None
        self._osqp_A_indptr = None
        self._osqp_problem_dirty = True
        self._osqp_used_update = False
        self._osqp_last_problem = None
        self._osqp_matrix_verify_count = 0
        self._osqp_solution_verify_count = 0

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
        self._osqp_problem_dirty = True

    def update_R(self, R: np.ndarray):
        self.R = R
        self._osqp_problem_dirty = True

    def update_QN(self, QN: np.ndarray):
        self.QN = QN
        self._osqp_problem_dirty = True

    def _build_fixed_pattern_equality_matrix(self, N, A_blocks, B_blocks):
        nx_N = self.nx * (N + 1)
        nu_N = self.nu * N
        rows = []
        cols = []
        data = []

        for index in range(nx_N):
            rows.append(index)
            cols.append(index)
            data.append(-1.0)

        for n, (A_lin, B_lin) in enumerate(zip(A_blocks, B_blocks)):
            row_offset = (n + 1) * self.nx
            state_col_offset = n * self.nx
            input_col_offset = nx_N + n * self.nu

            for row, col in ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 2)):
                rows.append(row_offset + row)
                cols.append(state_col_offset + col)
                data.append(A_lin[row, col])

            for row, col in ((1, 1), (2, 0)):
                rows.append(row_offset + row)
                cols.append(input_col_offset + col)
                data.append(B_lin[row, col])

        return sparse.csc_matrix((data, (rows, cols)), shape=(nx_N, nx_N + nu_N))

    def _setup_or_update_problem(self, P, q, A, l, u, N):
        self._osqp_used_update = False
        self._osqp_last_problem = (P, q.copy(), A.copy(), l.copy(), u.copy())
        if not self._osqp_cache_enabled:
            self.optimizer = osqp.OSQP()
            self.optimizer.setup(P=P, q=q, A=A, l=l, u=u, verbose=False, eps_abs=1.0e-8, eps_rel=1.0e-8)
            self._osqp_problem_dirty = False
            return

        cache_key = (N, P.shape, A.shape)
        can_update = (
            self._osqp_cache_enabled
            and not self._osqp_problem_dirty
            and self._osqp_cache_key == cache_key
            and self._osqp_A_indices is not None
            and self._osqp_A_indptr is not None
            and np.array_equal(A.indices, self._osqp_A_indices)
            and np.array_equal(A.indptr, self._osqp_A_indptr)
        )

        if can_update:
            try:
                self.optimizer.update(q=q, l=l, u=u, Ax=A.data)
                self._osqp_used_update = True
                return
            except Exception as exc:
                print(f"OSQP update rejected; disabling cache and falling back to setup: {exc}")
                self._osqp_cache_enabled = False

        self.optimizer = osqp.OSQP()
        self.optimizer.setup(P=P, q=q, A=A, l=l, u=u, verbose=False, eps_abs=1.0e-8, eps_rel=1.0e-8)
        self._osqp_cache_key = cache_key
        self._osqp_A_indices = A.indices.copy()
        self._osqp_A_indptr = A.indptr.copy()
        self._osqp_problem_dirty = False

    def _verify_updated_solution(self, updated_solution):
        if (
            not self._osqp_used_update
            or not self._osqp_cache_enabled
            or self._osqp_solution_verify_count >= 5
            or self._osqp_last_problem is None
        ):
            return updated_solution

        P, q, A, l, u = self._osqp_last_problem
        fresh_optimizer = osqp.OSQP()
        fresh_optimizer.setup(P=P, q=q, A=A, l=l, u=u, verbose=False, eps_abs=1.0e-8, eps_rel=1.0e-8)
        fresh_solution = fresh_optimizer.solve()

        if (
            fresh_solution.x is None
            or updated_solution.x is None
            or fresh_solution.info.status != updated_solution.info.status
            or not np.allclose(fresh_solution.x, updated_solution.x, rtol=1.0e-5, atol=1.0e-5)
        ):
            print("OSQP update solution diverged from fresh setup; disabling cache")
            self._osqp_cache_enabled = False
            self._osqp_problem_dirty = True
            updated_solution = fresh_solution

        self._osqp_solution_verify_count += 1
        return updated_solution

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

        should_verify_matrix = self._osqp_matrix_verify_count < 5
        # Legacy dense matrices are kept only for the initial strict equivalence checks.
        A = np.zeros((nx_N, nx_N)) if should_verify_matrix else None
        B = np.zeros((nx_N, nu_N)) if should_verify_matrix else None
        A_blocks = []
        B_blocks = []

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
            A_blocks.append(A_lin)
            B_blocks.append(B_lin)
            if should_verify_matrix:
                A[(n+1) * self.nx: (n+2)*self.nx, n * self.nx:(n+1)*self.nx] = A_lin
                B[(n+1) * self.nx: (n+2)*self.nx, n * self.nu:(n+1)*self.nu] = B_lin

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

        Aeq = self._build_fixed_pattern_equality_matrix(N, A_blocks, B_blocks)

        if should_verify_matrix:
            Ax = sparse.kron(sparse.eye(N + 1), -sparse.eye(self.nx)) + sparse.csc_matrix(A)
            Bu = sparse.csc_matrix(B)
            Aeq_legacy = sparse.hstack([Ax, Bu])
            if not np.allclose(Aeq.toarray(), Aeq_legacy.toarray(), rtol=1.0e-12, atol=1.0e-12):
                print("Fixed-pattern MPC matrix differs from legacy matrix; disabling OSQP cache")
                Aeq = Aeq_legacy
                self._osqp_cache_enabled = False
            self._osqp_matrix_verify_count += 1

        # ステアリングレート制約の行列を構築
        n_rate_constraints = N - 1
        steering_rate_matrix = np.zeros((n_rate_constraints, nx_N + nu_N))

        # ステアリングレート制約の行列を設定
        for i in range(n_rate_constraints):
            # 連続する制御入力間の差分に対する係数を設定
            steering_rate_matrix[i, nx_N + self.nu*i + 1] = -1  # 現在のステア角
            steering_rate_matrix[i, nx_N + self.nu*(i+1) + 1] = 1  # 次のステア角

        # 制約行列の結合
        A_inequality = sparse.vstack([
            sparse.eye(nx_N + nu_N),  # 状態と入力の基本的な制約
            sparse.csc_matrix(steering_rate_matrix)  # ステアリングレート制約
        ])

        # 完全な制約行列
        A_full = sparse.vstack([Aeq, A_inequality], format='csc')

        # 境界制約の構築
        x0 = np.array(self.model.spatial_state[:])
        leq = np.hstack([-x0, uq])
        ueq = leq

        # 入力と状態の制約境界
        lineq_basic = np.hstack([xmin_dyn, np.kron(np.ones(N), umin)])
        uineq_basic = np.hstack([xmax_dyn, umax_dyn])

        # ステアリングレート制約の境界
        max_delta_change = self.max_steering_rate * self.model.Ts
        lineq_rate = -max_delta_change * np.ones(n_rate_constraints)
        uineq_rate = max_delta_change * np.ones(n_rate_constraints)

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

        # オプティマイザの設定
        self._setup_or_update_problem(P, q, A_full, l, u, N)

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
            dec = self._verify_updated_solution(dec)
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
