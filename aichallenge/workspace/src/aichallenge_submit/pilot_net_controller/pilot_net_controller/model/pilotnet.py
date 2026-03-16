import torch
import torch.nn as nn
import torch.nn.functional as F

from . import (
    conv2d,
    linear,
    relu,
    tanh,
    flatten,
    kaiming_normal_init,
    zeros_init,
)

# ============================================================
# PyTorch Models
# ============================================================

class PilotNet(nn.Module):
    """NVIDIA PilotNet-style CNN model for camera image data (Conv5 + FC4).

    Processes RGB images through 5 convolutional layers followed by
    4 fully connected layers. Based on the NVIDIA End-to-End Learning paper.

    Attributes:
        conv1-conv5: Convolutional layers.
        fc1-fc4: Fully connected layers.
    """

    def __init__(self, image_height=256, image_width=384, output_dim=2):
        super().__init__()
        self.image_height = image_height
        self.image_width = image_width

        # --- Convolutional Layers ---
        self.conv1 = nn.Conv2d(3, 24, kernel_size=5, stride=2)
        self.conv2 = nn.Conv2d(24, 36, kernel_size=5, stride=2)
        self.conv3 = nn.Conv2d(36, 48, kernel_size=5, stride=2)
        self.conv4 = nn.Conv2d(48, 64, kernel_size=3)
        self.conv5 = nn.Conv2d(64, 64, kernel_size=3)

        # --- Calculate Flatten Dimension ---
        with torch.no_grad():
            dummy_input = torch.zeros(1, 3, image_height, image_width)
            x = self.conv5(self.conv4(self.conv3(self.conv2(self.conv1(dummy_input)))))
            flatten_dim = x.view(1, -1).shape[1]

        # --- Fully Connected Layers ---
        self.fc1 = nn.Linear(flatten_dim, 100)
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

    def forward(self, x):
        """Forward pass.
        Args:
            x (torch.Tensor): Input tensor of shape (batch_size, 3, image_height, image_width).
        Returns:
            torch.Tensor: Output tensor of shape (batch_size, output_dim) with Tanh activation.
        """
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


# ============================================================
# NumPy Inference Models (Exact Naming Match with PyTorch)
# ============================================================

class PilotNetNp:
    """NumPy implementation of PilotNet (Conv5 + FC4).

    Provides pure NumPy inference matching the PyTorch PilotNet architecture.

    Attributes:
        params (dict): Stores weights and biases for all layers.
        strides (dict): Stores stride values for convolutional layers.
        shapes (dict): Stores parameter shapes for initialization.
    """

    def __init__(self, image_height=256, image_width=384, output_dim=2):
        self.image_height = image_height
        self.image_width = image_width
        self.output_dim = output_dim
        self.params = {}

        # Stride definitions (as tuples for conv2d)
        self.strides = {
            'conv1': (2, 2), 'conv2': (2, 2), 'conv3': (2, 2),
            'conv4': (1, 1), 'conv5': (1, 1)
        }

        # Shape definitions matching PyTorch (out_ch, in_ch, kh, kw)
        self.shapes = {
            'conv1_weight': (24, 3, 5, 5),   'conv1_bias': (24,),
            'conv2_weight': (36, 24, 5, 5),   'conv2_bias': (36,),
            'conv3_weight': (48, 36, 5, 5),   'conv3_bias': (48,),
            'conv4_weight': (64, 48, 3, 3),   'conv4_bias': (64,),
            'conv5_weight': (64, 64, 3, 3),   'conv5_bias': (64,),
        }

        flatten_dim = self._get_conv_output_dim()
        self.shapes.update({
            'fc1_weight': (100, flatten_dim), 'fc1_bias': (100,),
            'fc2_weight': (50, 100),          'fc2_bias': (50,),
            'fc3_weight': (10, 50),           'fc3_bias': (10,),
            'fc4_weight': (output_dim, 10),   'fc4_bias': (output_dim,),
        })

        self._initialize_weights()

    def _get_conv_output_dim(self):
        """Calculates the flattened dimension after the last convolution layer."""
        h, w = self.image_height, self.image_width
        for i in range(1, 6):
            kh, kw = self.shapes[f'conv{i}_weight'][2], self.shapes[f'conv{i}_weight'][3]
            sh, sw = self.strides[f'conv{i}']
            h = (h - kh) // sh + 1
            w = (w - kw) // sw + 1
        c = self.shapes['conv5_weight'][0]
        return c * h * w

    def _initialize_weights(self):
        for name, shape in self.shapes.items():
            if name.endswith('_weight'):
                if 'conv' in name:
                    fan_out = shape[0] * shape[2] * shape[3]
                else:
                    fan_out = shape[0]
                self.params[name] = kaiming_normal_init(shape, fan_out)
            elif name.endswith('_bias'):
                self.params[name] = zeros_init(shape)

    def __call__(self, x):
        """Forward pass.
        Args:
            x (np.ndarray): Input array of shape (batch_size, 3, image_height, image_width).
        Returns:
            np.ndarray: Output array of shape (batch_size, output_dim).
        """
        x = relu(conv2d(x, self.params['conv1_weight'], self.params['conv1_bias'], self.strides['conv1']))
        x = relu(conv2d(x, self.params['conv2_weight'], self.params['conv2_bias'], self.strides['conv2']))
        x = relu(conv2d(x, self.params['conv3_weight'], self.params['conv3_bias'], self.strides['conv3']))
        x = relu(conv2d(x, self.params['conv4_weight'], self.params['conv4_bias'], self.strides['conv4']))
        x = relu(conv2d(x, self.params['conv5_weight'], self.params['conv5_bias'], self.strides['conv5']))
        x = flatten(x)
        x = relu(linear(x, self.params['fc1_weight'], self.params['fc1_bias']))
        x = relu(linear(x, self.params['fc2_weight'], self.params['fc2_bias']))
        x = relu(linear(x, self.params['fc3_weight'], self.params['fc3_bias']))
        return tanh(linear(x, self.params['fc4_weight'], self.params['fc4_bias']))
