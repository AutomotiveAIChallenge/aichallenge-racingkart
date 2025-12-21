import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor
from jaxtyping import Float

class TinyLidarNet(nn.Module):
    """
    Standard CNN architecture for 1D LiDAR data processing.
    Assumes default input_dim=1080 for shape annotations.
    """

    def __init__(self, input_dim: int = 1080, output_dim: int = 2):
        super().__init__()

        # --- Convolutional Layers ---
        # Input: 1080
        self.conv1 = nn.Conv1d(1, 24, kernel_size=10, stride=4)  # -> (1080-10)/4 + 1 = 268
        self.conv2 = nn.Conv1d(24, 36, kernel_size=8, stride=4)  # -> (268-8)/4 + 1 = 66
        self.conv3 = nn.Conv1d(36, 48, kernel_size=4, stride=2)  # -> (66-4)/2 + 1 = 32
        self.conv4 = nn.Conv1d(48, 64, kernel_size=3)            # -> (32-3)/1 + 1 = 30
        self.conv5 = nn.Conv1d(64, 64, kernel_size=3)            # -> (30-3)/1 + 1 = 28
        
        # Flatten size: 64 ch * 28 length = 1792
        
        # --- Fully Connected Layers ---
        # Note: Dynamic calculation is good, but for jaxtyping clarity we assume logic matches
        with torch.no_grad():
            dummy = torch.zeros(1, 1, input_dim)
            out = self.conv5(self.conv4(self.conv3(self.conv2(self.conv1(dummy)))))
            self.flatten_dim = out.view(1, -1).shape[1]

        self.fc1 = nn.Linear(self.flatten_dim, 100)
        self.fc2 = nn.Linear(100, 50)
        self.fc3 = nn.Linear(50, 10)
        self.fc4 = nn.Linear(10, output_dim)

        self._initialize_weights()

    def _initialize_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, (nn.Conv1d, nn.Linear)):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(
        self, 
        x: Float[Tensor, "batch 1 1080"]
    ) -> Float[Tensor, "batch 2"]:
        
        # Feature Extraction (Conv + ReLU)
        # Input: [B, 1, 1080]
        x: Float[Tensor, "batch 24 268"] = F.relu(self.conv1(x))
        x: Float[Tensor, "batch 36 66"]  = F.relu(self.conv2(x))
        x: Float[Tensor, "batch 48 32"]  = F.relu(self.conv3(x))
        x: Float[Tensor, "batch 64 30"]  = F.relu(self.conv4(x))
        x: Float[Tensor, "batch 64 28"]  = F.relu(self.conv5(x))

        # Flatten: (Batch, 64, 28) -> (Batch, 1792)
        x: Float[Tensor, "batch 1792"] = torch.flatten(x, start_dim=1)

        # Regression Head (FC + ReLU)
        x: Float[Tensor, "batch 100"] = F.relu(self.fc1(x))
        x: Float[Tensor, "batch 50"]  = F.relu(self.fc2(x))
        x: Float[Tensor, "batch 10"]  = F.relu(self.fc3(x))

        # Output Layer
        x: Float[Tensor, "batch 2"] = torch.tanh(self.fc4(x))
        
        return x


class TinyLidarNetSmall(nn.Module):
    """
    Lightweight CNN architecture.
    Assumes default input_dim=1080 for shape annotations.
    """

    def __init__(self, input_dim: int = 1080, output_dim: int = 2):
        super().__init__()

        # --- Convolutional Layers ---
        self.conv1 = nn.Conv1d(1, 24, kernel_size=10, stride=4) # -> 268
        self.conv2 = nn.Conv1d(24, 36, kernel_size=8, stride=4) # -> 66
        self.conv3 = nn.Conv1d(36, 48, kernel_size=4, stride=2) # -> 32
        
        # Flatten size: 48 ch * 32 length = 1536

        with torch.no_grad():
            dummy = torch.zeros(1, 1, input_dim)
            out = self.conv3(self.conv2(self.conv1(dummy)))
            self.flatten_dim = out.view(1, -1).shape[1]

        self.fc1 = nn.Linear(self.flatten_dim, 100)
        self.fc2 = nn.Linear(100, 50)
        self.fc3 = nn.Linear(50, output_dim)

        self._initialize_weights()

    def _initialize_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, (nn.Conv1d, nn.Linear)):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(
        self, 
        x: Float[Tensor, "batch 1 1080"]
    ) -> Float[Tensor, "batch 2"]:
        
        # Feature Extraction
        x: Float[Tensor, "batch 24 268"] = F.relu(self.conv1(x))
        x: Float[Tensor, "batch 36 66"]  = F.relu(self.conv2(x))
        x: Float[Tensor, "batch 48 32"]  = F.relu(self.conv3(x))
        
        # Flatten: (Batch, 48, 32) -> (Batch, 1536)
        x: Float[Tensor, "batch 1536"] = torch.flatten(x, start_dim=1)
        
        # Regression Head
        x: Float[Tensor, "batch 100"] = F.relu(self.fc1(x))
        x: Float[Tensor, "batch 50"]  = F.relu(self.fc2(x))
        
        # Output Layer
        x: Float[Tensor, "batch 2"] = torch.tanh(self.fc3(x))
        
        return x
