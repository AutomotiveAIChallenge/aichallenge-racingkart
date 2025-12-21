import torch
import torch.nn as nn


class WeightedSmoothL1Loss(nn.Module):
    """
    Computes a weighted sum of Smooth L1 losses for Acceleration and Steering.

    This loss function calculates the Smooth L1 loss independently for the
    acceleration and steering components, averages them across the batch,
    and then computes a weighted sum. This allows for balancing the learning
    signals between longitudinal (acceleration) and lateral (steering) control.

    Attributes:
        accel_weight (float): Weight coefficient for the acceleration loss.
        steer_weight (float): Weight coefficient for the steering loss.
        criterion (nn.SmoothL1Loss): The underlying loss criterion (reduction='none').
    """

    def __init__(self, accel_weight: float = 1.0, steer_weight: float = 1.0):
        """
        Initializes the WeightedSmoothL1Loss.

        Args:
            accel_weight: Coefficient to scale the acceleration loss.
            steer_weight: Coefficient to scale the steering loss.
        """
        super().__init__()
        self.accel_weight = accel_weight
        self.steer_weight = steer_weight
        
        # Use reduction='none' to calculate losses per element (accel/steer) individually
        # before averaging and weighting manually.
        self.criterion = nn.SmoothL1Loss(reduction='none')

    def forward(self, outputs: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        """
        Calculates the weighted loss.

        The input tensors are expected to have the last dimension size of 2,
        ordered as [acceleration, steering].

        Args:
            outputs: Model predictions. Shape: (Batch_Size, 2) or (Batch*Seq, 2).
                     Index 0: Acceleration, Index 1: Steering.
            targets: Ground truth values. Shape: (Batch_Size, 2) or (Batch*Seq, 2).
                     Index 0: Acceleration, Index 1: Steering.

        Returns:
            torch.Tensor: A scalar tensor representing the weighted combined loss.
        """
        # Calculate element-wise loss
        # Shape: (N, 2)
        loss = self.criterion(outputs, targets)

        # Separate losses based on channel index
        # Index 0 is Acceleration, Index 1 is Steering (as defined in the Dataset)
        loss_accel = loss[:, 0]
        loss_steer = loss[:, 1]
        
        # Compute the weighted sum of means
        weighted_loss = (self.accel_weight * loss_accel.mean()) + \
                        (self.steer_weight * loss_steer.mean())
        
        return weighted_loss
