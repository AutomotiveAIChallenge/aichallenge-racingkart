import torch
import torch.nn as nn
import torch.nn.functional as F


class PilotNet(nn.Module):
    """NVIDIA PilotNet-style CNN for camera-based end-to-end driving.

    Architecture: Conv5 + FC4, input RGB image (256x384), output [accel, steer].
    """

    def __init__(self, image_height=256, image_width=384, output_dim=2):
        super().__init__()
        self.conv1 = nn.Conv2d(3, 24, kernel_size=5, stride=2)
        self.conv2 = nn.Conv2d(24, 36, kernel_size=5, stride=2)
        self.conv3 = nn.Conv2d(36, 48, kernel_size=5, stride=2)
        self.conv4 = nn.Conv2d(48, 64, kernel_size=3)
        self.conv5 = nn.Conv2d(64, 64, kernel_size=3)

        with torch.no_grad():
            dummy = torch.zeros(1, 3, image_height, image_width)
            out = self.conv5(self.conv4(self.conv3(self.conv2(self.conv1(dummy)))))
            self.flatten_dim = out.view(1, -1).shape[1]

        self.fc1 = nn.Linear(self.flatten_dim, 100)
        self.fc2 = nn.Linear(100, 50)
        self.fc3 = nn.Linear(50, 10)
        self.fc4 = nn.Linear(10, output_dim)
        self._initialize_weights()

    def _initialize_weights(self):
        for m in self.modules():
            if isinstance(m, (nn.Conv2d, nn.Linear)):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
        # Last layer uses tanh: use small init to avoid saturation
        nn.init.xavier_normal_(self.fc4.weight, gain=0.1)
        nn.init.constant_(self.fc4.bias, 0)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = F.relu(self.conv4(x))
        x = F.relu(self.conv5(x))
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))
        return torch.tanh(self.fc4(x))
